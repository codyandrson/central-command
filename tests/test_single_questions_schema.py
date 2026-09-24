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
import re
from pathlib import Path

import pytest

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
    setup = (SINGLE / "setup.sh").read_text(encoding="utf-8")
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


def test_the_api_key_is_the_only_secret():
    secrets = {r["key"] for r in ROWS if r["secret"] == "y"}
    assert secrets == {"CC_LLM_UPSTREAM_API_KEY"}, (
        "a secret answer is read with no echo and printed as "
        f"`(secret, not printed)` in the diff — that set is: {sorted(secrets)}"
    )


def test_check_and_configure_both_read_the_schema():
    """The whole point of a data schema is that neither command owns it. If one
    of these two stops reading `questions.tsv`, the other's list is a copy."""
    setup = (SINGLE / "setup.sh").read_text(encoding="utf-8")
    assert "check_schema_answers" in setup and "cmd_configure" in setup
    body = setup[setup.index("check_schema_answers() {"):]
    body = body[: body.index("\n}\n")]
    assert "q_rows " in body and "q_when_holds" in body, (
        "check's answers section must WALK the schema, not carry its own "
        "required-key list (that list is what v2.45.0 replaced)"
    )
