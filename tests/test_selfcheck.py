"""The application self-check (`central_command/selfcheck.py`, design record
2026-10-01 D4) — every row of D4's table can FAIL, by name, and says how to
fix it; nothing it prints carries a credential.

Every outward call is faked at the seam the check uses (`selfcheck._get`,
`selfcheck._pg_connect`, `resolve_model`, `neo4j_writer.request_embedding`,
`graphiti.get_status` (a bolt ping), `graphiti_client.patch_state`, `selfcheck._rerank_probe`, `email_facade.list_refs`, the Atlassian probe loader):
no test here reaches a network or a live service. The one exception is
`test_spine_against_the_test_database`, which uses the suite's own disposable
test database (conftest) to prove the real query and that schema.sql seeds a
roster before any boot.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import httpx
import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from central_command import selfcheck
from central_command.config import settings
from central_command.integrations import (
    email_facade,
    exchange,
    graphiti,
    graphiti_client,
    neo4j_writer,
)
from central_command.runtime import models
from tests.conftest import needs_pg

ROOT = Path(__file__).resolve().parents[1]

# Marker values fed through Settings / the environment; none may ever appear
# in a message, a remedy, a CLI line or the JSON.
MARK_LLM = "MARKER-llm-api-key-0001"
MARK_ADMIN = "MARKER-admin-key-0002"
MARK_DB = "MARKERdbpass0003"
MARK_SBX = "MARKER-runner-token-0004"
MARK_MAIL = "MARKER-facade-token-0005"
MARK_JIRA = "MARKER-jira-token-0006"
MARK_WIKI = "MARKER-wiki-token-0007"
MARK_EXCH = "MARKER-exchange-pw-0008"
MARK_NEO = "MARKER-neo4j-pw-0009"
MARK_AGENT = "MARKER-agent-key-0010"
MARKERS = (MARK_LLM, MARK_ADMIN, MARK_DB, MARK_SBX, MARK_MAIL, MARK_JIRA, MARK_WIKI,
           MARK_EXCH, MARK_NEO, MARK_AGENT)

PROXY = "http://proxy.example.com:4000"
RUNNER = "http://127.0.0.1:8090"
CRAWLER = "http://127.0.0.1:8091"
COCKPIT = "http://127.0.0.1:3080/"
LINKS = {
    "llm_proxy_ui_url": "http://proxy.example.com:4000/ui/",
    "neo4j_browser_url": "http://graph.example.com:7474/browser/",
    "n8n_ui_url": "http://n8n.example.com:5678",
    "crawler_docs_url": "http://127.0.0.1:8091/docs",
    "sandbox_docs_url": "http://127.0.0.1:8090/docs",
}
ALIAS = "cc-default"
DIM = 4


class FakeConn:
    def __init__(self, tables, roster=8):
        self.tables, self.roster = tables, roster

    async def fetch(self, _query):
        return [{"table_name": t} for t in self.tables]

    async def fetchval(self, _query):
        return self.roster

    async def close(self):
        pass


class Fakes:
    """The healthy install. A test breaks exactly one thing."""

    def __init__(self):
        self.responses: dict[str, object] = {
            f"{PROXY}/v1/models": httpx.Response(200, json={"data": [{"id": ALIAS}, {"id": "cc-embedding"}]}),
            f"{RUNNER}/openapi.json": httpx.Response(200, json={}),
            f"{CRAWLER}/healthz": httpx.Response(200, json={"ok": True}),
            COCKPIT: httpx.Response(200, text="<html>"),
            **{url: httpx.Response(200) for url in LINKS.values()},
        }
        self.calls: list[tuple[str, dict]] = []
        self.conn = FakeConn(selfcheck._schema_tables())
        self.pg_error: Exception | None = None
        self.completion_settings: list[dict] = []
        self.completion = lambda messages, info: ModelResponse(parts=[TextPart("OK")])
        self.vector = [0.0] * DIM
        self.embed_error: Exception | None = None
        self.status: object = {"status": "ok", "message": "connected"}
        self.patches = {"1729-invalidation-scope.patch": "patched",
                        "1666-reasoning-first-dedupe.patch": "patched"}
        self.refs: object = [{"uuid": "abc", "conversation_id": "t1"}]
        self.atlassian = {"checks": 9, "failures": 0, "fail_lines": []}
        self.rerank_error: Exception | None = None
        self.rerank_calls: list[tuple[str, str]] = []
        self.rerank_order = [selfcheck.RERANK_PROBE_RELEVANT, selfcheck.RERANK_PROBE_IRRELEVANT]

    async def get(self, url, *, headers=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        answer = self.responses.get(url)
        if answer is None:
            raise httpx.ConnectError(f"refused {url}")
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def pg_connect(self, dsn):
        if self.pg_error:
            raise self.pg_error
        return self.conn

    async def resolve_model(self, *a, **k):
        async def fn(messages, info):
            self.completion_settings.append(dict(info.model_settings or {}))
            out = self.completion(messages, info)
            return await out if asyncio.iscoroutine(out) else out

        return FunctionModel(fn, model_name=ALIAS)

    async def request_embedding(self, text, timeout=60.0):
        if self.embed_error:
            raise self.embed_error
        return self.vector

    async def get_status(self):
        if isinstance(self.status, Exception):
            raise self.status
        return self.status

    async def rerank_probe(self, kind, alias):
        self.rerank_calls.append((kind, alias))
        if self.rerank_error:
            raise self.rerank_error
        return [(p, 1.0 - i / 2) for i, p in enumerate(self.rerank_order)]

    async def list_refs(self, query):
        if isinstance(self.refs, Exception):
            raise self.refs
        return self.refs

    def probe(self):
        fakes = self

        class Probe:
            async def run_checks(self, *, jira_on=True, confluence_on=True):
                return fakes.atlassian

        return Probe()


@pytest.fixture
def fakes(monkeypatch):
    f = Fakes()
    for name, value in {
        "demo_mode": False,
        "default_model": f"openai:{ALIAS}",
        "llm_base_url": PROXY,
        "llm_api_key": MARK_LLM,
        "llm_proxy_base_url": PROXY,
        "llm_proxy_admin_key": MARK_ADMIN,
        "embed_alias": "cc-embedding",
        "embed_dim": DIM,
        "database_url": f"postgresql://cc:{MARK_DB}@db.example.com:5442/cc",
        "litellm_db_url": f"postgresql://ll:{MARK_DB}@db.example.com:5443/ll",
        "neo4j_url": "bolt://graph.example.com:7687",
        "neo4j_password": MARK_NEO,
        "sandbox_runner_url": RUNNER,
        "sandbox_runner_token": MARK_SBX,
        "crawler_url": CRAWLER,
        "email_facade_url": "http://n8n.example.com:5678/webhook/cc-email-facade",
        "email_facade_token": MARK_MAIL,
        "exchange_url": "",
        "exchange_username": "",
        "exchange_password": "",
        # The healthy install READS mail (the feed is on), so the mail check
        # proves the provider; the heartbeat's loop is off.
        "feed_enabled": True,
        "heartbeat_enabled": False,
        "jira_base_url": "https://jira.example.com",
        "jira_email": "op@example.com",
        "jira_api_token": MARK_JIRA,
        "jira_auth_mode": "",
        "jira_api_flavor": "cloud",
        "confluence_base_url": "",
        "confluence_api_token": MARK_WIKI,
        "llama_swap_ui_url": "",
        "vlogs_ui_url": "",
        "db_ui_url": "",
        "api_port": 8080,
        "graph_rerank_alias": "cc-rerank",
        "graph_rerank_kind": "rerank",
        **LINKS,
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setenv("CC_ENABLE_SANDBOX", "1")
    monkeypatch.setenv("CC_ENABLE_CRAWLER", "1")
    monkeypatch.setenv("CC_ENABLE_N8N", "1")
    monkeypatch.setenv("CC_COCKPIT_PORT", "3080")
    monkeypatch.setenv("CC_LLM_PROXY_KEY_SOME_AGENT", MARK_AGENT)
    monkeypatch.setattr(selfcheck, "_get", f.get)
    monkeypatch.setattr(selfcheck, "_pg_connect", f.pg_connect)
    monkeypatch.setattr(models, "resolve_model", f.resolve_model)
    monkeypatch.setattr(neo4j_writer, "request_embedding", f.request_embedding)
    monkeypatch.setattr(neo4j_writer, "_EMBED_MODEL", "cc-embedding")
    monkeypatch.setattr(graphiti, "get_status", f.get_status)
    monkeypatch.setattr(graphiti_client, "patch_state", lambda: f.patches)
    monkeypatch.setattr(selfcheck, "_rerank_probe", f.rerank_probe)
    monkeypatch.setattr(exchange, "configured", lambda: False)
    monkeypatch.setattr(email_facade, "list_refs", f.list_refs)
    monkeypatch.setattr(selfcheck, "_load_atlassian_probe", f.probe)
    return f


def by_name(doc: dict) -> dict[str, dict]:
    return {c["name"]: c for c in doc["checks"]}


def assert_no_marker(text: str) -> None:
    for marker in MARKERS:
        assert marker not in text, f"credential marker {marker!r} leaked: {text}"


# --- the healthy install --------------------------------------------------------


async def test_a_healthy_install_passes_every_check(fakes):
    doc = await selfcheck.run(mode="cli")
    assert [c["name"] for c in doc["checks"]] == [c.name for c in selfcheck.CHECKS]
    bad = {c["name"]: c["message"] for c in doc["checks"] if c["status"] != "pass"}
    assert bad == {}, bad
    assert doc["status"] == "pass" and selfcheck.exit_code(doc) == 0
    assert doc["mode"] == "cli" and doc["running"] is False and doc["version"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", doc["ran_at"])
    for check in doc["checks"]:
        assert set(check) == {"name", "status", "message", "remedy", "system", "duration_ms"}
        assert isinstance(check["duration_ms"], int)
    assert_no_marker(json.dumps(doc))


async def test_the_completion_is_one_request_with_a_one_token_ceiling(fakes):
    await selfcheck.run(mode="api")
    assert len(fakes.completion_settings) == 1
    assert fakes.completion_settings[0]["max_tokens"] == 1


async def test_proxy_check_asks_with_the_agents_key_and_embedding_with_the_writers(fakes):
    await selfcheck.run(mode="api")
    models_call = [h for u, h in fakes.calls if u.endswith("/v1/models")]
    assert models_call and models_call[0]["Authorization"] == f"Bearer {MARK_LLM}"
    runner_call = [h for u, h in fakes.calls if u.startswith(RUNNER + "/openapi")]
    assert runner_call and runner_call[0]["authorization"] == f"Bearer {MARK_SBX}"


async def test_api_mode_omits_the_cockpit(fakes):
    doc = await selfcheck.run(mode="api")
    assert "cockpit" not in by_name(doc)
    assert doc["mode"] == "api"


def test_every_check_sits_beside_a_real_systems_row_or_none():
    from central_command.api import systems

    entries = systems._entries()
    for e in entries:  # the table builds health coroutines; we only want the ids
        if asyncio.iscoroutine(e["health"]):
            e["health"].close()
    ids = {e["id"] for e in entries}
    for check in selfcheck.CHECKS:
        assert check.system is None or check.system in ids, (check.name, check.system)


# --- every row of D4's table can FAIL, by name ------------------------------------


def _break_spine(f, mp):
    f.pg_error = OSError("connection refused")
    return "CC_DATABASE_URL"


def _break_spine_roster(f, mp):
    f.conn.roster = 0
    return "./setup.sh"


def _break_spine_schema(f, mp):
    f.conn.tables = [t for t in f.conn.tables if t != "proposal"]
    return "./setup.sh"


def _break_proxy(f, mp):
    f.responses[f"{PROXY}/v1/models"] = httpx.Response(200, json={"data": [{"id": "other"}]})
    return "CC_DEFAULT_MODEL"


def _break_proxy_key(f, mp):
    f.responses[f"{PROXY}/v1/models"] = httpx.Response(401, json={"error": "bad key"})
    return "CC_LLM_API_KEY"


def _break_completion(f, mp):
    def refuse(messages, info):
        raise ModelHTTPError(status_code=401, model_name=ALIAS,
                             body={"error": {"message": f"Invalid key {MARK_LLM} sk-abcd1234"}})

    f.completion = refuse
    return "CC_LLM_API_KEY"


def _break_embedding(f, mp):
    f.vector = [0.0] * (DIM + 1)
    return "CC_EMBED_DIM"


def _break_embedding_key(f, mp):
    request = httpx.Request("POST", f"{PROXY}/v1/embeddings")
    f.embed_error = httpx.HTTPStatusError("401", request=request,
                                          response=httpx.Response(401, request=request))
    return "CC_LLM_PROXY_ADMIN_KEY"


def _break_graph(f, mp):
    f.status = {"status": "error", "error": "ServiceUnavailable",
                "message": "ServiceUnavailable: Couldn't connect"}
    return "CC_NEO4J_URL"


def _break_graph_neo4j(f, mp):
    f.status = {"status": "error", "error": "AuthError",
                "message": f"AuthError: unauthorized ({MARK_NEO})"}
    return "CC_NEO4J_PASSWORD"


def _break_graph_patches(f, mp):
    f.patches = {"1729-invalidation-scope.patch": "pristine",
                 "1666-reasoning-first-dedupe.patch": "pristine"}
    return "python scripts/apply_graphiti_patches.py"


def _break_graph_rerank(f, mp):
    request = httpx.Request("POST", f"{PROXY}/rerank")
    f.rerank_error = graphiti_client.RerankHTTPError(
        "HTTP 403", response=httpx.Response(403, request=request), body=None)
    return "CC_GRAPH_RERANK_ALIAS"


def _break_graph_rerank_kind(f, mp):
    mp.setattr(settings, "graph_rerank_kind", "cohere")
    return "CC_GRAPH_RERANK_KIND"


def _break_graph_rerank_malformed(f, mp):
    mp.setattr(settings, "graph_rerank_kind", "chat")
    f.rerank_error = graphiti_client.RerankError("the chat reranker answered 'We'")
    return "CC_GRAPH_RERANK_KIND"


def _break_sandbox(f, mp):
    f.responses[f"{RUNNER}/openapi.json"] = httpx.Response(401, json={"detail": "bad token"})
    return "CC_SANDBOX_RUNNER_TOKEN"


def _break_crawler(f, mp):
    del f.responses[f"{CRAWLER}/healthz"]
    return "CC_CRAWLER_URL"


def _break_mail(f, mp):
    f.refs = email_facade.EmailFacadeError(
        f"email façade list failed: HTTP 401 token {MARK_MAIL} rejected")
    return "CC_EMAIL_FACADE_TOKEN"


def _break_mail_exchange(f, mp):
    mp.setattr(exchange, "configured", lambda: True)

    async def boom():
        raise exchange.ExchangeError("exchange: UnauthorizedError: 401 for op@example.com")

    mp.setattr(selfcheck, "_exchange_one_ref", boom)
    return "CC_EXCHANGE_PASSWORD"


def _break_integrations(f, mp):
    f.atlassian = {"checks": 9, "failures": 1,
                   "fail_lines": ["FAIL jira GET /rest/api/3/myself — 401 Unauthorized"]}
    return "python scripts/atlassian_probe.py"


def _break_integrations_creds(f, mp):
    mp.setattr(settings, "jira_api_token", "")
    return "CC_JIRA_API_TOKEN"


def _break_cockpit(f, mp):
    del f.responses[COCKPIT]
    return "CC_COCKPIT_PORT"


def _break_links(f, mp):
    f.responses[LINKS["n8n_ui_url"]] = httpx.ConnectError("refused")
    return "CC_N8N_UI_URL"


BREAKS = [
    ("spine", _break_spine), ("spine", _break_spine_roster), ("spine", _break_spine_schema),
    ("proxy-as-app", _break_proxy), ("proxy-as-app", _break_proxy_key),
    ("completion-as-app", _break_completion),
    ("embedding-as-app", _break_embedding), ("embedding-as-app", _break_embedding_key),
    ("graph", _break_graph), ("graph", _break_graph_neo4j),
    ("graph-patches", _break_graph_patches),
    ("graph-rerank", _break_graph_rerank), ("graph-rerank", _break_graph_rerank_kind),
    ("graph-rerank", _break_graph_rerank_malformed),
    ("sandbox", _break_sandbox),
    ("crawler", _break_crawler),
    ("mail", _break_mail), ("mail", _break_mail_exchange),
    ("integrations", _break_integrations), ("integrations", _break_integrations_creds),
    ("cockpit", _break_cockpit),
    ("links", _break_links),
]


def test_the_break_table_covers_every_check():
    assert {name for name, _ in BREAKS} == {c.name for c in selfcheck.CHECKS}


@pytest.mark.parametrize("name,breaker", BREAKS, ids=[b.__name__ for _, b in BREAKS])
async def test_each_check_fails_by_name_and_names_its_remedy(fakes, monkeypatch, name, breaker):
    remedy = breaker(fakes, monkeypatch)
    doc = await selfcheck.run(mode="cli")
    checks = by_name(doc)
    check = checks[name]
    assert check["status"] == "fail", check
    assert check["remedy"] == remedy
    assert remedy in check["message"], check["message"]
    line = selfcheck.protocol_line(check)
    assert line.startswith(f"FAIL selfcheck-{name}: ")
    # Exactly the broken one fails: a fault is attributed to its own row.
    others = {n: c["status"] for n, c in checks.items() if n != name and c["status"] != "pass"}
    assert others == {}, others
    assert doc["status"] == "fail" and selfcheck.exit_code(doc) == 1
    assert_no_marker(json.dumps(doc))


# --- the one failure this module exists to catch -----------------------------------


@pytest.mark.parametrize("key", ["llm_api_key", "llm_base_url"])
async def test_an_empty_agent_key_or_url_fails_proxy_and_completion_naming_it(fakes, monkeypatch, key):
    monkeypatch.setattr(settings, key, "")
    env_key = f"CC_{key.upper()}"
    checks = by_name(await selfcheck.run(mode="cli"))
    for name in ("proxy-as-app", "completion-as-app"):
        assert checks[name]["status"] == "fail"
        assert checks[name]["remedy"] == env_key
        assert env_key in checks[name]["message"]
        assert "./setup.sh" in checks[name]["message"]
    assert fakes.completion_settings == [], "no request may be attempted without a key"


async def test_demo_mode_spends_no_model_request(fakes, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    checks = by_name(await selfcheck.run(mode="api"))
    for name in ("proxy-as-app", "completion-as-app"):
        assert checks[name]["status"] == "skip"
        assert checks[name]["message"].startswith("not applicable — ")
    assert fakes.completion_settings == []


# --- not applicable, pre-boot, warnings ----------------------------------------------


async def test_pre_boot_skips_the_cockpit_and_the_sandbox(fakes):
    # Nothing boot starts is up yet: both would FAIL if they were asked.
    del fakes.responses[COCKPIT]
    del fakes.responses[f"{RUNNER}/openapi.json"]
    del fakes.responses[LINKS["sandbox_docs_url"]]
    doc = await selfcheck.run(mode="cli", pre_boot=True)
    checks = by_name(doc)
    for name in ("cockpit", "sandbox"):
        assert checks[name]["status"] == "skip"
        assert selfcheck.protocol_line(checks[name]) == (
            f"PASS selfcheck-{name}: not checked before boot — boot starts it")
    assert checks["links"]["status"] == "pass"
    assert "CC_SANDBOX_DOCS_URL not checked before boot" in checks["links"]["message"]
    assert not any(u.startswith(RUNNER) or u == COCKPIT for u, _ in fakes.calls)
    assert doc["status"] == "pass" and selfcheck.exit_code(doc) == 0


async def test_without_pre_boot_the_cockpit_and_sandbox_are_checked(fakes):
    await selfcheck.run(mode="cli")
    urls = [u for u, _ in fakes.calls]
    assert COCKPIT in urls and f"{RUNNER}/openapi.json" in urls


async def test_flags_off_and_unconfigured_integrations_are_not_applicable(fakes, monkeypatch):
    monkeypatch.setenv("CC_ENABLE_SANDBOX", "0")
    monkeypatch.setenv("CC_ENABLE_CRAWLER", "0")
    monkeypatch.setenv("CC_ENABLE_N8N", "0")
    monkeypatch.setattr(settings, "feed_enabled", False)
    monkeypatch.setattr(settings, "exchange_password", "")
    monkeypatch.setattr(settings, "jira_base_url", "")
    monkeypatch.setattr(settings, "confluence_base_url", "")
    checks = by_name(await selfcheck.run(mode="cli"))
    for name in ("sandbox", "crawler", "mail", "integrations"):
        assert checks[name]["status"] == "skip", checks[name]
        line = selfcheck.protocol_line(checks[name])
        assert line.startswith(f"PASS selfcheck-{name}: not applicable — "), line


async def test_a_blank_always_derived_link_is_a_warning_naming_its_key(fakes, monkeypatch):
    monkeypatch.setattr(settings, "neo4j_browser_url", "")
    doc = await selfcheck.run(mode="cli")
    links = by_name(doc)["links"]
    assert links["status"] == "warn"
    assert links["remedy"] == "CC_NEO4J_BROWSER_URL"
    assert selfcheck.protocol_line(links).startswith("WARN selfcheck-links: ")
    assert doc["status"] == "warn" and selfcheck.exit_code(doc) == 2


async def test_a_blank_optional_link_is_not_probed_and_not_a_warning(fakes, monkeypatch):
    monkeypatch.setattr(settings, "n8n_ui_url", "")
    checks = by_name(await selfcheck.run(mode="cli"))
    assert checks["links"]["status"] == "pass"
    assert LINKS["n8n_ui_url"] not in [u for u, _ in fakes.calls]


def test_link_settings_are_exactly_the_systems_page_links():
    src = (ROOT / "central_command/api/systems.py").read_text(encoding="utf-8")
    on_page = set(re.findall(r'"url":\s*settings\.(\w+) or None', src))
    assert on_page and set(selfcheck.link_settings()) == on_page
    assert set(selfcheck.ALWAYS_DERIVED_LINKS) <= on_page


# --- timeouts, exit codes, the CLI ----------------------------------------------------


async def test_a_hung_dependency_fails_its_own_check_and_only_that_one(fakes):
    async def hang(messages, info):
        await asyncio.sleep(30)

    fakes.completion = hang
    doc = await selfcheck.run(mode="api", completion_timeout=0.05)
    checks = by_name(doc)
    assert checks["completion-as-app"]["status"] == "fail"
    assert "no answer within 0.05 s" in checks["completion-as-app"]["message"]
    assert checks["completion-as-app"]["remedy"] == "CC_SELFCHECK_COMPLETION_TIMEOUT"
    assert all(c["status"] == "pass" for n, c in checks.items() if n != "completion-as-app")


def test_the_exit_code_rule():
    def doc(*statuses):
        return {"checks": [{"status": s} for s in statuses]}

    assert selfcheck.exit_code(doc("pass", "skip")) == 0
    assert selfcheck.exit_code(doc("pass", "warn", "skip")) == 2
    assert selfcheck.exit_code(doc("warn", "fail", "pass")) == 1
    assert selfcheck.exit_code(doc()) == 0


def _all_broken(f, monkeypatch):
    """Every dependency fails, with exception text that tries to carry the
    credentials out — the worst case for a leak."""
    f.responses.clear()
    f.pg_error = OSError(f"password authentication failed: {MARK_DB} postgresql://cc:{MARK_DB}@h/cc")
    f.embed_error = RuntimeError(f"Bearer {MARK_ADMIN} rejected")
    f.status = RuntimeError(f"neo4j auth {MARK_NEO}")
    f.refs = email_facade.EmailFacadeError(f"HTTP 500 {MARK_MAIL} op@example.com")
    f.atlassian = {"checks": 2, "failures": 2, "fail_lines": [f"FAIL jira GET /x — 401 {MARK_JIRA}"]}
    monkeypatch.setattr(settings, "exchange_url", "https://mail.example.com/ews/exchange.asmx")
    monkeypatch.setattr(settings, "exchange_username", "EXAMPLE\\op")
    monkeypatch.setattr(settings, "exchange_password", MARK_EXCH)
    monkeypatch.setattr(exchange, "configured", lambda: True)

    async def exchange_boom():
        raise exchange.ExchangeError(f"exchange: login {MARK_EXCH} refused for op@example.com")

    monkeypatch.setattr(selfcheck, "_exchange_one_ref", exchange_boom)

    def refuse(messages, info):
        raise ModelHTTPError(status_code=500, model_name=ALIAS,
                             body={"error": {"message": f"{MARK_LLM} {MARK_AGENT} {MARK_SBX}"}})

    f.completion = refuse


@pytest.mark.parametrize("as_json", [False, True])
def test_the_cli_never_prints_a_credential(fakes, monkeypatch, capsys, as_json):
    _all_broken(fakes, monkeypatch)
    code = selfcheck.main(["--json"] if as_json else [])
    out = capsys.readouterr().out
    assert code == 1
    assert_no_marker(out)
    assert "op@example.com" not in out
    if as_json:
        doc = json.loads(out)
        assert doc["status"] == "fail" and doc["mode"] == "cli"
    else:
        lines = out.strip().splitlines()
        assert len(lines) == len(selfcheck.CHECKS)
        assert all(re.match(r"^(PASS|WARN|FAIL) selfcheck-[a-z-]+: \S", line) for line in lines)


def test_the_cli_timeout_flag_sets_the_completion_ceiling(fakes, monkeypatch, capsys):
    async def hang(messages, info):
        await asyncio.sleep(30)

    fakes.completion = hang
    code = selfcheck.main(["--timeout", "0.05", "--pre-boot"])
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL selfcheck-completion-as-app: no answer within 0.05 s" in out


# --- mail: checked when the app READS mail (2026-10-02 Windows run, F4) -------------


def _default_install_mail(monkeypatch):
    """What every single-node install looks like by default: make-secrets.sh
    GENERATED a façade token, n8n is off, the feed is off, no Exchange — and
    nothing listens at the façade URL."""
    monkeypatch.setenv("CC_ENABLE_N8N", "0")
    monkeypatch.setattr(settings, "feed_enabled", False)
    monkeypatch.setattr(settings, "heartbeat_enabled", False)
    monkeypatch.setattr(settings, "email_facade_token", MARK_MAIL)


async def test_a_default_install_does_not_dial_a_facade_it_never_deployed(fakes, monkeypatch):
    _default_install_mail(monkeypatch)
    fakes.refs = email_facade.EmailFacadeError(
        "email façade list unreachable at <url>: All connection attempts failed")
    doc = await selfcheck.run(mode="cli", pre_boot=True)
    mail = by_name(doc)["mail"]
    assert mail["status"] == "skip", mail
    assert selfcheck.protocol_line(mail).startswith("PASS selfcheck-mail: not applicable — ")
    assert "CC_FEED_ENABLED=0" in mail["message"], mail["message"]
    assert "turn the feed on" in mail["message"], mail["message"]
    assert doc["status"] == "pass" and selfcheck.exit_code(doc) == 0


async def test_n8n_on_alone_does_not_make_the_mail_check_apply(fakes, monkeypatch):
    """CC_ENABLE_N8N is the single-node installer's knob — the k3s profile
    does not honour it — so it is not what decides; the feed is."""
    _default_install_mail(monkeypatch)
    monkeypatch.setenv("CC_ENABLE_N8N", "1")
    assert by_name(await selfcheck.run(mode="cli"))["mail"]["status"] == "skip"


async def test_the_feed_on_with_the_facade_unreachable_fails_naming_its_url(fakes, monkeypatch):
    _default_install_mail(monkeypatch)
    monkeypatch.setattr(settings, "feed_enabled", True)
    fakes.refs = email_facade.EmailFacadeError(
        "email façade list unreachable at http://127.0.0.1:5678/x: All connection attempts failed")
    mail = by_name(await selfcheck.run(mode="cli"))["mail"]
    assert mail["status"] == "fail", mail
    assert mail["remedy"] == "CC_EMAIL_FACADE_URL"
    assert "CC_EMAIL_FACADE_URL" in mail["message"] and "CC_FEED_ENABLED=1" in mail["message"]
    assert "http://127.0.0.1" not in mail["message"], "never a URL"
    assert_no_marker(json.dumps(mail))


async def test_the_feed_on_with_no_facade_token_fails_naming_the_token(fakes, monkeypatch):
    _default_install_mail(monkeypatch)
    monkeypatch.setattr(settings, "feed_enabled", True)
    monkeypatch.setattr(settings, "email_facade_token", "")
    mail = by_name(await selfcheck.run(mode="cli"))["mail"]
    assert mail["status"] == "fail" and mail["remedy"] == "CC_EMAIL_FACADE_TOKEN", mail


@pytest.mark.parametrize("schedule_on,expected", [(True, "pass"), (False, "skip")])
async def test_the_heartbeat_mail_poll_counts_as_reading_mail(fakes, monkeypatch, schedule_on, expected):
    """The heartbeat's feed.poll action runs the same poll_once — with its loop
    on AND the mail-poll schedule enabled, the app reads mail with the feed's
    own loop off."""
    _default_install_mail(monkeypatch)
    monkeypatch.setattr(settings, "heartbeat_enabled", True)

    async def polls():
        return schedule_on

    monkeypatch.setattr(selfcheck, "_heartbeat_polls_mail", polls)
    mail = by_name(await selfcheck.run(mode="cli"))["mail"]
    assert mail["status"] == expected, mail
    if schedule_on:
        assert fakes.refs and "façade answers" in mail["message"]


@pytest.mark.parametrize("feed", [True, False])
async def test_exchange_configured_is_checked_whether_or_not_the_feed_is_on(fakes, monkeypatch, feed):
    _default_install_mail(monkeypatch)
    monkeypatch.setattr(settings, "feed_enabled", feed)
    monkeypatch.setattr(exchange, "configured", lambda: True)
    asked = []

    async def one_ref():
        asked.append(1)
        return 1

    monkeypatch.setattr(selfcheck, "_exchange_one_ref", one_ref)
    mail = by_name(await selfcheck.run(mode="cli"))["mail"]
    assert mail["status"] == "pass" and asked == [1], mail
    assert "Exchange lists the inbox" in mail["message"]


async def test_half_configured_exchange_fails_whether_or_not_the_feed_is_on(fakes, monkeypatch):
    _default_install_mail(monkeypatch)
    monkeypatch.setattr(settings, "exchange_url", "https://mail.example.com/ews/exchange.asmx")
    mail = by_name(await selfcheck.run(mode="cli"))["mail"]
    assert mail["status"] == "fail" and mail["remedy"] == "CC_EXCHANGE_USERNAME", mail
    assert "half-configured" in mail["message"]


# --- spine: a timeout is told apart from a refusal (2026-10-02 Windows run, F5) ------


@pytest.mark.parametrize("accepts", [True, False])
async def test_a_spine_connect_timeout_says_timed_out_and_which_half_stalled(fakes, monkeypatch, accepts):
    fakes.pg_error = TimeoutError()
    probed = []

    async def tcp(host, port, timeout=2.0):
        probed.append((host, port))
        return accepts

    monkeypatch.setattr(selfcheck, "_tcp_accepts", tcp)
    spine = by_name(await selfcheck.run(mode="cli"))["spine"]
    assert spine["status"] == "fail" and spine["remedy"] == "CC_DATABASE_URL", spine
    assert "TIMED OUT after" in spine["message"], spine["message"]
    assert "REFUSED" not in spine["message"]
    assert ("ACCEPTS a TCP connection" in spine["message"]) is accepts, spine["message"]
    assert probed == [("db.example.com", 5442)], "one diagnostic probe, never a retry loop"
    assert_no_marker(json.dumps(spine))


async def test_a_refused_spine_connect_says_refused(fakes, monkeypatch):
    fakes.pg_error = ConnectionRefusedError(10061, "Connect call failed ('127.0.0.1', 5442)")
    spine = by_name(await selfcheck.run(mode="cli"))["spine"]
    assert spine["status"] == "fail" and "REFUSED" in spine["message"], spine
    assert "TIMED OUT" not in spine["message"]


def test_the_checks_imports_happen_before_any_clock_starts(monkeypatch):
    """A synchronous first import inside one check blocks the loop while every
    other check's timer runs (one reading of F5) — run() imports them first."""
    order = []
    monkeypatch.setattr(selfcheck, "_warm_imports", lambda: order.append("warm"))

    async def one(check, ctx):
        order.append(check.name)
        return {"name": check.name, "status": "pass", "message": "", "remedy": None,
                "system": None, "duration_ms": 0}

    monkeypatch.setattr(selfcheck, "_run_one", one)
    asyncio.run(selfcheck.run(mode="cli"))
    assert order[0] == "warm" and len(order) == len(selfcheck.CHECKS) + 1, order
    for name in selfcheck._CHECK_IMPORTS:
        assert importlib.util.find_spec(name) is not None, name


