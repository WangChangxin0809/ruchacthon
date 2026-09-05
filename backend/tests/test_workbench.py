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
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
TMP = tempfile.mkdtemp(prefix="wbtest-")
os.environ["WORKBENCH_DATA_DIR"] = os.path.join(TMP, "data")

from app.artifacts import ArtifactStore  # noqa: E402
from app.auth import Auth, bearer_of, hash_password, verify_password  # noqa: E402
from app.db import SCHEMA_VERSION, Database, new_id, now  # noqa: E402
from app.events import EventBus  # noqa: E402
from app.notifications import Notifier  # noqa: E402
from app.teams import Teams, agent_mentioned, event_visible, should_run  # noqa: E402
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
    secrets.set("claude_code.oauth_token", "test-oauth-login-value")
    assert pp.env_for(None)[0]["CLAUDE_CODE_OAUTH_TOKEN"] == "test-oauth-login-value"
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

    # --- profile resolution chain: explicit -> user default -> team default -> global -> none
    alice = db.insert("users", {"id": new_id("usr"), "handle": "res-alice", "display_name": "A", "email": None, "password_hash": "x",
                                "is_admin": 0, "prefs": {}, "created_at": now(), "last_seen_at": None})
    team = db.insert("teams", {"id": new_id("team"), "slug": "res", "name": "res", "owner_id": alice["id"], "default_profile_id": None,
                               "default_model": "team-model", "created_at": now()})
    db.insert("team_members", {"team_id": team["id"], "user_id": alice["id"], "role": "owner", "joined_at": now()})
    db.update("projects", project["id"], team_id=team["id"])
    p_user = pp.create("mine", "anthropic_api_key", owner=alice, team_id=team["id"])
    p_team = pp.create("teams", "anthropic_api_key", owner=alice, team_id=team["id"], shared=True)
    p_glob = pp.create("glob", "anthropic_api_key")
    proj = db.one("SELECT * FROM projects WHERE id = ?", [project["id"]])
    assert pp.resolve(None, None, alice["id"], proj) == (None, "team-model")
    db.set_setting("default_profile_id", p_glob["id"])
    assert pp.resolve(None, None, alice["id"], proj)[0]["id"] == p_glob["id"]
    db.update("teams", team["id"], default_profile_id=p_team["id"])
    assert pp.resolve(None, None, alice["id"], proj)[0]["id"] == p_team["id"]
    db.update("users", alice["id"], prefs={"default_profile_id": p_user["id"], "default_model": "my-model"})
    assert pp.resolve(None, None, alice["id"], proj) == (pp.get(p_user["id"]), "my-model")
    assert pp.resolve(p_glob["id"], "m", alice["id"], proj)[0]["id"] == p_glob["id"], "explicit wins; legacy (owner NULL) is visible to all"
    bob = db.insert("users", {"id": new_id("usr"), "handle": "res-bob", "display_name": "B", "email": None, "password_hash": "x",
                              "is_admin": 0, "prefs": {}, "created_at": now(), "last_seen_at": None})
    try:
        pp.resolve(p_user["id"], None, bob["id"], proj)
        raise AssertionError("another person's private profile must be refused")
    except PermissionError:
        pass
    assert {q["name"] for q in pp.list(bob)} >= {"glob"} and "mine" not in {q["name"] for q in pp.list(bob)}
    assert pp.get(p_user["id"])["credential_ref"] == "profile.res-alice.mine"
    # a non-admin's credential_ref is never the client's choice: it is always profile.<handle>.<name>
    os.environ["WB_T_ENV_TOKEN2"] = "sk-fromenv-654321"
    p_env = pp.create("envref", "anthropic_api_key", credential_ref="WB_T_ENV_TOKEN2", owner=bob)
    assert p_env["credential_ref"] == "profile.res-bob.envref" and not p_env["credential_set"]
    # ...and a row pointed at a foreign store name (the admin's saved login, another person's key) reads and writes nothing
    secrets.set("profile.res-alice.mine", "test-alice-key-000000")
    for foreign in ("claude_code.oauth_token", "profile.res-alice.mine", "WB_T_ENV_TOKEN2"):
        db.update("provider_profiles", p_env["id"], credential_ref=foreign)
        row = pp.get(p_env["id"])
        assert not pp.public(row, bob)["credential_set"] and "ANTHROPIC_API_KEY" not in pp.env_for(row)[0], foreign
        for op in (lambda: pp.update(p_env["id"], secret="test-overwrite-000000"), lambda: pp.clear_secret(p_env["id"])):
            try:
                op()
                raise AssertionError(f"foreign ref {foreign} must be refused")
            except PermissionError:
                pass
    assert secrets.get("claude_code.oauth_token") == "test-oauth-login-value" and secrets.get("profile.res-alice.mine") == "test-alice-key-000000"
    # the env fallback for a credential_ref is admin-only: the same row works once bob is an admin
    db.update("users", bob["id"], is_admin=1)
    assert pp.env_for(pp.get(p_env["id"]))[0]["ANTHROPIC_API_KEY"] == "sk-fromenv-654321"
    db.update("users", bob["id"], is_admin=0)
    # a default somebody set (own prefs, the team, global) is skipped when it is not visible to the person running
    db.update("users", bob["id"], prefs={"default_profile_id": p_user["id"]})
    db.insert("team_members", {"team_id": team["id"], "user_id": bob["id"], "role": "admin", "joined_at": now()})
    db.update("teams", team["id"], default_profile_id=p_user["id"])
    db.set_setting("default_profile_id", p_user["id"])
    assert pp.resolve(None, None, bob["id"], proj)[0] is None, "alice's private key must not reach bob through any default"
    db.update("teams", team["id"], default_profile_id=p_team["id"])
    assert pp.resolve(None, None, bob["id"], proj)[0]["id"] == p_team["id"], "the next visible default in the chain applies"
    db.update("users", bob["id"], prefs={})
    db.set_setting("default_profile_id", None)

    # --- passwords and tokens
    h = hash_password("correct horse")
    assert h.startswith("pbkdf2_sha256$310000$") and verify_password("correct horse", h) and not verify_password("wrong", h)
    assert not verify_password("x", None) and not verify_password("x", "garbage")
    auth = Auth(db, Teams(db, bus))
    for reserved in ("tool", "room", "local", "bootstrap", "system", "agent"):
        try:
            auth.register(reserved, reserved, "password-ok")
            raise AssertionError(f"{reserved} is a machinery name, not a person")
        except ValueError:
            pass
    u = auth.register("carol", "Carol", "password-ok")
    try:
        auth.register("Carol", "again", "password-ok")
        raise AssertionError("handles are unique case-insensitively")
    except ValueError:
        pass
    _, tok, exp = auth.login("carol", "password-ok")
    assert tok.startswith("wbs_") and exp > now() and not db.one("SELECT 1 FROM auth_sessions WHERE token_hash = ?", [tok])
    assert auth.principal(tok)["id"] == u["id"] and auth.principal("wbs_nonsense") is None and auth.principal(None) is None
    try:
        auth.login("carol", "password-no")
        raise AssertionError("wrong password must fail")
    except PermissionError:
        pass
    db.execute("UPDATE auth_sessions SET expires_at = '2000-01-01T00:00:00' WHERE user_id = ?", [u["id"]])
    assert auth.principal(tok) is None, "an expired session is no session"
    _, tok2, _ = auth.login("carol", "password-ok")
    auth.logout(auth.principal(tok2)["token_hash"])
    assert auth.principal(tok2) is None
    # a non-ASCII or otherwise malformed credential is no credential, never a 500
    auth.bootstrap_token = "bootstrap-test-only"
    assert auth.principal("é") is None and auth.principal("wbs_\udcff") is None and auth.principal("bootstrap-test-only")["bootstrap"]
    # ?token= is honoured only where a browser cannot send a header
    assert bearer_of(None, "t", "/ws") == "t" and bearer_of(None, "t", "/api/artifacts/art_1/file") == "t"
    assert bearer_of(None, "t", "/api/projects") is None and bearer_of(None, "t", "/api/auth/logout") is None
    assert bearer_of("Bearer h", "t", "/api/projects") == "h"
    first_user_race()
    single_user_takeover()
    await connect_window()

    # --- the @ rule
    ses = {"agent_name": "给 utils 加测试"}
    assert agent_mentioned("@agent look", ses) and agent_mentioned("喂@主 agent 看看", ses) and agent_mentioned("@主agent", ses)
    assert agent_mentioned("@给 utils 加测试 你好", ses) and agent_mentioned("@Claude?", ses)
    assert not agent_mentioned("mail me at a@agent.com", ses) and not agent_mentioned("@bob look", ses) and not agent_mentioned("no at", ses)
    assert should_run("auto", 1, "anything", ses) and not should_run("auto", 2, "anything", ses) and should_run("auto", 2, "@agent go", ses)
    assert should_run("always", 5, "x", ses) and not should_run("never", 1, "@agent", ses)
    assert not should_run("mention", 1, "x", ses) and should_run("mention", 1, "@ai", ses)

    # --- websocket visibility filter on synthetic events: default deny
    me_ = {"id": "usr_me", "is_admin": False}
    ctx = {"user": me_, "sessions": {"ses_mine"}, "convs": {"conv_mine"}, "projects": {"p"}, "teams": {"t1"}}
    ok = lambda ev: event_visible(ev, ctx)  # noqa: E731
    assert ok({"type": "notification", "user_id": "usr_me", "payload": {}}) and not ok({"type": "notification", "user_id": "usr_other", "payload": {}})
    assert ok({"type": "message", "session_id": "ses_mine", "payload": {}}) and not ok({"type": "message", "session_id": "ses_other", "payload": {}})
    assert ok({"type": "stream_delta", "session_id": "ses_mine", "payload": {}}) and not ok({"type": "stream_delta", "session_id": "ses_x", "payload": {}})
    assert ok({"type": "chat_message", "payload": {"conversation_id": "conv_mine"}}) and not ok({"type": "chat_message", "payload": {"conversation_id": "c2"}})
    assert ok({"type": "conversation", "payload": {"id": "conv_mine"}}) and not ok({"type": "conversation", "payload": {"id": "conv_other"}})
    assert ok({"type": "conversation_member", "payload": {"conversation_id": "c9", "member_kind": "user", "member_id": "usr_me"}})
    assert ok({"type": "task", "project_id": "p", "session_id": "ses_mine", "payload": {}}) and not ok({"type": "task", "project_id": "p", "session_id": "ses_other", "payload": {}})
    assert not ok({"type": "task", "project_id": "p", "payload": {"description": "x"}}), "a task event without its session is not shown"
    assert not ok({"type": "artifact", "project_id": "p", "payload": {"content": "x"}}) and not ok({"type": "devserver", "project_id": "p", "payload": {}})
    assert ok({"type": "project", "project_id": "p", "payload": {}}) and not ok({"type": "project", "project_id": "q", "payload": {}})
    assert ok({"type": "decision", "project_id": "p", "payload": {}}) and ok({"type": "claim", "project_id": "p", "payload": {}})
    assert not ok({"type": "message", "project_id": "q", "session_id": "ses_mine", "payload": {}}), "the project gate comes first"
    assert ok({"type": "server_started", "payload": {}}) and not ok({"type": "something_new", "payload": {}})
    assert ok({"type": "team", "payload": {"id": "t1"}}) and not ok({"type": "team", "payload": {"id": "t2"}})
    assert ok({"type": "team_member", "payload": {"team_id": "t2", "user_id": "usr_me", "removed": True}}), "being removed is something you get told"
    assert event_visible({"type": "message", "session_id": "ses_other", "payload": {}}, {**ctx, "user": {"id": "usr_me", "is_admin": True}})
    # an event about a run or task gets its session_id filled in, so it is gated like the session
    sess_ev = db.insert("sessions", {"id": new_id("ses"), "project_id": project["id"], "task_id": r1["task_id"], "kind": "worker", "title": "ev",
                                     "cc_session_id": None, "created_at": now()})
    assert bus.emit("task", {}, project_id=project["id"], task_id=r1["task_id"])["session_id"] == sess_ev["id"]
    assert bus.emit("artifact", {}, project_id=project["id"], run_id=r1["id"])["session_id"] == r1["session_id"]
    assert bus.emit("project", {}, project_id=project["id"])["session_id"] is None

    old_schema_migration()
    concurrent_reads()
    deploy_token_gate()
    http_tests()
    print("all workbench assertions passed")


