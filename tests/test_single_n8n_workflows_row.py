"""`stack/n8n-workflows` — a fresh install applies the n8n façade workflows (v2.58.0).

Until this release only `update.sh` ran `deploy/n8n/apply-workflows.sh
--podman`, as a step of its own after `app`; `./setup.sh` never did. So a
single-node install with `CC_ENABLE_N8N=1` had no façade workflows until its
first update. The step is a manifest row now — the `stack` phase's last, after
the `n8n-credential` gate it requires (the import resolves the Gmail nodes BY
NAME) — and `update.sh` reaches it through the phase like everything else.

What is pinned here, against the REAL `./setup.sh stack` AND the REAL
`apply-workflows.sh` in a temp copy of the tree:

* flag off → the row is DONE and the script never runs;
* credential present → the script runs ONCE (one import, one n8n restart) and
  the row is done;
* the script's own `USERACTION n8n:` (a credential it could not resolve —
  it prints that and still exits 0) reaches the protocol as THIS row's
  USERACTION, exit 3, naming the one command — never a PASS, never a FAIL;
* a script that fails is a FAIL naming its reason;
* the probe — one read-only SELECT on n8n's own database — is false when a
  shipped workflow is missing, so a run that "succeeded" without it is the
  driver's "effect is absent" FAIL;
* the probe's expected ids are DERIVED from deploy/n8n/workflows/, with the
  script's own rule for the optional calendar pair.

The stubs: `podman` plays the n8n container (a directory under the stub dir,
so the script's copy-in and in-container sha256 check are real) and the n8n
database (it answers the SELECTs by what they ask); `curl` answers the
script's webhook poll. stack's image asserts and `compose up` are stubbed as in
tests/test_single_human_rows.py — the phase function, both n8n rows, their
probes, the ledger and the exit code are the shipped code.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from tests.installer_source import drives_installer, with_stub_path, write_lf
from tests.test_single_driver_ledger import (  # noqa: F401  (tree is a fixture)
    ROOT,
    _protocol,
    _run,
    _set,
    _state_dir,
    _write_ledger,
    tree,
)
from tests.test_single_human_rows import (
    CRED_ANSWER,
    _STACK_STUBS,
    _append_stubs,
    _ledger,
    _lines,
    _rows,
)

WORKFLOWS = ROOT / "deploy" / "n8n" / "workflows"
SCRIPT = ROOT / "deploy" / "n8n" / "apply-workflows.sh"
STACK = ROOT / "deploy" / "single" / "phases" / "stack.sh"

# The ids the files ship, by the pattern the script reads them with.
_ID = re.compile(r'^  "id": "([A-Za-z0-9]*)",$', re.M)
SHIPPED = {p.name: _ID.search(p.read_text(encoding="utf-8")).group(1)
           for p in sorted(WORKFLOWS.glob("*.json"))}
CALENDAR = ("cc-calendar-facade.json", "lib-google-calendar.json")

# The stub `curl` must beat Git's /mingw64/bin/curl, which Git Bash's launcher
# puts in front of the PATH it is handed: with_stub_path puts the stub
# directory first for real (these tests were skipped on Windows until the
# 2026-10-02 testbed run's second pass), and the stubs and their data are
# written LF (write_lf) — a CR survives `read -r`.

# podman, as the n8n container and n8n's database. Everything it is asked goes
# to $STUB/log. Flags (files in $STUB): n8n-down (the database does not
# answer), creds (the credential names present; _stack writes both shipped
# names unless told otherwise), missing-cred (the script's unresolved-credential
# SELECT answers this name), import-fails, wf-missing (one shipped workflow is
# not in workflow_entity).
_PODMAN = r'''#!/usr/bin/env bash
echo "podman $*" >> "$STUB/log"
''' + CRED_ANSWER + r'''
ctr="$STUB/ctr"
case "$1" in
  # The stack phase asks whether the RETIRED Graphiti server's container is
  # still there (phases/stack.sh, retire_graphiti_container): it is not.
  container) [[ "${3:-}" == *graphiti ]] && exit 1; exit 0 ;;
  cp)        dst="${3#*:}"; mkdir -p "$ctr$(dirname "$dst")"; cp "$2" "$ctr$dst"; exit 0 ;;
  restart)   echo restart >> "$STUB/restarts"; exit 0 ;;
  exec)      shift; [[ "$1" == -i ]] && shift
             c="$1"; shift
             if [[ "$c" == *n8n-db ]]; then
               [[ -f "$STUB/n8n-down" ]] && exit 125
               sql="${@: -1}"
               case "$sql" in
                 *credentials_entity*) answer_creds "$sql" ;;
                 *string_agg*)         cat "$STUB/missing-cred" 2>/dev/null ;;
                 "select count(*) from workflow_entity"*)
                   n="$(grep -o "'[A-Za-z0-9]*'" <<<"$sql" | wc -l | tr -d ' ')"
                   [[ -f "$STUB/wf-missing" ]] && n=$((n - 1))
                   echo "$n" ;;
               esac
               exit 0
             fi
             case "$1" in
               sh)        mkdir -p "$ctr/tmp/cc-workflows" ;;
               sha256sum) sha256sum "$ctr$2" ;;
               n8n)       [[ -f "$STUB/import-fails" ]] && { echo "import exploded" >&2; exit 1; }
                          echo "imported" >> "$STUB/imports" ;;
             esac
             exit 0 ;;
esac
exit 0
'''

# curl: the script's webhook poll. 500 is what the façade answers without a
# token (the WORKFLOW refused it) — registered, so the poll ends at once.
_CURL = '#!/usr/bin/env bash\necho "curl $*" >> "$STUB/log"\nprintf 500\n'


def _stack(repo: Path, *, n8n: str = "1", calendar: str = "", token: str = "test-facade-token",
           creds: tuple[str, ...] = ("Gmail account", "Google Calendar account"), **flags: str):
    stub = repo.parent / "stub"
    stub.mkdir(exist_ok=True)
    bindir = repo.parent / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in (("podman", _PODMAN), ("curl", _CURL)):
        write_lf(bindir / name, body, mode=0o755)
    write_lf(stub / "creds", "".join(c + "\n" for c in creds))
    for name, value in flags.items():
        write_lf(stub / name.replace("_", "-"), value + "\n")
    _append_stubs(repo, _STACK_STUBS)
    _set(repo / ".env", {"CC_ENABLE_N8N": n8n, "CC_N8N_PORT": "5679", "CC_EMBED_DIM": "1024",
                         "CC_POD_PREFIX": "cc-", "CC_EMAIL_FACADE_TOKEN": token,
                         "CC_CALENDAR_FACADE_TOKEN": calendar})
    phases_before = ("check", "machine", "fetch", "llm")
    _write_ledger(repo, [(f"{r[0]}/{r[1]}", "done") for r in _rows() if r[0] in phases_before])
    stubbed = with_stub_path({"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}, bindir)
    r = _run(repo, "stack", env_extra={**stubbed, "STUB": stub.as_posix()})
    log = (stub / "log").read_text(encoding="utf-8") if (stub / "log").exists() else ""
    return r, log, stub


def _count(stub: Path, name: str) -> int:
    f = stub / name
    return len(f.read_text().splitlines()) if f.exists() else 0


def _probe_selects(log: str) -> list[str]:
    return [l for l in log.splitlines()
            if l.startswith("podman exec cc-n8n-db psql") and "from workflow_entity" in l
            and "update " not in l and "string_agg" not in l]


# ── the row as declared ─────────────────────────────────────────────────────


def test_the_row_follows_the_credential_gate_and_reads_the_workflow_tree():
    rows = {(r[0], r[1]): r for r in _rows()}
    wf = rows[("stack", "n8n-workflows")]
    assert wf[2] == "run" and wf[6] == "p_n8n_workflows", wf
    assert wf[3] == "stack/n8n-credential", wf
    reads = wf[4].split(",")
    for key in ("CC_ENABLE_N8N", "CC_EMAIL_FACADE_TOKEN", "CC_CALENDAR_FACADE_TOKEN", "@deploy/n8n"):
        assert key in reads, (key, reads)
    assert wf[5] == "-", "the script writes nothing in .env"
    order = [(r[0], r[1]) for r in _rows()]
    # The stack phase's LAST row: nothing after it in stack waits on n8n.
    assert order.index(("stack", "n8n-workflows")) == order.index(("stack", "n8n-credential")) + 1
    assert order[order.index(("stack", "n8n-workflows")) + 1][0] != "stack"


def test_the_probe_and_the_script_agree_on_the_optional_calendar_pair():
    """The probe derives its ids from the files with the script's own rule;
    the rule's file list lives in both, and must not drift apart."""
    script = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"^\s*([\w.|-]+\.json)\)\s*\[\[ -n \"\$CAL_TOKEN\" \]\]", script, re.M)
    assert m, "apply-workflows.sh's calendar `case` arm moved — re-point this test"
    in_script = set(m.group(1).split("|"))
    m = re.search(r'^N8N_CALENDAR_WORKFLOWS="([^"]+)"', STACK.read_text(encoding="utf-8"), re.M)
    assert m, "N8N_CALENDAR_WORKFLOWS is gone from phases/stack.sh"
    assert set(m.group(1).split()) == in_script == set(CALENDAR)
    assert all((WORKFLOWS / f).is_file() for f in CALENDAR)


