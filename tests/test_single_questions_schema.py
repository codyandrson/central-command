"""`deploy/single/questions.tsv` is the ONE question schema, so it has to be real.

The 2026-09-23 design record's D6: declare each question ONCE — key, prompt,
default, required, validator, `when` guard, secret — and let TWO commands read
it, `./setup.sh configure` to ASK and `./setup.sh check` to VALIDATE. That is
debconf-preseed's and Zarf's InteractiveVariable pattern, and it only pays off
while the row and its consumers stay in step. The ways they can silently fall
out of step are exactly what this walk checks:

* a row whose key has no line in `.env.example` — then `configure` writes a key
  the operator's map does not document, which is how the four-answer-files mess
  started;
* a row naming a validator that does not exist — `check` would call a missing
  function, bash would report "command not found" and the reason line would be
  empty;
* a `when` guard the tiny expression language cannot parse — `q_when_holds`
  reads an unparseable clause as "does not hold", so the question would silently
  never be asked;
* an upstream-model row that does not correspond to a LiteLLM alias in
  `models.json` (or an alias with no row) — a required answer nothing consumes,
  or an alias nobody is ever asked about.

To see it fail: add a row for `CC_NOPE` and re-run.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# The bash that can run this repo's shell scripts. On Windows a bare "bash" is
# System32's WSL launcher (its error reads "The RPC call contains a handle
# that differs from the declared handle type") — 26 tests failed that way on
# the 2026-09-25 testbed run, all of them in files that shelled out with the
# bare name. `update._bash()` resolves Git Bash from git's own install.
from central_command.api.update import _bash as _resolve_bash  # noqa: E402
from tests.installer_source import drives_installer, installer_source, run_driver, write_lf  # noqa: E402
BASH = _resolve_bash() or "bash"


ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT / "deploy" / "single"
QUESTIONS = SINGLE / "questions.tsv"
QUESTIONS_LIB = SINGLE / "questions-lib.sh"
ENV_EXAMPLE = ROOT / ".env.example"
MODELS_JSON = SINGLE / "models.json"

COLUMNS = ("key", "group", "prompt", "default", "required", "validator", "when", "secret")
# The ask order. `advanced` is last because it is the only group `configure`
# skips unless asked for it with --all.
GROUP_ORDER = ["identity", "features", "network", "mirrors", "llm", "paths", "advanced"]


def _rows() -> list[dict[str, str]]:
    rows = []
    for n, line in enumerate(QUESTIONS.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        assert len(fields) == len(COLUMNS), (
            f"questions.tsv:{n}: {len(fields)} tab-separated fields, expected "
            f"{len(COLUMNS)} ({', '.join(COLUMNS)}). `-` is how an empty field "
            f"is written — an actually empty field would collapse under "
            f"IFS-splitting: {line!r}"
        )
        row = dict(zip(COLUMNS, fields))
        row["_line"] = str(n)
        rows.append(row)
    return rows


ROWS = _rows()


def test_the_schema_has_rows_and_no_duplicate_keys():
    keys = [r["key"] for r in ROWS]
    assert keys, "questions.tsv has no rows"
    dupes = {k for k in keys if keys.count(k) > 1}
    assert not dupes, f"a key asked twice would be asked twice: {sorted(dupes)}"


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["key"])
def test_every_key_is_declared_in_env_example(row):
    """`configure`'s only write target is `.env`, and `.env.example` is the map
    of that file — a key it does not document is a key the operator cannot find
    again. Commented (`#CC_X=`) counts: that is how an optional seam is shipped.
    """
    declared = set(
        re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), flags=re.M)
    )
    assert row["key"] in declared, (
        f"questions.tsv:{row['_line']} asks for {row['key']}, which .env.example "
        "does not declare — add a line (commented is fine) in the section it belongs to"
    )


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["key"])
def test_every_validator_named_exists_in_questions_lib(row):
    if row["validator"] == "-":
        return
    lib = QUESTIONS_LIB.read_text(encoding="utf-8")
    assert re.search(rf"^{re.escape(row['validator'])}\(\)", lib, flags=re.M), (
        f"questions.tsv:{row['_line']} names validator {row['validator']}(), which "
        f"{QUESTIONS_LIB.relative_to(ROOT)} does not define"
    )
    assert row["validator"].startswith("v_"), (
        "a validator is a v_* function — the prefix is what keeps it out of the "
        "schema reader's namespace"
    )


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["key"])
def test_every_when_guard_parses(row):
    """`KEY=value` / `KEY!=value`, comma-separated ANDs. NOT bash: a schema row
    is data, and `eval`ing a data file would make every row a code path."""
    if row["when"] == "-":
        return
    known = {r["key"] for r in ROWS}
    for clause in row["when"].split(","):
        m = re.fullmatch(r"([A-Z][A-Z0-9_]+)(!?=)(.*)", clause)
        assert m, (
            f"questions.tsv:{row['_line']}: `when` clause {clause!r} is not "
            "KEY=value or KEY!=value — q_when_holds reads an unparseable clause "
            "as 'does not hold', so the question would never be asked"
        )
        assert m.group(1) in known, (
            f"questions.tsv:{row['_line']}: `when` tests {m.group(1)}, which is not "
            "a key in this schema — the guard is evaluated against the ANSWERS, so "
            "it can only test a question that is asked before it"
        )


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["key"])
def test_every_row_is_well_formed(row):
    assert row["group"] in GROUP_ORDER, (
        f"questions.tsv:{row['_line']}: unknown group {row['group']!r} "
        f"(known: {GROUP_ORDER}) — q_group_blurb would print nothing for it"
    )
    assert row["required"] in ("y", "n"), f"required must be y or n: {row}"
    assert row["secret"] in ("y", "n"), f"secret must be y or n: {row}"
    assert len(row["prompt"]) > 15 and row["prompt"] != "-", (
        "the prompt IS the documentation an operator sees — one plain-English "
        "sentence saying what the seam means"
    )


def test_the_groups_are_in_ask_order_and_advanced_is_last():
    seen = []
    for row in ROWS:
        if row["group"] not in seen:
            seen.append(row["group"])
    assert seen == [g for g in GROUP_ORDER if g in seen], (
        f"the rows are in ASK order, so the groups appear in order too: {seen}"
    )
    assert seen[-1] == "advanced", "the ports group is asked last (and only with --all)"


def test_no_question_is_required():
    """"Required" means setup cannot run without it — and nothing qualifies.

    Every row has a working default or a documented blank meaning:
    `CC_OPERATOR_NAME` is asked by the cockpit on first run (v2.37.0), a blank
    mirror means the public source, and as of v2.45.1 a blank upstream LLM means
    the catalog is entered in the LiteLLM UI at the `llm` phase's deliberate
    exit-3 pause — the primary method, and the one the k3s profile uses, because
    LiteLLM expresses provider nuance a flat answer file cannot.

    The column and `configure`'s fail-closed path stay: the next genuinely
    unanswerable dependency is a `y` here rather than new code.
    """
    required = {r["key"] for r in ROWS if r["required"] == "y"}
    assert required == set(), (
        "no question may be REQUIRED unless setup truly cannot run without it. "
        "If you are adding one, say why in the schema header and update "
        f"deploy/AIRGAP.md and the /setup skill with it: {sorted(required)}"
    )


def test_the_upstream_llm_rows_are_optional_and_say_so():
    """The eight upstream keys are the shortcut past the UI pause, not the path.

    A prompt that does not SAY the row may be left blank is how an operator ends
    up inventing a base URL for a catalog they were going to fill in the UI.
    """
    rows = [r for r in ROWS if r["key"].startswith("CC_LLM_UPSTREAM")]
    assert len(rows) == 8, [r["key"] for r in rows]
    for row in rows:
        assert row["required"] == "n", f"{row['key']} must be optional"
        assert "LiteLLM UI" in row["prompt"], (
            f"{row['key']}'s prompt must name the alternative it is optional "
            f"against: {row['prompt']!r}"
        )


def test_check_reports_a_blank_catalog_as_a_pass_not_a_useraction():
    """The `all` gate refuses to continue past a USERACTION, so a blank catalog
    reported as one would make the NORMAL install impossible to run in one
    command. It is a PASS naming the pause instead (v2.45.1)."""
    setup = installer_source()
    body = setup[setup.index("check_llm() {"):]
    body = body[: body.index("\n}\n")]
    blank = body[: body.index("CC_LLM_UPSTREAM_API_KEY:-")]
    assert 'pass "llm" "catalog will be entered in the LiteLLM UI' in blank, (
        "check's llm section must PASS with the pause named when "
        "CC_LLM_UPSTREAM_BASE_URL is blank"
    )
    assert "useraction" not in blank, (
        "a blank catalog is the normal case and must never be a USERACTION — "
        "the full run's gate would refuse to continue"
    )


def test_the_model_rows_cover_exactly_the_litellm_aliases():
    """One `CC_LLM_UPSTREAM_MODEL_<ALIAS>` row per alias `models.json` declares,
    with the same derivation `cc_alias_env_key` and `register-models.py` use."""
    aliases = json.loads(MODELS_JSON.read_text(encoding="utf-8"))["registration_only"].keys()
    expected = {"CC_LLM_UPSTREAM_MODEL_" + re.sub(r"[^A-Z0-9]", "_", a.upper()) for a in aliases}
    actual = {r["key"] for r in ROWS if r["key"].startswith("CC_LLM_UPSTREAM_MODEL_")}
    assert actual == expected, (
        "every LiteLLM alias needs its own question and no question may name an "
        f"alias that does not exist.\n  missing: {sorted(expected - actual)}\n"
        f"  extra:   {sorted(actual - expected)}"
    )


def test_the_speech_rows_are_gated_on_the_speech_flag():
    """`cc_required_aliases` gates the speech pair on `CC_ENABLE_SPEECH`; the
    schema has to gate the matching questions the same way, or `check` would
    demand an upstream for an engine that is not installed."""
    for key in ("CC_LLM_UPSTREAM_MODEL_CC_TTS", "CC_LLM_UPSTREAM_MODEL_CC_STT",
                "CC_HF_ENDPOINT"):
        row = next(r for r in ROWS if r["key"] == key)
        assert row["when"] == "CC_ENABLE_SPEECH=1", (
            f"{key} only applies with the bundled speech engine: {row['when']!r}"
        )


def test_the_secret_rows_are_exactly_the_credentials():
    """Every row whose answer is a CREDENTIAL is marked secret, and nothing
    else is. `secret=y` costs something real — the answer is read with no echo,
    so the operator cannot see their own typo, and the diff prints
    `(secret, not printed)` instead of the value — so it is spent on values
    that must never reach a terminal scrollback or a log, and on nothing else.
    Adding a credential question means adding it here in the same commit."""
    secrets = {r["key"] for r in ROWS if r["secret"] == "y"}
    assert secrets == {"CC_LLM_UPSTREAM_API_KEY", "CC_EXCHANGE_PASSWORD",
                       "CC_JIRA_API_TOKEN", "CC_CONFLUENCE_API_TOKEN"}, (
        "a secret answer is read with no echo and printed as "
        f"`(secret, not printed)` in the diff — that set is: {sorted(secrets)}"
    )


def test_check_and_configure_both_read_the_schema():
    """The whole point of a data schema is that neither command owns it. If one
    of these two stops reading `questions.tsv`, the other's list is a copy."""
    setup = installer_source()
    assert "check_schema_answers" in setup and "cmd_configure" in setup
    body = setup[setup.index("check_schema_answers() {"):]
    body = body[: body.index("\n}\n")]
    assert "q_rows " in body and "q_when_holds" in body, (
        "check's answers section must WALK the schema, not carry its own "
        "required-key list (that list is what v2.45.0 replaced)"
    )


