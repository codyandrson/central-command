"""What the bundled-skill comparison must keep true across releases (DL-198,
amended by DL-232): the module's tier, the schema's shapes, that no apply is gated
on the bundled docs any more, the routes, and the command line the previous
release's updater calls.

The update INTO a release runs the previous release's updater: v2.126.0's calls
`apply --target T --ref R --actor updater` on the merged tree, so that command line
must still run the comparison for one release (`refresh._LEGACY`).
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


# `skill_doc_review` is unused since DL-232 and stays because the file is additive.
@pytest.mark.parametrize("table", ["skill_bundled_text", "skill_doc_flag", "skill_doc_review"])
def test_the_tables_are_created_idempotently(table: str):
    schema = SCHEMA.read_text(encoding="utf-8")
    assert f"create table if not exists {table} (" in schema
    assert not re.search(rf"create table {table}\b", schema)


# ── no apply is gated on the bundled docs (DL-232) ──────────────────────────


@pytest.mark.parametrize("path", [
    "deploy/k3s/cc-update.sh", "deploy/single/update.sh",
    "web/server/routes/cc-update.ts", "central_command/api/update.py",
    "web/src/components/UpdateBadge.tsx",
])
def test_nothing_refuses_an_update_over_the_bundled_docs(path: str):
    text = _text(path)
    assert "skills-unresolved" not in text
    assert "skills_review_gate" not in text and "skills_review_stage" not in text
    assert "update-review" not in text


@pytest.mark.parametrize("updater", ["deploy/k3s/cc-update.sh", "deploy/single/update.sh"])
def test_both_updaters_run_the_comparison_after_the_merge(updater: str):
    text = _text(updater)
    assert "\nrefresh_bundled_skills() {" in text
    assert re.search(r"^\s+refresh_bundled_skills\b", text, re.MULTILINE)
    assert re.search(r"skills_refresh_cli sync\b", text)


def test_no_git_is_read_by_the_module():
    text = REFRESH.read_text(encoding="utf-8")
    assert "subprocess" not in text and "merge-file" not in text and '"git"' not in text


# ── the routes reach their handlers ─────────────────────────────────────────


@pytest.mark.parametrize(
    "method,path,handler",
    [
        ("GET", "/api/skills/release-flags", "list_skill_release_flags_route"),
        ("POST", "/api/skills/release-flags/jira/api/resolve", "resolve_skill_release_flag_route"),
    ],
)
def test_the_first_route_that_matches_is_the_flags_own(method: str, path: str, handler: str):
    """The tests call handlers directly, which proves nothing about routing: a
    read once sat behind `/skills/{skill_id}`, and every request became a 404 for
    a skill named after the route."""
    from starlette.routing import Match

    from central_command.api.routes import router

    scope = {"type": "http", "method": method, "path": path, "headers": []}
    first = next(r for r in router.routes if r.matches(scope)[0] == Match.FULL)
    assert first.endpoint.__name__ == handler


# ── the previous release's updater (one release only) ───────────────────────
# v2.126.0's updaters, read with `git show v2.126.0:<path>`, call the module
# three ways. Stage and resolve run the INSTALLED tree's copy (their functions
# test `[[ -f "$REPO/…/refresh.py" ]]` before the merge), so only `apply` reaches
# this tree, from the `skills` phase (k3s) and `deploy_current_tree` (single):
#   k3s:    skills_refresh_cli apply --target "$TARGET_VERSION" --ref "v$TARGET_VERSION" --actor updater
#   single: "$py" -m central_command.skills.refresh apply --target "$target" --ref upstream --actor updater
# Each must still run here and exit 0 on a clean comparison.

OLD_ARGVS = [
    ["apply", "--target", "9.9.9", "--ref", "v9.9.9", "--actor", "updater"],
    ["apply", "--target", "9.9.9", "--ref", "upstream", "--actor", "updater"],
]


@pytest.mark.parametrize("argv", OLD_ARGVS)
def test_the_old_updaters_apply_runs_the_comparison(monkeypatch, argv):
    from central_command.skills import refresh

    ran = []

    async def fake_sync():
        ran.append(True)
        return {"release": "9.9.9", "counts": {"follow": 0}, "flagged": [], "failed": []}

    monkeypatch.setattr(refresh, "sync", fake_sync)
    assert refresh.main(argv) == 0
    assert ran == [True]


@pytest.mark.parametrize("command", ["review", "check"])
def test_the_old_stage_and_gate_commands_answer_nothing_to_review(monkeypatch, capsys, command):
    from central_command.skills import refresh

    async def must_not_run():
        raise AssertionError("review and check never compare")

    monkeypatch.setattr(refresh, "sync", must_not_run)
    assert refresh.main([command, "--target", "9.9.9", "--ref", "v9.9.9"]) == 0
    assert "nothing to review" in capsys.readouterr().out


def test_the_old_updaters_argv_is_what_v2_126_0_shipped():
    """Where the tag is here, read the old updaters and check the shapes above."""
    if not shutil.which("git"):
        pytest.skip("git needed")
    have = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "refs/tags/v2.126.0"],
        cwd=ROOT, capture_output=True,
    )
    if have.returncode != 0:
        pytest.skip("tag v2.126.0 is absent here")

    def old(path: str) -> str:
        return subprocess.run(
            ["git", "show", f"v2.126.0:{path}"], cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout

    k3s = old("deploy/k3s/cc-update.sh")
    single = old("deploy/single/update.sh")
    assert 'skills_refresh_cli apply --target "$TARGET_VERSION" --ref "v$TARGET_VERSION" --actor updater' in k3s
    assert '-m central_command.skills.refresh "$sub" --target "$target" --ref upstream "$@"' in single
    assert 'skills_refresh_cli apply "$target" --actor updater' in single


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
    script = f"K=(false)\n{_verify_fn()}\nskill_review_store"
    assert subprocess.run(["bash", "-c", script], capture_output=True, timeout=30).returncode != 0


@needs_pg
@pytest.mark.asyncio
async def test_the_verify_rows_query_answers_five_on_the_shipped_schema():
    """Its own SQL against a database `schema.sql` loaded: a typo in a name would
    otherwise fail every real run and no test."""
    query = re.search(r'-c "(select .*?)" 2>/dev/null', _verify_fn(), re.DOTALL).group(1)
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
