"""The two `human` rows P1 left out: the corporate CA on disk, the n8n credential.

The 2026-10-01 design record's D1 lists the operator's non-command steps the
manifest must carry; P4 adds the last two:

* **`check/ca-bundle`** — when `CC_CA_BUNDLE` is set, the PEM it names is on
  this host, readable and non-empty. The operator places it; nothing in the
  driver can.
* **`stack/n8n-credential`** — when `CC_ENABLE_N8N=1`, n8n holds EVERY
  credential the workflows about to be imported reference by name: "Gmail
  account" always, "Google Calendar account" when CC_CALENDAR_FACADE_TOKEN
  selects the calendar pair (v2.58.0 — the names are derived from
  deploy/n8n/workflows/, never typed in the driver). Creating one is an OAuth
  sign-in in the n8n UI.

D2: a `human` row whose probe is false is a GATE — the run stops with exit 3
and a USERACTION naming what to do and WHERE, and the ledger records `gate`.
Not applicable (the key unset, the flag off) is DONE.

These drive the REAL `./setup.sh check` / `./setup.sh stack` in a temp copy of
the tree (tests/test_single_driver_ledger.py's fixture). Only what is not
under test is stubbed, by functions appended just before the copy's final
`main "$@"`: check's network and host sections, and stack's image asserts and
`compose up` — the row's own reporter, its probe, the phase function, the
ledger and the exit code are the shipped code. `podman` is a stub on PATH that
answers the read-only SELECTs the n8n row asks, from a list of the credential
names "present" in n8n.
"""

from __future__ import annotations

import os
from pathlib import Path

from tests.test_single_driver_ledger import (  # noqa: F401  (tree is a fixture)
    ROOT,
    _bash_exe,
    _protocol,
    _run,
    _set,
    _state_dir,
    _write_ledger,
    tree,
)

STEPS = ROOT / "deploy" / "single" / "steps.tsv"


def _append_stubs(repo: Path, stubs: str) -> None:
    setup = repo / "deploy" / "single" / "setup.sh"
    text = setup.read_text(encoding="utf-8")
    tail = 'main "$@"'
    assert text.rstrip().endswith(tail), 'setup.sh no longer ends in main "$@"'
    setup.write_text(text.rstrip()[: -len(tail)] + stubs + "\n" + tail + "\n", encoding="utf-8")


def _ledger(repo: Path) -> dict[str, list[str]]:
    led = (_state_dir(repo) / "ledger.tsv").read_text(encoding="utf-8")
    return {l.split("\t")[0]: l.split("\t") for l in led.splitlines() if l and not l.startswith("#")}


def _lines(out: str, prefix: str) -> list[str]:
    return [l for l in _protocol(out) if l.startswith(prefix)]


def _rows() -> list[list[str]]:
    return [l.split("\t") for l in STEPS.read_text(encoding="utf-8").splitlines()
            if l and not l.startswith("#")]


def test_both_rows_are_declared_human_with_a_your_move_doc():
    rows = {(r[0], r[1]): r for r in _rows()}
    ca = rows[("check", "ca-bundle")]
    assert ca[2] == "human" and ca[4] == "CC_CA_BUNDLE" and ca[6] == "p_ca_bundle", ca
    n8n = rows[("stack", "n8n-credential")]
    assert n8n[2] == "human" and n8n[3] == "stack/up-stack" and n8n[6] == "p_n8n_credential", n8n
    assert "CC_ENABLE_N8N" in n8n[4].split(","), n8n
    for r in (ca, n8n):
        assert r[7].startswith("YOUR MOVE:"), r[7]
    # EARLY: the CA row is the check phase's, before the first phase that
    # changes anything, and the machine's CA install waits on it.
    order = [(r[0], r[1]) for r in _rows()]
    assert order.index(("check", "ca-bundle")) < order.index(("machine", "machine"))
    assert "check/ca-bundle" in rows[("machine", "machine-ca")][3].split(",")
    # AFTER the stack is up: the n8n UI exists only then.
    assert order.index(("stack", "n8n-credential")) > order.index(("stack", "up-stack"))


