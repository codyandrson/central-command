"""Run modes end to end (epic-run-modes, story 13).

Through the PUBLIC seams only — the app's lifespan in a TestClient, the
`run_modes.py` CLI, the email intake route, the dispatcher step route, the
cockpit's approve RPC and the Decisions RPC:

(a) the guard: each run-mode key in a `.env` loaded the way config.py loads it
    stops the app's startup naming the key, and fails the CLI's `--env-file`
    and `--environ` forms; with none present the rule passes;
(b) the test model is unreachable from the environment;
(c) a fresh database's first proposal — the installer's own fixture email,
    drafted as the graph episode the real demo drafts — executes live against
    a fake graph ingest client, with no mode consulted;
(d) no run-mode string survives outside the allowlist below;
(e) with NO fake installed, one approved proposal per write-capable family
    lands FAILED loud, never RETRY_PENDING, and opens no outbound socket.
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from central_command.api import nerve_gateway, routes
from central_command.config import Settings, settings
from central_command.db import repo
from central_command.gateway import gateway
from central_command.integrations import graphiti_ingest
from central_command.runtime.spike_model import make_deferred_tool_model
from tests.conftest import needs_pg

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "central_command" / "run_modes.py"
FIXTURE = ROOT / "fixtures" / "emails" / "007-ownership-change.eml"
KEYS = ("CC_TEST_MODEL", "CC_DEMO_MODE", "CC_EXECUTOR_MODE")
REFUSED = [
    ("CC_TEST_MODEL", "1"),
    ("CC_DEMO_MODE", "false"),
    ("CC_EXECUTOR_MODE", "live"),
    ("CC_EXECUTOR_MODE", ""),
]


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)


def _clear(monkeypatch):
    """config.py's load_dotenv mirrored the worktree's `.env` (a copy of the
    live one, which may still carry the old keys) into os.environ."""
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)


def _cli(*args):
    return subprocess.run([sys.executable, "-I", str(MODULE), *args],
                          capture_output=True, text=True, cwd=ROOT, timeout=60)


# --- (a) the guard -------------------------------------------------------------------


@pytest.mark.parametrize(("key", "value"), REFUSED)
def test_a_run_mode_key_in_the_env_file_stops_startup_naming_the_key(
    monkeypatch, tmp_path, key, value,
):
    from central_command.api import app as app_module

    _clear(monkeypatch)
    env = tmp_path / ".env"
    env.write_text(f"CC_LLM_BASE_URL=http://127.0.0.1:4000\n{key}={value}\n")
    opened = []
    monkeypatch.setattr(app_module.event_log, "opened", lambda: opened.append(1))
    load_dotenv(env)  # how config.py loads the root .env into the process
    try:
        assert os.environ[key] == value
        with pytest.raises(RuntimeError, match=f"refusing to start: {key}="):
            with TestClient(app_module.app):
                pytest.fail("the API served with a run-mode key present")
    finally:
        os.environ.pop(key, None)
    assert opened == [], "the lifespan opened the event log before refusing"


@pytest.mark.parametrize(("key", "value"), REFUSED)
def test_the_cli_fails_the_key_in_an_env_file_and_in_an_environ(tmp_path, key, value):
    env = tmp_path / ".env"
    env.write_text(f"CC_LLM_BASE_URL=http://127.0.0.1:4000\n{key}={value}\n")
    out = _cli("--env-file", str(env))
    assert out.returncode == 1, out.stdout
    [line] = out.stdout.splitlines()
    assert line.startswith(f"FAIL run-modes: {key}={value!r} is set in {env}: remove {key} ")

    environ = tmp_path / "environ"
    environ.write_bytes(f"PATH=/usr/bin\0{key}={value}\0HOME=/x\0".encode())
    out = _cli("--environ", str(environ))
    assert out.returncode == 1, out.stdout
    [line] = out.stdout.splitlines()
    assert line.startswith(f"FAIL run-modes: {key}={value!r} is set in {environ}")


def test_with_none_of_the_keys_the_rule_passes_and_the_cli_exits_0(monkeypatch, tmp_path):
    from central_command import run_modes
    from central_command.api import app as app_module

    _clear(monkeypatch)
    assert run_modes.check(os.environ) == []
    app_module._refuse_run_modes()  # the lifespan's first call: no raise

    env = tmp_path / ".env"
    env.write_text("CC_LLM_BASE_URL=http://127.0.0.1:4000\n# CC_EXECUTOR_MODE=live\n")
    environ = tmp_path / "environ"
    environ.write_bytes(b"".join(
        f"{k}={v}\0".encode() for k, v in os.environ.items()))
    out = _cli("--env-file", str(env), "--environ", str(environ))
    assert out.returncode == 0, out.stdout
    assert out.stdout.startswith("PASS run-modes: none of CC_TEST_MODEL")


# --- (b) the test model is unreachable from the environment -------------------------


def test_the_environment_cannot_turn_the_test_model_on():
    env = {k: v for k, v in os.environ.items() if k not in KEYS}
    env.update(CC_TEST_MODEL="true", CC_DEMO_MODE="true")
    code = ("from central_command.config import Settings, settings\n"
            "print('test_model', settings.test_model, Settings().test_model)\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=ROOT, env=env, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "test_model False False"


# --- shared: the public seams -------------------------------------------------------


def _client() -> httpx.AsyncClient:
    """The routes without the lifespan (its startup sweeps are not under test)."""
    from central_command.api.app import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cc")


async def _rpc(method: str, params: dict | None = None):
    return await nerve_gateway._dispatch(method, params or {}, None)


def _drafting(monkeypatch, model) -> None:
    """Both routes resolve the triage model themselves; the approve route
    resolves the session's for the closing turn. Hand all of them the fake."""
    async def fixed(*_a, **_kw):
        return model

    monkeypatch.setattr(routes, "resolve_model", fixed)
    monkeypatch.setattr(routes, "resolve_session_model", fixed)