# ── the row, run ────────────────────────────────────────────────────────────


def _n8n_container_asks(log: str) -> list[str]:
    """`container exists` calls the n8n script made — the stack phase's own
    question about the retired Graphiti container is not the script running."""
    return [l for l in log.splitlines() if "container exists" in l and "graphiti" not in l]


@drives_installer
def test_n8n_off_is_done_and_the_script_never_runs(tree: Path):
    r, log, stub = _stack(tree, n8n="0")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS n8n-workflows: CC_ENABLE_N8N is not 1" in r.stdout
    assert not _n8n_container_asks(log) and _count(stub, "imports") == 0, log
    assert not (_state_dir(tree) / "n8n-workflows.log").exists()
    assert _ledger(tree)["stack/n8n-workflows"][1] == "done"


@drives_installer
def test_the_credential_gate_stops_the_phase_before_the_script(tree: Path):
    r, log, stub = _stack(tree, creds=())
    assert r.returncode == 3, r.stdout + r.stderr
    assert _lines(r.stdout, "USERACTION n8n-credential:"), r.stdout
    assert not _lines(r.stdout, "PASS n8n-workflows:"), r.stdout
    assert _count(stub, "imports") == 0 and not _n8n_container_asks(log), log


@drives_installer
def test_credential_present_applies_once_and_the_row_is_done(tree: Path):
    r, log, stub = _stack(tree)
    assert r.returncode == 0, r.stdout + r.stderr
    assert any(l.startswith("PASS n8n-workflows:") for l in _protocol(r.stdout)), r.stdout
    assert _count(stub, "imports") == 1, log
    assert _count(stub, "restarts") == 1, log
    assert _ledger(tree)["stack/n8n-workflows"][1] == "done"
    # The script's narration is kept in the state dir, never the checkout.
    kept = (_state_dir(tree) / "n8n-workflows.log").read_text(encoding="utf-8")
    assert "façade webhook registered" in kept, kept
    # The probe is ONE read-only SELECT per evaluation, for exactly the ids the
    # files ship — the calendar pair left out, its token being blank.
    selects = _probe_selects(log)
    assert selects, log
    for sel in selects:
        assert "where active and id in" in sel, sel
        for name, wid in SHIPPED.items():
            assert (f"'{wid}'" in sel) == (name not in CALENDAR), (name, sel)