# --- the CLI writes UTF-8 whatever the stream's own encoding (F9) -------------------


def test_the_cli_writes_utf8_to_a_cp1252_stream(fakes, monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="replace", write_through=True)
    monkeypatch.setattr(sys, "stdout", stream)
    _default_install_mail(monkeypatch)  # a "not applicable — …" line carries an em dash
    selfcheck.main(["--pre-boot"])
    stream.flush()
    text = raw.getvalue().decode("utf-8")  # raises if it was cp1252
    assert "PASS selfcheck-mail: not applicable — " in text, text
    assert "\ufffd" not in text


# --- the real query against the suite's own disposable database ---------------------


@needs_pg
async def test_spine_against_the_test_database(monkeypatch):
    # schema.sql alone (conftest loads it, no API boot): the roster is seeded,
    # which is why an empty roster is a FAIL even under --pre-boot.
    out = await selfcheck.check_spine(selfcheck.Context("cli", True, 1.0))
    assert out.status == "pass", out.message
    assert "roster of" in out.message


# --- the docstring carries the readiness-only rule (design record D11) ---------------


def test_the_module_docstring_carries_the_readiness_only_rule():
    doc = " ".join((selfcheck.__doc__ or "").split())
    quote = ("Incorrect implementation of liveness probes can lead to cascading failures. "
             "This results in restarting of container under high load; failed client "
             "requests as your application became less scalable; and increased workload on "
             "remaining pods due to some failed pods. Understand the difference between "
             "liveness and readiness probes and when to apply them for your app.")
    assert quote in doc
    assert "kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/" in doc
    assert "READINESS" in doc and "LIVENESS" in doc
    assert "one model request each" in doc