def test_the_atlassian_rows_are_asked_and_gated():
    """v2.53.0: the Jira/Confluence credentials are ASKED for at setup.

    Nothing in either installer used to collect or test them, and the reference
    deployment's operator found out mid-tour that the team had no Jira — with a
    Confluence token nobody had exercised. So the keys the clients actually read
    are rows, the two credentials are secrets (pinned above), and everything
    after the base URL is gated on it: a deployment with no Jira is asked one
    question, not six.
    """
    rows = {r["key"]: r for r in ROWS}
    for key in ("CC_JIRA_BASE_URL", "CC_JIRA_EMAIL", "CC_JIRA_API_TOKEN",
                "CC_CONFLUENCE_BASE_URL", "CC_CONFLUENCE_EMAIL",
                "CC_CONFLUENCE_API_TOKEN", "CC_JIRA_API_FLAVOR",
                "CC_CONFLUENCE_API_FLAVOR"):
        assert key in rows, f"{key} is not asked for — it was the v2.53.0 whole point"

    assert rows["CC_JIRA_BASE_URL"]["when"] == "-", "the first question is unconditional"
    # Blank means NO Jira, and the prompt has to say what that costs.
    assert "no Jira" in rows["CC_JIRA_BASE_URL"]["prompt"]
    for key in ("CC_JIRA_EMAIL", "CC_JIRA_API_TOKEN", "CC_CONFLUENCE_BASE_URL",
                "CC_JIRA_API_FLAVOR"):
        assert rows[key]["when"] == "CC_JIRA_BASE_URL!=", rows[key]
    for key in ("CC_CONFLUENCE_EMAIL", "CC_CONFLUENCE_API_TOKEN",
                "CC_CONFLUENCE_API_FLAVOR"):
        assert rows[key]["when"] == "CC_CONFLUENCE_BASE_URL!=", rows[key]
    # Blank CC_CONFLUENCE_BASE_URL is OFF in the code (confluence.configured()),
    # never "the same site as Jira" — the prompt may not promise otherwise.
    assert "no Confluence" in rows["CC_CONFLUENCE_BASE_URL"]["prompt"]
    # The footgun the stale token came from: a Cloud API token is ACCOUNT-scoped.
    assert "ACCOUNT-scoped" in rows["CC_CONFLUENCE_API_TOKEN"]["prompt"]
    for key in ("CC_JIRA_API_FLAVOR", "CC_CONFLUENCE_API_FLAVOR"):
        assert rows[key]["default"] == "cloud"
        assert rows[key]["validator"] == "v_atlassian_flavor"
        assert rows[key]["group"] == "advanced"


