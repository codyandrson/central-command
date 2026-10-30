"""The epic-small-fixes verification ledger (story 11.16).

The epic's Done-when says each of its seventeen small-fix rulings (the ticket
lists eighteen ids), the operator's three additions (FR-51 env backup, stale
words, harness speed) and the sixteen orchestrator-found rows R22-R37 has a
test or a recorded verification naming its ruling id or origin. This file is
that check, so a row cannot be dropped silently: LEDGER below maps every row to
its proving test file(s), and the tests assert that each file exists and that
its module docstring (a web test: its describe text) names the row's id.

R20 (stale words) is proven by `test_the_stale_words_are_gone` below, which runs
story 11.14's ten verify greps over the files on disk and fails on any hit.

R37 is a recorded verification, not a test: the legacy-cohort count is read on
the deployment at release time (story 11.17) and recorded in the memlog. Its row
therefore points at this file, which is where the ledger names it.

To see it fail: delete an id from a listed file's docstring and re-run.
`python tests/test_small_fixes_ledger.py` prints the proving files, one per
line, for the one-process run: pytest -q $(python tests/test_small_fixes_ledger.py --pytest)
"""
import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# row -> (token the docstring must name, proving files relative to the repo root).
# A row is never removed to make the ledger pass: an unproven row is a finding
# for its story.
LEDGER: dict[str, tuple[str, tuple[str, ...]]] = {
    # The ruling ids.
    "GATE-009": ("GATE-009", ("tests/test_proposal_args.py",)),
    "GATE-067": ("GATE-067", ("tests/test_confidence.py", "tests/test_proposal_args.py")),
    "RUNTIME-006": ("RUNTIME-006", ("tests/test_context_overflow.py", "tests/test_context_window.py")),
    "GATE-084": ("GATE-084", ("tests/test_attachments.py",)),
    "INGEST-005": ("INGEST-005", ("tests/test_http_client_kwargs.py",)),
    "DEPLOY-SINGLE-009": ("DEPLOY-SINGLE-009", ("tests/test_update_runner.py",)),
    "DEPLOY-SINGLE-001": ("DEPLOY-SINGLE-001", ("tests/test_single_questions_schema.py",)),
    "PROCESS-006": ("PROCESS-006", ("tests/test_discover_proxy.py",)),
    "PROCESS-030": ("PROCESS-030", ("tests/test_prepush_scan.py",)),
    "GATE-077": ("GATE-077", ("tests/test_mcp_register.py",)),
    "MODELS-018": ("MODELS-018", ("tests/test_token_budget_retired.py",)),
    "RUNTIME-060": ("RUNTIME-060", ("tests/test_discussion_stall.py",)),
    "D7-P": ("D7-P", ("tests/test_orchestration.py",)),
    "GRAPH-064": ("GRAPH-064", ("tests/test_curation_embed_key.py",)),
    "PROCESS-048": ("PROCESS-048", ("tests/test_no_live_model.py",)),
    "DEPLOY-K3S-021": ("DEPLOY-K3S-021", ("tests/test_k3s_verify_selfcheck.py",)),
    "G-07": ("G-07", ("tests/test_sandbox_isolation.py",)),
    "COCKPIT-028": ("COCKPIT-028", ("web/server/routes/cc-file-browser-guard.test.ts",)),
    # The operator's three additions.
    "FR-51 env backup": ("FR-51", ("tests/test_k3s_backup_env.py",)),
    "stale words": ("R20", ("tests/test_small_fixes_ledger.py",)),
    "harness speed": ("R21", ("tests/test_conftest_database.py",)),
    # The orchestrator-found rows.
    "R22": ("R22", ("web/src/features/gates/useOverrides.test.tsx",)),
    "R23": ("R23", ("tests/test_consult_wait.py",)),
    "R24": ("R24", ("tests/test_world_moved.py", "tests/test_world_moved_e2e.py")),
    "R25": ("R25", ("tests/test_world_moved.py",)),
    "R26": ("R26", ("tests/test_graph_delete_episode.py",)),
    "R27": ("R27", ("tests/test_exec_failure_open.py", "tests/test_world_moved_limit.py")),
    "R28": ("R28", ("tests/test_resume_sweep_isolation.py",)),
    "R29": ("R29", ("tests/test_world_moved.py", "tests/test_world_moved_limit.py")),
    "R30": ("R30", ("tests/test_facade_configured.py",)),
    "R31": ("R31", ("tests/test_orchestration.py",)),
    "R32": ("R32", ("tests/test_retry_sweep.py", "tests/test_outage_wire.py")),
    "R33": ("R33", ("tests/test_ledger.py",)),
    "R34": ("R34", ("tests/test_resume_parking.py",)),
    "R35": ("R35", ("tests/test_graph_delete_episode.py", "tests/test_outage_doctrine.py")),
    "R36": ("R36", ("tests/test_outage_backoff.py",)),
    "R37": ("R37", ("tests/test_small_fixes_ledger.py",)),
}

