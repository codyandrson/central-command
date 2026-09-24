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

# Every key the schema marks required, with the bundled speech engine on (which
# is `.env.example`'s default, so it is what a fresh copy asks for).
REQUIRED = {
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
    env = dict(os.environ)
    home = repo.parent / "home"
    env.update(HOME=str(home), XDG_STATE_HOME=str(home / "state"))
    for stale in ("CC_STATE_DIR", "CC_ENABLE_SPEECH", "CC_LLM_UPSTREAM_BASE_URL"):
        env.pop(stale, None)
    return subprocess.run(
        ["bash", "setup.sh", "configure", *args],
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


def test_an_empty_answer_file_exits_3_listing_every_required_key(tree: Path):
    env_file = tree / ".env"
    env_file.write_text("", encoding="utf-8")

    r = _run(tree, "--non-interactive")

    assert r.returncode == 3, r.stdout + r.stderr
    for key in REQUIRED:
        assert f"USERACTION {key}:" in r.stdout, (
            f"{key} is required and unanswered, so it must be named: {r.stdout}"
        )
    assert "  set    " not in r.stderr, "it must not write an answer it never asked for"
    assert "configure asked nothing" in r.stderr
    # The one write P1 owns, and nothing else.
    body = env_file.read_text(encoding="utf-8")
    assert [ln.split("=")[0] for ln in body.splitlines() if ln.strip()] == ["CC_STATE_DIR"]


def test_a_missing_answer_file_is_created_from_the_template(tree: Path):
    assert not (tree / ".env").exists()

    r = _run(tree, "--non-interactive")

    assert "PASS answer-file: created" in r.stdout, r.stdout
    assert (tree / ".env").exists(), "configure is the ONE command that creates .env"
    # ...and then behaves exactly like the empty-file case.
    assert r.returncode == 3, r.stdout + r.stderr
    for key in REQUIRED:
        assert f"USERACTION {key}:" in r.stdout
    template_keys = {
        ln.split("=", 1)[0].lstrip("# ")
        for ln in (tree / ".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in ln
    }
    assert "CC_POD_PREFIX" in template_keys  # sanity: the template really is the copy source
    assert "CC_POD_PREFIX=cc-" in (tree / ".env").read_text(encoding="utf-8")


def test_a_prefilled_answer_file_asks_nothing_and_changes_nothing(tree: Path):
    """The preseed property: filled on a connected machine, carried across."""
    env_file = tree / ".env"
    shutil.copy2(tree / ".env.example", env_file)
    state = tree.parent / "home" / "state" / "carried"
    state.mkdir(parents=True)
    _set(env_file, {**REQUIRED, **GENERATED, "CC_STATE_DIR": str(state)})
    before = env_file.read_bytes()

    r = _run(tree, "--non-interactive")

    assert r.returncode == 0, r.stdout + r.stderr
    assert "  set    " not in r.stderr, f"nothing should be written:\n{r.stderr}"
    assert "USERACTION" not in r.stdout, r.stdout
    assert r.stderr.count("  keep   ") >= len(REQUIRED), (
        "every row that applies should report `keep`"
    )
    assert env_file.read_bytes() == before, "a carried-in answer file is left byte-identical"


def test_it_never_prompts_without_a_terminal_even_without_the_flag(tree: Path):
    """stdin is /dev/null here, which is the CI-runner and the agent-session
    case. Fail-closed applies to both, not only to the explicit flag."""
    shutil.copy2(tree / ".env.example", tree / ".env")

    r = _run(tree)

    assert r.returncode == 3, r.stdout + r.stderr
    assert "stdin is not a terminal" in r.stderr, r.stderr
    assert "USERACTION CC_LLM_UPSTREAM_BASE_URL:" in r.stdout
