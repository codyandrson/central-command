"""The cockpit's npm tree and build re-run only when their inputs changed (P5, F12).

The 2026-10-02 Windows acceptance run measured adoption — and every `fetch` /
`app` re-run — spending ~70 s on `npm ci` and ~66 s on `npm run build` with
nothing in web/ changed. The local images learned the rule in v2.57.0
(`cc.build-inputs`); the cockpit now records what it was made FROM in the
STATE dir (`<state>/cockpit.npm-inputs`, `<state>/cockpit.build-inputs` —
never inside web/) and skips the command when the record equals the tree.

These run the REAL setup.sh against a temp copy of deploy/ and a tiny web/,
with a stub `npm` (and `node`, `uv`) on PATH: `app` is the real phase_app (as
tests/test_single_update_acquire.py's failing-npm test runs it), `fetch` is the
real driver around the real `fetch_cockpit` — the rest of phase_fetch (images,
the Python graph) is replaced, because it needs a registry. The driver, the
ledger, the plan and the probes are the shipped code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.installer_source import (
    drives_installer,
    env_path,
    python_shim,
    run_driver,
    with_stub_path,
    write_lf,
)

ROOT = Path(__file__).resolve().parents[1]

# Skipped on Windows until the 2026-10-02 testbed run's second pass: Git Bash
# prepends /mingw64/bin:/usr/bin to PATH and the real npm/node won. The stubs
# now go first through with_stub_path, are written LF (write_lf), and `$PY` is
# a real interpreter (python_shim) rather than the stub `uv`. Every test runs
# the real setup.sh, so each is @drives_installer and invokes it via run_driver.

_DEBRIS = shutil.ignore_patterns("NUL", "nul", ".env", ".env.*", "__pycache__", "*.pyc")


def _bash() -> str:
    from central_command.api.update import _bash as resolve

    b = resolve()
    if not b:
        pytest.skip("no usable bash on this host")
    return b


def _exe(path: Path, body: str) -> None:
    write_lf(path, body, mode=0o755)


def _set(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    seen = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0] if "=" in line else None
        if key in values:
            lines[i] = f"{key}={values[key]}"
            seen.add(key)
    lines += [f"{k}={v}" for k, v in values.items() if k not in seen]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _version() -> str:
    for line in (ROOT / "VERSION").read_text().splitlines():
        if line.startswith("version="):
            return line.split("=", 1)[1].strip()
    raise AssertionError("no version")


# phase_fetch reduced to its cockpit step — every image and the Python graph
# need a registry this rig does not have. Every OTHER row's probe reads true
# (a stub venv cannot import the app); the cockpit's own probes stay REAL, so
# the plan and the post-phase check are the shipped decision.
_TAIL = r'''
for __f in $(compgen -A function p_); do
  case "$__f" in p_cockpit_npm|p_cockpit_build|p_node_ok) continue ;; esac
  eval "$__f() { return 0; }"
done
phase_fetch() { fetch_cockpit; }
'''


class Rig:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.repo = tmp / "repo"
        self.state = tmp / "state"
        self.web = self.repo / "web"
        self.npm_log = tmp / "npm.log"

    def run(self, phase: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        home = self.tmp / "home"
        env.update(HOME=str(home), XDG_STATE_HOME=str(home / "state"), CC_VERIFY_MAX_WAIT="1")
        for stale in ("CC_STATE_DIR", "CC_ENABLE_SPEECH", "CC_SETUP_UNLEDGERED", "CC_RUN_LOCK_PID",
                      "CC_LLM_PROXY_ADMIN_KEY", "CC_LLM_API_KEY", "CC_EXECUTOR_MODE",
                      "CC_STAGED_FOR"):
            env.pop(stale, None)
        env["PATH"] = str(self.tmp / "bin") + os.pathsep + env.get("PATH", "")
        env = with_stub_path(env, self.tmp / "bin")
        self.npm_log.write_text("")
        return run_driver([_bash(), "setup.sh", phase], cwd=self.repo / "deploy" / "single", env=env)

    def npm_calls(self) -> list[str]:
        return [l for l in self.npm_log.read_text().splitlines() if l]

    def record(self, kind: str) -> Path:
        return self.state / f"cockpit.{kind}-inputs"


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    r = Rig(tmp_path)
    repo = r.repo
    repo.mkdir()
    shutil.copytree(ROOT / "deploy", repo / "deploy", ignore=_DEBRIS)
    for f in (".env.example", "VERSION", ".gitignore"):
        shutil.copy2(ROOT / f, repo / f)
    shutil.copy2(ROOT / ".env.example", repo / ".env")
    (repo / "central_command" / "db").mkdir(parents=True)
    shutil.copy2(ROOT / "central_command" / "db" / "schema.sql",
                 repo / "central_command" / "db" / "schema.sql")
    (tmp_path / "home").mkdir()
    r.state.mkdir()
    _set(repo / ".env", {"CC_STATE_DIR": env_path(r.state),
                         "CC_LLM_API_KEY": "sk-already-minted-0000000000",
                         "CC_LLM_PROXY_ADMIN_KEY": "", "CC_EXECUTOR_MODE": "dry_run"})
    setup = repo / "deploy" / "single" / "setup.sh"
    text = setup.read_text(encoding="utf-8")
    tail = 'main "$@"'
    assert text.rstrip().endswith(tail)
    setup.write_text(text.rstrip()[: -len(tail)] + _TAIL + tail + "\n", encoding="utf-8")

    # A tiny cockpit: the files the build reads, CRLF-free.
    web = r.web
    (web / "src").mkdir(parents=True)
    (web / "server").mkdir()
    (web / "config").mkdir()
    write_lf(web / "package.json", '{"name": "web", "version": "0.0.0"}\n')
    write_lf(web / "package-lock.json", '{"name": "web", "lockfileVersion": 3}\n')
    write_lf(web / "index.html", "<!doctype html>\n")
    write_lf(web / "vite.config.ts", "export default {}\n")
    write_lf(web / "tsconfig.json", "{}\n")
    write_lf(web / "config" / "tsconfig.app.json", "{}\n")
    write_lf(web / "src" / "main.tsx", "export const a = 1\n")
    write_lf(web / "server" / "index.ts", "export const b = 2\n")

    # The ledger: everything fetch/cockpit and app's rows require, done.
    led = ["# prepared"]
    for s in ("check/tree-pristine", "machine/machine-registries", "fetch/venv", "llm/secrets",
              "llm/litellm-live"):
        led.append(f"{s}\tdone\t{_version()}\t2026-10-01T00:00:00Z\tnone\t")
    (r.state / "ledger.tsv").write_text("\n".join(led) + "\n")

    py = repo / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    _exe(py, "#!/usr/bin/env bash\nexit 0\n")
    (repo / ".venv" / "bin" / "uvicorn").write_text("")
    b = tmp_path / "bin"
    b.mkdir()
    _exe(b / "uv", "#!/usr/bin/env bash\nexit 0\n")
    _exe(b / "node", "#!/usr/bin/env bash\necho v22.11.0\n")
    python_shim(b)
    _exe(b / "npm", f'''#!/usr/bin/env bash
echo "npm $*" >> "{r.npm_log.as_posix()}"
case "$1" in
  ci)  rm -rf node_modules && mkdir -p node_modules ;;
  run) rm -rf dist server-dist && mkdir -p dist server-dist && touch server-dist/index.js ;;
esac
exit 0
''')
    return r


def _ok(p: subprocess.CompletedProcess) -> None:
    assert p.returncode in (0, 2), p.stdout + p.stderr


@drives_installer
def test_the_first_run_installs_and_builds_and_records_both_in_the_state_dir(rig: Rig):
    f = rig.run("fetch")
    _ok(f)
    assert rig.npm_calls() == ["npm ci"]
    assert "PASS cockpit: npm tree installed" in f.stdout
    a = rig.run("app")
    _ok(a)
    assert rig.npm_calls() == ["npm run build"], "app reinstalled a tree fetch had just recorded"
    assert "PASS cockpit: cockpit built (web/)" in a.stdout
    for kind in ("npm", "build"):
        assert rig.record(kind).read_text().strip(), f"no {kind} record"
    # Never inside the checkout.
    assert not [p for p in rig.web.rglob("*inputs*")], "a record was written inside web/"


@drives_installer
def test_a_second_run_skips_both_and_says_so(rig: Rig):
    _ok(rig.run("fetch"))
    _ok(rig.run("app"))
    f = rig.run("fetch")
    _ok(f)
    assert rig.npm_calls() == [], rig.npm_calls()
    assert "npm ci was skipped" in f.stdout, f.stdout
    a = rig.run("app")
    _ok(a)
    assert rig.npm_calls() == [], rig.npm_calls()
    assert "npm run build was skipped" in a.stdout, a.stdout


@drives_installer
def test_a_changed_source_file_rebuilds_but_does_not_reinstall(rig: Rig):
    _ok(rig.run("fetch"))
    _ok(rig.run("app"))
    write_lf(rig.web / "server" / "index.ts", "export const b = 3\n")
    f = rig.run("fetch")
    _ok(f)
    assert rig.npm_calls() == [], "a source change reinstalled the npm tree"
    a = rig.run("app")
    _ok(a)
    assert rig.npm_calls() == ["npm run build"], rig.npm_calls()
    # ...and the record now matches the new source, so the next run skips again.
    _ok(rig.run("app"))
    assert rig.npm_calls() == []


@drives_installer
def test_a_crlf_only_difference_is_not_a_change(rig: Rig):
    """A Windows checkout with core.autocrlf rewrites every line ending; that is
    not the release changing, so it rebuilds nothing."""
    _ok(rig.run("fetch"))
    _ok(rig.run("app"))
    src = rig.web / "src" / "main.tsx"
    src.write_bytes(src.read_bytes().replace(b"\n", b"\r\n"))
    _ok(rig.run("app"))
    assert rig.npm_calls() == []


@drives_installer
def test_a_changed_lockfile_reinstalls_and_rebuilds_and_the_plan_says_so(rig: Rig):
    _ok(rig.run("fetch"))
    _ok(rig.run("app"))
    write_lf(rig.web / "package-lock.json", '{"name": "web", "lockfileVersion": 3, "x": 1}\n')
    f = rig.run("fetch")
    _ok(f)
    # The probe compares the same record, so the plan predicted the re-run.
    assert "PLAN fetch: WILL RUN" in f.stdout + f.stderr, f.stdout + f.stderr
    assert rig.npm_calls() == ["npm ci"], rig.npm_calls()
    a = rig.run("app")
    _ok(a)
    assert "PLAN app: WILL RUN" in a.stdout + a.stderr
    assert rig.npm_calls() == ["npm run build"], rig.npm_calls()


@drives_installer
def test_a_missing_build_output_rebuilds_even_with_a_matching_record(rig: Rig):
    _ok(rig.run("fetch"))
    _ok(rig.run("app"))
    shutil.rmtree(rig.web / "server-dist")
    _ok(rig.run("app"))
    assert rig.npm_calls() == ["npm run build"]


@drives_installer
def test_a_failed_build_leaves_no_record_behind(rig: Rig):
    """The record is dropped BEFORE the command runs: the build deletes
    server-dist first, so a failure half-way must never read as current."""
    _ok(rig.run("fetch"))
    _ok(rig.run("app"))
    write_lf(rig.web / "src" / "main.tsx", "export const a = 2\n")
    _exe(rig.tmp / "bin" / "npm", f'''#!/usr/bin/env bash
echo "npm $*" >> "{rig.npm_log.as_posix()}"
[[ "$1" == run ]] && {{ mkdir -p dist server-dist; touch server-dist/index.js; exit 1; }}
exit 0
''')
    a = rig.run("app")
    assert a.returncode == 1, a.stdout + a.stderr
    assert not rig.record("build").exists(), "a failed build left a record that reads current"