def _proposal(capability: str, arguments: dict, claim: str, system: str) -> dict:
    return {
        "intent": f"{capability} from the email",
        "actions": [{"capability": capability, "arguments": arguments,
                     "target_ref": {"system": system, "id": "new"},
                     "reversibility": "reversible"}],
        "evidence": [{"kind": "email", "source_ref": "email:body",
                      "locator": "body", "claim": claim}],
        "expected_effect": f"{capability} performed",
        "confidence": {"level": "high", "rationale": "The test states it."},
    }


async def _feed_and_approve(monkeypatch, text: str, proposal: dict) -> tuple[str, dict]:
    """POST the email, step the dispatcher, approve through the cockpit RPC —
    then wait for the detached closing turn the approval started."""
    _drafting(monkeypatch, make_deferred_tool_model("propose_action", {"proposal": proposal}))
    async with _client() as c:
        fed = await c.post("/api/emails", json={"text": text})
        assert fed.status_code == 200, fed.text
        pid = fed.json().get("proposal_id")
        assert pid, fed.json()
        # The intake route already dispatched its item; the step finds nothing of ours.
        stepped = await c.post("/api/dispatch/step")
        assert stepped.status_code == 200, stepped.text
        assert pid not in str(stepped.json())
    out = await _rpc("proposals.approve", {"id": pid})
    await asyncio.gather(*list(gateway._detached_resumes))
    return pid, out


async def _history_row(pid: str) -> dict:
    page = await _rpc("decisions.history", {"limit": 200})
    rows = [r for r in jsonable_encoder(page["proposals"]) if r["id"] == pid]
    assert len(rows) == 1, f"{pid} not on the first Decisions history page"
    return rows[0]


# --- (c) first run live ---------------------------------------------------------------


@pytest.fixture
async def fresh_database():
    """The test database emptied of proposals (it is throwaway, made per
    session), and of the fixture's own work item so its real Message-ID
    enrolls again."""
    conn = await repo._conn()
    try:
        await conn.execute("update wiki_claim set proposal_id = null")
        await conn.execute("delete from proposal")
        await conn.execute("delete from work_item where message_id = $1",
                           "<ownership-2026-07-06@example.com>")
        assert await conn.fetchval("select count(*) from proposal") == 0
    finally:
        await conn.close()


class _NoMode:
    """Settings carry no run mode; any read of one would raise here."""

    def __init__(self):
        self.reads: list[str] = []

    def attr(self, name: str):
        def read(_self):
            self.reads.append(name)
            raise AssertionError(f"a run mode was consulted: settings.{name}")
        return property(read)


@needs_pg
async def test_a_fresh_databases_first_proposal_executes_live_against_a_fake(
    monkeypatch, fresh_database,
):
    no_mode = _NoMode()
    for name in ("executor_mode", "demo_mode"):
        assert name not in Settings.model_fields
        monkeypatch.setattr(Settings, name, no_mode.attr(name), raising=False)

    writes: list[dict] = []

    async def fake_enqueue(name, episode_body, source_description, group_id, *,
                           reference_time, proposal_id, scope, marker):
        writes.append({"name": name, "proposal_id": proposal_id, "scope": scope})
        return {"verification": {"id": "v-1"}, "job": {"id": 7}}

    monkeypatch.setattr(graphiti_ingest, "enqueue", fake_enqueue)

    episode = {"name": "Billing service ownership", "scope": "shared",
               "reference_time": "2026-07-06T10:02:11Z",
               "episode_body": "As of 2026-07-06 Priya Rivers owns the billing service; "
                               "Marcus Doe handed it over and moved to the platform team."}
    pid, out = await _feed_and_approve(
        monkeypatch, FIXTURE.read_text(),
        _proposal("graph.add_episode", episode,
                  "Priya Rivers owns the billing service", "graphiti"))

    assert out["ok"] is True, out
    assert writes == [{"name": episode["name"], "proposal_id": pid, "scope": "shared"}]
    events = await repo.list_events(ref_id=pid, limit=100)
    kinds = [e["kind"] for e in events]
    assert kinds.index("proposal.decided") < kinds.index("proposal.executed"), kinds
    [executed] = [e for e in events if e["kind"] == "proposal.executed"]
    assert "[dry-run]" not in executed["payload"]["result_text"]
    assert "queued for extraction" in executed["payload"]["result_text"]

    async with _client() as c:
        got = (await c.get(f"/api/proposals/{pid}")).json()
    assert got["status"] == "EXECUTED"
    assert got["simulated"] is False
    assert (await _history_row(pid))["simulated"] is False
    assert no_mode.reads == []


