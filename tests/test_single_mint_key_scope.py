"""The spine key is scoped to `cc_required_aliases` — the ONE list.

The 2026-10-01 design record, D4 ("Known consequence, to be measured in P2")
and P2's spine-key scope fix. The app phase used to mint the spine's LiteLLM
virtual key with a hard-coded `["cc-default", "cc-tts", "cc-stt"]`: a second,
hand-kept alias list beside `deploy/env-lib.sh`'s `cc_required_aliases`, and
one that had already drifted (graphiti-llm, cc-embedding and gpt-4.1-nano were
never in it). Measured before the fix: the graph writer embeds with the ADMIN
key, so this is not what broke embedding — but a scope is a list, and a list
has one definition.

What these prove, against the REAL `./setup.sh app` in a temp copy of the tree
with a stub `curl` on PATH that records what it was asked:

* a fresh mint asks for exactly `cc_required_aliases`, with speech on and off;
* an existing key whose list is narrower GAINS exactly the missing aliases —
  a union: what an operator added stays, the key's value never changes, and
  nothing is re-minted;
* an existing key whose list is EMPTY ("all models" in LiteLLM) is untouched;
* a proxy that cannot be asked is a WARN, not a FAIL — the self-check is what
  proves the key works;
* and through all of it, no credential ever appears in an argv (`ps` shows
  argv): the stub's argv log is searched for every marker.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.installer_source import installer_source

ROOT = Path(__file__).resolve().parents[1]

_DEBRIS = shutil.ignore_patterns(
    "NUL", "nul", "CON", "con", "AUX", "aux", "PRN", "prn",
    ".env", ".env.*", "__pycache__", "*.pyc",
)

ADMIN = "MARKER_ADMIN_aaaaaaaaaaaa"
SPINE = "MARKER_SPINE_bbbbbbbbbbbb"
MINTED = "sk-MARKER_MINTED_cccccccccc"


def _bash_exe() -> str:
    from central_command.api.update import _bash

    resolved = _bash()
    if not resolved:
        pytest.skip("no usable bash on this host")
    return resolved


def _version() -> str:
    for line in (ROOT / "VERSION").read_text(encoding="utf-8").splitlines():
        if line.startswith("version="):
            return line.split("=", 1)[1].strip()
    raise AssertionError("no version= in VERSION")


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


def _get(path: Path, key: str) -> str:
    out = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            out = line.split("=", 1)[1]
    return out


def _required(speech: str) -> list[str]:
    """The ONE list, asked of the one function — never re-typed here."""
    r = subprocess.run(
        [_bash_exe(), "-c", '. ./deploy/env-lib.sh; cc_required_aliases'],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "CC_ENABLE_SPEECH": speech},
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.split()


# The stub records argv (one line per call), stdin (the curl config the
# management calls travel in) and which endpoint was asked, and answers from
# files the test writes. Every other URL — the probes the ledger runs after the
# phase — is "connection refused".
CURL_STUB = r"""#!/usr/bin/env bash
dir='@DIR@'
printf '%s\n' "$*" >> "$dir/argv.log"
input=""
if [ ! -t 0 ]; then input="$(cat)"; fi
all="$* $input"
case "$all" in
  *"/key/generate"*) ep=generate ;;
  *"/key/info"*)     ep=info ;;
  *"/key/update"*)   ep=update ;;
  *) echo "curl: (7) Failed to connect" >&2; exit 7 ;;
esac
printf '%s\n' "$ep" >> "$dir/endpoints.log"
printf '%s\n' "$input" > "$dir/stdin-$ep.txt"
if [ -f "$dir/down" ]; then echo "curl: (7) Failed to connect" >&2; exit 7; fi
case "$ep" in
  generate) printf '{"key": "%s"}\n' "$(cat "$dir/minted")" ;;
  info)     cat "$dir/info.json" ;;
  update)   echo '{"ok": true}' ;;
