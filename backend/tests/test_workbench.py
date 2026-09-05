#!/usr/bin/env python3
"""Unit-level proof of the workbench mechanics with no Claude Code process:
event ordering/replay, Room claims/leases/conflicts/decisions, artifact
versioning, provider env + scrubbing, task status derivation, restart
reconciliation, and the shared-edit CRDT merge. What it cannot prove --
that a real model uses these tools well -- is covered by the acceptance
log in docs/reference/acceptance-2026-09-05.md.

Run: python3 backend/tests/test_workbench.py
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
TMP = tempfile.mkdtemp(prefix="wbtest-")
os.environ["WORKBENCH_DATA_DIR"] = os.path.join(TMP, "data")

from app.artifacts import ArtifactStore  # noqa: E402
from app.db import Database, new_id, now  # noqa: E402
from app.events import EventBus  # noqa: E402
from app.providers import ProviderProfiles, scrub  # noqa: E402
from app.secrets_store import SecretStore  # noqa: E402
from app.room import Room, normalize_path, overlaps  # noqa: E402
from app.shared_edit import SharedEditor  # noqa: E402
from app.workspaces import WorkspaceManager  # noqa: E402


def git_repo() -> str:
    d = tempfile.mkdtemp(prefix="wbrepo-", dir=TMP)
    subprocess.run(["git", "init", "-q", d], check=True)
    Path(d, "a.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], check=True)
    return d


async def main() -> None:
    db = Database(Path(TMP, "data", "t.db"))
    bus = EventBus(db)
    bus.bind_loop(asyncio.get_running_loop())
    seen: list[dict] = []
    bus.subscribe(seen.append)

    # --- events: monotonic seq, replay from a point, ephemeral not persisted
    e1 = bus.emit("a", {"n": 1})
    bus.emit("stream_delta", {"text": "x"}, persist=False)
    e3 = bus.emit("b", {"n": 3})
    assert e1["seq"] < e3["seq"] and len(seen) == 3
    assert [e["type"] for e in bus.since(e1["seq"])] == ["b"], "replay must return exactly the persisted events after seq"

    # --- project + workspaces
    repo = git_repo()
    wm = WorkspaceManager(db)
    project = db.insert("projects", {"id": new_id("prj"), "name": "t", "root_path": repo, "is_git": 1, "created_at": now()})
    main_ws = wm.main_workspace(project)
    ws1 = wm.create_isolated(project, "worker one")
    ws2 = wm.create_isolated(project, "worker two")
    assert ws1["kind"] == "worktree" and ws1["path"] != ws2["path"] and Path(ws1["path"], "a.py").exists()
    Path(ws1["path"], "a.py").write_text("x = 2\n")
    d = wm.diff(ws1, project)
    assert d["available"] and "+x = 2" in d["diff"]
    assert not wm.diff(db.insert("workspaces", {"id": new_id("ws"), "project_id": project["id"], "kind": "dir", "path": TMP, "branch": None,
                                                 "base_ref": None, "status": "active", "edit_mode": "exclusive", "created_at": now()}), project)["available"], \
        "a plain-dir workspace must say it cannot diff/merge"

    # --- room: path normalisation and overlap
    assert normalize_path(f"{ws1['path']}/src/x.py", ws1["path"], repo) == "src/x.py"
    assert normalize_path("src\\x.py", ws1["path"], repo) == "src/x.py"
    try:
        normalize_path("../etc/passwd", ws1["path"], repo)
        raise AssertionError("escape must be refused")
    except ValueError:
        pass
    assert overlaps("src", "src/x.py") and not overlaps("src/a.py", "src/b.py")

    room = Room(db, bus)
    delivered: list[tuple[str, str]] = []

    async def fake_deliver(run_id: str, text: str) -> dict:
        delivered.append((run_id, text))
        return {"ok": True, "how": "live"}

    room.deliver = fake_deliver

    def mk_run(ws: dict, title: str) -> dict:
        t = now()
        task = db.insert("tasks", {"id": new_id("task"), "project_id": project["id"], "title": title, "description": "", "kind": "worker",
                                   "parent_task_id": None, "depends_on": [], "review_status": "unreviewed", "merge_status": "unmerged",
                                   "workspace_id": ws["id"], "created_at": t, "updated_at": t})
        ses = db.insert("sessions", {"id": new_id("ses"), "project_id": project["id"], "task_id": task["id"], "kind": "worker", "title": title,
                                     "cc_session_id": None, "created_at": t})
        return db.insert("runs", {"id": new_id("run"), "project_id": project["id"], "task_id": task["id"], "session_id": ses["id"],
                                  "workspace_id": ws["id"], "profile_id": None, "profile_snapshot": {}, "kind": "worker", "attempt_no": 1,
                                  "prompt": "", "status": "running", "outcome": None, "cc_session_id": None, "pid": None, "started_at": t,
                                  "ended_at": None, "result_summary": None, "error": None, "cost_usd": None, "num_turns": None, "created_at": t})

    r1, r2, r3 = mk_run(ws1, "one"), mk_run(ws2, "two"), mk_run(ws1, "three")
    c1 = await room.claim(r1, "src/x.py", "editing")
    assert c1["ok"] and not c1["overlaps"]
    c2 = await room.claim(r2, f"{ws2['path']}/src/x.py", "also editing")
    assert c2["ok"] and c2["overlaps"][0]["run_id"] == r1["id"], "different workspaces: overlap notice, not a lock"
    assert delivered and delivered[-1][0] == r1["id"] and "Overlap" in delivered[-1][1]
    c3 = await room.claim(r3, "src/x.py", "conflict")
    assert c3["conflict"] and c3["status"] == "pending", "same workspace: must escalate, never auto-resolve"
    pending = db.all("SELECT * FROM decisions WHERE status = 'pending'")
    assert len(pending) == 1 and pending[0]["blocked_run_id"] == r3["id"]
    c3b = await room.claim(r3, "src/x.py", "again")
    assert c3b["decision_id"] == pending[0]["id"] and len(db.all("SELECT * FROM decisions")) == 1, "repeat claim must not duplicate the decision"
    res = await room.decide(pending[0]["id"], "approve", "three wins", "human")
    assert res["ok"] and any("granted" in e for e in res["effects"])
    holder_claim = db.one("SELECT status FROM claims WHERE id = ?", [c1["claim_id"]])
    assert holder_claim["status"] == "released", "approval must release the holder's claim"
    assert {d[0] for d in delivered[-2:]} == {r1["id"], r3["id"]}, "both runs must receive the decision"
    assert (await room.decide(pending[0]["id"], "reject", "", "x"))["ok"] is False, "a decision is final"

    # leases: expire -> claim gone; heartbeat renews
    db.execute("UPDATE claims SET expires_at = '2000-01-01T00:00:00' WHERE run_id = ?", [r2["id"]])
    assert room.expire() and not [c for c in room.active_claims(project["id"]) if c["run_id"] == r2["id"]]
    await room.claim(r2, "src/y.py")
    before = db.one("SELECT expires_at FROM claims WHERE run_id = ? AND status='active'", [r2["id"]])["expires_at"]
    await asyncio.sleep(0.01)
    room.heartbeat(r2["id"])
    after = db.one("SELECT expires_at FROM claims WHERE run_id = ? AND status='active'", [r2["id"]])["expires_at"]
    assert after > before

    # handoff transfers and notifies the target's latest run
    h = await room.handoff(r3, "src/x.py", r2["task_id"], "yours now")
    assert h["ok"] and h["delivered_to_run"] == r2["id"] and "Handoff" in delivered[-1][1]
    assert db.one("SELECT status FROM claims WHERE run_id = ? AND path = 'src/x.py' ORDER BY created_at DESC LIMIT 1", [r3["id"]])["status"] == "handed_off"

    # --- artifacts: versions supersede, diff versions per task, feedback recorded against the version
    store = ArtifactStore(db, bus, wm)
    a1 = store.submit(r1, "markdown", "Summary", content="v1")
    a2 = store.submit(r1, "markdown", "Summary", content="v2")
    assert a2["version"] == 2 and db.one("SELECT status FROM artifacts WHERE id = ?", [a1["artifact_id"]])["status"] == "superseded"
    d1 = store.submit(r1, "diff", "first diff")
    d2 = store.submit(r1, "diff", "renamed diff")
    assert d2["version"] == 2 and db.one("SELECT status FROM artifacts WHERE id = ?", [d1["artifact_id"]])["status"] == "superseded"
    bad = store.submit(r1, "image", "pic", path="../../etc/passwd")
    assert not bad["ok"]
    Path(ws1["path"], "shot.png").write_bytes(b"\x89PNG")
    img = store.submit(r1, "image", "pic", path="shot.png")
    assert img["ok"] and store.public(store.get(img["artifact_id"]))["has_file"]
    fb = store.record_feedback(store.get(a2["artifact_id"]), "human", "request_changes", "more")
    assert fb["version"] == 2 and db.one("SELECT review_status FROM tasks WHERE id = ?", [r1["task_id"]])["review_status"] == "changes_requested"

    # --- providers: env injection and scrubbing, never a secret in a snapshot
    secrets = SecretStore(Path(TMP, "data", "secrets.json"))
    pp = ProviderProfiles(db, secrets)
    prof = pp.create("gw", "anthropic_compatible_gateway", base_url="http://gw.local", models=["claude-sonnet-5", "claude-opus-5"],
                     extra_env={"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "8000"}, secret="sk-verysecret-abcdef")
    assert prof["credential_set"] and prof["credential_source"] == "store" and prof["model"] == "claude-sonnet-5"
    assert oct(os.stat(secrets.path).st_mode & 0o777) == "0o600", "secret file must be private"
    env, snap = pp.env_for(pp.get(prof["id"]), "claude-opus-5")
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-verysecret-abcdef" and env["ANTHROPIC_BASE_URL"] == "http://gw.local" and env["ANTHROPIC_MODEL"] == "claude-opus-5"
    assert "sk-verysecret" not in str(snap) and "sk-verysecret" not in str(pp.public(pp.get(prof["id"]))) and "sk-verysecret" not in str(pp.list())
    assert scrub("auth failed for sk-verysecret-abcdef", env) == "auth failed for <ANTHROPIC_AUTH_TOKEN>"
    # env-var fallback still works for a ref that is not in the store
    os.environ["WB_T_ENV_TOKEN"] = "sk-fromenv-123456"
    prof2 = pp.create("gw2", "anthropic_api_key", credential_ref="WB_T_ENV_TOKEN")
    assert prof2["credential_set"] and prof2["credential_source"] == "env"
    assert pp.env_for(pp.get(prof2["id"]))[0]["ANTHROPIC_API_KEY"] == "sk-fromenv-123456"
    # the saved Claude Code login token reaches default-profile runs and is shadowed by API-key profiles
    secrets.set("claude_code.oauth_token", "sk-ant-oat01-login")
    assert pp.env_for(None)[0]["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-login"
    assert pp.env_for(pp.get(prof2["id"]))[0]["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    try:
        pp.create("bad", "anthropic_api_key", extra_env={"API_KEY": "x"})
        raise AssertionError("secret-looking extra_env must be refused")
    except ValueError:
        pass
    pp.delete(prof["id"])
    assert not secrets.is_set("profile.gw"), "deleting a profile deletes its secret"

    # --- shared edit (experimental): concurrent whole-file writes converge
    ed = SharedEditor(ws1["path"])
    Path(ws1["path"], "s.txt").write_text("a\nb\nc\n")
    ed.note_seen("A", "s.txt")
    ed.note_seen("B", "s.txt")
    m1 = ed.merge_write("A", "s.txt", "a1\nb\nc\n")
    Path(ws1["path"], "s.txt").write_text(m1)
    m2 = ed.merge_write("B", "s.txt", "a\nb\nc2\n")
    assert m2 == "a1\nb\nc2\n", m2

    # --- restart reconciliation: a 'running' run with no live process becomes 'interrupted'
    from app.devservers import DevServerManager
    from app.runs import RunManager
    devs = DevServerManager(db, bus)
    mgr = RunManager(db, bus, wm, room, store, devs, pp)
    fixed = mgr.reconcile()
    assert {r["id"] for r in fixed} >= {r1["id"], r2["id"], r3["id"]}
    assert db.one("SELECT status FROM runs WHERE id = ?", [r1["id"]])["status"] == "interrupted"
    assert not [c for c in room.active_claims(project["id"]) if c["run_id"] == r2["id"]], "interrupted runs drop their claims"
    # task status derives from run facts + review + merge, in that order
    assert mgr.task_status(r1["task_id"]) == "interrupted"
    db.update("runs", r1["id"], status="succeeded")
    assert mgr.task_status(r1["task_id"]) == "changes_requested"
    db.update("tasks", r1["task_id"], review_status="approved")
    assert mgr.task_status(r1["task_id"]) == "ready_to_merge"
    db.update("tasks", r1["task_id"], merge_status="merged")
    assert mgr.task_status(r1["task_id"]) == "done"
    # merge is refused for non-worktree, works for a worktree
    assert not wm.merge_into_main(main_ws, project, "x")["ok"]
    m = wm.merge_into_main(ws1, project, "merge one")
    assert m["ok"], m
    assert Path(repo, "a.py").read_text() == "x = 2\n"
    print("all workbench assertions passed")


if __name__ == "__main__":
    asyncio.run(main())
