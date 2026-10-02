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
    assert requires.split() == ["app/install", "app/mint-key", "verify/selfcheck"], requires
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
      cc_ledger_write "{led}" verify/selfcheck done 2.55.0 now none ""
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


# ── v2.56.0 (P2): the batch write and `started` (D11) ───────────────────────


def test_a_batch_rewrites_its_rows_in_place_appends_new_ones_and_keeps_the_rest(tmp_path):
    led = tmp_path / "ledger.tsv"
    p = led.as_posix()
    ok(f"""
      cc_ledger_write "{p}" check/tree-pristine done 2.55.0 t0 none ""
      cc_ledger_write "{p}" test/test           done 2.55.0 t0 none ""
      cc_ledger_write "{p}" boot/boot-api       failed 2.55.0 t0 fp "no health"
    """)
    ok(f"""
      cc_ledger_write_batch "{p}" \\
        test/test     started 2.56.0 t1 none "" \\
        demo/demo-feed started 2.56.0 t1 fp2 "a\treason"
    """)
    rows = [l.split("\t") for l in ok(f'cc_ledger_read "{p}"').splitlines()]
    assert [r[0] for r in rows] == ["check/tree-pristine", "test/test", "boot/boot-api",
                                    "demo/demo-feed"], rows
    assert rows[0][1:3] == ["done", "2.55.0"], "a row not in the batch is untouched"
    assert rows[1][1:4] == ["started", "2.56.0", "t1"], "replaced IN PLACE, not appended"
    assert rows[2][5] == "no health"
    assert rows[3][5] == "a reason", "a tab in a reason cannot shift the columns"
    assert not list(tmp_path.glob("*.tmp")), "a tmp file survived the rename"
    assert led.read_text(encoding="utf-8").startswith("#")


def test_a_batch_is_idempotent_and_an_empty_batch_writes_nothing(tmp_path):
    led = tmp_path / "ledger.tsv"
    p = led.as_posix()
    batch = f'cc_ledger_write_batch "{p}" app/venv done 2.56.0 t fp "" app/install gate 2.56.0 t fp "x"'
    ok(batch)
    once = led.read_bytes()
    ok(batch)
    assert led.read_bytes() == once
    ok(f'cc_ledger_write_batch "{tmp_path.as_posix()}/never.tsv"')
    assert not (tmp_path / "never.tsv").exists()


def test_a_batch_with_a_short_row_is_refused(tmp_path):
    r = sh(f'cc_ledger_write_batch "{(tmp_path / "l.tsv").as_posix()}" app/venv done 2.56.0 t fp')
    assert r.returncode == 1
    assert "six fields per row" in r.stderr
    assert not (tmp_path / "l.tsv").exists()


def test_mark_started_writes_every_row_of_the_phase_and_nothing_else(tmp_path):
    """D11: written BEFORE the phase runs, one rewrite, every row: version, now,
    the CURRENT fingerprint, and an empty reason (the previous run's reason
    belongs to the previous run)."""
    led = tmp_path / "ledger.tsv"
    env = tmp_path / ".env"
    env.write_text("CC_API_PORT=8080\nCC_DATABASE_URL=postgresql://x\n", encoding="utf-8")
    p = led.as_posix()
    out = ok(f"""
      cc_steps_load ./steps.tsv || exit 1
      cc_ledger_write "{p}" app/install done   2.55.0 t0 none ""
      cc_ledger_write "{p}" boot/boot-api failed 2.55.0 t0 old "it never answered"
      cc_ledger_mark_started "{p}" boot 2.56.0 2026-10-02T00:00:00Z "{env.as_posix()}" || exit 1
      cc_fingerprint "{env.as_posix()}" CC_API_PORT,CC_DATABASE_URL,CC_CA_BUNDLE,CC_TLS_INSECURE,CC_PROXY
    """)
    rows = {l.split("\t")[0]: l.split("\t") for l in ok(f'cc_ledger_read "{p}"').splitlines()}
    boot = [k for k in rows if k.startswith("boot/")]
    assert boot == ["boot/boot-api", "boot/operator-name", "boot/boot-sandbox",
                    "boot/boot-roster", "boot/skills-imported", "boot/boot-cockpit",
                    "boot/boot-at-logon"], boot
    for k in boot:
        assert rows[k][1:4] == ["started", "2.56.0", "2026-10-02T00:00:00Z"], rows[k]
        assert rows[k][5] == "", rows[k]
    assert rows["boot/boot-api"][4] == out, "the fingerprint of the inputs it is about to run with"
    assert rows["app/install"][1] == "done", "another phase's row is untouched"
    # A phase with no manifest rows (status, validate, preflight) writes nothing.
    ok(f'cc_steps_load ./steps.tsv && cc_ledger_mark_started "{(tmp_path / "x.tsv").as_posix()}" status 2.56.0 t "{env.as_posix()}"')
    assert not (tmp_path / "x.tsv").exists()