# Declared apart from the table, so deleting a row from LEDGER fails here.
EXPECTED_ROWS = {
    "GATE-009", "GATE-067", "RUNTIME-006", "GATE-084", "INGEST-005",
    "DEPLOY-SINGLE-009", "DEPLOY-SINGLE-001", "PROCESS-006", "PROCESS-030",
    "GATE-077", "MODELS-018", "RUNTIME-060", "D7-P", "GRAPH-064", "PROCESS-048",
    "DEPLOY-K3S-021", "G-07", "COCKPIT-028",
    "FR-51 env backup", "stale words", "harness speed",
    *{f"R{n}" for n in range(22, 38)},
}

PAIRS = [(row, f) for row, (_tok, files) in LEDGER.items() for f in files]


def proving_files() -> list[str]:
    """Every proving file once, in table order."""
    seen: dict[str, None] = {}
    for _row, f in PAIRS:
        seen.setdefault(f)
    return list(seen)


def python_proving_files() -> list[str]:
    return [f for f in proving_files() if f.endswith(".py")]


def _named_text(path: Path) -> str:
    """The text that must carry the id: a Python module's docstring, a web
    test's describe titles."""
    source = path.read_text()
    if path.suffix == ".py":
        return ast.get_docstring(ast.parse(source)) or ""
    return "\n".join(
        m.group(2) for m in re.finditer(r"describe\(\s*(['\"`])(.*?)\1", source, re.S)
    )


def _names(text: str, token: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9-]){re.escape(token)}(?![0-9])", text) is not None


def test_no_row_is_dropped():
    assert set(LEDGER) == EXPECTED_ROWS


def test_every_row_lists_a_proving_file():
    assert all(files for _tok, files in LEDGER.values())


@pytest.mark.parametrize("row,rel", PAIRS, ids=[f"{r}:{f}" for r, f in PAIRS])
def test_the_proving_file_exists_and_names_its_row(row, rel):
    path = ROOT / rel
    assert path.is_file(), f"{row}: proving file {rel} does not exist"
    token = LEDGER[row][0]
    assert _names(_named_text(path), token), (
        f"{row}: {rel} does not name {token} in its "
        f"{'module docstring' if path.suffix == '.py' else 'describe text'}"
    )


# R20, stale words: story 11.14's ten verify greps. Each phrase was false when
# it was written or after a later story; none may come back. (phrase,
# ignore case, paths, a line prefix that is allowed to stay.)
STALE_WORDS = [
    ("no network egress", True, ("central_command", "deploy"), None),
    ("default-deny", False, ("central_command", "deploy/k3s/sandbox.Dockerfile"), None),
    ("nothing applies it at runtime", False, ("deploy/k3s",), None),
    ("seeded enabled for the same class", False, ("central_command/db/schema.sql",), None),
    ("no propose_*/Executor writes exist yet", False, ("central_command",), None),
    ("façade fallback until", False, ("central_command",), None),
    ("falls through to the n8n façade", False, ("central_command/integrations/jira.py",), None),
    ("deny-all", True, ("deploy/k3s/60-sandbox.yaml",), "# INGRESS stays deny-all"),
    ("vector index", True, ("deploy/single/verify.sh",
                            "central_command/integrations/neo4j_writer.py",
                            "tests/test_graph_embedding_width.py"), None),
    ("DL-007", False, ("skills/mcp/references/sandbox-mechanics.md",), None),
]
_SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv"}


def _files_under(rel: str, root: Path = ROOT):
    path = root / rel
    if path.is_file():
        yield path
        return
    for p in sorted(path.rglob("*")):
        if p.is_file() and not _SKIP_DIRS & set(p.relative_to(root).parts):
            yield p


def stale_hits(phrase, ignore_case, paths, allowed_prefix, root: Path = ROOT):
    hits = []
    needle = phrase.lower() if ignore_case else phrase
    for rel in paths:
        for f in _files_under(rel, root):
            try:
                lines = f.read_text().splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(lines, 1):
                hay = line.lower() if ignore_case else line
                if needle in hay and not (
                    allowed_prefix and line.lstrip().startswith(allowed_prefix)
                ):
                    hits.append(f"{f.relative_to(root)}:{n}: {line.strip()}")
    return hits


@pytest.mark.parametrize(
    "phrase,ignore_case,paths,allowed", STALE_WORDS, ids=[w[0] for w in STALE_WORDS]
)
def test_the_stale_words_are_gone(phrase, ignore_case, paths, allowed):
    """R20 (story 11.14): the phrase returns nothing over its paths."""
    hits = stale_hits(phrase, ignore_case, paths, allowed)
    assert not hits, f"stale words {phrase!r} are back:\n" + "\n".join(hits)


def test_the_proving_files_are_listed(capsys):
    """Print the list so the one-process run can be driven from it
    (`pytest -s` or `-rP` shows it)."""
    files = python_proving_files()
    with capsys.disabled():
        print("\nLEDGER proving files (python):", " ".join(files))
        print("LEDGER proving files (web):",
              " ".join(f for f in proving_files() if not f.endswith(".py")))
    assert files


if __name__ == "__main__":
    selected = python_proving_files() if "--pytest" in sys.argv else proving_files()
    print("\n".join(selected))