# --- (d) the string guard -------------------------------------------------------------


WALK_ROOTS = ("central_command", "web/src", "web/server/routes", "deploy", "scripts",
              "skills", ".claude/rules", ".claude/skills", "tests", "AGENTS.md",
              "README.md", "docs/ARCHITECTURE.md", ".env.example")
WALK_EXCLUDED = ("deploy/desktop-superseded", "scripts/oneoff")
SKIP_DIRS = {"node_modules", "__pycache__", ".pytest_cache", "dist"}

# The run-mode tokens. The BARE identifier dry_run and every `--dry-run` flag are
# a different concept (register-models.py's own flag, CC_BUILD_DRY_RUN, kubectl's
# --dry-run=client) and are not matched; 'demo mode' also catches 'demo model'.
TOKENS = re.compile(
    r"executor_mode|CC_EXECUTOR_MODE|demo_mode|CC_DEMO_MODE|demoMode|executorMode"
    r"|'dry_run'|\"dry_run\"|\[dry-run\]|DRY RUN|(?i:demo mode)")
KEY_NAMES = frozenset({"CC_DEMO_MODE", "CC_EXECUTOR_MODE"})
ANY = None  # every token

# path -> (the tokens it may carry, why). Each reason is the allowlist's record.
ALLOWLIST: dict[str, tuple[frozenset | None, str]] = {
    "central_command/run_modes.py": (KEY_NAMES, "the rule itself: KEYS names the refused keys"),
    "central_command/db/repo.py": (frozenset({"[dry-run]"}),
        "the history read: a proposal.executed whose result_text starts '[dry-run]' "
        "reads simulated forever (history is never rewritten)"),
    "web/src/features/decisions/types.ts": (frozenset({"[dry-run]"}),
        "the simulated badge's type comment: the mark is read off that history"),
    "deploy/single/phases/check.sh": (KEY_NAMES,
        "the guard line story 9 wrote: validate_run_modes names what it refuses"),
    "AGENTS.md": (KEY_NAMES, "states the guard: the API refuses to start on the keys"),
    "docs/ARCHITECTURE.md": (KEY_NAMES, "states the guard"),
    ".claude/skills/setup/SKILL.md": (KEY_NAMES, "states the guard"),
    "deploy/k3s/README.md": (KEY_NAMES, "states the guard"),
    "tests/test_run_modes.py": (ANY, "the run-mode guard's own cases"),
    "tests/test_test_model.py": (ANY, "the test model's own cases (the env cannot set it)"),
    "tests/test_install_starts_live.py": (ANY, "the installer writes no mode key: its own cases"),
    "tests/test_simulated_history.py": (ANY, "the simulated mark's own cases"),
    "tests/test_run_modes_e2e.py": (ANY, "this file: the tokens and the allowlist"),
}
# The documents in the allowlist are there because they STATE the guard; a key
# name there with no refusal beside it would be a mode described, not refused.
STATES_THE_GUARD = ("AGENTS.md", "docs/ARCHITECTURE.md", ".claude/skills/setup/SKILL.md",
                    "deploy/k3s/README.md", "deploy/single/phases/check.sh")


def _walk():
    for rel in WALK_ROOTS:
        top = ROOT / rel
        paths = [top] if top.is_file() else sorted(top.rglob("*"))
        for p in paths:
            r = p.relative_to(ROOT).as_posix()
            if (not p.is_file() or r.startswith(WALK_EXCLUDED)
                    or SKIP_DIRS & set(p.relative_to(ROOT).parts)):
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            yield r, text.splitlines()