@drives_installer
def test_with_the_calendar_token_the_probe_expects_all_four(tree: Path):
    r, log, _ = _stack(tree, calendar="test-calendar-token")
    assert r.returncode == 0, r.stdout + r.stderr
    for sel in _probe_selects(log):
        assert all(f"'{wid}'" in sel for wid in SHIPPED.values()), sel


@drives_installer
def test_the_scripts_own_useraction_reaches_the_operator_as_one(tree: Path):
    """apply-workflows.sh prints `USERACTION n8n: …` on stderr when the import
    left a credential unresolved, then activates, restarts and exits 0. Under
    update.sh's `step` that was a PASS. Here it is the row's USERACTION — the
    BACKSTOP: the n8n-credential gate asks for every name the import needs
    first, so this fires only when n8n disagrees with that gate (a credential
    deleted in between, a type the import would not bind)."""
    r, _, stub = _stack(tree, calendar="test-calendar-token", missing_cred="Google Calendar account")
    assert r.returncode == 3, r.stdout + r.stderr
    ua = _lines(r.stdout, "USERACTION n8n-workflows:")
    assert len(ua) == 1, r.stdout
    assert "Google Calendar account" in ua[0], ua[0]
    # The one command, never "this script" (which the operator never runs).
    assert "re-run ./setup.sh" in ua[0] and "this script" not in ua[0], ua[0]
    assert not _lines(r.stdout, "FAIL "), r.stdout
    assert not _lines(r.stdout, "PASS n8n-workflows:"), r.stdout
    assert _count(stub, "imports") == 1
    row = _ledger(tree)["stack/n8n-workflows"]
    assert row[1] == "gate" and "Google Calendar account" in row[5], row


