"""The driver's hot path forks a tenth as often — and answers exactly as before.

P5, the first laptop acceptance run of v2.58.0 (finding F10): on Windows every
`./setup.sh` spent ~21 s before its first line, the plan ~60 s, and the suite's
real-driver tests ~2,900 of 3,817 s. Process creation is the cost on MSYS (a
fork/exec is tens of milliseconds), and nearly all of it was bash forking for
things bash can do itself: a `printf | cut` per manifest column, a
`$(cc_get_kv …)` per fingerprinted key, a `sha256sum | cut` per row, a `date`
per log line. Those helpers were rewritten to fork less; this file holds each
one against the answer the old code gave.

Two kinds of test:

* **parity** — the rewritten helper beside a REFERENCE that is the old code,
  verbatim, on inputs chosen to be awkward (quoted values, `=` in a value,
  CRLF, a key defined twice, a key that is a prefix of another, empty fields,
  duplicate ledger rows). The fingerprint parity matters most: digests are
  recorded in the ledgers of installs that already exist, and a digest that
  moved would re-run every row of every install once after this update;
* **no exec** — the hot paths run with `PATH` pointing at an empty directory,
  where any external command fails. A helper that still answers correctly
  there spawned no process. (Subshells are not caught this way; the
  measurement in the commit message is the evidence for those.)
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess
import textwrap

import pytest

from tests.installer_source import SETUP, functions_in

ROOT = pathlib.Path(__file__).resolve().parents[1]
SINGLE = ROOT / "deploy" / "single"


def bash() -> str:
    from central_command.api.update import _bash

    resolved = _bash()
    if not resolved:
        pytest.skip("no usable bash on this host")
    return resolved


def sh(script: str, cwd: pathlib.Path = SINGLE) -> subprocess.CompletedProcess:
    prelude = f". '{(ROOT / 'deploy' / 'env-lib.sh').as_posix()}'\n. '{(SINGLE / 'ledger-lib.sh').as_posix()}'\n"
    return subprocess.run(
        [bash(), "-c", prelude + textwrap.dedent(script)],
        cwd=cwd, capture_output=True, text=True,
    )


def ok(script: str, cwd: pathlib.Path = SINGLE) -> str:
    r = sh(script, cwd)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


# The old code, verbatim (v2.58.0), as the reference the rewrites are held to.
OLD_REFERENCE = r"""
old_step_cut() { printf '%s' "$1" | cut -d$'\t' -f"$2"; }
old_sha256_stdin() { local h; h="$(sha256sum 2>/dev/null | cut -d' ' -f1)"; printf '%s' "${h:-nohash}"; }
old_fingerprint() {
  local f="$1" csv="$2" payload="" key root
  [[ -z "$csv" || "$csv" == "-" ]] && { printf 'none'; return 0; }
  local oldifs="$IFS"
  IFS=','
  local keys=($csv)
  IFS="$oldifs"
  for key in ${keys[@]+"${keys[@]}"}; do
    [[ -n "$key" ]] || continue
    if [[ "$key" == @* ]]; then
      root="${LEDGER_TREE_ROOT:-}"
      [[ -n "$root" ]] || root="$(cd "$(dirname "$f")" 2>/dev/null && pwd)"
      payload="${payload}${key}=$(cc_tree_hash "$root" "${key#@}")"$'\n'
      continue
    fi
    payload="${payload}${key}=$(cc_get_kv "$f" "$key")"$'\n'
  done
  printf '%s' "$payload" | old_sha256_stdin
}
old_ledger_field() {
  local line out=""
  while IFS= read -r line; do
    [[ "$line" == "$2"$'\t'* ]] || continue
    out="$(printf '%s' "$line" | cut -d$'\t' -f"$3")"
  done < <(cc_ledger_read "$1")
  printf '%s' "$out"
}
old_install_id() {
  local norm="$1" h=""
  h="$(printf '%s' "$norm" | sha256sum 2>/dev/null | cut -c1-8)"
  printf '%s-%s' "$(basename "$norm")" "${h:-nohash}"
}
"""

# An answer file chosen to be awkward for a cache: every shape cc_get_kv reads.
TRICKY_ENV = (
    "# a comment: CC_A=not-this\n"
    "\n"
    "CC_A=first\n"
    "CC_AB=prefix-of-nothing\n"
    "CC_A=second\n"                       # defined twice: the last one wins
    'CC_QUOTED="Jane Doe"\n'
    "CC_SINGLE='single quoted'\n"
    "CC_EQ=a=b=c\n"
    "CC_CRLF=windows\r\n"
    "CC_EMPTY=\n"
    "  CC_INDENTED=not-a-definition\n"
    "export CC_EXPORTED=not-a-definition\n"
    "CC_SPACES=  padded  \n"
    "CC.DOTTED=dotted-key\n"
    "=no-key\n"
    "CC_LAST=no-trailing-newline"
)
TRICKY_KEYS = ["CC_A", "CC_AB", "CC_QUOTED", "CC_SINGLE", "CC_EQ", "CC_CRLF", "CC_EMPTY",
               "CC_INDENTED", "CC_EXPORTED", "CC_SPACES", "CC.DOTTED", "CC_LAST",
               "CC_MISSING", "CC", "A"]


# ── the answer-file cache ───────────────────────────────────────────────────


def test_the_cached_reader_answers_every_key_exactly_as_cc_get_kv(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(TRICKY_ENV.encode())
    keys = " ".join(f"'{k}'" for k in TRICKY_KEYS)
    out = ok(f"""
      for k in {keys}; do
        a="$(cc_get_kv "{env.as_posix()}" "$k")"
        cc_get_kv_cached "{env.as_posix()}" "$k"
        printf '%s|%s|%s\\n' "$k" "$a" "$KV_OUT"
      done
    """)
    rows = [l.split("|") for l in out.splitlines()]
    assert len(rows) == len(TRICKY_KEYS)
    for key, uncached, cached in rows:
        assert cached == uncached, (key, uncached, cached)
    got = {r[0]: r[1] for r in rows}
    # ...and cc_get_kv's own answers are what this file means.
    assert got["CC_A"] == "second"
    assert got["CC_QUOTED"] == '"Jane Doe"'
    assert got["CC_EQ"] == "a=b=c"
    assert got["CC_CRLF"] == "windows"
    assert got["CC_INDENTED"] == "" and got["CC_EXPORTED"] == ""
    assert got["CC.DOTTED"] == "dotted-key"
    assert got["CC_LAST"] == "no-trailing-newline"


def test_the_cache_follows_every_writer_of_the_file(tmp_path):
    """Self-validating: cc_set_kv, a script appending behind the driver's back,
    and the file disappearing are all seen on the next read."""
    env = tmp_path / ".env"
    env.write_text("CC_X=1\n", encoding="utf-8")
    e = env.as_posix()
    out = ok(f"""
      cc_get_kv_cached "{e}" CC_X; printf '%s ' "$KV_OUT"
      cc_set_kv "{e}" CC_X 2
      cc_get_kv_cached "{e}" CC_X; printf '%s ' "$KV_OUT"
      printf 'CC_X=3\\n' >>"{e}"
      cc_get_kv_cached "{e}" CC_X; printf '%s ' "$KV_OUT"
      rm -f "{e}"
      cc_get_kv_cached "{e}" CC_X; printf '[%s]' "$KV_OUT"
    """)
    assert out == "1 2 3 []"


# ── the manifest split ──────────────────────────────────────────────────────


@pytest.mark.parametrize("row", [
    "a\tb\tc\td\te\tf\tg\th",
    "a\t\tc\t-\te\t\t\th",          # empty fields stay empty, as with cut
    "\tb\tc\td\te\tf\tg\th",        # an empty FIRST field
    "a\tb\tc\td\te\tf\tg\t",        # an empty LAST field
    "a doc with spaces\tb\tc",      # fewer columns than asked for
])
def test_a_manifest_column_is_what_cut_prints(row):
    q = row.replace("\t", "\\t")
    out = ok(f"""
      {OLD_REFERENCE}
      row=$'{q}'
      for n in 1 2 3 4 5 6 7 8 9; do
        printf '%s|%s\\n' "$(old_step_cut "$row" "$n")" "$(cc__step_cut "$row" "$n")"
      done
    """)
    for line in out.splitlines():
        old, new = line.split("|")
        assert new == old, (row, out)


def test_the_shipped_manifest_loads_without_running_a_single_program():
    """cc_steps_load was ~350 `cut` processes per command. With PATH empty,
    any external command fails — so a load that still succeeds, with the same
    rows, ran none."""
    out = ok("""
      PATH=/nonexistent-cc-no-programs
      cc_steps_load ./steps.tsv || exit 1
      cc_steps_all | { n=0; while read -r _; do n=$((n+1)); done; printf '%s ' "$n"; }
      cc_step_field boot boot-api kind; printf ' '
      cc_steps_requires boot boot-api
    """)
    assert re.match(r"^\d+ run app/", out), out


# ── the fingerprint ─────────────────────────────────────────────────────────


def _env_with_values(tmp_path: pathlib.Path) -> pathlib.Path:
    env = tmp_path / ".env"
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    text += "\nCC_LITELLM_PORT=4111\nCC_API_PORT=8181\nCC_CA_BUNDLE=\"/a path/ca.pem\"\nCC_EMBED_DIM=1024\r\n"
    env.write_bytes(text.encode())
    return env


def test_every_shipped_row_fingerprints_exactly_as_before(tmp_path):
    """The values recorded in installed ledgers must not move. Every row of
    the shipped manifest, against the old cc_fingerprint verbatim: one at a
    time, after a batch prime, and through the public printing form."""
    env = _env_with_values(tmp_path)
    out = ok(f"""
      {OLD_REFERENCE}
      export LEDGER_TREE_ROOT='{ROOT.as_posix()}'
      cc_steps_load ./steps.tsv || exit 1
      e="{env.as_posix()}"
      declare -A single=()
      for k in "${{STEPS_ORDER[@]}}"; do
        cc__step_split "${{k%%/*}}" "${{k#*/}}"
        cc__fingerprint_into "$e" "$ROWDEF_READS"; single["$k"]="$FP_OUT"
      done
      cc_ledger_cache_drop
      cc_fingerprint_prime_rows "$e"
      for k in "${{STEPS_ORDER[@]}}"; do
        cc__step_split "${{k%%/*}}" "${{k#*/}}"
        cc__fingerprint_into "$e" "$ROWDEF_READS"
        printf '%s|%s|%s|%s|%s\\n' "$k" "$(old_fingerprint "$e" "$ROWDEF_READS")" \\
          "${{single[$k]}}" "$FP_OUT" "$(cc_fingerprint "$e" "$ROWDEF_READS")"
      done
    """)
    rows = [l.split("|") for l in out.splitlines()]
    assert len(rows) > 50, out
    for step, old, single, primed, public in rows:
        assert single == old and primed == old and public == old, (step, old, single, primed, public)
    # The tree inputs are among them, so the tree path is covered too.
    assert any(r[0] == "boot/skills-imported" for r in rows)


def test_a_tree_input_with_no_tree_root_fingerprints_as_before(tmp_path):
    """With LEDGER_TREE_ROOT unset the tree is the directory the answer file
    sits in — resolved now without `dirname`."""
    (tmp_path / "skills" / "one").mkdir(parents=True)
    (tmp_path / "skills" / "one" / "SKILL.md").write_bytes(b"line one\r\nline two")
    env = tmp_path / ".env"
    env.write_text("CC_X=1\n", encoding="utf-8")
    out = ok(f"""
      {OLD_REFERENCE}
      unset LEDGER_TREE_ROOT
      printf '%s|' "$(old_fingerprint "{env.as_posix()}" CC_X,@skills,@absent)"
      cc_fingerprint "{env.as_posix()}" CC_X,@skills,@absent
    """)
    old, new = out.split("|")
    assert new == old and re.fullmatch(r"[0-9a-f]{64}", new)


def test_a_cached_fingerprint_follows_an_env_edit(tmp_path):
    env = tmp_path / ".env"
    env.write_text("CC_X=1\n", encoding="utf-8")
    e = env.as_posix()
    out = ok(f"""
      {OLD_REFERENCE}
      cc_fingerprint_prime "{e}" CC_X CC_X,CC_Y
      cc__fingerprint_into "{e}" CC_X; a="$FP_OUT"
      cc_set_kv "{e}" CC_X 2
      cc__fingerprint_into "{e}" CC_X; b="$FP_OUT"
      printf '%s %s %s\\n' "$a" "$b" "$(old_fingerprint "{e}" CC_X)"
    """)
    a, b, old_now = out.split()
    assert a != b and b == old_now
    assert a == hashlib.sha256(b"CC_X=1\n").hexdigest()


def test_a_decision_on_a_primed_phase_runs_no_program(tmp_path):
    """The plan's work per row — the ledger, the fingerprint, the verdict —
    once the batch has hashed, costs no process at all."""
    env = _env_with_values(tmp_path)
    led = tmp_path / "ledger.tsv"
    out = ok(f"""
      export LEDGER_TREE_ROOT='{ROOT.as_posix()}'
      cc_steps_load ./steps.tsv || exit 1
      e="{env.as_posix()}"
      rows=()
      for s in $(cc_steps_for_phase llm); do
        cc__step_split llm "$s"
        rows+=("llm/$s" done 9.9.9 2026-10-01T00:00:00Z "$(cc_fingerprint "$e" "$ROWDEF_READS")" "")
      done
      cc_ledger_write_batch "{led.as_posix()}" "${{rows[@]}}"
      cc_fingerprint_prime_rows "$e" llm
      PATH=/nonexistent-cc-no-programs
      cc_phase_decide "{led.as_posix()}" llm 9.9.9 "$e"
      printf '%s %s\\n' "$PHASE_VERDICT" "$PHASE_CODE"
      cc__phase_plan_text_into llm 9.9.9; printf '%s\\n' "$PLAN_TEXT"
      cc__ledger_blocked_into "{led.as_posix()}" stack && printf 'blocked %s\\n' "$LEDGER_BLOCKED"
    """)
    # Every row is done and current, so the first probe decides — and the
    # probes are setup.sh's, not defined here: `drift`, never `inputs`.
    assert out.splitlines()[0] == "run drift", out
    assert "llm: WILL RUN — effect absent:" in out


# ── the probe memo ──────────────────────────────────────────────────────────


def test_a_probe_is_asked_once_per_run_until_something_could_have_changed(tmp_path):
    env = tmp_path / ".env"
    env.write_text("CC_X=1\n", encoding="utf-8")
    count = tmp_path / "count"
    out = ok(f"""
      p_counted() {{ printf x >>"{count.as_posix()}"; return 0; }}
      e="{env.as_posix()}"
      cc__kv_cache_sync "$e"
      cc_probe_memo p_counted "$e"; cc_probe_memo p_counted "$e"
      printf '%s ' "$(cat "{count.as_posix()}")"
      cc_ledger_cache_drop                       # a phase ran
      cc_probe_memo p_counted "$e"
      printf '%s ' "$(cat "{count.as_posix()}")"
      printf 'CC_X=2\\n' >"$e"; cc__kv_cache_sync "$e"   # .env moved (the caller syncs)
      cc_probe_memo p_counted "$e"; cc_probe_memo p_counted "$e"
      printf '%s' "$(cat "{count.as_posix()}")"
    """)
    assert out == "x xx xxx"


def test_a_false_probe_stays_false_in_the_memo(tmp_path):
    env = tmp_path / ".env"
    env.write_text("CC_X=1\n", encoding="utf-8")
    out = ok(f"""
      p_no() {{ return 7; }}
      cc__kv_cache_sync "{env.as_posix()}"
      cc_probe_memo p_no "{env.as_posix()}"; a=$?
      cc_probe_memo p_no "{env.as_posix()}"; b=$?
      printf '%s %s' "$a" "$b"
    """)
    assert out == "1 1"


# ── the ledger readers ──────────────────────────────────────────────────────


def test_the_ledger_readers_answer_as_cut_did(tmp_path):
    led = tmp_path / "ledger.tsv"
    led.write_bytes(
        b"# header\tdone\n"
        b"app/install\tfailed\t1.0\t2026-10-01T00:00:00Z\tfp\tbroke\n"
        b"app/install\tdone\t1.0\t2026-10-02T00:00:00Z\tfp2\t\r\n"   # twice: the last wins
        b"app/venv\n"                                                   # no tab at all
        b"\n"
        b"stack/up-core\tgate\t1.0\t2026-10-01T00:00:00Z\tfp\twaiting on you"
    )
    lp = led.as_posix()
    out = ok(f"""
      {OLD_REFERENCE}
      for s in app/install app/venv stack/up-core absent/row; do
        for n in 1 2 3 4 5 6 7; do
          printf '%s|%s|%s\\n' "$s:$n" "$(old_ledger_field "{lp}" "$s" "$n")" "$(cc_ledger_field "{lp}" "$s" "$n")"
        done
      done
    """)
    for line in out.splitlines():
        what, old, new = line.split("|")
        assert new == old, (what, old, new)


def test_blocked_reads_the_last_row_and_names_the_first_require(tmp_path):
    led = tmp_path / "ledger.tsv"
    out = ok(f"""
      cc_steps_load ./steps.tsv || exit 1
      rows=()
      for k in "${{STEPS_ORDER[@]}}"; do
        [[ "${{k%%/*}}" == boot || "${{k%%/*}}" == demo ]] && continue
        rows+=("$k" done 1.0 2026-10-01T00:00:00Z none "")
      done
      cc_ledger_write_batch "{led.as_posix()}" "${{rows[@]}}"
      cc_ledger_blocked "{led.as_posix()}" boot && echo "BLOCKED" || echo "free"
      printf 'app/install\\tfailed\\t1.0\\tt\\tnone\\tbroke\\n' >>"{led.as_posix()}"
      cc_ledger_blocked "{led.as_posix()}" boot; echo
    """)
    lines = out.splitlines()
    assert lines[0] == "free", out
    assert lines[1].startswith("app/install failed"), out


# ── the small helpers ───────────────────────────────────────────────────────


def test_the_timestamp_is_dates_and_needs_no_date(tmp_path):
    out = ok("""
      a="$(date -u +%FT%TZ)"
      PATH_SAVED="$PATH"; PATH=/nonexistent-cc-no-programs
      cc_now_utc; n="$NOW_UTC"
      PATH="$PATH_SAVED"
      b="$(date -u +%FT%TZ)"
      printf '%s %s %s' "$a" "$n" "$b"
    """, cwd=tmp_path)
    a, n, b = out.split()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", n)
    assert a <= n <= b


@pytest.mark.parametrize("path", [
    "/home/jane/central-command", "/home/jane/central-command/", "C:/Users/Jane Doe/cc",
    "/", "relative/dir", "plain",
])
def test_the_install_id_is_what_the_pipeline_printed(path):
    out = ok(f"""
      {OLD_REFERENCE}
      printf '%s|%s' "$(old_install_id '{path}')" "$(cc_install_id '{path}')"
    """)
    old, new = out.split("|")
    assert new == old


def test_the_hasher_prints_what_sha256sum_and_cut_printed():
    out = ok(f"""
      {OLD_REFERENCE}
      printf '%s|%s' "$(printf 'abc\\n' | old_sha256_stdin)" "$(printf 'abc\\n' | cc_sha256_stdin)"
    """)
    old, new = out.split("|")
    assert new == old == hashlib.sha256(b"abc\n").hexdigest()


@pytest.mark.parametrize("text, expected", [
    ("version=2.58.0\n", "2.58.0"),
    ("# x\nversion= 2.58.1 \r\nversion=9\n", "2.58.1"),
    ("version=3.0.0", "3.0.0"),
    ("nothing here\n", "unknown"),
])
def test_installed_version_reads_version_as_the_sed_pipeline_did(tmp_path, text, expected):
    (tmp_path / "VERSION").write_bytes(text.encode())
    fns = functions_in(SETUP.read_text(encoding="utf-8"))
    body = "".join(f"{n}() {{{fns[n]}\n}}\n" for n in ("installed_version_load", "installed_version"))
    r = subprocess.run(
        [bash(), "-c", f"REPO_ROOT='{tmp_path.as_posix()}'\nINSTALLED_VERSION=''\n{body}"
         "old=\"$(sed -n 's/^version=//p' \"$REPO_ROOT/VERSION\" 2>/dev/null | head -1 | tr -d ' \\r')\"\n"
         "printf '%s|%s' \"${old:-unknown}\" \"$(installed_version)\""],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    old, new = r.stdout.split("|")
    assert new == old == expected


def test_the_utc_calendar_is_arithmetic_and_right_across_leap_days_and_centuries():
    """cc_now_utc formats EPOCHSECONDS with integer arithmetic — no TZ, no
    strftime — so it is held here against Python's own UTC calendar. Python's
    PURE calendar: the epoch plus a timedelta, formatted field by field —
    never fromtimestamp() or strftime(), which ask the platform's C library;
    fromtimestamp() raised OSError (EINVAL) on Windows for the far-future
    epochs below (2026-10-02 testbed run, second pass)."""
    import datetime

    epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)

    epochs = [0, 1, 59, 86399, 86400, 951_782_399, 951_782_400, 951_868_800,
              1_709_164_800, 1_740_787_199, 4_102_444_799, 4_107_542_400,
              1_759_460_000, 253_402_300_799]
    epochs += list(range(1_700_000_000, 1_700_000_000 + 400 * 86_400 * 3, 86_400 * 3 + 3_607))
    out = ok("for t in " + " ".join(map(str, epochs)) + "; do cc__utc_from_epoch $t; echo $NOW_UTC; done")
    got = out.split()
    want = [f"{d.year:04d}-{d.month:02d}-{d.day:02d}T{d.hour:02d}:{d.minute:02d}:{d.second:02d}Z"
            for d in (epoch + datetime.timedelta(seconds=t) for t in epochs)]
    assert got == want


def test_a_row_that_reads_nothing_still_reprobes_after_an_env_edit(tmp_path):
    """Its decision takes no fingerprint, so the memo's key would otherwise be
    about an older .env than the one its probe reads."""
    env = tmp_path / ".env"
    env.write_text("CC_X=1\n", encoding="utf-8")
    count = tmp_path / "count"
    out = ok(f"""
      p_counted() {{ printf x >>"{count.as_posix()}"; return 0; }}
      e="{env.as_posix()}"
      row=$'demo/x\\tdone\\t1.0\\t2026-10-01T00:00:00Z\\tnone\\t'
      cc_row_decide "$row" 1.0 "$e" "" p_counted; printf '%s ' "$ROW_VERDICT"
      cc_row_decide "$row" 1.0 "$e" "" p_counted
      printf '%s ' "$(cat "{count.as_posix()}")"
      printf 'CC_X=2\\n' >"$e"
      cc_row_decide "$row" 1.0 "$e" "" p_counted
      printf '%s' "$(cat "{count.as_posix()}")"
    """)
    assert out == "skip x xx"