esac
exit 0
"""


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copytree(ROOT / "deploy", repo / "deploy", ignore=_DEBRIS)
    shutil.copy2(ROOT / ".env.example", repo / ".env.example")
    shutil.copy2(ROOT / ".env.example", repo / ".env")
    shutil.copy2(ROOT / "VERSION", repo / "VERSION")
    shutil.copy2(ROOT / ".gitignore", repo / ".gitignore")
    (repo / "central_command" / "db").mkdir(parents=True)
    shutil.copy2(ROOT / "central_command" / "db" / "schema.sql",
                 repo / "central_command" / "db" / "schema.sql")
    (tmp_path / "home").mkdir()
    state = tmp_path / "state"
    state.mkdir()
    _set(repo / ".env", {
        "CC_STATE_DIR": str(state),
        "CC_LLM_PROXY_ADMIN_KEY": ADMIN,
        "CC_LLM_API_KEY": "",
        "CC_ENABLE_SPEECH": "1",
    })
    # The install's venv: present, and anything asked of it succeeds.
    py = repo / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    py.chmod(0o755)
    # The stubs: curl (recording), uv (the install step), node (too old, so
    # the cockpit build is a WARN and nothing runs npm).
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "curl").write_text(CURL_STUB.replace("@DIR@", str(stub)), encoding="utf-8")
    (stub / "uv").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (stub / "node").write_text("#!/usr/bin/env bash\necho v18.0.0\n", encoding="utf-8")
    for f in stub.iterdir():
        f.chmod(0o755)
    (stub / "minted").write_text(MINTED, encoding="utf-8")
    # app's cross-phase prerequisites, recorded done.
    led = state / "ledger.tsv"
    rows = ["# ledger.tsv — prepared by the test"]
    for step in ("check/tree-pristine", "fetch/venv", "fetch/cockpit",
                 "llm/secrets", "llm/litellm-live"):
        rows.append(f"{step}\tdone\t{_version()}\t2026-10-01T00:00:00Z\tnone\t")
    led.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return repo


def _stub(repo: Path) -> Path:
    return repo.parent / "stub"


def _run_app(repo: Path):
    env = dict(os.environ)
    home = repo.parent / "home"
    env.update(HOME=str(home), XDG_STATE_HOME=str(home / "state"),
               PATH=f"{_stub(repo)}{os.pathsep}{env.get('PATH', '')}")
    for stale in ("CC_STATE_DIR", "CC_ENABLE_SPEECH", "CC_SETUP_UNLEDGERED",
                  "CC_LLM_PROXY_ADMIN_KEY", "CC_LLM_API_KEY", "CC_EXECUTOR_MODE",
                  "VIRTUAL_ENV"):
        env.pop(stale, None)
    return subprocess.run(
        [_bash_exe(), "setup.sh", "app"],
        cwd=repo / "deploy" / "single",
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300, env=env,
    )


def _endpoints(repo: Path) -> list[str]:
    f = _stub(repo) / "endpoints.log"
    return f.read_text(encoding="utf-8").split() if f.exists() else []


def _mint_line(out: str) -> str:
    lines = [l for l in out.splitlines()
             if l.startswith(("PASS mint-key:", "WARN mint-key:", "FAIL mint-key:"))]
    assert len(lines) == 1, out
    return lines[0]


def _assert_no_secret_in_argv(repo: Path) -> None:
    """THE rule: a credential is never in an argv. Every marker — the admin
    key, an existing spine key, a freshly minted one — is searched for in
    every recorded command line."""
    argv = (_stub(repo) / "argv.log").read_text(encoding="utf-8")
    for secret in (ADMIN, SPINE, MINTED, "MARKER"):
        assert secret not in argv, f"a credential reached curl's argv:\n{argv}"


def _cfg_data(cfg: str) -> dict:
    """The JSON body out of a curl config's `data = "..."` line."""
    m = re.search(r'^data = "(.*)"$', cfg, flags=re.M)
    assert m, cfg
    raw = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
    return json.loads(raw)


# ── a fresh mint ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("speech", ["1", "0"])
def test_a_fresh_mint_is_scoped_to_exactly_the_required_aliases(tree: Path, speech: str):
    _set(tree / ".env", {"CC_ENABLE_SPEECH": speech})

    r = _run_app(tree)

    line = _mint_line(r.stdout)
    assert line.startswith("PASS mint-key: minted"), line
    assert _endpoints(tree) == ["generate"], _endpoints(tree)
    argv = (_stub(tree) / "argv.log").read_text(encoding="utf-8")
    m = re.search(r'\{"models": (\[[^\]]*\])', argv)
    assert m, argv
    assert json.loads(m.group(1)) == _required(speech)
    for alias in _required(speech):
        assert alias in line, line
    assert _get(tree / ".env", "CC_LLM_API_KEY") == MINTED
    _assert_no_secret_in_argv(tree)


