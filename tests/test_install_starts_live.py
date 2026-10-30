"""A fresh install starts live: no installer, unit or script carries a run-mode key.

epic-run-modes R8 (rulings 2, 3 and 5). The deterministic test model and the
dry-run executor are test-only; a deployment carries none of `CC_TEST_MODEL`,
`CC_DEMO_MODE` and `CC_EXECUTOR_MODE`, and the API refuses to start on any of
them (`central_command/run_modes.py`). Two things pin that on the install side:

* a static walk: no file under `deploy/` WRITES or SETS one of the three keys
  — through a `set_kv*` writer, a unit's `Environment=` line, an `export` or a
  `KEY=value` line. A comment that states the guard is not a write;
* the single-node `check` FAILS a root `.env` that carries one, whatever its
  value, and passes the `run-modes` row for one that carries none.
"""

from __future__ import annotations

import re
from pathlib import Path

from central_command import run_modes
from tests.installer_source import drives_installer
from tests.test_single_driver_ledger import (  # noqa: F401  (tree is a fixture)
    ROOT,
    _protocol,
    _run,
    _set,
    tree,
)
from tests.test_single_human_rows import _CHECK_STUBS, _append_stubs

_KEY = "|".join(run_modes.KEYS)

# A write or a set of one of the keys, in the shapes a deploy file uses.
_WRITES = [
    re.compile(rf"\bset_kv\w*\s+\S+\s+[\"']?(?:{_KEY})\b"),   # set_kv / set_kv_if_unset
    re.compile(rf"^\s*Environment=[\"']?(?:{_KEY})\b"),       # a systemd unit
    re.compile(rf"\bexport\s+(?:{_KEY})\b"),                  # export KEY=...
    re.compile(rf"^\s*(?:{_KEY})\s*="),                       # a KEY=value line
]


def _deploy_files() -> list[Path]:
    out = []
    for path in sorted((ROOT / "deploy").rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".png", ".ico", ".zip"}:
            continue
        out.append(path)
    return out


def test_no_deploy_file_writes_or_sets_a_run_mode_key():
    offenders = []
    for path in _deploy_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if any(rx.search(line) for rx in _WRITES):
                offenders.append(f"{path.relative_to(ROOT).as_posix()}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_the_walk_sees_the_shapes_it_claims_to_forbid():
    """The patterns are not vacuous: each shape matches a sample."""
    samples = [
        'set_kv "$ENV_FILE" CC_EXECUTOR_MODE live "x"',
        'set_kv_if_unset "$ENV_FILE" CC_DEMO_MODE false "x"',
        "Environment=CC_DEMO_MODE=false",
        "export CC_TEST_MODEL=1",
        "CC_EXECUTOR_MODE=live",
    ]
    for sample in samples:
        assert any(rx.search(sample) for rx in _WRITES), sample
    assert not any(rx.search("see CC_EXECUTOR_MODE in the guard") for rx in _WRITES)


def _check(repo: Path, **env_keys: str):
    _append_stubs(repo, _CHECK_STUBS)
    if env_keys:
        _set(repo / ".env", env_keys)
    return _run(repo, "check")


@drives_installer
def test_check_fails_a_dotenv_carrying_demo_mode_false(tree: Path):
    r = _check(tree, CC_DEMO_MODE="false")

    assert r.returncode == 1, r.stdout + r.stderr
    fails = [l for l in _protocol(r.stdout) if l.startswith("FAIL run-modes:")]
    assert len(fails) == 1 and "CC_DEMO_MODE" in fails[0], r.stdout
    assert "CC_EXECUTOR_MODE" not in fails[0]


@drives_installer
def test_check_fails_a_dotenv_carrying_executor_mode_live(tree: Path):
    r = _check(tree, CC_EXECUTOR_MODE="live")

    assert r.returncode == 1, r.stdout + r.stderr
    fails = [l for l in _protocol(r.stdout) if l.startswith("FAIL run-modes:")]
    assert len(fails) == 1 and "CC_EXECUTOR_MODE" in fails[0], r.stdout


@drives_installer
def test_check_fails_an_empty_run_mode_key_too(tree: Path):
    """Presence is the rule, whatever the value (ruling 2)."""
    r = _check(tree, CC_TEST_MODEL="")

    assert r.returncode == 1, r.stdout + r.stderr
    assert any(l.startswith("FAIL run-modes:") and "CC_TEST_MODEL" in l
               for l in _protocol(r.stdout)), r.stdout


@drives_installer
def test_check_passes_the_row_for_a_dotenv_with_none(tree: Path):
    r = _check(tree)

    assert r.returncode in (0, 2), r.stdout + r.stderr
    passes = [l for l in _protocol(r.stdout) if l.startswith("PASS run-modes:")]
    assert len(passes) == 1, r.stdout
    assert not any(l.startswith("FAIL run-modes:") for l in _protocol(r.stdout))
