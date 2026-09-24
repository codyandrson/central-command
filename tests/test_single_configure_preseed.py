"""`.env` stays an ANSWER FILE in the preseed sense, and `configure` fails closed.

The 2026-09-23 design record's D6 borrows two rules from installers that got
this right:

* **debconf-preseed / Zarf's InteractiveVariable** — one schema drives both the
  interactive run and a PREFILLED answer file. So a `.env` filled in on a
  connected machine, carried across with the release zip, must make
  `./setup.sh configure` ask NOTHING: every row `keep`, not one `set`, and the
  file unchanged byte for byte.
* **rustup's fail-closed rule** — an installer that cannot ask does not guess.
  With no TTY (or `--non-interactive`) `configure` prompts for nothing: it lists
  every unanswered REQUIRED key as a `USERACTION` and exits 3.

**Since v2.45.1 no shipped row is REQUIRED** (D3 reworded on the Windows
testbed: the LiteLLM UI is the primary way the model catalog is filled in, so
the `CC_LLM_UPSTREAM_*` keys are an optional shortcut past the `llm` phase's
deliberate pause). That makes exit 3 unreachable from the shipped schema — so
the fail-closed rule is exercised against a schema with a required row ADDED,
which is what keeps the rule under test for the day a genuinely unanswerable
dependency arrives.

These run the real script in a temp COPY of the deploy tree, with `HOME` and
`XDG_STATE_HOME` pointed at the temp directory so the state dir lands there
too. `CC_STATE_DIR` being written into the temp `.env` on the first run is P1's
documented behaviour (`cc_state_dir` resolves and persists it), which is why the
byte-identical case prefills it.

To see the fail-closed test fail: make `configure` default a missing required
key instead of reporting it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# The optional upstream-LLM shortcut, in full. Not required any more, but a
# carried-in file that DOES declare it must still be left alone.
UPSTREAM = {
    "CC_LLM_UPSTREAM_BASE_URL": "https://llm.corp.example/v1",
    "CC_LLM_UPSTREAM_API_KEY": "none",
    "CC_LLM_UPSTREAM_MODEL_CC_DEFAULT": "chat-1",
    "CC_LLM_UPSTREAM_MODEL_GRAPHITI_LLM": "chat-1",
    "CC_LLM_UPSTREAM_MODEL_CC_EMBEDDING": "embed-1",
    "CC_LLM_UPSTREAM_MODEL_GPT_4_1_NANO": "chat-1",
    "CC_LLM_UPSTREAM_MODEL_CC_TTS": "tts-1",
    "CC_LLM_UPSTREAM_MODEL_CC_STT": "stt-1",
}
# What make-secrets.sh would generate. Prefilled in the byte-identical case:
# `configure` runs it (that is how `.env` is COMPLETE before `check`), and it
# only ever fills a blank.
GENERATED = {
    "CC_LLM_PROXY_ADMIN_KEY": "aaa1",
    "CC_LITELLM_SALT_KEY": "aaa2",
    "LITELLM_POSTGRES_PASSWORD": "aaa3",
    "CC_NEO4J_PASSWORD": "aaa4",
    "N8N_ENCRYPTION_KEY": "aaa5",
    "N8N_DB_PASSWORD": "aaa6",
}


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A temp copy of everything `configure` reads: the deploy tree and the
    answer file's template. Nothing else — it must not need the venv, node, or
    a database to ask a question."""
    repo = tmp_path / "repo"
    (repo).mkdir()
    shutil.copytree(ROOT / "deploy", repo / "deploy")
    shutil.copy2(ROOT / ".env.example", repo / ".env.example")
    (tmp_path / "home").mkdir()
    return repo


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return _run_setup(repo, "configure", *args)