@pytest.mark.parametrize("value,rc", [("cloud", 0), ("server", 0), ("Cloud", 1),
                                      ("datacenter", 1), ("", 1)])
def test_v_atlassian_flavor_accepts_only_the_two_flavors(value, rc):
    """It selects the REST PATHS, so a typo 404s every read instead of degrading.
    One line of reason on failure, nothing on success, and it writes nothing."""
    r = subprocess.run([BASH, "-c", f'. "{QUESTIONS_LIB}"; v_atlassian_flavor "{value}"'],
                       capture_output=True, text=True)
    assert r.returncode == rc, r.stdout + r.stderr
    assert len(r.stdout.splitlines()) == (0 if rc == 0 else 1), r.stdout


# ── F33: a PATH answer is stored in the spelling every consumer accepts ──────
# `/c/Users/me/ca.pem` is what Git Bash tab completion produces. Bash reads it,
# so it validated and was stored verbatim — and then curl.exe, podman.exe and the
# podman machine all rejected it. On MSYS/Cygwin the answer is rewritten with
# `cygpath -m` BEFORE it is validated or stored, so `.env` carries
# `C:/Users/me/ca.pem`, which bash, Python and both .exe accept.
#
# There is no cygpath on a Linux test host, so the test brings its own on PATH:
# a two-line stand-in doing exactly what `cygpath -m` does to these inputs. That
# is also what makes the second half meaningful — with cygpath present and the
# platform NOT MSYS, the answer must come back untouched.
CYGPATH_STUB = """#!/usr/bin/env bash
# Stand-in for `cygpath -m`: /c/X -> C:/X, and a /msysroot prefix -> $STUB_ROOT,
# which is how the validator can be shown resolving the REWRITTEN path.
[ "$1" = "-m" ] && shift
printf '%s' "$1" | sed -E "s|^/msysroot|${STUB_ROOT:-/msysroot}|; s|^/([a-zA-Z])/|\\U\\1:/|"
"""