def test_no_run_mode_string_survives_outside_the_allowlist():
    """The allowlist, each entry with its reason:

    - central_command/run_modes.py — the rule itself: KEYS names the refused keys
    - central_command/db/repo.py — the history read ('[dry-run]' result text
      reads simulated forever)
    - web/src/features/decisions/types.ts — the simulated badge's type comment
    - deploy/single/phases/check.sh — the guard line story 9 wrote
    - AGENTS.md, docs/ARCHITECTURE.md, .claude/skills/setup/SKILL.md,
      deploy/k3s/README.md — each STATES the guard (the key names beside a
      refusal), allowlisted by file for that reason only
    - tests/test_run_modes.py, tests/test_test_model.py,
      tests/test_install_starts_live.py, tests/test_simulated_history.py and
      this file — the run-mode tests' own cases

    deploy/k3s/verify.sh is not listed: since story 12 it only calls the
    module and names no key. A product or document entry may carry only the
    tokens named for it, and every entry must still exist and still be hit.
    """
    hits: list[str] = []
    seen: set[str] = set()
    for rel, lines in _walk():
        allowed = ALLOWLIST.get(rel)
        for n, line in enumerate(lines, 1):
            for m in TOKENS.finditer(line):
                token = m.group(0)
                if allowed is not None and (allowed[0] is None or token in allowed[0]):
                    seen.add(rel)
                    if rel in STATES_THE_GUARD:
                        window = " ".join(lines[max(0, n - 3):n + 2]).lower()
                        assert "refus" in window, f"{rel}:{n} names {token} but states no refusal"
                    continue
                hits.append(f"{rel}:{n}: {token}: {line.strip()[:120]}")
    assert hits == [], "run-mode strings outside the allowlist:\n" + "\n".join(hits)
    for rel, (_, reason) in ALLOWLIST.items():
        assert (ROOT / rel).is_file(), f"allowlisted {rel} is gone ({reason})"
    assert set(ALLOWLIST) - seen == set(), "allowlist entries no longer hit — drop them"


# --- (e) no fake: every write-capable family fails loud and stays home -------------


FAMILIES = [
    ("jira.set_due_date", {"issue_key": "DEMO-1", "due_date": "2026-08-03"}, "jira",
     "Jira is not configured"),
    ("confluence.update_page", {"page_id": "42", "title": "Roadmap",
                                "body_storage": "<p>new</p>", "expected_version": 3},
     "confluence", "Confluence is not configured"),
    ("mail.send", {"to": "dana@example.com", "subject": "Hi", "body": "Hello"}, "mail",
     "set CC_EXCHANGE_URL"),
    ("calendar.create_event", {"title": "Sync", "start": "2026-11-02T10:00:00Z",
                               "end": "2026-11-02T10:30:00Z"}, "calendar",
     "Invalid port"),
    ("litellm.add_model", {"model_name": "doe-small", "model": "openai/doe-small"},
     "litellm", "LiteLLM admin not configured"),
    ("mcp.build_image", {"server_id": "e2e-build"}, "mcp", "no build host configured"),
]
# Why each lands FAILED: the guard holds Jira, Confluence, Exchange and the
# LiteLLM admin at nothing (unconfigured), the calendar façade at an unparseable
# port (SEMANTIC, story 2), and the MCP build host empty; the build's server row
# is planted as sync-reviewed so the build gets past review to the empty host.


@pytest.fixture
def connects(monkeypatch):
    """Every socket connection attempted while the test runs."""
    seen: list = []
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def spy(self, address):
        seen.append(address)
        return real_connect(self, address)

    def spy_ex(self, address):
        seen.append(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", spy)
    monkeypatch.setattr(socket.socket, "connect_ex", spy_ex)
    return seen


def _outbound(addresses: list) -> list:
    """Connections to anything but this host (the test Postgres is loopback)."""
    return [a for a in addresses if isinstance(a, tuple)
            and a[0] not in ("127.0.0.1", "::1", "localhost")]


@needs_pg
@pytest.mark.parametrize(("capability", "arguments", "system", "why"), FAMILIES,
                         ids=[f[0] for f in FAMILIES])
async def test_with_no_fake_an_approved_write_lands_failed_and_opens_no_socket(
    monkeypatch, connects, capability, arguments, system, why,
):
    if capability == "mcp.build_image":
        arguments = {"server_id": f"e2e-build-{uuid.uuid4().hex[:8]}"}
        await repo.upsert_mcp_server_synced(arguments["server_id"], "human:test")
    text = (f"Message-ID: <e2e-{uuid.uuid4().hex}@example.com>\n"
            "From: dana@example.com\nSubject: please do the thing\n\n"
            "Dana asks for the thing to be done.\n")
    pid, out = await _feed_and_approve(
        monkeypatch, text, _proposal(capability, arguments, "Dana asks", system))

    assert out.get("ok") is False and out.get("failed_capability") == capability, out
    assert why in out["error"], out["error"]
    row = await repo.load_proposal(pid)
    assert row["status"] == "FAILED", row["status"]  # never RETRY_PENDING
    assert _outbound(connects) == []