def test_a_started_row_blocks_what_requires_it(tmp_path):
    led = (tmp_path / "ledger.tsv").as_posix()
    out = ok(f"""
      cc_steps_load ./steps.tsv || exit 1
      cc_ledger_write "{led}" app/install  done    2.56.0 now none ""
      cc_ledger_write "{led}" app/mint-key started 2.56.0 now none ""
      cc_ledger_blocked "{led}" boot
    """)
    assert out == "app/mint-key started", out


# ── the decision, in one place (D2 rule 2 + D11's drift rule) ───────────────


def _decide(tmp_path, row: str, probe: str = "true", reads: str = "CC_A,CC_B",
            env_body: str = "CC_A=1\nCC_B=2\n", version: str = "2.56.0") -> tuple[str, str, str]:
    env = tmp_path / ".env"
    env.write_text(env_body, encoding="utf-8")
    calls = tmp_path / "probe-calls"
    out = ok(f"""
      counted() {{ echo x >> "{calls.as_posix()}"; {probe}; }}
      cc_row_decide $'{row}' {version} "{env.as_posix()}" "{reads}" counted
      printf '%s|%s|%s' "$ROW_VERDICT" "$ROW_CODE" "$ROW_DETAIL"
    """)
    return tuple(out.split("|"))  # type: ignore[return-value]


def _fp_of(tmp_path, body="CC_A=1\nCC_B=2\n", reads="CC_A,CC_B") -> str:
    env = tmp_path / "fp.env"
    env.write_text(body, encoding="utf-8")
    return ok(f'cc_fingerprint "{env.as_posix()}" "{reads}"')


def test_decide_skips_only_a_done_current_unchanged_row_whose_probe_holds(tmp_path):
    fp = _fp_of(tmp_path)
    assert _decide(tmp_path, f"s\\tdone\\t2.56.0\\tT1\\t{fp}\\t") == ("skip", "done", "T1")
    assert (tmp_path / "probe-calls").read_text().count("x") == 1


@pytest.mark.parametrize("status,code,detail", [
    ("failed", "failed", "the key never came back"),
    ("gate", "gate", "the key never came back"),
    ("started", "started", "T1"),
    ("pending", "pending", ""),
    ("whatever-this-is", "pending", ""),
])
def test_decide_runs_every_status_but_done_and_never_probes_it(tmp_path, status, code, detail):
    fp = _fp_of(tmp_path)
    got = _decide(tmp_path, f"s\\t{status}\\t2.56.0\\tT1\\t{fp}\\tthe key never came back")
    assert got == ("run", code, detail)
    assert not (tmp_path / "probe-calls").exists(), "a row the ledger already runs costs no probe"


def test_decide_an_absent_row_is_pending(tmp_path):
    assert _decide(tmp_path, "") == ("run", "pending", "")
    assert not (tmp_path / "probe-calls").exists()