def test_the_mint_body_is_built_from_the_one_list_not_typed():
    """The source keeps no second alias list: the mint's models come from
    cc_required_aliases (through spine_aliases_json)."""
    src = installer_source()
    assert '"cc-default", "cc-tts", "cc-stt"' not in src
    body = src.split("spine_aliases_json() {", 1)[1].split("\n}", 1)[0]
    assert "cc_required_aliases" in body


# ── an existing key ─────────────────────────────────────────────────────────


def test_an_existing_narrow_key_gains_exactly_the_missing_aliases(tree: Path):
    """The pre-fix key (cc-default/cc-tts/cc-stt) plus a model the operator
    added by hand: the three missing aliases are ADDED, the operator's model
    is kept, the key's value is untouched, and nothing is re-minted."""
    _set(tree / ".env", {"CC_LLM_API_KEY": SPINE})
    (_stub(tree) / "info.json").write_text(json.dumps({
        "key": SPINE,
        "info": {"models": ["cc-default", "cc-tts", "cc-stt", "operator-extra"]},
    }), encoding="utf-8")

    r = _run_app(tree)

    line = _mint_line(r.stdout)
    assert line.startswith("PASS mint-key:"), line
    assert _endpoints(tree) == ["info", "update"], _endpoints(tree)
    missing = [a for a in _required("1") if a not in ("cc-default", "cc-tts", "cc-stt")]
    assert "ADDED " + " ".join(missing) in line, line

    # The request carried the key in the STDIN config, never the argv.
    info_cfg = (_stub(tree) / "stdin-info.txt").read_text(encoding="utf-8")
    assert f"/key/info?key={SPINE}" in info_cfg, info_cfg
    assert f"Authorization: Bearer {ADMIN}" in info_cfg, info_cfg
    body = _cfg_data((_stub(tree) / "stdin-update.txt").read_text(encoding="utf-8"))
    assert body["key"] == SPINE
    assert body["models"] == ["cc-default", "cc-tts", "cc-stt", "operator-extra", *missing]
    assert set(body) == {"key", "models"}, "an update changes the scope and nothing else"

    assert _get(tree / ".env", "CC_LLM_API_KEY") == SPINE
    _assert_no_secret_in_argv(tree)


def test_an_existing_key_that_covers_the_list_is_not_updated(tree: Path):
    _set(tree / ".env", {"CC_LLM_API_KEY": SPINE})
    (_stub(tree) / "info.json").write_text(json.dumps({
        "key": SPINE, "info": {"models": _required("1") + ["operator-extra"]},
    }), encoding="utf-8")

    r = _run_app(tree)

    line = _mint_line(r.stdout)
    assert line.startswith("PASS mint-key:") and "already covers" in line, line
    assert _endpoints(tree) == ["info"]
    _assert_no_secret_in_argv(tree)


def test_an_existing_key_with_an_empty_list_is_untouched(tree: Path):
    """An empty list means "every model on the proxy" in LiteLLM
    (docs/vendor/litellm/docs/proxy/key_auth_arch.md) — "fixing" it would
    NARROW the key."""
    _set(tree / ".env", {"CC_LLM_API_KEY": SPINE})
    (_stub(tree) / "info.json").write_text(json.dumps({
        "key": SPINE, "info": {"models": []},
    }), encoding="utf-8")

    r = _run_app(tree)

    line = _mint_line(r.stdout)
    assert line.startswith("PASS mint-key:") and "EMPTY" in line, line
    assert _endpoints(tree) == ["info"]
    assert _get(tree / ".env", "CC_LLM_API_KEY") == SPINE
    _assert_no_secret_in_argv(tree)


def test_an_unreachable_proxy_is_a_warn_not_a_fail(tree: Path):
    _set(tree / ".env", {"CC_LLM_API_KEY": SPINE})
    (_stub(tree) / "down").write_text("", encoding="utf-8")

    r = _run_app(tree)

    line = _mint_line(r.stdout)
    assert line.startswith("WARN mint-key:"), line
    assert "could not read its scope" in line and "CC_LLM_PROXY_ADMIN_KEY" in line, line
    assert "update" not in _endpoints(tree)
    assert _get(tree / ".env", "CC_LLM_API_KEY") == SPINE
    # The phase went on past it: the derived keys after the mint were written.
    assert any(l.startswith(("PASS app-llm-base-url:", "WARN app-llm-base-url:"))
               for l in r.stdout.splitlines()), r.stdout
    _assert_no_secret_in_argv(tree)