@drives_installer
def test_a_failing_script_is_a_fail_naming_why(tree: Path):
    r, _, _ = _stack(tree, import_fails="1")
    assert r.returncode == 1, r.stdout + r.stderr
    fl = _lines(r.stdout, "FAIL n8n-workflows:")
    assert fl and "exit 1" in fl[0] and "n8n import:workflow failed" in fl[0], r.stdout
    assert not _lines(r.stdout, "USERACTION n8n-workflows:"), r.stdout
    assert _ledger(tree)["stack/n8n-workflows"][1] == "failed"


@drives_installer
def test_a_blank_facade_token_is_a_fail_not_a_silent_skip(tree: Path):
    """The script refuses a blank CC_EMAIL_FACADE_TOKEN before it touches n8n
    (no curl reached), and that refusal is the row's FAIL."""
    r, log, stub = _stack(tree, token="")
    fl = _lines(r.stdout, "FAIL n8n-workflows:")
    assert r.returncode == 1 and fl and "CC_EMAIL_FACADE_TOKEN is empty" in fl[0], r.stdout
    assert _count(stub, "imports") == 0 and "curl" not in log, log


@drives_installer
def test_the_probe_is_false_when_a_shipped_workflow_is_missing(tree: Path):
    """The script exits 0, but n8n's database holds one shipped workflow fewer
    than the files: the effect is absent, and the driver says so by name."""
    r, _, _ = _stack(tree, wf_missing="1")
    assert r.returncode == 1, r.stdout + r.stderr
    fl = _lines(r.stdout, "FAIL n8n-workflows:")
    assert fl and "effect is absent" in fl[-1] and "p_n8n_workflows" in fl[-1], r.stdout
    assert _ledger(tree)["stack/n8n-workflows"][1] == "failed"


# ── the credential names the gate asks for ──────────────────────────────────


def _json_credentials(files) -> dict[str, str]:
    """name -> type, by a real JSON parse: every node's credentials entry."""
    import json

    out: dict[str, str] = {}
    for f in files:
        for node in json.loads(f.read_text(encoding="utf-8")).get("nodes", []):
            for ctype, ref in (node.get("credentials") or {}).items():
                out[ref["name"]] = ctype
    return out


def _bash_required(calendar: str) -> dict[str, str]:
    import subprocess

    from tests.test_single_driver_ledger import _bash_exe

    lib = (ROOT / "deploy" / "single" / "phases" / "stack.sh").as_posix()
    script = (f'REPO_ROOT="{ROOT.as_posix()}"; p_flag() {{ local v="${{!1:-}}"; printf "%s" "${{v:-$2}}"; }}; '
              f'. "{lib}"; n8n_required_credentials')
    r = subprocess.run([_bash_exe(), "-c", script], capture_output=True, text=True,
                       env={**os.environ, "CC_CALENDAR_FACADE_TOKEN": calendar})
    assert r.returncode == 0, r.stderr
    return dict(l.split("\t") for l in r.stdout.splitlines() if l)


def test_the_gate_asks_for_exactly_the_credentials_the_import_references():
    """The names are derived, never typed: the bash extraction equals a JSON
    parse of the files the script imports, under the script's calendar rule."""
    email_only = [p for p in sorted(WORKFLOWS.glob("*.json")) if p.name not in CALENDAR]
    assert _bash_required("") == _json_credentials(email_only)
    assert _bash_required("cal-token") == _json_credentials(sorted(WORKFLOWS.glob("*.json")))
    assert "Gmail account" in _bash_required("")
    assert "Google Calendar account" not in _bash_required("")


def test_the_rows_doc_names_every_credential_the_import_can_demand():
    """The checklist's bold row is the operator's warning, so its sentence must
    name every credential a full import (calendar pair included) references."""
    doc = next(r for r in _rows() if (r[0], r[1]) == ("stack", "n8n-credential"))[7]
    assert doc.startswith("YOUR MOVE:"), doc
    for name in _json_credentials(sorted(WORKFLOWS.glob("*.json"))):
        assert f'"{name}"' in doc, (name, doc)
    reads = next(r for r in _rows() if (r[0], r[1]) == ("stack", "n8n-credential"))[4].split(",")
    assert "CC_CALENDAR_FACADE_TOKEN" in reads and "@deploy/n8n/workflows" in reads, reads
