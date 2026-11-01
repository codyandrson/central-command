"""What the bundled-skill refresh must keep true across releases (epic-skills-
refresh-on-update R7, R8, DL-198): the module's tier, the schema's shapes, the gate
strings in every place that gates, and the bridge rule.

The update INTO a release runs the previous release's updater, so the release that
ships the steps merges with no refresh. A change under `skills/` in that release would
reach the library as `modified` at the next update, against a base it never received.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from central_command.db import repo
from tests.conftest import needs_pg

ROOT = Path(__file__).resolve().parents[1]
REFRESH = ROOT / "central_command" / "skills" / "refresh.py"
SCHEMA = ROOT / "central_command" / "db" / "schema.sql"
VERIFY = ROOT / "deploy" / "k3s" / "verify.sh"


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ── the tier ────────────────────────────────────────────────────────────────


def test_the_module_imports_nothing_from_the_gateway_or_the_executor():
    imported: list[str] = []
    for node in ast.walk(ast.parse(REFRESH.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported += [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
    assert any(i.startswith("central_command.") for i in imported), imported
    assert not [i for i in imported if re.search(r"\b(gateway|executor)\b", i)], imported


def test_the_module_loads_neither_the_gateway_nor_the_runtime():
    code = (
        "import sys, central_command.skills.refresh\n"
        "print([m for m in sys.modules if m.startswith("
        "('central_command.gateway', 'central_command.executor', 'central_command.runtime'))])"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)}, cwd=ROOT,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"


def test_nothing_under_runtime_imports_the_module():
    for f in (ROOT / "central_command" / "runtime").rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"skills\.refresh|skills import .*\brefresh\b", text), f


# ── the schema ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("column", ["bundled_release", "bundled_path", "bundled_hash"])
def test_the_provenance_columns_are_added_idempotently(column: str):
    assert re.search(
        rf"alter table skill_doc add column if not exists {column}\s+text;",
        SCHEMA.read_text(encoding="utf-8"),
    ), column


@pytest.mark.parametrize("table", ["skill_bundled_text", "skill_doc_review"])
def test_the_tables_are_created_idempotently(table: str):
    schema = SCHEMA.read_text(encoding="utf-8")
    assert f"create table if not exists {table} (" in schema
    assert not re.search(rf"create table {table}\b", schema)


# ── the gate, wherever an apply can start ───────────────────────────────────


@pytest.mark.parametrize("updater", ["deploy/k3s/cc-update.sh", "deploy/single/update.sh"])
@pytest.mark.parametrize("step", ["skills_review_stage", "skills_review_gate", "refresh_bundled_skills"])
def test_both_updaters_hold_the_three_steps(updater: str, step: str):
    text = _text(updater)
    assert f"\n{step}() {{" in text, (updater, step)
    assert re.search(rf"^\s+{step}\b", text, re.M), (updater, step)


@pytest.mark.parametrize("route", ["web/server/routes/cc-update.ts", "central_command/api/update.py"])
def test_both_apply_routes_refuse_by_the_string_the_dialog_reads(route: str):
    assert "skills-unresolved" in _text(route)


def test_the_dialog_reads_that_string():
    assert "includes('skills-unresolved')" in _text("web/src/components/UpdateBadge.tsx")


# ── the routes reach their handlers ─────────────────────────────────────────


@pytest.mark.parametrize(
    "method,path,handler",
    [
        ("POST", "/api/skills/update-review", "compute_skill_review_route"),
        ("GET", "/api/skills/update-review", "get_latest_skill_review_route"),
        ("GET", "/api/skills/update-review/2.68.0", "get_skill_review_route"),
        ("POST", "/api/skills/update-review/2.68.0/items/jira/api/resolve", "resolve_skill_review_item_route"),
        ("POST", "/api/skills/update-review/2.68.0/apply", "apply_skill_review_route"),
    ],
)
def test_the_first_route_that_matches_is_the_reviews_own(method: str, path: str, handler: str):
    """The tests call handlers directly, which proves nothing about routing: the
    no-target read once sat behind `/skills/{skill_id}`, and every request became
    a 404 for a skill named `update-review`."""
    from starlette.routing import Match

    from central_command.api.routes import router

    scope = {"type": "http", "method": method, "path": path, "headers": []}
    first = next(r for r in router.routes if r.matches(scope)[0] == Match.FULL)
    assert first.endpoint.__name__ == handler


# ── the bridge ──────────────────────────────────────────────────────────────

_HEADING = re.compile(r"^## .*?\bv(\d+\.\d+\.\d+)\b:?\s*(.*)$", re.M)


def _bridge_problem(changelog: str, repo_root: Path) -> str | None:
    """The bridge rule over a CHANGELOG's top two headings. Returns what is wrong,
    None when it holds, or 'skip' when the rule cannot be checked here."""
    top = _HEADING.findall(changelog)[:2]
    if len(top) < 2:
        return "skip"
    (newer, title), (older, _) = top
    if not re.search(r"\bbridge\b", title, re.I):
        return None

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True)

    for version in (older, newer):
        if git("rev-parse", "--verify", "--quiet", f"refs/tags/v{version}").returncode != 0:
            return "skip"
    changed = git("diff", "--quiet", f"v{older}", f"v{newer}", "--", "skills/")
    if changed.returncode == 0:
        return None
    return f"v{newer} names itself a bridge and changes skills/ since v{older}"


def test_a_release_named_a_bridge_changes_nothing_under_skills():
    problem = _bridge_problem(_text("CHANGELOG.md"), ROOT)
    if problem == "skip":
        pytest.skip("a tag of the top two CHANGELOG versions is absent here")
    assert problem is None, problem


@pytest.fixture
def tagged_repo(tmp_path: Path) -> Path:
    if not shutil.which("git"):
        pytest.skip("git needed")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
             "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
            cwd=tmp_path, check=True, capture_output=True,
        )

    git("init", "-q")
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "doc.md").write_text("one\n")
    git("add", "--all")
    git("commit", "-q", "-m", "older")
    git("tag", "v1.0.0")
    (tmp_path / "other.txt").write_text("code\n")
    git("add", "--all")
    git("commit", "-q", "-m", "clean")
    git("tag", "v1.1.0")
    (tmp_path / "skills" / "doc.md").write_text("two\n")
    git("add", "--all")
    git("commit", "-q", "-m", "changes a doc")
    git("tag", "v1.2.0")
    return tmp_path


def _log(newer: str, older: str, title: str = "the bridge: docs follow the release") -> str:
    return (
        f"# Changelog\n\n## 2026-11-01 — v{newer}: {title}\n\nbody\n\n"
        f"## 2026-10-30 — v{older}: an older entry\n\nbody\n"
    )


def test_the_bridge_check_refuses_a_bridge_that_changed_a_doc(tagged_repo: Path):
    problem = _bridge_problem(_log("1.2.0", "1.1.0"), tagged_repo)
    assert problem and "changes skills/" in problem


def test_the_bridge_check_passes_a_bridge_that_changed_no_doc(tagged_repo: Path):
    assert _bridge_problem(_log("1.1.0", "1.0.0"), tagged_repo) is None


def test_the_bridge_check_ignores_a_release_not_named_a_bridge(tagged_repo: Path):
    assert _bridge_problem(_log("1.2.0", "1.1.0", "an ordinary release"), tagged_repo) is None


def test_the_bridge_check_skips_when_a_tag_is_absent(tagged_repo: Path):
    assert _bridge_problem(_log("9.9.9", "1.1.0"), tagged_repo) == "skip"


# ── verify.sh's row ─────────────────────────────────────────────────────────


def _verify_fn() -> str:
    text = VERIFY.read_text(encoding="utf-8")
    start = text.index("\nskill_review_store() {") + 1
    return text[start:text.index("\n}\n", start) + 3]


@pytest.mark.parametrize("answer,passes", [("5", True), ("4", False), ("", False), ("15", False)])
def test_the_verify_row_passes_only_on_all_five(tmp_path: Path, answer: str, passes: bool):
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("POSIX bash needed")
    stub = tmp_path / "kubectl"
    stub.write_text(f'#!/usr/bin/env bash\necho "$*" > "{tmp_path}/argv"\necho "{answer}"\n')
    stub.chmod(0o755)
    script = "\n".join([f"K=('{stub}')", _verify_fn(), "skill_review_store"])
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert (done.returncode == 0) is passes, done.stdout + done.stderr
    assert "deploy/cc-postgres" in (tmp_path / "argv").read_text()


def test_the_verify_row_fails_when_the_query_cannot_run(tmp_path: Path):
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("POSIX bash needed")
    script = "\n".join(["K=(false)", _verify_fn(), "skill_review_store"])
    assert subprocess.run(["bash", "-c", script], capture_output=True, timeout=30).returncode != 0


@needs_pg
@pytest.mark.asyncio
async def test_the_verify_rows_query_answers_five_on_the_shipped_schema():
    """Its own SQL against a database `schema.sql` loaded: a typo in a name would
    otherwise fail every real run and no test."""
    query = re.search(r'-c "(select .*?)" 2>/dev/null', _verify_fn(), re.S).group(1)
    conn = await repo._conn()
    try:
        assert await conn.fetchval(query) == 5
    finally:
        await conn.close()


def test_the_verify_row_runs_in_both_modes():
    text = VERIFY.read_text(encoding="utf-8")
    call = text.index('if skill_review_store; then ok "spine:')
    line_start = text.rfind("\n", 0, call) + 1
    assert text[line_start:call] == "", "the row must sit outside any --clean-install branch"
    assert text.index('echo "== B.') < call < text.index('echo "== C.')