def test_the_ca_question_judges_the_shape_and_still_normalises_the_path():
    """The answer is a PATH (configure rewrites any v_path* answer for Windows,
    F33); whether a file is there is the row's business, so configure takes the
    path before the file arrives and check does not FAIL the answer."""
    q = next(l.split("\t") for l in (ROOT / "deploy/single/questions.tsv").read_text(encoding="utf-8").splitlines()
             if l.startswith("CC_CA_BUNDLE\t"))
    assert q[5] == "v_path", q
    import subprocess
    lib = (ROOT / "deploy" / "single" / "questions-lib.sh").as_posix()
    r = subprocess.run([_bash_exe(), "-c", f'. "{lib}"; v_path /nowhere/yet.pem && echo ok; v_path https://ca.example.com/x.pem; echo "rc=$?"'],
                       capture_output=True, text=True)
    assert r.stdout.splitlines()[0] == "ok", r.stdout
    assert "not a URL" in r.stdout and "rc=1" in r.stdout, r.stdout


# ── check/ca-bundle ─────────────────────────────────────────────────────────
# check's later sections dial the network (registries, indexes, the upstream
# LLM) or read this host (ports, podman): none is under test, all are stubbed.
_CHECK_STUBS = """
preflight_host() { pass "host-stub" "the host section ran"; }
machine_report() { :; }
check_images() { :; }
check_indexes() { :; }
check_llm() { :; }
check_integrations() { :; }
validate_compose_config() { :; }
check_models() { :; }
check_ports_free() { :; }
p_linger() { return 0; }
"""


def _check(repo: Path, ca: str | None):
    _append_stubs(repo, _CHECK_STUBS)
    if ca is not None:
        _set(repo / ".env", {"CC_CA_BUNDLE": ca})
    return _run(repo, "check")


def test_ca_unset_is_done(tree: Path):
    r = _check(tree, None)
    assert r.returncode in (0, 2), r.stdout + r.stderr
    assert any(l.startswith("PASS ca-bundle: CC_CA_BUNDLE is blank") for l in _protocol(r.stdout)), r.stdout
    assert _ledger(tree)["check/ca-bundle"][1] == "done"


def test_ca_set_and_absent_stops_for_the_operator_naming_the_key_and_the_path(tree: Path):
    missing = tree.parent / "corp" / "ca-bundle.pem"
    r = _check(tree, missing.as_posix())
    assert r.returncode == 3, r.stdout + r.stderr
    ua = _lines(r.stdout, "USERACTION ca-bundle:")
    assert len(ua) == 1, r.stdout
    assert "YOUR MOVE:" in ua[0] and "CC_CA_BUNDLE" in ua[0] and missing.as_posix() in ua[0], ua[0]
    assert "no such file" in ua[0], ua[0]
    assert not _lines(r.stdout, "FAIL "), r.stdout
    # A gate STOPS: nothing after the answers section ran on a missing trust store.
    assert "host-stub" not in r.stdout, r.stdout
    row = _ledger(tree)["check/ca-bundle"]
    assert row[1] == "gate" and "YOUR MOVE" in row[5], row


def test_ca_set_and_empty_is_not_placed(tree: Path):
    empty = tree.parent / "empty.pem"
    empty.write_text("", encoding="utf-8")
    r = _check(tree, empty.as_posix())
    assert r.returncode == 3, r.stdout + r.stderr
    ua = _lines(r.stdout, "USERACTION ca-bundle:")
    assert ua and "EMPTY" in ua[0], r.stdout
    assert _ledger(tree)["check/ca-bundle"][1] == "gate"


def test_ca_placed_is_done_and_check_goes_on(tree: Path):
    pem = tree.parent / "corp-ca.pem"
    pem.write_text("-----BEGIN CERTIFICATE-----\nMIIBplaceholder\n-----END CERTIFICATE-----\n", encoding="utf-8")
    r = _check(tree, pem.as_posix())
    assert r.returncode in (0, 2), r.stdout + r.stderr
    assert any(l.startswith("PASS ca-bundle:") and pem.as_posix() in l for l in _protocol(r.stdout)), r.stdout
    assert "host-stub" in r.stdout
    assert _ledger(tree)["check/ca-bundle"][1] == "done"


