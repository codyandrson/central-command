"""What the ledger DECIDES, tested by invoking bash directly.

The 2026-10-01 design record's D1/D2. `deploy/single/ledger-lib.sh` exists as a
separate file of pure functions for the same reason `machine-lib.sh` does: the
decisions are what matter and none of them needs podman, a network or a
database. Which step comes first, whether a step's inputs changed, whether a
phase may run at all — those are the questions nothing in the installer was
asking before 2026-10-01, and they are all testable here, on Linux.

What each test pins:

* **the fingerprint changes when a `reads` key's VALUE changes, and not
  otherwise.** That is the whole mechanism behind "an `.env` edit re-runs
  exactly the steps whose inputs it changed"; a fingerprint that moved on an
  unrelated edit would re-run the world, and one that did not move on a
  relevant edit would skip a step that has to happen again;
* **the ledger round-trips, atomically, one row per step** — and a `reason`
  carrying a tab cannot shift the columns of the row it is in;
* **blocked detection** — the driver's first rule: never run ahead, and name
  the first require that is not done;
* **`cc_exit_code`'s precedence table** — FAIL > USERACTION > WARN, the one
  rule that replaced three copies. A FAIL reported as "stopped for your
  action" is how `phase_fetch` became a phase that could never return 1, and
  how `update.sh apply` merged past a failed build.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SINGLE = ROOT / "deploy" / "single"
LIB = SINGLE / "ledger-lib.sh"


def bash() -> str:
    """The bash that can run this repo's shell scripts.

    A bare `"bash"` is NOT it on Windows: PATH's first `bash` is System32's WSL
    launcher, a shell in a different filesystem where this checkout's paths
    mean nothing. The product resolves it the same way
    (`central_command.api.update._bash`).
    """
    from central_command.api.update import _bash

    resolved = _bash()
    if not resolved:
        pytest.skip("no usable bash on this host")
    return resolved


def sh(script: str) -> subprocess.CompletedProcess:
    """Run a snippet with env-lib.sh and ledger-lib.sh sourced.

    cwd= + RELATIVE source paths, so no absolute Windows path ever reaches
    bash (the pattern test_single_machine_lib.py uses).
    """
    prelude = ". ../env-lib.sh\n. ./ledger-lib.sh\n"
    return subprocess.run(
        [bash(), "-c", prelude + textwrap.dedent(script)],
        cwd=SINGLE,
        capture_output=True,
        text=True,
    )


def ok(script: str) -> str:
    r = sh(script)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_the_library_parses():
    subprocess.run([bash(), "-n", LIB.name], cwd=LIB.parent, check=True)


def test_the_library_has_no_side_effects_at_source_time():
    """It is SOURCED by setup.sh before anything is decided, so sourcing it may
    not touch the filesystem or print."""
    r = sh("true")
    assert r.returncode == 0
    assert r.stdout == "", r.stdout


# ── the manifest ────────────────────────────────────────────────────────────


def test_the_shipped_manifest_loads_and_declares_the_phases_in_order():
    out = ok("""
      cc_steps_load ./steps.tsv || exit 1
      cc_steps_phases
    """)
    assert out.split() == [
        "check", "machine", "fetch", "llm", "stack", "app",
        "verify", "test", "boot", "demo",
    ], out


def test_a_row_with_a_lost_field_is_refused():
    """The manifest is release content: a hand edit that lost a tab is a stop,
    not something to guess around."""
    r = sh("""
      printf 'check\\tbroken\\trun\\t-\\t-\\t-\\n' > "$PWD/../../.cc-steps-test.tsv"
      cc_steps_load "$PWD/../../.cc-steps-test.tsv"
      rc=$?
      rm -f "$PWD/../../.cc-steps-test.tsv"
      exit $rc
    """)
    assert r.returncode == 1
    assert "8 tab-separated fields" in r.stderr, r.stderr


def test_a_row_with_no_probe_is_refused():
    r = sh("""
      t="$(mktemp)"
      printf 'check\\tx\\trun\\t-\\t-\\t-\\t-\\tdoc\\n' > "$t"
      cc_steps_load "$t"; rc=$?
      rm -f "$t"; exit $rc
    """)
    assert r.returncode == 1
    assert "no probe" in r.stderr, r.stderr


def test_a_reads_glob_is_refused():
    """A fingerprint is taken over NAMED keys in the manifest's order; a glob
    has no stable order, so it would make the digest depend on the shell."""
    r = sh("""
      t="$(mktemp)"
      printf 'check\\tx\\trun\\t-\\tCC_IMG_*\\t-\\tp_always\\tdoc\\n' > "$t"
      cc_steps_load "$t"; rc=$?
      rm -f "$t"; exit $rc
    """)
    assert r.returncode == 1
    assert "GLOB" in r.stderr, r.stderr


def test_step_fields_and_requires_come_back_qualified():
    out = ok("""
      cc_steps_load ./steps.tsv || exit 1
      printf '%s|' "$(cc_step_field boot boot-api kind)"
      printf '%s|' "$(cc_steps_requires boot boot-api)"
      printf '%s|' "$(cc_step_field llm catalog-filled kind)"
      printf '%s' "$(cc_step_field check tree-pristine reads)"
    """)
    kind, requires, gate_kind, empty_reads = out.split("|")
    assert kind == "run"
    # Space-separated and `<phase>/<step>`-qualified, which is what the ledger
    # keys on — the same step name lives in two phases (venv, cockpit,
    # image-sandbox, embed-dimension).
    assert requires.split() == ["app/install", "app/mint-key"], requires
    assert gate_kind == "gate"
    assert empty_reads == "", "a `-` field must read back as empty, not as '-'"


# ── the fingerprint ─────────────────────────────────────────────────────────


def _fp(env_body: str, reads: str, tmp_path: pathlib.Path) -> str:
    env = tmp_path / ".env"
    env.write_text(env_body, encoding="utf-8")
    return ok(f'cc_fingerprint "{env.as_posix()}" "{reads}"')


def test_the_fingerprint_moves_only_when_an_input_value_moves(tmp_path):
    base = "CC_LITELLM_PORT=4000\nCC_ENABLE_SPEECH=1\nCC_PROXY=\n"
    reads = "CC_LITELLM_PORT,CC_ENABLE_SPEECH"

    first = _fp(base, reads, tmp_path)
    assert len(first) == 64, f"expected a sha256, got {first!r}"
    assert first == _fp(base, reads, tmp_path), "the same inputs must hash the same"

    # An edit to a key this step does NOT read changes nothing: that is what
    # keeps one answer from re-running the whole install.
    unrelated = base.replace("CC_PROXY=", "CC_PROXY=http://proxy.corp.example:8080")
    assert _fp(unrelated, reads, tmp_path) == first

    # An edit to a key it DOES read changes everything about this row.
    changed = base.replace("CC_LITELLM_PORT=4000", "CC_LITELLM_PORT=4100")
    assert _fp(changed, reads, tmp_path) != first

    # ...and so does CLEARING one, which is the shape of defect the fingerprint
    # is here to catch (a key that went blank between releases).
    blanked = base.replace("CC_LITELLM_PORT=4000", "CC_LITELLM_PORT=")
    assert _fp(blanked, reads, tmp_path) != first


def test_the_order_of_the_reads_column_is_part_of_the_digest(tmp_path):
    """The digest is a property of the ROW, not of a shell's hash ordering, so
    two different `reads` columns over the same keys are different rows."""
    body = "A_ONE=1\nB_TWO=2\n"
    assert _fp(body, "A_ONE,B_TWO", tmp_path) != _fp(body, "B_TWO,A_ONE", tmp_path)


def test_a_step_with_no_inputs_has_a_stable_sentinel(tmp_path):
    env = tmp_path / ".env"
    env.write_text("X=1\n", encoding="utf-8")
    for reads in ("", "-"):
        assert ok(f'cc_fingerprint "{env.as_posix()}" "{reads}"') == "none"


# ── the ledger file ─────────────────────────────────────────────────────────


def test_the_ledger_round_trips_one_row_per_step(tmp_path):
    led = (tmp_path / "ledger.tsv").as_posix()
    out = ok(f"""
      cc_ledger_write "{led}" app/mint-key failed 2.55.0 2026-10-01T00:00:00Z abc "no key came back"
      cc_ledger_write "{led}" app/install   done   2.55.0 2026-10-01T00:00:01Z def ""
      # ...and the SAME step again: an update, never a second row.
      cc_ledger_write "{led}" app/mint-key done   2.55.0 2026-10-01T00:00:02Z abc ""
      printf 'rows=%s|' "$(cc_ledger_read "{led}" | grep -c .)"
      printf 'status=%s|' "$(cc_ledger_status "{led}" app/mint-key)"
      printf 'at=%s|' "$(cc_ledger_field "{led}" app/mint-key 4)"
      printf 'fp=%s|' "$(cc_ledger_field "{led}" app/install 5)"
      printf 'absent=[%s]' "$(cc_ledger_status "{led}" app/nothing)"
    """)
    assert "rows=2|" in out, out
    assert "status=done|" in out, out
    assert "at=2026-10-01T00:00:02Z|" in out, out
    assert "fp=def|" in out, out
    # An absent row reads as EMPTY, which the driver treats as `pending` —
    # which is what an empty ledger is made of.
    assert out.endswith("absent=[]"), out


def test_a_reason_carrying_a_tab_cannot_shift_the_columns(tmp_path):
    led = (tmp_path / "ledger.tsv").as_posix()
    out = ok(f"""
      cc_ledger_write "{led}" llm/catalog gate 2.55.0 now ff "a\treason\twith\ttabs"
      printf 'status=%s|fp=%s' \\
        "$(cc_ledger_status "{led}" llm/catalog)" \\
        "$(cc_ledger_field "{led}" llm/catalog 5)"
    """)
    assert out == "status=gate|fp=ff", out


def test_the_ledger_is_written_atomically_and_leaves_no_temp(tmp_path):
    led = tmp_path / "ledger.tsv"
    ok(f'cc_ledger_write "{led.as_posix()}" check/tree-pristine done 2.55.0 now none ""')
    assert led.exists()
    assert not list(tmp_path.glob("*.tmp")), "a tmp file survived the rename"


def test_the_ledger_header_is_a_comment_and_not_a_row(tmp_path):
    """`init_state` creates the file with its own header so an EMPTY ledger
    still exists — its existence is what says "this tree is a deployment"."""
    led = tmp_path / "ledger.tsv"
    ok(f'cc_ledger_write "{led.as_posix()}" check/tree-pristine done 2.55.0 now none ""')
    text = led.read_text(encoding="utf-8")
    assert text.startswith("#"), text
    rows = ok(f'cc_ledger_read "{led.as_posix()}"')
    assert rows.count("\n") == 1, rows


# ── blocked detection: the driver's first rule ──────────────────────────────


def test_an_empty_ledger_blocks_every_phase_that_requires_anything(tmp_path):
    led = (tmp_path / "ledger.tsv").as_posix()
    out = ok(f"""
      cc_steps_load ./steps.tsv || exit 1
      printf 'boot=[%s]|' "$(cc_ledger_blocked "{led}" boot)"
      printf 'verify=[%s]|' "$(cc_ledger_blocked "{led}" verify)"
      # `check` is the first phase and requires nothing, so it is never blocked.
      cc_ledger_blocked "{led}" check && printf 'check=BLOCKED' || printf 'check=free'
    """)
    assert "boot=[app/install pending]|" in out, out
    assert "verify=[stack/up-stack pending]|" in out, out
    assert out.endswith("check=free"), out


def test_a_failed_row_blocks_everything_downstream_of_it(tmp_path):
    """D2: a mid-function abort leaves `failed` rows, and the STATUS is what
    the refusal names — "requires app/mint-key, which is failed" is a
    different sentence from "which is pending", and both are better than
    `./setup.sh boot` being accepted."""
    led = (tmp_path / "ledger.tsv").as_posix()
    out = ok(f"""
      cc_steps_load ./steps.tsv || exit 1
      cc_ledger_write "{led}" app/install  done   2.55.0 now none ""
      cc_ledger_write "{led}" app/mint-key failed 2.55.0 now none "/key/generate did not return a key"
      cc_ledger_blocked "{led}" boot
    """)
    assert out == "app/mint-key failed", out


def test_a_phase_whose_requires_are_all_done_is_not_blocked(tmp_path):
    """And an INTRA-phase require is not a prerequisite the driver can wait
    for: `boot/boot-roster` requires `boot/boot-api`, which only becomes done
    once `boot` has run. P1 keeps the phase FUNCTIONS (D1), so that ordering
    is the phase's own linear bash; counting it here would block `boot` on
    every install that has not booted yet — forever."""
    led = (tmp_path / "ledger.tsv").as_posix()
    r = sh(f"""
      cc_steps_load ./steps.tsv || exit 1
      cc_ledger_write "{led}" app/install  done 2.55.0 now none ""
      cc_ledger_write "{led}" app/mint-key done 2.55.0 now none ""
      cc_ledger_write "{led}" app/cockpit  done 2.55.0 now none ""
      cc_ledger_blocked "{led}" boot
    """)
    assert r.returncode == 1, r.stdout
    assert r.stdout == ""


# ── the one exit-code rule (D5) ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "fails,warns,actions,expected",
    [
        (0, 0, 0, "0"),
        (0, 1, 0, "2"),
        (0, 0, 1, "3"),
        (0, 1, 1, "3"),   # USERACTION outranks WARN
        (1, 0, 0, "1"),
        (1, 0, 1, "1"),   # ...and a FAIL outranks the gate. THE change.
        (1, 1, 1, "1"),
        (2, 3, 4, "1"),
    ],
)
def test_the_exit_code_precedence_table(fails, warns, actions, expected):
    """FAIL > USERACTION > WARN, from the one function. The row that matters is
    (1, 0, 1): the old `run_phase` returned 3 there — "stopped for your
    action" — and `phase_fetch`, whose only FAIL path ended in a USERACTION
    summary, could therefore never return 1. `update.sh apply` read that 3 as
    the operator's move and merged the new code over a failed build."""
    assert ok(f"cc_exit_code {fails} {warns} {actions}") == expected


def test_the_exit_code_tolerates_an_empty_counter():
    """Called from a `set -u` script with a counter that was never bumped."""
    assert ok('cc_exit_code "" "" ""') == "0"