def test_selfcheck_restarts_nothing():
    """Readiness, never liveness — mechanically: the module shells out to
    nothing and calls no lifecycle verb."""
    tree = ast.parse((ROOT / "central_command/selfcheck.py").read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree)
                if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in (n.names if isinstance(n, ast.Import) else [ast.alias(n.module or "")])}
    assert "subprocess" not in imported
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not calls & {"restart", "stop", "kill", "terminate", "system", "Popen"}


# --- graph-rerank: three tiers, and a failure says search will ERROR ------------------


async def test_no_reranker_is_a_configured_state_not_a_failure(fakes, monkeypatch):
    monkeypatch.setattr(settings, "graph_rerank_alias", "")
    check = by_name(await selfcheck.run(mode="cli"))["graph-rerank"]
    assert check["status"] == "skip"
    assert "rank fusion" in check["message"] and "cc-rerank" in check["message"]
    assert selfcheck.protocol_line(check).startswith("PASS selfcheck-graph-rerank: ")
    assert fakes.rerank_calls == []  # nothing spent


@pytest.mark.parametrize("kind,expected", [("", "rerank"), ("rerank", "rerank"), ("chat", "chat")])
async def test_the_probe_runs_the_configured_kind_with_one_relevant_and_one_irrelevant(
    fakes, monkeypatch, kind, expected
):
    monkeypatch.setattr(settings, "graph_rerank_kind", kind)
    check = by_name(await selfcheck.run(mode="cli"))["graph-rerank"]
    assert check["status"] == "pass", check
    assert fakes.rerank_calls == [(expected, "cc-rerank")]


async def test_a_failed_chat_probe_says_search_will_error_and_names_the_three_causes(fakes, monkeypatch):
    monkeypatch.setattr(settings, "graph_rerank_kind", "chat")
    request = httpx.Request("POST", f"{PROXY}/v1/chat/completions")
    import openai

    fakes.rerank_error = openai.APIStatusError(
        "bad", response=httpx.Response(400, request=request), body=None)
    check = by_name(await selfcheck.run(mode="cli"))["graph-rerank"]
    assert check["status"] == "fail"
    msg = check["message"]
    assert "ERRORS" in msg and "no unranked fallback" in msg
    assert "logprobs" in msg and "thinking" in msg and "scope" in msg


async def test_a_reranker_that_ranks_backwards_is_a_warning(fakes):
    fakes.rerank_order.reverse()
    check = by_name(await selfcheck.run(mode="cli"))["graph-rerank"]
    assert check["status"] == "warn" and check["remedy"] == "CC_GRAPH_RERANK_ALIAS"
