"""`./setup.sh report` carries key NAMES, never a value — and proves it.

The 2026-10-01 design record's D10.3. The report exists because of the rule
above it: a deployment configures through `.env` and never rewrites any part of
Central Command, so anything else it needs is a FINDING. The skill's whole
instruction on a repository defect becomes one line — run `./setup.sh report`,
hand the operator the path, end the turn — and that only works if the file is
safe to paste into a chat.

"Safe" is a mechanism here, not a claim — two of them since P2 (D11's
`report` row, from Replicated troubleshoot.sh's redactors):

* IN PROCESS: every section is piped through one filter as it is collected,
  which replaces what `deploy/single/redact.tsv` declares — the VALUE of every
  matching `.env` key, as `[REDACTED:<KEY>]`, and credential SHAPES (`user:pw@`
  in a URL, a bearer token, an `sk-` key) whether or not `.env` knows them. A
  value that turns up in a log is replaced instead of costing the operator the
  whole report;
* THE GUARD: the result is still written to a temp file, SCANNED for every
  credential value `.env` holds, and REFUSED if one appears. It is the guard
  nobody else has, so a test below defeats the filter on purpose and proves
  the refusal path is still alive.

This file is the record's own acceptance for both — feed `.env` an answer file
of marker values, plant them where they leak in real life, and grep the output.

The marker values stand in for the three sources the scan covers: the rows
`questions.tsv` marks `secret=y`, the keys the app phase mints
(`CC_LLM_API_KEY`), and the shapes `make-secrets.sh` generates (`*_PASSWORD`,
`*_TOKEN`, `*_KEY`).
"""

from __future__ import annotations

import os
import re
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


def _run(repo: Path, *args: str, path_prefix: Path | None = None):
    env = dict(os.environ)
    env.update(HOME=str(repo.parent / "home"),
               XDG_STATE_HOME=str(repo.parent / "home" / "state"),
               CC_VERIFY_MAX_WAIT="1")
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env.get('PATH', '')}"
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


def test_a_value_in_the_logs_is_redacted_in_process_and_the_report_is_written(tree: Path):
    """The P2 change, and the reason for it: a credential in the last run's
    log, or in a process log, used to make the whole report a FAIL — at
    exactly the moment the operator needed it. Now each is replaced as it is
    collected, labelled with the KEY NAME, and the report is written."""
    state = tree.parent / "state"
    (state / "setup-log.txt").write_text(
        "2026-10-01T00:00:00Z run run start: ./setup.sh llm\n"
        f"2026-10-01T00:00:01Z llm FAIL oops: {MARKERS['CC_LLM_API_KEY']}\n",
        encoding="utf-8",
    )
    (state / "uvicorn.log").write_text(
        f"INFO connecting to neo4j with {MARKERS['CC_NEO4J_PASSWORD']}\n",
        encoding="utf-8",
    )

    r = _run(tree, "report")

    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS report:" in r.stdout, r.stdout
    body = _reports(tree)[0].read_text(encoding="utf-8", errors="replace")
    assert "MARKER_SECRET" not in body
    log = body.split("== the whole log of the last run", 1)[1].split("\n== ", 1)[0]
    assert "llm FAIL oops: [REDACTED:CC_LLM_API_KEY]" in log, log
    procs = body.split("== the processes this install starts", 1)[1].split("\n== ", 1)[0]
    assert "connecting to neo4j with [REDACTED:CC_NEO4J_PASSWORD]" in procs, procs
    # The header says how the file was made safe, and in which order.
    head = "\n".join(body.splitlines()[:4])
    assert "REDACTED IN PROCESS" in head and "second check" in head, head


def test_credential_shapes_are_redacted_whether_or_not_env_knows_them(tree: Path):
    """A connection string with a password, a bearer token and an sk- key that
    no .env key holds — the shapes the declared list catches on their own."""
    state = tree.parent / "state"
    (state / "uvicorn.log").write_text(
        "dsn postgresql://someone:Pl4ntedUserinfoPass@db.example.com:5432/x\n"
        "request Authorization: Bearer Pl4ntedBearerToken.abc-123\n"
        "litellm virtual key sk-Pl4ntedVirtualKey0123456789 rejected\n"
        "harmless http://db.example.com/path@ref and disk-usage_report_nightly_x\n",
        encoding="utf-8",
    )

    r = _run(tree, "report")

    assert r.returncode == 0, r.stdout + r.stderr
    body = _reports(tree)[0].read_text(encoding="utf-8", errors="replace")
    assert "Pl4nted" not in body, body.split("== the processes", 1)[1][:800]
    assert "postgresql://[REDACTED:url-userinfo]@db.example.com:5432/x" in body
    assert "Authorization: Bearer [REDACTED:bearer-token]" in body
    assert "virtual key [REDACTED:sk-key] rejected" in body
    # ...and what only LOOKS like a shape is left alone.
    assert "harmless http://db.example.com/path@ref and disk-usage_report_nightly_x" in body


