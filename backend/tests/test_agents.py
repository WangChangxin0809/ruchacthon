#!/usr/bin/env python3
"""Agent definitions and the agent-to-agent tools, with no Claude Code
process: seeding, copy-to-create, authorisation, defaults per role per team,
session binding and its refusal while a run is live, the run spec a
definition produces, addressing between agents and its membership refusals,
ask_human, the room inbox cursor, and the schema 2 -> 3 migration.

Run: python3 backend/tests/test_agents.py
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
TMP = tempfile.mkdtemp(prefix="wbagents-")
os.environ["WORKBENCH_DATA_DIR"] = os.path.join(TMP, "data")

from fastapi import HTTPException  # noqa: E402

from app.agents import BUILTINS, AgentDefinitions, seed_builtins  # noqa: E402
from app.artifacts import ArtifactStore  # noqa: E402
from app.auth import Auth  # noqa: E402
from app.db import SCHEMA_VERSION, Database, new_id, now  # noqa: E402
from app.devservers import DevServerManager  # noqa: E402
from app.events import EventBus  # noqa: E402
from app.notifications import Notifier  # noqa: E402
from app.providers import ProviderProfiles  # noqa: E402
from app.room import Room  # noqa: E402
from app.runs import RunManager  # noqa: E402
from app.secrets_store import SecretStore  # noqa: E402
from app.teams import Teams  # noqa: E402
from app.workspaces import WorkspaceManager  # noqa: E402


def git_repo() -> str:
    d = tempfile.mkdtemp(prefix="wbrepo-", dir=TMP)
    subprocess.run(["git", "init", "-q", d], check=True)
    Path(d, "a.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], check=True)
    return d


def raises(fn, status: int, needle: str = "") -> HTTPException:
    try:
        fn()
    except HTTPException as e:
        assert e.status_code == status, f"expected {status}, got {e.status_code}: {e.detail}"
        assert needle in str(e.detail), f"expected {needle!r} in {e.detail!r}"
        return e
    raise AssertionError(f"expected HTTP {status}, nothing raised")


async def main() -> None:
    db = Database(Path(TMP, "data", "t.db"))
    bus = EventBus(db)
    bus.bind_loop(asyncio.get_running_loop())
    events: list[dict] = []
    bus.subscribe(events.append)
    notify = Notifier(db, bus)
    teams = Teams(db, bus, notify)
    agents = AgentDefinitions(db, bus, teams)
    auth = Auth(db, teams)

    # --- the four built-ins exist, are system-trust, and re-seeding is a no-op
    rows = db.all("SELECT * FROM agent_definitions")
    assert len(rows) == 4 and {r["id"] for r in rows} == {b["id"] for b in BUILTINS}
    assert all(r["trust"] == "system" for r in rows)
    seed_builtins(db)
    assert len(db.all("SELECT * FROM agent_definitions")) == 4, "seeding twice must not duplicate"
    assert agents.get("reviewer")["permission_mode"] == "plan", "the reviewer may not edit"
    assert "Bash" in agents.get("reviewer")["disallowed_tools"]
    assert agents.get("chat")["can_spawn"] == 0

    alice = auth.register("alice", "Alice", "pw-alice-123")
    bob = auth.register("bob", "Bob", "pw-bob-12345")
    team = teams.create_team(alice, "演示团队")
    teams.add_member(team, bob["id"], "member", added_by=alice["id"])
    other = teams.create_team(bob, "Bob 的团队")

    # --- visibility: built-ins to everyone, a team's own to its members only
    mine = agents.create(alice, {"copy_from": "worker", "name": "前端工作者", "model": "claude-haiku-4-5-20251001"}, team["id"])
    assert mine["trust"] == "user" and mine["owner_id"] == alice["id"] and mine["team_id"] == team["id"]
    assert mine["prompt_role"] == "worker" and mine["model"] == "claude-haiku-4-5-20251001"
    assert mine["allowed_tools"] == agents.get("worker")["allowed_tools"], "copy-to-create copies the source's fields"
    # alice registered first, so she is the instance admin and sees everything;
    # bob is the ordinary user the visibility rules have to hold for
    solo = agents.create(alice, {"copy_from": "chat", "name": "Alice 私有"}, None)
    assert [d["id"] for d in agents.visible(alice)][:4] == [b["id"] for b in BUILTINS], "built-ins come first"
    assert mine["id"] in [d["id"] for d in agents.visible(bob)], "a teammate sees the team's definitions"
    assert solo["id"] not in [d["id"] for d in agents.visible(bob)], "a personal definition stays personal"
    raises(lambda: agents.require_visible(bob, solo["id"]), 403)
    assert [d["id"] for d in agents.visible(alice, role="orchestrator")] == ["orchestrator"]

    # --- editing: owner yes, teammate no, team admin yes, built-in never
    raises(lambda: agents.require_editable(alice, "worker"), 403, "内置定义不能改")
    raises(lambda: agents.require_editable(bob, mine["id"]), 403)
    agents.update(agents.require_editable(alice, mine["id"]), {"description": "只做前端"})
    assert agents.get(mine["id"])["description"] == "只做前端"
    teams.set_role(team, bob["id"], "admin")
    agents.update(agents.require_editable(bob, mine["id"]), {"max_turns": 12})
    assert agents.get(mine["id"])["max_turns"] == 12
    raises(lambda: agents.update(agents.get(mine["id"]), {"role": "nonsense"}), 400)
    raises(lambda: agents.update(agents.get(mine["id"]), {"permission_mode": "yolo"}), 400)
    raises(lambda: agents.update(agents.get(mine["id"]), {"max_turns": 0}), 400)
    raises(lambda: agents.create(alice, {"name": " "}, team["id"]), 400)

    # --- defaults: one per role per team; a built-in choice clears the override
    assert agents.defaults(team["id"])["worker"] == "worker", "no override means the built-in"
    agents.set_default(agents.get(mine["id"]), team["id"])
    assert agents.defaults(team["id"])["worker"] == mine["id"]
    assert agents.defaults(other["id"])["worker"] == "worker", "another team is unaffected"
    second = agents.create(alice, {"copy_from": "worker", "name": "后端工作者"}, team["id"])
    agents.set_default(agents.get(second["id"]), team["id"])
    assert agents.defaults(team["id"])["worker"] == second["id"]
    assert db.one("SELECT is_default FROM agent_definitions WHERE id = ?", [mine["id"]])["is_default"] == 0, "one default per role"
    agents.set_default(agents.get("worker"), team["id"])
    assert agents.defaults(team["id"])["worker"] == "worker", "picking the built-in clears the team override"
    raises(lambda: agents.set_default(agents.get(second["id"]), other["id"]), 400)

    # --- what a session may be bound to
    raises(lambda: agents.check_for_kind(alice, "chat", "main"), 400) if agents.get("chat")["role"] not in ("orchestrator", "any") else None
    raises(lambda: agents.check_for_kind(alice, "orchestrator", "worker"), 400, "不能用在")
    assert agents.check_for_kind(alice, "chat", "worker")["id"] == "chat", "role 'any' fits either kind"
    assert agents.check_for_kind(alice, "reviewer", "worker")["id"] == "reviewer"

    # --- a deleted definition falls back to the built-in of the session kind
    gone = agents.create(alice, {"copy_from": "worker", "name": "临时的"}, team["id"])
    agents.delete(agents.require_editable(alice, gone["id"]))
    d, missing = agents.resolve(gone["id"], "worker")
    assert d["id"] == "worker" and missing is True
    assert agents.binding({"agent_definition_id": gone["id"], "kind": "worker"})["missing"] is True
    assert "missing" not in agents.binding({"agent_definition_id": "reviewer", "kind": "worker"})
    assert agents.binding({"agent_definition_id": None, "kind": "main"})["id"] == "orchestrator"
    assert agents.binding({"agent_definition_id": "reviewer", "kind": "worker"})["permission_label"] == "仅可查看"
    assert [e for e in events if e["type"] == "agent_definition" and e["payload"]["op"] == "deleted"], "deletion is an event"

    # --- a run spec built from a definition
    workspaces = WorkspaceManager(db)
    room = Room(db, bus)
    artifacts = ArtifactStore(db, bus, workspaces)
    profiles = ProviderProfiles(db, SecretStore())
    runs = RunManager(db, bus, workspaces, room, artifacts, DevServerManager(db, bus), profiles,
                      teams=teams, notify=notify, agents=agents)
    root = git_repo()
    project = db.insert("projects", {"id": new_id("prj"), "name": "demo", "root_path": root, "team_id": team["id"], "is_git": 1,
                                     "created_at": now(), "created_by": alice["id"]})
    ws = workspaces.main_workspace(project)

    def session_of(kind: str, definition: str | None, title: str, creator: dict) -> dict:
        s = db.insert("sessions", {"id": new_id("ses"), "project_id": project["id"], "task_id": None, "kind": kind, "title": title,
                                   "cc_session_id": None, "created_at": now(), "created_by": creator["id"], "agent_name": title,
                                   "agent_definition_id": definition})
        teams.ensure_session_conversation(s, creator["id"])
        return s

    main_s = session_of("main", "orchestrator", "主 agent", alice)
    run = runs.create_run(project=project, session=main_s, workspace=ws, kind="main", prompt="hi", task_id=None, profile_id=None, created_by=alice["id"])
    spec = runs._spec(run)
    assert set(spec.mcp_servers) == {"room", "workbench"}, "an orchestrator may spawn, so it gets both servers"
    assert spec.permission_mode == "acceptEdits" and spec.max_turns == 80
    assert "spawn_worker" in spec.system_prompt_append and "room_claim" in spec.system_prompt_append
    assert "demo" in spec.system_prompt_append and ws["path"] in spec.system_prompt_append
    assert db.one("SELECT agent_definition_id FROM runs WHERE id = ?", [run["id"]])["agent_definition_id"] == "orchestrator"

    review_s = session_of("worker", "reviewer", "审查", alice)
    rrun = runs.create_run(project=project, session=review_s, workspace=ws, kind="worker", prompt="look", task_id=None, profile_id=None, created_by=alice["id"])
    rspec = runs._spec(rrun)
    assert set(rspec.mcp_servers) == {"room"}, "a definition without can_spawn gets no workbench server"
    assert "spawn_worker" not in rspec.system_prompt_append, "the prompt may not name a tool the run lacks"
    assert rspec.permission_mode == "plan" and "Bash" in rspec.disallowed_tools
    assert "message_agent" in rspec.system_prompt_append, "every run can address other agents"

    # a definition's own text is appended, and its model wins over nothing else set
    custom = agents.create(alice, {"copy_from": "worker", "name": "带指示的", "system_prompt": "永远先写测试。"}, team["id"])
    cs = session_of("worker", custom["id"], "带指示的", alice)
    crun = runs.create_run(project=project, session=cs, workspace=ws, kind="worker", prompt="go", task_id=None, profile_id=None, created_by=alice["id"])
    assert "永远先写测试。" in runs._spec(crun).system_prompt_append

    # --- addressing between agents. bob is not an instance admin, so the
    # membership rules actually bite for his agents
    teams.add_conv_member(alice, teams.conv_of_session(main_s["id"]), bob["id"])
    private = session_of("worker", "worker", "Alice 的活", alice)
    bob_s = session_of("worker", "worker", "Bob 的活", bob)
    brun = runs.create_run(project=project, session=bob_s, workspace=ws, kind="worker", prompt="go", task_id=None,
                           profile_id=None, created_by=bob["id"])
    found, refusal = runs._find_session(brun, "main")
    assert found["id"] == main_s["id"] and refusal is None, "'main' resolves to the orchestrator he shares"
    assert runs._find_session(brun, "Alice 的活")[1].startswith("refused:"), "a private session of another person is refused"
    assert runs._find_session(brun, "没这个人")[1].startswith("refused:")
    assert runs._find_session(brun, bob_s["id"])[1].startswith("refused:"), "talking to yourself is refused"
    assert runs._find_session(brun, bob_s["title"])[1].startswith("refused:")

    res = await runs.message_agent(brun, "main", "我卡住了")
    assert res["delivered"] and res["session_id"] == main_s["id"]
    got = db.all("SELECT * FROM messages WHERE session_id = ? ORDER BY created_at", [main_s["id"]])[-1]
    assert got["blocks"][0]["text"].startswith("[from agent Bob 的活"), "the receiver is told who is talking"
    assert got["meta"]["from_session"] == bob_s["id"]
    assert isinstance(await runs.message_agent(brun, "Alice 的活", "hi"), str), "a refusal comes back as text, not an exception"

    cards = [runs._session_card(x) for x in db.all("SELECT * FROM sessions WHERE project_id = ?", [project["id"]])
             if x["id"] == brun["session_id"] or runs._reachable(brun, x) is None]
    assert private["id"] not in [c["session_id"] for c in cards], "list_agents hides what message_agent would refuse"
    assert main_s["id"] in [c["session_id"] for c in cards]
    assert [c for c in cards if c["session_id"] == main_s["id"]][0]["agent_definition_id"] == "orchestrator"

    # --- ask_human notifies every human member and marks the message
    conv_b = teams.conv_of_session(bob_s["id"])
    before = len(notify.list(bob["id"]))
    asked = runs.ask_human(brun, "用蓝色还是绿色？", ["蓝色", "绿色"])
    row = db.one("SELECT * FROM messages WHERE id = ?", [asked["message_id"]])
    assert row["meta"]["needs_human"] is True and row["meta"]["options"] == ["蓝色", "绿色"]
    assert row["role"] == "assistant", "the question is the agent speaking, not a system row"
    after = notify.list(bob["id"])
    assert len(after) == before + 1 and after[0]["kind"] == "question"
    assert after[0]["link"]["session_id"] == bob_s["id"]

    # --- room inbox cursor: each read returns only what is new
    other_run = runs.create_run(project=project, session=private, workspace=ws, kind="worker", prompt="go", task_id=None, profile_id=None, created_by=alice["id"])
    await room.broadcast(other_run, "第一条")
    first = room.inbox(brun, None)
    assert len(first) == 1
    cursor = room.inbox_cursor(first[0])
    assert room.inbox(brun, cursor) == [], "nothing new after the cursor"
    await room.broadcast(other_run, "第二条")
    fresh = room.inbox(brun, cursor)
    assert len(fresh) == 1 and fresh[0]["payload"]["text"] == "第二条"

    # two broadcasts inside the same millisecond share a created_at: a cursor
    # that is only a timestamp loses one of them, and which one depends on how
    # fast the machine is. The cursor carries the id for exactly this.
    cursor = room.inbox_cursor(fresh[0])
    for i in range(6):
        await room.broadcast(other_run, f"同一毫秒 {i}")
    seen: list[str] = []
    while (batch := room.inbox(brun, cursor)):
        seen += [m["payload"]["text"] for m in batch]     # a batch is newest first
        cursor = room.inbox_cursor(batch[0])
    assert seen == [f"同一毫秒 {i}" for i in reversed(range(6))], seen
    assert room.inbox(brun, cursor) == [], "and nothing is delivered twice"
    # a cursor from a build that stored a timestamp still works: it may repeat
    # its own millisecond, but it never swallows one
    assert len(room.inbox(brun, first[0]["created_at"])) >= 7

    # --- renaming keeps the task label in step
    task = db.insert("tasks", {"id": new_id("task"), "project_id": project["id"], "title": "旧名字", "description": "", "kind": "worker",
                               "depends_on": [], "review_status": "unreviewed", "merge_status": "unmerged", "workspace_id": ws["id"],
                               "created_at": now(), "updated_at": now(), "created_by": alice["id"]})
    db.update("sessions", bob_s["id"], task_id=task["id"])
    renamed = runs.rename_session(db.one("SELECT * FROM sessions WHERE id = ?", [bob_s["id"]]), "新名字")
    assert renamed["ok"] and db.one("SELECT title FROM tasks WHERE id = ?", [task["id"]])["title"] == "新名字"
    assert db.one("SELECT agent_name FROM sessions WHERE id = ?", [bob_s["id"]])["agent_name"] == "新名字"

    # --- what a project's agents may spawn: built-ins plus the owning team's
    pickable = agents.visible_for_project(project, role_in=("worker", "any"))
    assert {"worker", "reviewer", "chat"} <= {d["id"] for d in pickable}
    assert "orchestrator" not in {d["id"] for d in pickable}
    assert solo["id"] not in {d["id"] for d in pickable}, "a personal definition is not spawnable by a project agent"

    print("all agent-definition assertions passed")


def migration() -> None:
    """A schema-2 database (batch 1) gains the built-ins and a binding for
    every session, and starting again changes nothing."""
    d = Path(TMP, "mig")
    d.mkdir(parents=True, exist_ok=True)
    db = Database(d / "old.db")
    db.set_setting("schema_version", 2)
    t = now()
    db.execute("INSERT INTO sessions (id, project_id, task_id, kind, title, created_at) VALUES (?, ?, ?, ?, ?, ?)",
               ["ses_old_main", "prj_x", None, "main", "老主会话", t])
    db.execute("INSERT INTO sessions (id, project_id, task_id, kind, title, created_at) VALUES (?, ?, ?, ?, ?, ?)",
               ["ses_old_worker", "prj_x", "task_x", "worker", "老 worker", t])
    db.execute("DELETE FROM agent_definitions")
    db.close()

    db = Database(d / "old.db")
    assert int(db.setting("schema_version")) == SCHEMA_VERSION
    assert len(db.all("SELECT * FROM agent_definitions")) == 4
    assert db.one("SELECT agent_definition_id FROM sessions WHERE id = 'ses_old_main'")["agent_definition_id"] == "orchestrator"
    assert db.one("SELECT agent_definition_id FROM sessions WHERE id = 'ses_old_worker'")["agent_definition_id"] == "worker"
    db.execute("UPDATE sessions SET agent_definition_id = 'reviewer' WHERE id = 'ses_old_worker'")
    db.close()

    db = Database(d / "old.db")
    assert db.one("SELECT agent_definition_id FROM sessions WHERE id = 'ses_old_worker'")["agent_definition_id"] == "reviewer", \
        "a second start must not re-backfill what a person has since chosen"
    assert len(db.all("SELECT * FROM agent_definitions")) == 4
    db.close()
    print("migration 2 -> 3 is idempotent")


if __name__ == "__main__":
    migration()
    asyncio.run(main())