def test_decide_a_version_change_runs_and_says_from_what_to_what(tmp_path):
    fp = _fp_of(tmp_path)
    assert _decide(tmp_path, f"s\\tdone\\t2.55.0\\tT1\\t{fp}\\t") == ("run", "version", "2.55.0 -> 2.56.0")
    assert not (tmp_path / "probe-calls").exists()


def test_decide_an_input_change_runs_without_a_probe(tmp_path):
    fp = _fp_of(tmp_path)
    got = _decide(tmp_path, f"s\\tdone\\t2.56.0\\tT1\\t{fp}\\t", env_body="CC_A=1\nCC_B=3\n")
    assert got == ("run", "inputs", "")
    assert not (tmp_path / "probe-calls").exists()


def test_decide_a_done_row_whose_effect_is_gone_is_drift(tmp_path):
    """DSC's Test-before-Set: the probe IS the refresh. A `done` row whose
    effect is absent runs again — it is never a skip."""
    fp = _fp_of(tmp_path)
    assert _decide(tmp_path, f"s\\tdone\\t2.56.0\\tT1\\t{fp}\\t", probe="false") == \
        ("run", "drift", "counted")


def test_decide_with_no_probe_never_skips(tmp_path):
    fp = _fp_of(tmp_path)
    env = tmp_path / ".env"
    env.write_text("CC_A=1\nCC_B=2\n", encoding="utf-8")
    out = ok(f"""
      cc_row_decide $'s\\tdone\\t2.56.0\\tT1\\t{fp}\\t' 2.56.0 "{env.as_posix()}" CC_A,CC_B -
      printf '%s|%s' "$ROW_VERDICT" "$ROW_CODE"
    """)
    assert out == "run|unprobed"


# ── a phase, and its plan sentence ──────────────────────────────────────────