def _bash(snippet: str, *, bindir: Path, ostype: str, stub_root: str = "") -> subprocess.CompletedProcess:
    # Every path in the spelling bash resolves (as_posix), and the stub
    # directory put FIRST from inside the script — after Git Bash's launcher
    # has prepended its own /usr/bin, whose real cygpath would otherwise win.
    # A PATH entry cannot carry `C:`'s colon, so on MSYS it goes in as
    # `cygpath -u` spells it (resolved BEFORE the stub shadows cygpath).
    b = bindir.as_posix()
    script = (
        f'__b="$(cygpath -u \'{b}\' 2>/dev/null || printf %s \'{b}\')"\n'
        'export PATH="$__b:$PATH"\n'
        f'OSTYPE={ostype}\n'
        f'export STUB_ROOT="{stub_root}"\n'
        f'. "{QUESTIONS_LIB.as_posix()}"\n'
        f'{snippet}\n'
    )
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, check=False)


# q__is_msys asks OSTYPE first and `uname -o` second, so a Linux box is
# simulated on Windows too: the stub uname answers what a Linux one does (on a
# Linux host it is what the real one says anyway).
UNAME_STUB = """#!/usr/bin/env bash
case "${1:-}" in -o) echo GNU/Linux ;; *) echo Linux ;; esac
"""