# ── stack/n8n-credential ────────────────────────────────────────────────────
# How a stub n8n database answers the two credential SELECTs, from
# <stub>/creds (the names "present", one per line; absent file = none): the
# probe's `count(distinct name) … in (…)` and the phase's per-name
# `count(*) … where name = '…'`. Shared with tests/test_single_n8n_workflows_row.py.
CRED_ANSWER = r'''
answer_creds() { # answer_creds <sql>
  local sql="$1" c n=0 want
  if [[ "$sql" == *"count(distinct name)"* ]]; then
    while IFS= read -r c; do [[ -n "$c" && "$sql" == *"'$c'"* ]] && n=$((n+1)); done < <(cat "$STUB/creds" 2>/dev/null)
    echo "$n"
  else
    want="${sql#*where name = \'}"; want="${want%\'*}"
    grep -qxF -- "$want" "$STUB/creds" 2>/dev/null && echo 1 || echo 0
  fi
}
'''

# The stub podman: `machine list` answers nothing (no machine), `exec` logs its
# argv and answers from <stub>/creds — unless <stub>/n8n-down, which fails it.
_PODMAN = r'''#!/usr/bin/env bash
echo "podman $*" >> "$STUB/log"
''' + CRED_ANSWER + r'''
case "$1" in
  exec) [[ -f "$STUB/n8n-down" ]] && exit 125
        answer_creds "${@: -1}"
        exit 0 ;;
esac
exit 0
'''

# stack's image asserts, `compose up` and the catch-up are not under test;
# neither are the probes of the rows they own.
_STACK_STUBS = """
need_image() { pass "$1" "stub: present"; return 0; }
compose() { return 0; }
catch_up_images() { pass "$1" "stub: every container on its image"; return 0; }
p_image_sandbox() { return 0; }
p_image_crawler() { return 0; }
p_up_stack() { return 0; }
p_restart_on_boot() { return 0; }
"""

# The row AFTER the credential, stack/n8n-workflows, runs the real
# deploy/n8n/apply-workflows.sh; it has its own harness
# (tests/test_single_n8n_workflows_row.py), so here it is a stub that says it
# ran — appended by _stack only, never part of _STACK_STUBS, which that harness
# shares.
_WORKFLOWS_STUB = """
stack_n8n_workflows() { pass "n8n-workflows" "stub: applied"; return 0; }
p_n8n_workflows() { return 0; }
"""


GMAIL, CALENDAR = "Gmail account", "Google Calendar account"


def _stack(repo: Path, *, n8n: str, creds: tuple[str, ...] = (), calendar: str = "",
           down: bool = False):
    stub = repo.parent / "stub"
    stub.mkdir(exist_ok=True)
    bindir = repo.parent / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "podman").write_text(_PODMAN, encoding="utf-8")
    (bindir / "podman").chmod(0o755)
    if creds:
        (stub / "creds").write_text("".join(c + "\n" for c in creds), encoding="utf-8")
    if down:
        (stub / "n8n-down").write_text("", encoding="utf-8")
    _append_stubs(repo, _STACK_STUBS + _WORKFLOWS_STUB)
    _set(repo / ".env", {"CC_ENABLE_N8N": n8n, "CC_N8N_PORT": "5679", "CC_EMBED_DIM": "1024",
                         "CC_POD_PREFIX": "cc-", "CC_CALENDAR_FACADE_TOKEN": calendar})
    # Every row before stack is done at this release, so the ledger lets it run.
    phases_before = ("check", "machine", "fetch", "llm")
    _write_ledger(repo, [(f"{r[0]}/{r[1]}", "done") for r in _rows() if r[0] in phases_before])
    r = _run(repo, "stack", env_extra={"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
                                       "STUB": str(stub)})
    log = (stub / "log").read_text(encoding="utf-8") if (stub / "log").exists() else ""
    return r, log


def test_n8n_off_is_done_and_n8n_is_never_asked(tree: Path):
    r, log = _stack(tree, n8n="0")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS n8n-credential: CC_ENABLE_N8N is not 1" in r.stdout
    assert "podman exec" not in log, log
    assert _ledger(tree)["stack/n8n-credential"][1] == "done"