def _phase(tmp_path, rows: list[str], probes: dict[str, str] | None = None,
           env_body: str = "CC_PORT=1\n", version: str = "2.56.0") -> dict[str, str]:
    """A two-phase manifest of our own (so the probes are ours to define), a
    ledger of the given rows, and cc_phase_decide + cc_phase_plan_text."""
    steps = tmp_path / "steps.tsv"
    steps.write_text(
        "one\ta\trun\t-\tCC_PORT\t-\tp_a\tdoc\n"
        "one\tb\trun\tone/a\t-\t-\tp_b\tdoc\n"
        "one\tc\tgate\tone/b\tCC_PORT,CC_OTHER\t-\tp_c\tdoc\n"
        "two\tz\trun\tone/c\t-\t-\tp_z\tdoc\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text(env_body, encoding="utf-8")
    led = tmp_path / "ledger.tsv"
    led.write_text("# test\n" + "".join(r + "\n" for r in rows), encoding="utf-8")
    calls = tmp_path / "probe-calls"
    defs = "\n".join(
        f'p_{n}() {{ echo {n} >> "{calls.as_posix()}"; {(probes or {}).get(n, "true")}; }}'
        for n in "abcz")
    out = ok(f"""
      {defs}
      cc_steps_load "{steps.as_posix()}" || exit 1
      cc_phase_decide "{led.as_posix()}" one {version} "{env.as_posix()}"
      printf 'verdict=%s\\ncode=%s\\nstep=%s\\nsame=%s\\nlines=%s\\n' \\
        "$PHASE_VERDICT" "$PHASE_CODE" "$PHASE_STEP" "$PHASE_SAME" "${{PHASE_DONE_LINES//$'\\n'/;}}"
      printf 'text=%s\\n' "$(cc_phase_plan_text one {version})"
    """)
    res = dict(l.split("=", 1) for l in out.splitlines())
    res["probed"] = calls.read_text().split() if calls.exists() else []
    return res


def _done(tmp_path, step: str, reads: str, at: str = "T1", version: str = "2.56.0",
          env_body: str = "CC_PORT=1\n") -> str:
    fp = _fp_of(tmp_path, env_body, reads) if reads else "none"
    return f"one/{step}\tdone\t{version}\t{at}\t{fp}\t"


def test_a_phase_on_an_empty_ledger_runs_without_a_single_probe(tmp_path):
    r = _phase(tmp_path, [])
    assert (r["verdict"], r["code"], r["step"], r["same"]) == ("run", "pending", "one/a", "2")
    assert r["probed"] == [], "a fresh install's plan reads no probe"
    assert r["text"] == "one: WILL RUN — never run: one/a is pending (and 2 more rows pending)"


def test_a_phase_whose_rows_are_all_done_skips_and_says_how_many_and_when(tmp_path):
    rows = [_done(tmp_path, "a", "CC_PORT", "T1"), _done(tmp_path, "b", "", "T3"),
            _done(tmp_path, "c", "CC_PORT,CC_OTHER", "T2")]
    r = _phase(tmp_path, rows)
    assert r["verdict"] == "skip"
    assert r["lines"] == "a\tT1;b\tT3;c\tT2;"
    assert r["probed"] == ["a", "b", "c"]
    assert r["text"] == ("one: WILL SKIP — all 3 rows are done at 2.56.0 with the same inputs, "
                         "and every effect still reads present (last done T3)")


def test_a_phase_plan_names_the_changed_inputs_by_key_name_only(tmp_path):
    rows = [_done(tmp_path, "a", "CC_PORT"), _done(tmp_path, "b", ""),
            _done(tmp_path, "c", "CC_PORT,CC_OTHER")]
    r = _phase(tmp_path, rows, env_body="CC_PORT=4242\n")
    assert (r["verdict"], r["code"], r["step"]) == ("run", "inputs", "one/a")
    assert r["same"] == "1", "row c reads CC_PORT too"
    assert r["text"] == ("one: WILL RUN — inputs changed: one/a reads one of CC_PORT "
                         "(and 1 more row with changed inputs)")
    assert "4242" not in r["text"]
    # Row a's verdict was known WITHOUT its probe, so no later row was probed.
    assert r["probed"] == []


def test_a_phase_plan_for_drift_names_the_row_and_its_probe(tmp_path):
    rows = [_done(tmp_path, "a", "CC_PORT"), _done(tmp_path, "b", ""),
            _done(tmp_path, "c", "CC_PORT,CC_OTHER")]
    r = _phase(tmp_path, rows, probes={"b": "false"})
    assert (r["verdict"], r["code"], r["step"]) == ("run", "drift", "one/b")
    assert r["probed"] == ["a", "b"], "c's probe is not asked once the verdict is known"
    assert r["text"] == ("one: WILL RUN — effect absent: one/b is recorded done but its probe "
                         "p_b reads false now (drift)")


@pytest.mark.parametrize("row,expected", [
    ("one/a\tfailed\t2.56.0\tT1\tx\tthe proxy said 401",
     'one: WILL RUN — failed last time: one/a — "the proxy said 401"'),
    ("one/a\tstarted\t2.56.0\tT9\tx\t",
     "one: WILL RUN — the last run was interrupted here: one/a was started at T9 and never finished"),
    ("one/a\tgate\t2.56.0\tT1\tx\tfill in the catalog",
     'one: WILL RUN — waiting on you: one/a — "fill in the catalog"'),
    ("one/a\tdone\t2.55.0\tT1\tx\t",
     "one: WILL RUN — version changed, 2.55.0 -> 2.56.0: one/a"),
])
def test_the_plan_sentence_for_each_reason(tmp_path, row, expected):
    r = _phase(tmp_path, [row, _done(tmp_path, "b", ""), _done(tmp_path, "c", "CC_PORT,CC_OTHER")])
    assert r["text"] == expected, r


def test_the_plan_text_counts_later_rows_with_the_same_reason(tmp_path):
    rows = [f"one/{s}\tstarted\t2.56.0\tT9\tx\t" for s in "abc"]
    r = _phase(tmp_path, rows)
    assert r["text"].endswith("(and 2 more rows started and never finished)"), r["text"]
