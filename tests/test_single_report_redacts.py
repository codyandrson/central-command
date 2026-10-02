"""`./setup.sh report` carries key NAMES, never a value — and proves it.

The 2026-10-01 design record's D10.3. The report exists because of the rule
above it: a deployment configures through `.env` and never rewrites any part of
Central Command, so anything else it needs is a FINDING. The skill's whole
instruction on a repository defect becomes one line — run `./setup.sh report`,
hand the operator the path, end the turn — and that only works if the file is
safe to paste into a chat.

"Safe" is a mechanism here, not a claim: every function the report is built
from prints names, and then the report is written to a temp file, SCANNED for
every credential value `.env` actually holds, and REFUSED if one appears. This
test is the record's own acceptance for it — feed `.env` an answer file of
marker values and grep the output.

The marker values stand in for the three sources the scan covers: the rows
`questions.tsv` marks `secret=y`, the keys the app phase mints
(`CC_LLM_API_KEY`), and the shapes `make-secrets.sh` generates (`*_PASSWORD`,
`*_TOKEN`, `*_KEY`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_DEBRIS = shutil.ignore_patterns(
    "NUL", "nul", "CON", "con", "AUX", "aux", "PRN", "prn",
    ".env", ".env.*", "__pycache__", "*.pyc",
)

# One marker per source the scan has to cover. Each is long enough that the
# eight-character floor (which keeps `none`, `0` and `1` from matching half the
# English language) does not skip it.
MARKERS = {
    # questions.tsv secret=y
    "CC_JIRA_API_TOKEN": "MARKER_SECRET_jira_aaaaaaaa",
    "CC_LLM_UPSTREAM_API_KEY": "MARKER_SECRET_upstream_bbbbbbbb",
    "CC_EXCHANGE_PASSWORD": "MARKER_SECRET_exchange_cccccccc",
    # minted by the app phase, asked by nobody
    "CC_LLM_API_KEY": "MARKER_SECRET_spine_dddddddd",
    # make-secrets.sh's generated set, by shape
    "CC_LLM_PROXY_ADMIN_KEY": "MARKER_SECRET_admin_eeeeeeee",
    "CC_NEO4J_PASSWORD": "MARKER_SECRET_neo4j_ffffffff",
    "N8N_ENCRYPTION_KEY": "MARKER_SECRET_n8nkey_gggggggg",
    "CC_EMAIL_FACADE_TOKEN": "MARKER_SECRET_facade_hhhhhhhh",
}


def _bash_exe() -> str:
    from central_command.api.update import _bash

    resolved = _bash()
    if not resolved:
        pytest.skip("no usable bash on this host")
    return resolved


def _set(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    seen = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0] if "=" in line else None
        if key in values:
            lines[i] = f"{key}={values[key]}"
            seen.add(key)
    for key, val in values.items():
        if key not in seen:
            lines.append(f"{key}={val}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copytree(ROOT / "deploy", repo / "deploy", ignore=_DEBRIS)
    shutil.copy2(ROOT / ".env.example", repo / ".env.example")
    shutil.copy2(ROOT / ".env.example", repo / ".env")
    shutil.copy2(ROOT / "VERSION", repo / "VERSION")
    (repo / "central_command" / "db").mkdir(parents=True)
    shutil.copy2(ROOT / "central_command" / "db" / "schema.sql",
                 repo / "central_command" / "db" / "schema.sql")
    (tmp_path / "home").mkdir()
    state = tmp_path / "state"
    state.mkdir()
    _set(repo / ".env", {"CC_STATE_DIR": str(state), **MARKERS})
    return repo


def _run(repo: Path, *args: str):
    env = dict(os.environ)
    env.update(HOME=str(repo.parent / "home"),
               XDG_STATE_HOME=str(repo.parent / "home" / "state"),
               CC_VERIFY_MAX_WAIT="1")
    for stale in ("CC_STATE_DIR", *MARKERS):
        env.pop(stale, None)
    return subprocess.run(
        [_bash_exe(), "setup.sh", *args],
        cwd=repo / "deploy" / "single",
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300, env=env,
    )


def _reports(repo: Path) -> list[Path]:
    return sorted((repo.parent / "state").glob("report-*.txt"))


def test_the_report_carries_no_marker_value_and_leads_with_the_ledger(tree: Path):
    state = tree.parent / "state"
    (state / "ledger.tsv").write_text(
        "# ledger.tsv\n"
        "check/tree-pristine\tdone\t2.55.0\t2026-10-01T00:00:00Z\tnone\t\n"
        "app/mint-key\tfailed\t2.55.0\t2026-10-01T00:00:01Z\tabc\t/key/generate did not return a key\n"
        "llm/catalog-filled\tgate\t2.55.0\t2026-10-01T00:00:02Z\tdef\tthe catalog needs your provider details\n",
        encoding="utf-8",
    )

    r = _run(tree, "report")

    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS report:" in r.stdout, r.stdout
    written = _reports(tree)
    assert len(written) == 1, written
    body = written[0].read_text(encoding="utf-8", errors="replace")

    # THE assertion: not one value, from any of the three sources.
    for key, value in MARKERS.items():
        assert value not in body, f"{key}'s VALUE reached the report"
    assert "MARKER_SECRET" not in body

    # ...while the NAMES are all there, which is what makes the file useful.
    for key in MARKERS:
        assert key in body, f"{key} should appear by name"

    # The ledger is the first section, and every failed/waiting row comes with
    # its reason and the NAMES of the keys that shaped it.
    assert body.index("== the ledger") < body.index("== the whole log")
    assert "app/mint-key" in body and "failed" in body
    assert "/key/generate did not return a key" in body
    assert "llm/catalog-filled" in body and "gate" in body
    open_rows = body.split("== every failed or waiting row", 1)[1]
    open_rows = open_rows.split("== the whole log", 1)[0]
    assert "app/mint-key [failed]" in open_rows, open_rows
    assert "CC_LLM_PROXY_ADMIN_KEY" in open_rows, (
        "a failed row must name the .env keys that shaped it — that is how a "
        f"line becomes an action:\n{open_rows}"
    )

    # Mode 0600, like every other file this profile generates that could carry
    # anything sensitive. (A silent no-op on NTFS, hence the platform guard.)
    if os.name != "nt":
        assert oct(written[0].stat().st_mode)[-3:] == "600"


def test_the_report_refuses_to_write_when_a_value_got_in(tree: Path):
    """Belt and braces (D10.3): the scan is not decoration. A log line that
    somehow carried a credential — which is exactly how the three historic
    spine-key incidents were discovered — makes the whole report a FAIL, and
    nothing is written."""
    state = tree.parent / "state"
    # The report includes the WHOLE log of the last run, so a leak there is
    # the realistic shape of this defect.
    (state / "setup-log.txt").write_text(
        "2026-10-01T00:00:00Z run run start: ./setup.sh llm\n"
        f"2026-10-01T00:00:01Z llm FAIL oops: {MARKERS['CC_LLM_API_KEY']}\n",
        encoding="utf-8",
    )

    r = _run(tree, "report")

    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL report: REFUSING to write the report" in r.stdout, r.stdout
    assert "CC_LLM_API_KEY" in r.stdout, "the refusal names the KEY, never the value"
    assert MARKERS["CC_LLM_API_KEY"] not in r.stdout
    assert not _reports(tree), "nothing may be written when the scan trips"
    assert not list(state.glob("*.partial")), "no half-written report may survive"


def test_diagnose_is_an_alias_for_report(tree: Path):
    """D10.3 retires the old bundle: one shape, and no `tail -40` window
    cutting off above wherever the run stopped."""
    r = _run(tree, "diagnose")

    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS report:" in r.stdout, r.stdout
    assert len(_reports(tree)) == 1
    assert not (tree.parent / "state" / "setup-diagnostics.txt").exists(), (
        "the point-in-time bundle is replaced, not kept alongside"
    )


def test_the_report_names_the_state_dir_first(tree: Path):
    """The record's cap on size is "the last run, and say so in the first
    line"; the first SECTION is the state dir, because every path the rest of
    the file mentions is relative to it."""
    r = _run(tree, "report")
    assert r.returncode == 0, r.stdout + r.stderr
    body = _reports(tree)[0].read_text(encoding="utf-8", errors="replace")
    head = body.split("== ", 2)
    assert "state directory" in head[1], body[:600]
    assert str(tree.parent / "state") in head[1]
    assert "WHOLE last run" in body.splitlines()[3], body[:400]