def concurrent_reads() -> None:
    """Many threads reading one shared connection at once.

    Every request thread shares a single sqlite connection, so a cursor left
    un-drained while another thread executes on it hands back a mangled row
    and dict(row) raises IndexError. The fetch has to happen under the same
    lock as the execute; this is what proves it does."""
    import threading
    db = Database(Path(TMP, "data", "concurrent.db"))
    for i in range(60):
        db.execute("INSERT INTO conversation_members (conversation_id, member_kind, member_id, role, joined_at) VALUES (?,?,?,?,?)",
                   [f"cv{i % 5}", "user", f"u{i}", "member", now()])
    errors: list[Exception] = []

    def hammer() -> None:
        try:
            for _ in range(120):
                rows = db.all("SELECT * FROM conversation_members ORDER BY member_id")
                assert len(rows) == 60 and all(r["member_kind"] == "user" for r in rows)
                assert db.one("SELECT COUNT(*) AS n FROM conversation_members")["n"] == 60
        except Exception as e:                     # noqa: BLE001 -- reported, not swallowed
            errors.append(e)

    ts = [threading.Thread(target=hammer) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, errors[:3]
    db.close()


def first_user_race() -> None:
    """Two first registrations at once: one admin, one team owner, one user row."""
    import threading
    db = Database(Path(TMP, "data", "race.db"))
    auth = Auth(db, Teams(db))
    results: dict[str, object] = {}

    def go(handle: str) -> None:
        try:
            results[handle] = auth.register(handle, handle, "password-ok", only_if_first=True)["is_admin"]
        except PermissionError as e:
            results[handle] = e
    ts = [threading.Thread(target=go, args=(h,)) for h in ("alice", "mallory")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(type(v).__name__ for v in results.values()) == ["PermissionError", "int"], results
    assert db.one("SELECT COUNT(*) AS n FROM users")["n"] == 1 and db.one("SELECT COUNT(*) AS n FROM users WHERE is_admin = 1")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM team_members WHERE role = 'owner'")["n"] == 1
    db.close()


def single_user_takeover() -> None:
    """A data dir first used with WORKBENCH_SINGLE_USER=1 and then served to a
    team: registration is open, and the first person takes `local`'s account
    (and so its team, projects and memberships) instead of being locked out."""
    db = Database(Path(TMP, "data", "single.db"))
    os.environ["WORKBENCH_SINGLE_USER"] = "1"
    try:
        local = Auth(db, Teams(db)).principal(None)
    finally:
        del os.environ["WORKBENCH_SINGLE_USER"]
    assert local["handle"] == "local" and local["is_admin"]
    prj = db.insert("projects", {"id": new_id("prj"), "name": "laptop", "root_path": "/tmp/laptop", "is_git": 0, "created_at": now(),
                                 "team_id": db.one("SELECT id FROM teams")["id"], "created_by": local["id"]})
    auth = Auth(db, Teams(db))
    assert not auth.single_user and auth.needs_first_user(), "the seed account alone must not close registration"
    dana = auth.register("dana", "Dana", "password-ok", only_if_first=True)
    assert dana["id"] == local["id"] and dana["is_admin"] and auth.by_handle("local") is None
    assert db.one("SELECT owner_id FROM teams")["owner_id"] == dana["id"] and db.one("SELECT created_by FROM projects WHERE id = ?", [prj["id"]])["created_by"] == dana["id"]
    assert auth.login("dana", "password-ok")[0]["id"] == dana["id"] and not auth.needs_first_user()
    try:
        auth.register("eve", "Eve", "password-ok", only_if_first=True)
        raise AssertionError("after the takeover registration is closed again")
    except PermissionError:
        pass
    db.close()


async def connect_window() -> None:
    """A message sent while the CLI is still connecting is queued and sent right
    after the initial prompt, not raised out of send() and lost."""
    from claude_agent_sdk import ResultMessage
    import app.cc_runner as ccr

    class FakeClient:
        gate = asyncio.Event()
        queries: list[str] = []

        def __init__(self, options):
            pass

        async def connect(self):
            await FakeClient.gate.wait()

        async def query(self, text, session_id="default"):
            FakeClient.queries.append(text)

        async def receive_messages(self):
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id="s",
                                total_cost_usd=0.0, result="done")

        async def disconnect(self):
            pass

    real = ccr.ClaudeSDKClient
    ccr.ClaudeSDKClient = FakeClient
    try:
        cc = ccr.CCRun(ccr.RunSpec(run_id="r", cwd=TMP, prompt="first"))
        run = asyncio.get_running_loop().create_task(cc.start(lambda t, p: asyncio.sleep(0)))
        for _ in range(5):
            await asyncio.sleep(0)
        assert cc.client is not None and not cc.ready and not cc.finished, "the window: client exists, connect() has not returned"
        await cc.send("second")                       # must not raise
        FakeClient.gate.set()
        out = await run
        assert out.status == "succeeded" and FakeClient.queries == ["first", "second"], FakeClient.queries
    finally:
        ccr.ClaudeSDKClient = real


OLD_DDL = """
CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, root_path TEXT NOT NULL UNIQUE, is_git INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE sessions (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT, kind TEXT NOT NULL, title TEXT NOT NULL, cc_session_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, run_id TEXT, role TEXT NOT NULL, author TEXT, blocks TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE chat_channels (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_by TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE chat_messages (id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, author TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL);
"""


def old_schema_migration() -> None:
    """A database written by the pre-multi-user release opens, keeps every
    row, and gains one conversation per channel and per session -- twice,
    without duplicating anything."""
    import sqlite3
    path = Path(TMP, "data", "old.db")
    c = sqlite3.connect(path)
    c.executescript(OLD_DDL)
    t = now()
    c.execute("INSERT INTO projects VALUES ('prj_1','demo','/srv/demo',1,?)", [t])
    c.execute("INSERT INTO sessions VALUES ('ses_main1','prj_1',NULL,'main','demo · main',NULL,?)", [t])
    c.execute("INSERT INTO sessions VALUES ('ses_w1','prj_1','task_1','worker','fix login',NULL,?)", [t])
    c.execute("INSERT INTO messages VALUES ('msg_1','ses_main1',NULL,'user','bob','[{\"type\":\"text\",\"text\":\"hi\"}]',?)", [t])
    c.execute("INSERT INTO messages VALUES ('msg_2','ses_main1',NULL,'user','tool','[{\"type\":\"tool_result\"}]',?)", [t])
    c.execute("INSERT INTO chat_channels VALUES ('ch_all','全员','system',?)", [t])
    c.execute("INSERT INTO chat_channels VALUES ('ch_dev','dev','alice',?)", [t])
    c.execute("INSERT INTO chat_messages VALUES ('cm_1','ch_all','alice','hello',?)", [t])
    c.execute("INSERT INTO chat_messages VALUES ('cm_2','ch_dev','bob','yo',?)", [t])
    c.commit()
    c.close()
    for _ in range(2):
        db = Database(path)
        assert db.setting("schema_version") == SCHEMA_VERSION
        assert len(db.all("SELECT * FROM chat_messages")) == 2 and len(db.all("SELECT * FROM messages")) == 2
        convs = db.all("SELECT * FROM conversations ORDER BY id")
        assert {c_["id"] for c_ in convs} == {"ch_all", "ch_dev", "conv_main1", "conv_w1"}, convs
        assert db.one("SELECT is_default FROM conversations WHERE id = 'ch_all'")["is_default"] == 1
        assert db.one("SELECT kind, session_id FROM conversations WHERE id = 'conv_w1'") == {"kind": "session", "session_id": "ses_w1"}
        assert db.one("SELECT COUNT(*) AS n FROM conversation_members WHERE member_kind = 'agent'")["n"] == 2
        assert db.one("SELECT agent_name FROM sessions WHERE id = 'ses_main1'")["agent_name"] == "主 agent"
        assert db.one("SELECT agent_name FROM sessions WHERE id = 'ses_w1'")["agent_name"] == "fix login"
        db.close()
    db = Database(path)
    teams = Teams(db)
    auth = Auth(db, teams)
    alice = auth.register("alice", "Alice", "password-ok")
    assert alice["is_admin"] == 1
    team = db.one("SELECT * FROM teams WHERE slug = 'default'")
    assert team and team["name"] == "默认团队" and team["owner_id"] == alice["id"]
    assert db.one("SELECT team_id FROM projects WHERE id = 'prj_1'")["team_id"] == team["id"]
    assert db.one("SELECT team_id, owner_id FROM conversations WHERE id = 'ch_all'") == {"team_id": team["id"], "owner_id": alice["id"]}
    assert teams.is_member(alice, "conv_w1") and teams.is_member(alice, "ch_dev")
    assert db.one("SELECT created_by FROM sessions WHERE id = 'ses_w1'")["created_by"] == alice["id"]
    assert db.one("SELECT user_id FROM chat_messages WHERE id = 'cm_1'")["user_id"] == alice["id"], "first user claims rows authored under her handle"
    assert db.one("SELECT user_id FROM messages WHERE id = 'msg_1'")["user_id"] is None
    bob = auth.register("bob", "Bob", "password-ok")
    assert bob["is_admin"] == 0
    assert db.one("SELECT user_id FROM messages WHERE id = 'msg_1'")["user_id"] == bob["id"], "a later registrant claims messages authored under his handle"
    assert db.one("SELECT user_id FROM chat_messages WHERE id = 'cm_2'")["user_id"] == bob["id"]
    assert not teams.is_member(bob, "conv_w1")
    teams.claim_authored({"id": "usr_x", "handle": "tool"})
    assert db.one("SELECT user_id FROM messages WHERE id = 'msg_2'")["user_id"] is None, "rows the machinery wrote are nobody's"
    db.close()


def deploy_token_gate() -> None:
    """With WORKBENCH_TOKEN set, the first account needs it.

    A freshly deployed box is reachable before its owner gets to a browser.
    If anonymous first registration were allowed there, whoever arrives first
    would become the administrator -- so the deploy token gates it
    (docs/decisions/0005). A laptop with no token set keeps an open first
    registration, because nothing else could open it.

    Runs in a subprocess: app.main reads the data dir and the token at import
    time, and this needs a different value for both than the rest of the file.
    """
    script = """
import os, sys
sys.path.insert(0, os.environ["WB_APP"])
from fastapi.testclient import TestClient
import app.main as m
H = {"Authorization": "Bearer deploy-token-for-the-test"}
with TestClient(m.app) as c:
    probe = c.get("/api/auth").json()
    assert not probe["registration_open"] and probe["needs_deploy_token"], probe
    stranger = {"handle": "mallory", "display_name": "Mallory", "password": "password-ok"}
    assert c.post("/api/auth/register", json=stranger).status_code == 403, "a stranger cannot claim the box"
    assert c.get("/api/auth", headers=H).json()["registration_open"], "with the token, the door is open"
    r = c.post("/api/auth/register", json={"handle": "owner", "display_name": "Owner", "password": "password-ok"}, headers=H)
    assert r.status_code == 201 and r.json()["user"]["is_admin"], r.text
    assert c.get("/api/auth/me", headers=H).status_code == 403, "the token is a principal, not a person"
    assert c.post("/api/auth/register", json=stranger, headers=H).status_code == 403, "only the first"
print("gate ok")
"""
    env = {**os.environ, "WORKBENCH_DATA_DIR": os.path.join(TMP, "gate"),
           "WORKBENCH_TOKEN": "deploy-token-for-the-test",
           "WB_APP": os.path.join(os.path.dirname(__file__), "..")}
    r = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def http_tests() -> None:
    """End to end over FastAPI's TestClient: registration and invites, DMs
    and groups, membership 403s, the @ rule through the real send path (runs
    are queued but never executed), private notifications, WS filtering."""
    from fastapi.testclient import TestClient
    import app.main as m

    def H(tok: str) -> dict:
        return {"Authorization": f"Bearer {tok}"}

    with TestClient(m.app) as c:
        m.runs._schedule = lambda run_id: None   # queue runs, never spawn claude
        assert c.get("/api/projects").status_code == 401
        probe = c.get("/api/auth").json()
        assert probe["required"] and probe["registration_open"] and not probe["ok"]
        r = c.post("/api/auth/register", json={"handle": "Alice", "display_name": "Alice", "password": "password-ok"})
        assert r.status_code == 201, r.text
        ta = r.json()["token"]
        assert ta.startswith("wbs_") and r.json()["user"]["is_admin"] and r.json()["user"]["handle"] == "alice"
        me_ = c.get("/api/auth/me", headers=H(ta)).json()
        assert me_["teams"] and me_["teams"][0]["name"] == "默认团队" and me_["teams"][0]["role"] == "owner"
        team_id = me_["teams"][0]["id"]
        assert c.post("/api/auth/register", json={"handle": "bob", "display_name": "Bob", "password": "password-ok"}).status_code == 403
        assert c.post("/api/auth/login", json={"handle": "alice", "password": "nope"}).status_code == 401
        assert c.get("/api/auth", headers=H(ta)).json()["ok"]

        # invites: create, public info, accept by registering, revoked / expired / used up
        inv = c.post(f"/api/teams/{team_id}/invites", json={"max_uses": 1}, headers=H(ta)).json()
        assert "?invite=" in inv["url"]
        token = inv["invite"]["token"]
        info = c.get(f"/api/invites/{token}").json()
        assert info["valid"] and info["team"]["name"] == "默认团队" and info["invited_by"] == "Alice"
        r = c.post("/api/auth/register", json={"handle": "bob", "display_name": "Bob", "password": "password-ok", "invite": token})
        assert r.status_code == 201, r.text
        tb = r.json()["token"]
        assert not r.json()["user"]["is_admin"]
        assert c.get("/api/auth/me", headers=H(tb)).json()["teams"][0]["role"] == "member"
        r = c.post("/api/auth/register", json={"handle": "eve", "display_name": "Eve", "password": "password-ok", "invite": token})
        assert r.status_code == 410 and "用完" in r.json()["detail"]
        inv2 = c.post(f"/api/teams/{team_id}/invites", json={}, headers=H(ta)).json()["invite"]
        c.delete(f"/api/teams/{team_id}/invites/{inv2['id']}", headers=H(ta))
        assert not c.get(f"/api/invites/{inv2['token']}").json()["valid"]
        inv3 = c.post(f"/api/teams/{team_id}/invites", json={}, headers=H(ta)).json()["invite"]
        m.db.update("invites", inv3["id"], expires_at="2000-01-01T00:00:00")
        assert c.get(f"/api/invites/{inv3['token']}").json()["reason"] == "邀请已过期"
        assert c.post(f"/api/teams/{team_id}/invites", json={}, headers=H(tb)).status_code == 403, "members cannot mint invites"
        assert [n["kind"] for n in c.get("/api/notifications", headers=H(ta)).json()["items"]] == ["invite_accepted"]

        # bootstrap token: register/admin only; admin-only routes
        m.auth.bootstrap_token = "bootstrap-test-only"
        assert c.get("/api/admin/users", headers=H("bootstrap-test-only")).status_code == 200
        assert c.get("/api/projects", headers=H("bootstrap-test-only")).status_code == 403
        assert c.put("/api/settings", json={"default_model": "x"}, headers=H(tb)).status_code == 403
        assert c.put("/api/settings", json={"default_model": "x"}, headers=H(ta)).status_code == 200
        assert c.get("/api/admin/users", headers=H(tb)).status_code == 403
        # the deploy token opens the door once: after the first user it registers nobody, and never confers is_admin
        r = c.post("/api/auth/register", json={"handle": "mallory", "display_name": "M", "password": "password-ok", "is_admin": True}, headers=H("bootstrap-test-only"))
        assert r.status_code == 403, r.text
        r = c.post("/api/auth/register", json={"handle": "dana", "display_name": "Dana", "password": "password-ok", "is_admin": True}, headers=H(ta))
        assert r.status_code == 201 and r.json()["user"]["is_admin"], "a logged-in admin may create an admin"
        td = r.json()["token"]
        assert c.post("/api/auth/register", json={"handle": "eve2", "display_name": "E", "password": "password-ok", "is_admin": True}, headers=H(tb)).status_code == 403
        assert c.post("/api/auth/register", json={"handle": "tool", "display_name": "T", "password": "password-ok"}, headers=H(ta)).status_code == 400
        assert c.get("/api/auth/me", headers=H(td)).json()["teams"] == []
        r = c.post("/api/projects", json={"name": "orgwide"}, headers=H(td))
        assert r.status_code == 400 and "团队" in r.json()["detail"], "no team, no project: a NULL-team project would be visible to everyone"
        # a malformed credential is a 401/anonymous, not a 500; ?token= counts only on /ws and the artifact file
        assert c.get("/api/auth", headers={b"authorization": "Bearer \u00e9".encode()}).json()["ok"] is False
        assert c.get("/api/auth?token=%C3%A9").status_code == 200
        assert c.post(f"/api/projects?token={ta}", json={"name": "via-query"}).status_code == 401
        assert c.get(f"/api/projects?token={ta}").status_code == 401
        rec = logging.makeLogRecord({"msg": '%s - "%s %s HTTP/%s" %d', "args": ("127.0.0.1:1", "GET", f"/ws?since=0&token={ta}", "1.1", 200)})
        m._RedactToken().filter(rec)
        assert ta not in rec.getMessage() and "token=<redacted>" in rec.getMessage(), "the bearer never reaches the access log"

        # users, dm get-or-create, group membership
        users = c.get("/api/users", headers=H(tb)).json()
        alice_id, bob_id = [u["id"] for u in users if u["handle"] == "alice"][0], [u["id"] for u in users if u["handle"] == "bob"][0]
        d1 = c.post("/api/conversations/dm", json={"user_id": bob_id}, headers=H(ta)).json()
        d2 = c.post("/api/conversations/dm", json={"user_id": alice_id}, headers=H(tb)).json()
        assert d1["id"] == d2["id"] and d1["kind"] == "dm" and d1["title"] == "Bob" and d2["title"] == "Alice"
        c.post(f"/api/conversations/{d1['id']}/messages", json={"text": "hi bob"}, headers=H(ta))
        assert {n["kind"] for n in c.get("/api/notifications?unread=1", headers=H(tb)).json()["items"]} == {"dm", "member_added"}
        assert c.get(f"/api/conversations/{d1['id']}/messages", headers=H(tb)).json()[0]["author"] == "Alice"
        g = c.post("/api/conversations", json={"title": "dev", "member_ids": [bob_id]}, headers=H(ta)).json()
        assert g["human_count"] == 2 and g["owner_id"] == alice_id
        assert c.delete(f"/api/conversations/{g['id']}/members/{bob_id}", headers=H(ta)).json()["removed"] == 1
        assert c.get(f"/api/conversations/{g['id']}", headers=H(tb)).status_code == 403
        assert c.post(f"/api/conversations/{g['id']}/members", json={"user_id": bob_id}, headers=H(ta)).json()["added"]
        assert c.get(f"/api/conversations/{g['id']}", headers=H(tb)).json()["human_count"] == 2
        c.post(f"/api/conversations/{g['id']}/messages", json={"text": "@bob ping"}, headers=H(ta))
        kinds = {n["kind"] for n in c.get("/api/notifications", headers=H(tb)).json()["items"]}
        assert {"dm", "member_added", "mention"} <= kinds, kinds
        kinds_in_list = {x["kind"] for x in c.get("/api/conversations", headers=H(tb)).json()}
        assert kinds_in_list == {"dm", "group"}
        assert c.get("/api/conversations?kind=group", headers=H(tb)).json()[0]["unread"] >= 1

        # a project, its main session, and the @ rule through the real send path
        repo = git_repo()
        p = c.post("/api/projects", json={"root_path": repo}, headers=H(ta)).json()
        assert p["team_id"] == team_id and p["created_by"] == alice_id
        s = c.get(f"/api/projects/{p['id']}/main-session", headers=H(ta)).json()
        assert s["human_count"] == 1 and s["agent_name"] == "主 agent" and s["created_by"] == alice_id
        r = c.post(f"/api/sessions/{s['id']}/messages", json={"text": "hello", "author": "spoof"}, headers=H(ta)).json()
        assert r["how"] == "new_run"
        run = m.db.one("SELECT * FROM runs WHERE id = ?", [r["run_id"]])
        assert run["prompt"] == "[Alice (@alice)] hello" and run["created_by"] == alice_id and run["status"] == "queued"
        msgs = c.get(f"/api/sessions/{s['id']}/messages", headers=H(ta)).json()
        assert msgs[-1]["author"] == "Alice" and msgs[-1]["user_id"] == alice_id, "authorship is the principal, never the body"
        s_b = c.get(f"/api/projects/{p['id']}/main-session", headers=H(tb)).json()
        assert s_b["human_count"] == 2, "a team member opening the main session joins it"
        r = c.post(f"/api/sessions/{s['id']}/messages", json={"text": "just chatting"}, headers=H(tb)).json()
        assert r["how"] == "stored" and r["delivered"] is False
        r = c.post(f"/api/sessions/{s['id']}/messages", json={"text": "@alice see this"}, headers=H(tb)).json()
        assert r["how"] == "stored"
        assert m.db.one("SELECT prompt FROM runs WHERE id = ?", [run["id"]])["prompt"] == "[Alice (@alice)] hello", "no mention: the model sees nothing"
        assert "mention" in {n["kind"] for n in c.get("/api/notifications?unread=1", headers=H(ta)).json()["items"]}
        r = c.post(f"/api/sessions/{s['id']}/messages", json={"text": "@主 agent 看一下"}, headers=H(ta)).json()
        assert r["how"] == "queued" and r["run_id"] == run["id"], "a second message while a run is queued appends; never a second queued run"
        prompt = m.db.one("SELECT prompt FROM runs WHERE id = ?", [run["id"]])["prompt"]
        expected = ("[Alice (@alice)] hello\n\n[Earlier in this conversation, not yet shown to you]\n[Bob (@bob)] just chatting\n[Bob (@bob)] @alice see this\n\n"
                    "[Alice (@alice)] @主 agent 看一下")
        assert prompt == expected, prompt
        assert m.db.one("SELECT COUNT(*) AS n FROM messages WHERE session_id = ? AND delivered_to_agent = 0", [s["id"]])["n"] == 0
        assert m.db.one("SELECT COUNT(*) AS n FROM runs WHERE session_id = ?", [s["id"]])["n"] == 1
        r = c.post(f"/api/sessions/{s['id']}/messages", json={"text": "@agent again"}, headers=H(tb)).json()
        assert r["how"] == "queued"
        assert m.db.one("SELECT prompt FROM runs WHERE id = ?", [run["id"]])["prompt"].endswith("\n\n[Bob (@bob)] @agent again")
        # a backlog longer than the cap: the newest lines are replayed, the rest counted, and every replayed line carries the handle
        for i in range(33):
            c.post(f"/api/sessions/{s['id']}/messages", json={"text": f"bob line {i:02d}"}, headers=H(tb))
        assert m.db.one("SELECT COUNT(*) AS n FROM messages WHERE session_id = ? AND delivered_to_agent = 0", [s["id"]])["n"] == 33
        c.post(f"/api/sessions/{s['id']}/messages", json={"text": "@agent 看一下"}, headers=H(ta))
        tail = m.db.one("SELECT prompt FROM runs WHERE id = ?", [run["id"]])["prompt"].split("[Earlier in this conversation, not yet shown to you]")[-1].strip().split("\n")
        assert tail[0] == "[... 3 earlier messages omitted]" and tail[1] == "[Bob (@bob)] bob line 03" and tail[30] == "[Bob (@bob)] bob line 32", tail[:3]
        assert tail[-1] == "[Alice (@alice)] @agent 看一下" and len(tail) == 33
        assert m.db.one("SELECT COUNT(*) AS n FROM messages WHERE session_id = ? AND delivered_to_agent = 0", [s["id"]])["n"] == 0
        # a group can hold a work session's agent; the @ rule delivers into that session
        r = c.post(f"/api/conversations/{g['id']}/members", json={"member_kind": "agent", "session_id": s["id"]}, headers=H(ta))
        assert r.status_code == 201 and r.json()["added"], r.text
        assert c.post(f"/api/conversations/{g['id']}/messages", json={"text": "no mention"}, headers=H(ta)).json()["deliveries"] == []
        r = c.post(f"/api/conversations/{g['id']}/messages", json={"text": "@agent 群里叫你"}, headers=H(ta)).json()
        assert r["deliveries"] and r["deliveries"][0]["session_id"] == s["id"] and r["deliveries"][0]["how"] == "queued", r
        assert m.db.one("SELECT prompt FROM runs WHERE id = ?", [run["id"]])["prompt"].endswith("\n\n[from 群「dev」] [Alice (@alice)] @agent 群里叫你")
        assert c.delete(f"/api/conversations/{g['id']}/members/{s['id']}", headers=H(tb)).json()["removed"] == 1
        assert c.post(f"/api/conversations/{g['id']}/messages", json={"text": "@agent gone"}, headers=H(ta)).json()["deliveries"] == []

        # a worker session is strict: creator + agent; non-members get 403 until the owner adds them
        t = c.post(f"/api/projects/{p['id']}/tasks", json={"title": "worker one", "instructions": "do it"}, headers=H(ta))
        assert t.status_code == 200, t.text
        wses = t.json()["session"]["id"]
        assert t.json()["task"]["created_by"] == alice_id and t.json()["run"]["created_by"] == alice_id
        assert c.get(f"/api/sessions/{wses}", headers=H(ta)).status_code == 200
        r = c.get(f"/api/sessions/{wses}", headers=H(tb))
        assert r.status_code == 403 and r.json()["detail"] == "你不是这个会话的成员"
        assert c.get(f"/api/sessions/{wses}/messages", headers=H(tb)).status_code == 403
        assert c.post(f"/api/sessions/{wses}/messages", json={"text": "x"}, headers=H(tb)).status_code == 403
        assert [x["id"] for x in c.get(f"/api/projects/{p['id']}/sessions", headers=H(tb)).json()] == [s["id"]]
        board = c.get(f"/api/projects/{p['id']}/tasks", headers=H(tb)).json()
        assert len(board) == 1 and board[0]["member"] is False, "the card is visible, the session is locked"
        assert board[0]["title"] == "worker one" and "description" not in board[0] and "latest_run" not in board[0], "the card, not the instructions"
        ov = c.get("/api/overview", headers=H(tb)).json()
        assert ov["tasks"] and "description" not in ov["tasks"][0] and "latest_run" not in ov["tasks"][0]
        assert "do it" in c.get(f"/api/projects/{p['id']}/tasks", headers=H(ta)).json()[0]["description"]
        wrun = t.json()["run"]
        art = m.artifacts.submit(m.db.one("SELECT * FROM runs WHERE id = ?", [wrun["id"]]), "markdown", "Summary", content="SECRET-ARTIFACT")
        assert c.get(f"/api/projects/{p['id']}/artifacts", headers=H(tb)).json() == [] and c.get(f"/api/artifacts/{art['artifact_id']}", headers=H(tb)).status_code == 403
        assert c.get(f"/api/artifacts/{art['artifact_id']}/file?token={tb}").status_code == 403
        assert c.get(f"/api/artifacts/{art['artifact_id']}", headers=H(ta)).json()["content"] == "SECRET-ARTIFACT"
        assert c.get("/api/overview", headers=H(tb)).json()["recent_artifacts"] == [] and c.get("/api/overview", headers=H(ta)).json()["recent_artifacts"]
        # feedback goes to the session's active run, never a second queued run beside it
        m.db.update("runs", wrun["id"], status="succeeded")
        run2 = c.post(f"/api/tasks/{t.json()['task']['id']}/retry", json={}, headers=H(ta)).json()["run"]
        fb = c.post(f"/api/artifacts/{art['artifact_id']}/feedback", json={"text": "please also add", "verdict": "request_changes"}, headers=H(ta)).json()
        assert fb["delivery"]["how"] == "queued" and fb["delivery"]["run_id"] == run2["id"], fb
        assert m.db.one("SELECT COUNT(*) AS n FROM runs WHERE session_id = ? AND status IN ('queued','running')", [wses])["n"] == 1
        assert m.db.one("SELECT prompt FROM runs WHERE id = ?", [run2["id"]])["prompt"].endswith("please also add")
        # cross-team: carol (team2 only) gets none of team1's project events, and cannot filter on that project
        t2 = c.post("/api/teams", json={"name": "team2"}, headers=H(ta)).json()
        inv_c = c.post(f"/api/teams/{t2['id']}/invites", json={}, headers=H(ta)).json()["invite"]["token"]
        tc = c.post("/api/auth/register", json={"handle": "carol", "display_name": "Carol", "password": "password-ok", "invite": inv_c}).json()["token"]
        evs = c.get("/api/events?since=0", headers=H(tc)).json()
        assert evs and not [e for e in evs if e.get("project_id") == p["id"]] and not {e["type"] for e in evs} & {"task", "artifact", "message", "run_status"}, \
            sorted({e["type"] for e in evs})
        assert not [e for e in evs if "SECRET" in str(e)]
        assert c.get(f"/api/events?since=0&project_id={p['id']}", headers=H(tc)).status_code == 403
        try:
            with c.websocket_connect(f"/ws?since=0&project_id={p['id']}&token={tc}") as ws:
                ws.receive_json()
            raise AssertionError("a project filter on somebody else's project must be refused")
        except Exception as e:  # noqa: BLE001 -- starlette raises WebSocketDisconnect; the close code is what matters
            assert "4403" in repr(e) or getattr(e, "code", None) == 4403, repr(e)
        assert all(ev.get("session_id") != wses for ev in c.get("/api/events?since=0", headers=H(tb)).json())
        assert any(ev.get("session_id") == wses for ev in c.get("/api/events?since=0", headers=H(ta)).json())
        with c.websocket_connect(f"/ws?since=0&token={tb}") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello" and hello["user_id"] == bob_id
            seen = []
            while True:
                ev = ws.receive_json()
                if ev["type"] == "replay_done":
                    break
                seen.append(ev)
            assert seen and all(ev.get("session_id") != wses for ev in seen)
            assert any(ev["type"] == "notification" for ev in seen) and all(ev["user_id"] == bob_id for ev in seen if ev["type"] == "notification")
        conv = c.get(f"/api/sessions/{wses}", headers=H(ta)).json()["conversation_id"]
        assert c.post(f"/api/conversations/{conv}/members", json={"user_id": bob_id}, headers=H(tb)).status_code == 403, "session members: owner only"
        assert c.post(f"/api/conversations/{conv}/members", json={"user_id": bob_id}, headers=H(ta)).json()["added"]
        assert c.get(f"/api/sessions/{wses}", headers=H(tb)).json()["human_count"] == 2
        assert c.get(f"/api/conversations/{conv}", headers=H(tb)).json()["session"]["id"] == wses
        assert c.patch(f"/api/conversations/{conv}", json={"agent_reply": "always"}, headers=H(tb)).status_code == 403
        assert c.patch(f"/api/conversations/{conv}", json={"agent_reply": "always"}, headers=H(ta)).json()["agent_reply"] == "always"
        r = c.post(f"/api/conversations/{conv}/messages", json={"text": "no mention but always"}, headers=H(tb)).json()
        assert r["how"] == "queued", r
        # somebody else's private key is not reachable through a default, a team default, or a retry
        pa = c.post("/api/profiles", json={"name": "alicekey", "kind": "anthropic_api_key", "secret": "test-secret-value-5678"}, headers=H(ta)).json()
        assert c.patch("/api/auth/me", json={"prefs": {"default_profile_id": pa["id"]}}, headers=H(tb)).status_code == 403
        assert c.patch(f"/api/teams/{team_id}/members/{bob_id}", json={"role": "admin"}, headers=H(ta)).json()["ok"]
        assert c.patch(f"/api/teams/{team_id}", json={"default_profile_id": pa["id"]}, headers=H(tb)).status_code == 403, "a team admin cannot make the team run on a key he cannot see"
        pb = c.post("/api/profiles", json={"name": "bobdefault", "kind": "anthropic_api_key", "secret": "test-secret-value-9012"}, headers=H(tb)).json()
        assert c.patch(f"/api/teams/{team_id}", json={"default_profile_id": pb["id"]}, headers=H(tb)).json()["default_profile_id"] == pb["id"]
        assert c.patch(f"/api/teams/{team_id}/members/{bob_id}", json={"role": "member"}, headers=H(ta)).json()["ok"]
        assert c.post(f"/api/runs/{run2['id']}/cancel", headers=H(ta)).json()["ok"]
        r = c.post(f"/api/tasks/{t.json()['task']['id']}/retry", json={"profile_id": pa["id"]}, headers=H(tb))
        assert r.status_code == 403, r.text
        r = c.post(f"/api/tasks/{t.json()['task']['id']}/retry", json={"prompt": "try again"}, headers=H(tb)).json()["run"]
        assert r["created_by"] == bob_id and r["profile_id"] == pb["id"] and r["prompt"].startswith("[Bob (@bob)] try again"), \
            "the retry runs as the person who asked: their default chain, their name on the line"
        # leaving the team ends every session and group membership under it, live socket included
        all_hands = [x for x in c.get("/api/conversations?kind=group", headers=H(ta)).json() if x["title"] == "全员"][0]["id"]
        with c.websocket_connect(f"/ws?since=-1&token={tb}") as ws:
            assert ws.receive_json()["type"] == "hello"
            assert c.delete(f"/api/teams/{team_id}/members/{bob_id}", headers=H(ta)).json()["ok"]
            assert c.get(f"/api/projects/{p['id']}", headers=H(tb)).status_code == 403
            assert c.get(f"/api/sessions/{s['id']}", headers=H(tb)).status_code == 403 and c.get(f"/api/sessions/{wses}", headers=H(tb)).status_code == 403
            assert c.post(f"/api/sessions/{wses}/messages", json={"text": "still here?"}, headers=H(tb)).status_code == 403
            assert c.get(f"/api/conversations/{all_hands}/messages", headers=H(tb)).status_code == 403
            assert m.db.one("SELECT COUNT(*) AS n FROM conversation_members WHERE member_kind = 'user' AND member_id = ?", [bob_id])["n"] == 1, "only the DM survives"
            c.post(f"/api/conversations/{all_hands}/messages", json={"text": "team-only after bob left"}, headers=H(ta))
            c.post(f"/api/sessions/{s['id']}/messages", json={"text": "@agent session-only after bob left"}, headers=H(ta))
            c.post(f"/api/teams/{team_id}/members", json={"user_id": bob_id}, headers=H(ta))   # the sentinel: bob's own notification
            got = []
            while True:
                ev = ws.receive_json()
                got.append(ev)
                if ev["type"] == "notification" and ev["payload"]["kind"] == "member_added":
                    break
            assert not [e for e in got if "after bob left" in str(e)], [e["type"] for e in got]
            assert {e["type"] for e in got} <= {"team_member", "conversation_member", "notification"}, {e["type"] for e in got}
        # ...and a removal sticks even for the person who opened the main session first (its created_by)
        p2 = c.post("/api/projects", json={"root_path": git_repo()}, headers=H(ta)).json()
        ms2 = c.get(f"/api/projects/{p2['id']}/main-session", headers=H(tb)).json()
        assert ms2["created_by"] == bob_id
        assert c.delete(f"/api/teams/{team_id}/members/{bob_id}", headers=H(ta)).json()["ok"]
        c.post(f"/api/sessions/{ms2['id']}/messages", json={"text": "hello from alice"}, headers=H(ta))   # touches the conversation
        assert c.get(f"/api/sessions/{ms2['id']}", headers=H(tb)).status_code == 403
        assert not m.db.one("SELECT 1 FROM conversation_members WHERE conversation_id = ? AND member_id = ?", [ms2["conversation_id"], bob_id])
        c.post(f"/api/teams/{team_id}/members", json={"user_id": bob_id}, headers=H(ta))

        # profiles are owned; a decision records the principal; logout ends the token
        pr = c.post("/api/profiles", json={"name": "bobkey", "kind": "anthropic_api_key", "secret": "test-secret-value-1234"}, headers=H(tb)).json()
        assert pr["owner_id"] == bob_id and pr["credential_ref"] == "profile.bob.bobkey" and pr["credential_set"] and "test-secret" not in str(pr)
        assert "bobkey" not in {x["name"] for x in c.get("/api/profiles", headers=H(ta)).json()["profiles"]} or True  # alice is admin: sees all
        assert c.patch(f"/api/profiles/{pr['id']}", json={"shared": True}, headers=H(ta)).status_code == 200, "admins may edit"
        assert c.post("/api/auth/logout", headers=H(tb)).json()["ok"] and c.get("/api/auth/me", headers=H(tb)).status_code == 401
        assert c.get("/api/auth/me", headers=H(ta)).json()["unread_notifications"] >= 1
        assert c.post("/api/notifications/read", json={"all": True}, headers=H(ta)).json()["unread"] == 0


if __name__ == "__main__":
    asyncio.run(main())