@pytest.fixture
def cygpath_bin(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    write_lf(bindir / "cygpath", CYGPATH_STUB, mode=0o755)
    write_lf(bindir / "uname", UNAME_STUB, mode=0o755)
    return bindir


def test_a_path_answer_is_rewritten_on_msys_and_untouched_elsewhere(cygpath_bin):
    r = _bash("q_norm_path_answer /c/Users/me/ca.pem", bindir=cygpath_bin, ostype="msys")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "C:/Users/me/ca.pem", r.stdout

    # Same cygpath on PATH, a Linux OSTYPE: a Linux box with cygpath installed
    # must not have its answers rewritten.
    r = _bash("q_norm_path_answer /c/Users/me/ca.pem", bindir=cygpath_bin,
              ostype="linux-gnu")
    assert r.stdout == "/c/Users/me/ca.pem", r.stdout

    # And a blank answer stays blank — blank is a valid answer for these keys.
    r = _bash('q_norm_path_answer ""', bindir=cygpath_bin, ostype="cygwin")
    assert r.stdout == "", r.stdout


def test_the_path_validators_validate_the_rewritten_path(cygpath_bin, tmp_path):
    """v_path_readable used to accept the MSYS spelling and store it."""
    real = tmp_path / "corp-ca.pem"
    real.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")

    # The stub maps /msysroot/<x> onto tmp_path/<x>, so this answer names a file
    # that exists only under its REWRITTEN spelling.
    r = _bash("v_path_readable /msysroot/corp-ca.pem", bindir=cygpath_bin,
              ostype="msys", stub_root=tmp_path.as_posix())
    assert r.returncode == 0, (
        "the validator must resolve the path cygpath -m produces, not the raw "
        f"answer: {r.stdout}{r.stderr}"
    )

    # A missing file still fails, and the REASON names the rewritten spelling —
    # the one the operator's other tools will use.
    r = _bash("v_path_readable /msysroot/nope.pem", bindir=cygpath_bin,
              ostype="msys", stub_root=tmp_path.as_posix())
    assert r.returncode == 1
    assert f"no such file: {tmp_path.as_posix()}/nope.pem" in r.stdout, r.stdout

    # The directory validator shares the normalisation.
    r = _bash("v_path_dir_or_creatable /msysroot/state", bindir=cygpath_bin,
              ostype="msys", stub_root=tmp_path.as_posix())
    assert r.returncode == 0, r.stdout + r.stderr


def test_configure_normalises_before_it_validates_or_stores():
    """The rewrite has to happen in q_ask, or an invalid spelling gets written."""
    setup = installer_source()
    start = setup.index("q_ask() {")
    body = setup[start:setup.index("\ncmd_configure()", start)]
    assert "q_norm_path_answer" in body, (
        "configure must normalise a path ANSWER before validating/storing it "
        "(F33) — otherwise .env keeps the MSYS spelling native tools reject"
    )
    assert body.index("q_norm_path_answer") < body.index('"$validator" "$reply"'), (
        "normalise BEFORE the validator runs"
    )



# ── a BACKSLASH path answer is refused, by every v_path* ────────────────────
# `.env` is SOURCED by setup.sh, and bash drops the backslashes of an unquoted
# `C:\Users\me\state`, while update.sh reads the text as written — the two
# disagreed about the state dir (2026-10-02 testbed run, second pass). The
# validators are the one definition of "a path answer", so the refusal lives
# there, on the answer AS WRITTEN (before any cygpath rewrite).


@pytest.mark.parametrize("validator", ["v_path", "v_path_readable", "v_path_dir_or_creatable"])
def test_every_path_validator_refuses_a_backslash_and_names_the_forward_spelling(validator, tmp_path):
    target = tmp_path / ("state" if validator == "v_path_dir_or_creatable" else "f.pem")
    if validator == "v_path_dir_or_creatable":
        target.mkdir()
    else:
        target.write_text("x\n", encoding="utf-8")
    good = target.as_posix()
    bad = good.replace("/", "\\")
    r = subprocess.run([BASH, "-c", f'. "{QUESTIONS_LIB.as_posix()}"; {validator} "$1"', "_", bad],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "backslashes" in r.stdout and f"write it with forward slashes: {good}" in r.stdout, r.stdout
    r = subprocess.run([BASH, "-c", f'. "{QUESTIONS_LIB.as_posix()}"; {validator} "$1"', "_", good],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, (r.stdout, r.stderr)


@drives_installer
def test_check_fails_a_backslash_state_dir_naming_the_key_and_the_spelling(tmp_path):
    """check's answers section, the real check_schema_answers over the real
    schema: a hand-edited CC_STATE_DIR with backslashes is a FAIL naming the
    key and the forward-slash spelling — and the same path written with
    forward slashes passes."""
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "deploy", repo / "deploy",
                    ignore=shutil.ignore_patterns(".env", ".env.*", "__pycache__", "NUL", "nul"))
    for f in (".env.example", "VERSION"):
        shutil.copy2(ROOT / f, repo / f)
    setup = repo / "deploy" / "single" / "setup.sh"
    text = setup.read_text(encoding="utf-8")
    tail = 'main "$@"'
    assert text.rstrip().endswith(tail)
    hook = 'if [[ -n "${__CALL:-}" ]]; then "$@"; exit $?; fi\n'
    write_lf(setup, text.rstrip()[: -len(tail)] + hook + tail + "\n")
    state = tmp_path / "state"
    state.mkdir()
    (tmp_path / "home").mkdir()
    example = ENV_EXAMPLE.read_text(encoding="utf-8")

    def answers(state_dir: str) -> str:
        write_lf(repo / ".env", example + f"\nCC_STATE_DIR={state_dir}\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith("CC_")}
        # The hook runs the one function, so init_state — which would act on
        # the answer (mkdir) before check judged it — never runs.
        env.update(__CALL="1", HOME=str(tmp_path / "home"),
                   XDG_STATE_HOME=str(tmp_path / "home" / "state"))
        r = run_driver([BASH, "setup.sh", "check_schema_answers"],
                       cwd=repo / "deploy" / "single", env=env)
        return r.stdout + r.stderr

    bad = state.as_posix().replace("/", "\\")
    out = answers(bad)
    fails = [l for l in out.splitlines() if l.startswith("FAIL answers-CC_STATE_DIR:")]
    assert len(fails) == 1, out
    assert "backslashes" in fails[0] and f"forward slashes: {state.as_posix()}" in fails[0], fails[0]

    out = answers(state.as_posix())
    assert not [l for l in out.splitlines() if l.startswith("FAIL answers-CC_STATE_DIR:")], out
    assert "PASS answers-schema:" in out, out