def test_n8n_on_without_the_credential_stops_naming_it_and_the_ui(tree: Path):
    r, log = _stack(tree, n8n="1")
    assert r.returncode == 3, r.stdout + r.stderr
    ua = _lines(r.stdout, "USERACTION n8n-credential:")
    assert len(ua) == 1, r.stdout
    assert "YOUR MOVE:" in ua[0] and '"Gmail account"' in ua[0], ua[0]
    # No calendar token: the calendar pair is not imported, so not asked for.
    assert CALENDAR not in ua[0], ua[0]
    assert "http://127.0.0.1:5679/" in ua[0], ua[0]
    row = _ledger(tree)["stack/n8n-credential"]
    assert row[1] == "gate", row
    # The question is ONE read-only SELECT, in n8n's own database container.
    asks = [l for l in log.splitlines() if l.startswith("podman exec cc-n8n-db psql")]
    assert asks, log
    assert all(" -c select count(*) from " in a for a in asks), asks
    assert any("from credentials_entity where name = 'Gmail account'" in a for a in asks), asks
    # The gate stopped the phase BEFORE the row after it ran.
    assert not _lines(r.stdout, "PASS n8n-workflows:"), r.stdout
    assert not any(w in log.lower() for w in ("insert", "update ", "delete", "drop ")), log


def test_n8n_on_with_the_credential_is_done(tree: Path):
    r, _ = _stack(tree, n8n="1", creds=(GMAIL,))
    assert r.returncode == 0, r.stdout + r.stderr
    assert any(l.startswith("PASS n8n-credential:") and "Gmail account" in l for l in _protocol(r.stdout)), r.stdout
    assert _ledger(tree)["stack/n8n-credential"][1] == "done"


def test_calendar_token_and_only_gmail_stops_naming_exactly_the_calendar_credential(tree: Path):
    """make-secrets.sh generates CC_CALENDAR_FACADE_TOKEN, so the import will
    bring the calendar pair and its credential. The stop is HERE, on the
    checklist's bold row — not one row later at the import — and it names only
    what is missing."""
    r, log = _stack(tree, n8n="1", creds=(GMAIL,), calendar="cal-token")
    assert r.returncode == 3, r.stdout + r.stderr
    ua = _lines(r.stdout, "USERACTION n8n-credential:")
    assert len(ua) == 1, r.stdout
    assert f'"{CALENDAR}"' in ua[0] and "http://127.0.0.1:5679/" in ua[0], ua[0]
    assert f'"{GMAIL}"' not in ua[0], "a present credential is not asked for again: " + ua[0]
    assert _ledger(tree)["stack/n8n-credential"][1] == "gate"
    assert not _lines(r.stdout, "PASS n8n-workflows:"), r.stdout
    asked = [l for l in log.splitlines() if "credentials_entity" in l]
    assert any(f"name = '{CALENDAR}'" in l for l in asked), asked


def test_calendar_token_and_both_credentials_is_done(tree: Path):
    r, log = _stack(tree, n8n="1", creds=(GMAIL, CALENDAR), calendar="cal-token")
    assert r.returncode == 0, r.stdout + r.stderr
    ok = [l for l in _protocol(r.stdout) if l.startswith("PASS n8n-credential:")]
    assert ok and GMAIL in ok[0] and CALENDAR in ok[0], r.stdout
    assert _ledger(tree)["stack/n8n-credential"][1] == "done"
    # The PROBE (asked after a phase that reported success) is ONE read-only
    # SELECT counting the distinct required names present.
    probe = [l for l in log.splitlines() if "count(distinct name)" in l]
    assert probe, log
    assert all("from credentials_entity where name in ('Gmail account','Google Calendar account')" in l
               for l in probe), probe


def test_n8n_database_that_does_not_answer_is_a_fail_not_a_gate(tree: Path):
    """Waiting on the operator is exit 3; a database that cannot be asked is
    not the operator's move."""
    r, _ = _stack(tree, n8n="1", down=True)
    assert r.returncode == 1, r.stdout + r.stderr
    assert _lines(r.stdout, "FAIL n8n-credential:"), r.stdout
    assert not _lines(r.stdout, "USERACTION n8n-credential:"), r.stdout
    assert _ledger(tree)["stack/n8n-credential"][1] == "failed"
