"""Dev servers a worker wants a human to look at, owned by the server: we
start the process, pick/verify the port, tail its log, probe health, and
stop it. An artifact of kind `devserver` is only "available" while the
process we started is alive and answering.
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path

from .db import DATA_DIR, Database, new_id, now
from .events import EventBus


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DevServerManager:
    def __init__(self, db: Database, bus: EventBus):
        self.db = db
        self.bus = bus
        self.procs: dict[str, subprocess.Popen] = {}
        self.log_dir = DATA_DIR / "devserver-logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    async def start(self, artifact: dict, run: dict, command: str, port: int | None) -> dict:
        port = int(port) if port else free_port()
        if port_open(port):
            return {"ok": False, "error": f"port {port} is already in use; pick another or omit it"}
        ws = self.db.one("SELECT * FROM workspaces WHERE id = ?", [artifact["workspace_id"]])
        ds_id = new_id("ds")
        log_path = self.log_dir / f"{ds_id}.log"
        env = {**os.environ, "PORT": str(port)}
        log = open(log_path, "ab")
        try:
            kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}
            p = subprocess.Popen(command, shell=True, cwd=ws["path"], stdout=log, stderr=subprocess.STDOUT, env=env, **kwargs)
        except OSError as e:
            return {"ok": False, "error": f"could not start: {e}"}
        row = self.db.insert("devservers", {"id": ds_id, "artifact_id": artifact["id"], "run_id": run["id"], "workspace_id": ws["id"],
                                            "command": command, "port": port, "pid": p.pid, "status": "starting", "health": None,
                                            "log_path": str(log_path), "started_at": now(), "ended_at": None})
        self.procs[ds_id] = p
        self.bus.emit("devserver", row, project_id=run["project_id"], task_id=run["task_id"], run_id=run["id"])
        asyncio.get_running_loop().create_task(self._watch(ds_id, run))
        return {"ok": True, "devserver_id": ds_id, "port": port, "url": f"http://127.0.0.1:{port}/", "log_path": str(log_path)}

    async def _watch(self, ds_id: str, run: dict) -> None:
        p = self.procs.get(ds_id)
        for _ in range(60):
            await asyncio.sleep(1)
            if p is not None and p.poll() is not None:
                break
            row = self.db.one("SELECT * FROM devservers WHERE id = ?", [ds_id])
            if row and port_open(row["port"]):
                self._set(ds_id, run, status="running", health="healthy")
                break
        while True:
            await asyncio.sleep(3)
            row = self.db.one("SELECT * FROM devservers WHERE id = ?", [ds_id])
            if not row or row["status"] in ("stopped", "exited"):
                return
            if p is not None and p.poll() is not None:
                self._set(ds_id, run, status="exited", health="down", ended_at=now())
                return
            healthy = port_open(row["port"])
            health = "healthy" if healthy else "unhealthy"
            if row["health"] != health or (row["status"] == "starting" and healthy):
                self._set(ds_id, run, status="running" if healthy else row["status"], health=health)

    def _set(self, ds_id: str, run: dict, **fields) -> None:
        self.db.update("devservers", ds_id, **fields)
        row = self.db.one("SELECT * FROM devservers WHERE id = ?", [ds_id])
        self.bus.emit("devserver", row, project_id=run["project_id"], task_id=run["task_id"], run_id=run["id"])

    def stop(self, ds_id: str) -> dict:
        row = self.db.one("SELECT * FROM devservers WHERE id = ?", [ds_id])
        if not row:
            return {"ok": False, "error": "no such devserver"}
        p = self.procs.pop(ds_id, None)
        if p is not None and p.poll() is None:
            _kill_tree(p)
        elif row["pid"]:
            from .cc_runner import kill_pid
            kill_pid(row["pid"])
        self.db.update("devservers", ds_id, status="stopped", health="down", ended_at=now())
        run = self.db.one("SELECT * FROM runs WHERE id = ?", [row["run_id"]])
        row = self.db.one("SELECT * FROM devservers WHERE id = ?", [ds_id])
        self.bus.emit("devserver", row, project_id=run["project_id"] if run else None, task_id=run["task_id"] if run else None, run_id=row["run_id"])
        return {"ok": True}

    def logs(self, ds_id: str, tail: int = 200) -> str:
        row = self.db.one("SELECT * FROM devservers WHERE id = ?", [ds_id])
        if not row or not Path(row["log_path"]).exists():
            return ""
        lines = Path(row["log_path"]).read_text(errors="replace").splitlines()
        return "\n".join(lines[-tail:])

    def reconcile_after_restart(self) -> None:
        """We do not own processes started by a previous server process, so
        anything still marked running is now, honestly, unknown -> stopped."""
        for row in self.db.all("SELECT * FROM devservers WHERE status IN ('starting','running')"):
            from .cc_runner import kill_pid, pid_alive
            if pid_alive(row["pid"]):
                kill_pid(row["pid"])
            self.db.update("devservers", row["id"], status="stopped", health="down", ended_at=now())

    def shutdown(self) -> None:
        for ds_id in list(self.procs):
            self.stop(ds_id)


def _kill_tree(p: subprocess.Popen) -> None:
    try:
        import psutil
        proc = psutil.Process(p.pid)
        for c in proc.children(recursive=True):
            c.kill()
        proc.kill()
    except Exception:  # noqa: BLE001
        try:
            p.kill()
        except OSError:
            pass