def _run_setup(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    home = repo.parent / "home"
    env.update(HOME=str(home), XDG_STATE_HOME=str(home / "state"))
    # The session running pytest may itself have a Central Command .env sourced;
    # make-secrets.sh only fills a BLANK, and it reads the environment first, so
    # a leaked credential would make this temp install skip generating one.
    for stale in ("CC_STATE_DIR", "CC_ENABLE_SPEECH", "CC_LLM_UPSTREAM_BASE_URL",
                  *GENERATED):
        env.pop(stale, None)
    return subprocess.run(
        ["bash", "setup.sh", *args],
        cwd=repo / "deploy" / "single",
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300, env=env,
    )


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


def _require(repo: Path, key: str) -> None:
    """Add a REQUIRED row to the temp copy's schema — the fail-closed fixture."""
    schema = repo / "deploy" / "single" / "questions.tsv"
    row = "\t".join([key, "identity", f"a value {key} nothing can default",
                     "-", "y", "-", "-", "n"])
    schema.write_text(schema.read_text(encoding="utf-8").rstrip("\n") + "\n" + row + "\n",
                      encoding="utf-8")


def test_an_empty_answer_file_needs_no_answers_and_exits_0(tree: Path):
    """v2.45.1: nothing in the shipped schema is required, so a file with no
    answers at all is a complete one — the blanks all have documented meanings,
    and the LLM catalog is the LiteLLM UI's. `configure` writes the state dir,
    generates the credentials, and reports clean."""
    env_file = tree / ".env"
    env_file.write_text("", encoding="utf-8")

    r = _run(tree, "--non-interactive")

    assert r.returncode == 0, r.stdout + r.stderr
    assert "USERACTION" not in r.stdout, r.stdout
    assert "  set    " not in r.stderr, "it must not write an answer it never asked for"
    # CC_STATE_DIR (P1's one write) plus exactly the credentials make-secrets owns.
    keys = [ln.split("=")[0] for ln in env_file.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]
    assert keys[0] == "CC_STATE_DIR"
    assert set(keys) == {"CC_STATE_DIR", *GENERATED}, keys


def test_a_required_row_with_no_terminal_is_reported_and_exits_3(tree: Path):
    """rustup's rule, against a schema that HAS a required row. No shipped row
    is required today, so this is the guard for the next one that is."""
    (tree / ".env").write_text("", encoding="utf-8")
    _require(tree, "CC_SOMETHING_UNANSWERABLE")

    r = _run(tree, "--non-interactive")

    assert r.returncode == 3, r.stdout + r.stderr
    assert "USERACTION CC_SOMETHING_UNANSWERABLE:" in r.stdout, r.stdout
    assert "configure asked nothing" in r.stderr
    # A run that stopped for the operator must not have generated six
    # credentials on the way out (design record, P4 delta).
    body = tree.joinpath(".env").read_text(encoding="utf-8")
    assert "CC_LITELLM_SALT_KEY" not in body, body


def test_a_missing_answer_file_is_created_from_the_template(tree: Path):
    assert not (tree / ".env").exists()

    r = _run(tree, "--non-interactive")

    assert "PASS answer-file: created" in r.stdout, r.stdout
    assert (tree / ".env").exists(), "configure is the ONE command that creates .env"
    assert r.returncode == 0, r.stdout + r.stderr
    template_keys = {
        ln.split("=", 1)[0].lstrip("# ")
        for ln in (tree / ".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in ln
    }
    assert "CC_POD_PREFIX" in template_keys  # sanity: the template really is the copy source
    assert "CC_POD_PREFIX=cc-" in (tree / ".env").read_text(encoding="utf-8")


@pytest.mark.parametrize("declare_upstream", [True, False],
                         ids=["upstream-declared", "catalog-in-the-litellm-ui"])
def test_a_prefilled_answer_file_asks_nothing_and_changes_nothing(
    tree: Path, declare_upstream: bool
):
    """The preseed property: filled on a connected machine, carried across.

    Both ways of answering the LLM question are carried files: the declared
    upstream, and the normal one where the catalog is entered in the LiteLLM UI
    and every `CC_LLM_UPSTREAM_*` row is deliberately blank.
    """
    env_file = tree / ".env"
    shutil.copy2(tree / ".env.example", env_file)
    state = tree.parent / "home" / "state" / "carried"
    state.mkdir(parents=True)
    values = {**GENERATED, "CC_STATE_DIR": str(state)}
    if declare_upstream:
        values |= UPSTREAM
    _set(env_file, values)
    before = env_file.read_bytes()

    r = _run(tree, "--non-interactive")

    assert r.returncode == 0, r.stdout + r.stderr
    assert "  set    " not in r.stderr, f"nothing should be written:\n{r.stderr}"
    assert "USERACTION" not in r.stdout, r.stdout
    if declare_upstream:
        assert r.stderr.count("  keep   ") >= len(UPSTREAM), (
            "every row that applies should report `keep`"
        )
    assert env_file.read_bytes() == before, "a carried-in answer file is left byte-identical"


def test_it_never_prompts_without_a_terminal_even_without_the_flag(tree: Path):
    """stdin is /dev/null here, which is the CI-runner and the agent-session
    case. Fail-closed applies to both, not only to the explicit flag."""
    shutil.copy2(tree / ".env.example", tree / ".env")
    _require(tree, "CC_SOMETHING_UNANSWERABLE")

    r = _run(tree)

    assert r.returncode == 3, r.stdout + r.stderr
    assert "stdin is not a terminal" in r.stderr, r.stderr
    assert "USERACTION CC_SOMETHING_UNANSWERABLE:" in r.stdout


def test_check_with_no_answer_file_stops_for_the_operator(tree: Path):
    """"Run configure" is the operator's MOVE, which is exit 3 in this protocol
    — not exit 1, which sends them to "fix the FAIL lines above" for a file that
    has simply never been created. `check_gate`'s exit-3 text already walks them
    to `configure`; exit 1 skipped past it (2026-09-24 Windows run)."""
    assert not (tree / ".env").exists()

    r = _run_setup(tree, "check")

    assert r.returncode == 3, r.stdout + r.stderr
    assert "USERACTION answer-file:" in r.stdout, r.stdout
    assert "./setup.sh configure" in r.stdout
    assert not (tree / ".env").exists(), "check never creates the answer file"