def test_container_logs_go_through_the_filter_too(tree: Path):
    """diagnose_sections — the compose, podman and container-log half — is the
    likeliest place for a credential of all. A stub podman prints one."""
    stub = tree.parent / "stub"
    stub.mkdir()
    (stub / "podman").write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        "  ps) echo cc-litellm ;;\n"
        f"  logs) echo 'litellm: master key {MARKERS['CC_LLM_PROXY_ADMIN_KEY']} loaded' ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (stub / "podman").chmod(0o755)

    r = _run(tree, "report", path_prefix=stub)

    assert r.returncode == 0, r.stdout + r.stderr
    body = _reports(tree)[0].read_text(encoding="utf-8", errors="replace")
    assert "litellm: master key [REDACTED:CC_LLM_PROXY_ADMIN_KEY] loaded" in body, (
        body.split("== last 100 log lines", 1)[-1][:600]
    )
    assert "MARKER_SECRET" not in body


def test_the_guard_still_refuses_when_the_filter_is_defeated(tree: Path):
    """Belt and braces (D10.3): the scan is the guard nobody else has, so it
    must stay alive behind the filter. Here the filter is DEFEATED on purpose —
    the temp copy's report_redact is redefined as a pass-through — and a log
    line carrying a credential (the shape the three historic spine-key
    incidents were discovered in) makes the whole report a FAIL; nothing is
    written."""
    setup = tree / "deploy" / "single" / "setup.sh"
    text = setup.read_text(encoding="utf-8")
    assert text.rstrip().endswith('main "$@"'), text[-200:]
    head, _, _ = text.rstrip().rpartition('main "$@"')
    setup.write_text(head + 'report_redact() { cat; }\nmain "$@"\n', encoding="utf-8")
    state = tree.parent / "state"
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


def test_an_emptied_redaction_list_refuses_rather_than_running_unredacted(tree: Path):
    """redact.tsv is release content. A copy with no `key` row would leave the
    filter AND the scan without the list they share, so the report refuses
    outright — fail closed, the way steps.tsv does — and writes nothing."""
    redact = tree / "deploy" / "single" / "redact.tsv"
    redact.write_text("# emptied\n", encoding="utf-8")

    r = _run(tree, "report")

    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL report: REFUSING to write the report" in r.stdout, r.stdout
    assert "redact.tsv" in r.stdout
    assert not _reports(tree)
    assert not list((tree.parent / "state").glob("*.partial"))


# ── redact.tsv itself ───────────────────────────────────────────────────────

REDACT = ROOT / "deploy" / "single" / "redact.tsv"


def _redact_rows() -> list[list[str]]:
    rows = []
    for raw in REDACT.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\r")
        if not line.strip() or line.startswith("#"):
            continue
        rows.append(line.split("\t"))
    return rows


def test_redact_tsv_parses_and_every_row_is_well_formed():
    rows = _redact_rows()
    assert rows, "redact.tsv declares no rows"
    for f in rows:
        assert len(f) == 4, f"redact.tsv: kind, pattern, replace, doc — got {f!r}"
        kind, pattern, replace, doc = f
        assert kind in ("key", "shape"), f
        assert pattern not in ("", "-"), f
        assert doc not in ("", "-") and doc.endswith("."), f"one plain sentence: {f!r}"
        assert "\x01" not in pattern, "\\001 is the filter's sed delimiter"
        if kind == "key":
            assert re.fullmatch(r"[A-Z0-9_*]+", pattern), f"a key row is a glob over KEY NAMES: {f!r}"
            assert replace == "-", f"a key row's replacement is always [REDACTED:<KEY>]: {f!r}"
        else:
            assert replace not in ("", "-") and "[REDACTED:" in replace, f
            # The pattern must compile as the ERE the filter hands sed -E.
            r = subprocess.run(["sed", "-E", "-e", f"s\x01{pattern}\x01{replace}\x01g"],
                               input="probe\n", capture_output=True, text=True)
            assert r.returncode == 0, f"{pattern!r}: {r.stderr}"


def test_redact_tsv_declares_the_default_set():
    keys = {f[1] for f in _redact_rows() if f[0] == "key"}
    for glob in ("*_PASSWORD", "*_TOKEN", "*_KEY", "*_SECRET",
                 "CC_LLM_API_KEY", "CC_LLM_PROXY_ADMIN_KEY"):
        assert glob in keys, f"redact.tsv must declare the key row {glob}"
    shapes = [f for f in _redact_rows() if f[0] == "shape"]
    labels = " ".join(f[2] for f in shapes)
    for label in ("url-userinfo", "bearer-token", "sk-key"):
        assert label in labels, f"redact.tsv must declare the {label} shape"


def test_the_filter_and_the_guard_read_one_list():
    """report_secret_values is THE list; the filter loads from it and the guard
    scans with it, so they cannot disagree about what a secret is — and no
    second hard-coded glob survives in the code."""
    src = (ROOT / "deploy" / "single" / "setup.sh").read_text(encoding="utf-8")
    load = src.split("report_redact_load() {", 1)[1].split("\n}", 1)[0]
    guard = src.split("report_leaking_keys() {", 1)[1].split("\n}", 1)[0]
    assert "report_secret_values" in load and "report_secret_values" in guard
    values = src.split("report_secret_values() {", 1)[1].split("\n}", 1)[0]
    assert "*_PASSWORD|" not in values, "the globs are redact.tsv's, not the code's"


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
