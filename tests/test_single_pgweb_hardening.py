"""pgweb is a read-only window on both profiles (single-node v2.122.0, k3s
v2.123.0; enforced by Postgres since DL-235).

It has no login and listens on loopback, which does not stop a page open in
the operator's browser from sending it a request. So it logs in as `cc_pgweb`,
a role holding nothing but `pg_read_all_data` (created by schema.sql), and the
server refuses the writes; pgweb's own `--readonly` keyword blacklist is not
relied on. It stays locked to its one connection, with SSH tunnelling off.
"""
import contextlib
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
import yaml

from central_command.config import settings
from tests.conftest import needs_pg

COMPOSE = Path(__file__).resolve().parents[1] / "deploy" / "single" / "compose.yaml"
K3S = Path(__file__).resolve().parents[1] / "deploy" / "k3s" / "85-pgweb.yaml"
FLAGS = ("--lock-session", "--no-ssh")
ROLE = "cc_pgweb"


def _k3s_container() -> dict:
    (deployment,) = [d for d in yaml.safe_load_all(K3S.read_text()) if d and d["kind"] == "Deployment"]
    (container,) = deployment["spec"]["template"]["spec"]["containers"]
    return container


def _compose_service() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]["pgweb"]


def _user(url: str) -> str:
    return urlsplit(url).username or ""


def test_pgweb_runs_locked():
    command = _compose_service().get("command") or []
    for flag in FLAGS:
        assert flag in command, f"pgweb must run with {flag}"
    assert "--readonly" not in command, "the read-only role is the guard, not pgweb's keyword list"


def test_pgweb_is_published_on_loopback_only():
    assert all(str(port).startswith("127.0.0.1:") for port in _compose_service()["ports"])


def test_k3s_pgweb_runs_locked():
    container = _k3s_container()
    for flag in FLAGS:
        assert flag in (container.get("args") or []), f"pgweb must run with {flag}"
    assert "--readonly" not in (container.get("args") or [])
    assert "command" not in container, "args append to the image entrypoint; a command would replace it"


def test_both_profiles_log_in_as_the_read_only_role_not_the_owner():
    k3s_url = {e["name"]: e["value"] for e in _k3s_container()["env"]}["PGWEB_DATABASE_URL"]
    compose_url = _compose_service()["environment"]["PGWEB_DATABASE_URL"]
    assert k3s_url == compose_url, "the two profiles connect the same way"
    assert _user(compose_url) == ROLE
    assert _user(compose_url) != yaml.safe_load(COMPOSE.read_text())["services"]["postgres"]["environment"]["POSTGRES_USER"]


def _as_role(url: str) -> str:
    """The test database's URL with the manifests' pgweb credential."""
    parts = urlsplit(url)
    manifest = urlsplit(_compose_service()["environment"]["PGWEB_DATABASE_URL"])
    netloc = f"{manifest.username}:{manifest.password}@{parts.hostname}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@needs_pg
@pytest.mark.parametrize("statement", [
    "insert into app_setting (key, value) values ('pgweb-test', '1')",
    "update agent set name = name",
    "delete from app_setting where key = 'pgweb-test'",
    "truncate app_setting",
    "create table pgweb_probe (id int)",
    "alter table agent add column pgweb_probe int",
    "do $$ begin insert into app_setting (key, value) values ('pgweb-test', '1'); end $$",
    "select nextval(pg_get_serial_sequence('audit_event', 'id'))",
    "set role central_command",
])
async def test_the_role_cannot_write(statement):
    """Connected with the manifests' credential, the server refuses every
    write — the ones pgweb's keyword list missed (ALTER, DO, SET) included."""
    conn = await asyncpg.connect(_as_role(settings.database_url))
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(statement)
    finally:
        await conn.close()


@needs_pg
@pytest.mark.parametrize("statement", [
    "grant select on agent to public",
    "revoke select on agent from public",
])
async def test_the_role_cannot_change_a_grant(statement):
    """GRANT/REVOKE by a role without grant options is a WARNING in Postgres,
    not an error — so the proof is that no privilege moved."""
    conn = await asyncpg.connect(_as_role(settings.database_url))
    try:
        # A GRANT that grants nothing may still write the default ACL out
        # explicitly, so the comparison is of the EFFECTIVE privileges.
        acl = ("select coalesce(relacl, acldefault('r', relowner))::text "
               "from pg_class where oid = 'agent'::regclass")
        before = await conn.fetchval(acl)
        with contextlib.suppress(asyncpg.InsufficientPrivilegeError):
            await conn.execute(statement)
        assert await conn.fetchval(acl) == before
    finally:
        await conn.close()


@needs_pg
async def test_the_role_reads_and_holds_nothing_else():
    conn = await asyncpg.connect(_as_role(settings.database_url))
    try:
        assert await conn.fetchval("select count(*) >= 0 from agent")
        row = await conn.fetchrow(
            "select rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
            "from pg_roles where rolname = current_user")
        assert not any(row.values()), dict(row)
        memberships = await conn.fetch(
            "select r.rolname from pg_auth_members m join pg_roles r on r.oid = m.roleid "
            "where m.member = (select oid from pg_roles where rolname = current_user)")
        assert [m["rolname"] for m in memberships] == ["pg_read_all_data"]
    finally:
        await conn.close()
