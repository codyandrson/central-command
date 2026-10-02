"""`boot` starts, and `stop` stops, exactly three host processes — supervised.

The 2026-10-01 design record's D6 ("everything the install starts, it
supervises") and D7 ("importing the bundled skills is a step"), P3's half of
v2.57.0. The acceptance the operator set for P3 — "on Linux, `stop` then `boot`
leaves three listeners and a non-empty skills library" — is proven HERE, by
stub-driven execution of the REAL `./setup.sh` in a temp copy of the tree, plus
`systemd-analyze --user verify` of the rendered units. Not by a real install:
the host this repository is developed on runs a LIVE deployment with real
systemd units, so nothing in this file may enable, start, stop, link or reload
anything on it.

What is real and what is a stub:

* `./setup.sh boot` / `stop` are the shipped scripts, run in a temp copy (the
  pattern tests/test_single_driver_ledger.py established), with every port in
  the temp `.env` a FREE one — never the 8080/3080 a live deployment holds;
* `.venv/bin/uvicorn` and `node` are fakes that serve just enough HTTP on the
  port they are given: the API's /health, /api/agents, /api/skills and POST
  /api/skills/import; the runner's bearer-token check; the cockpit's `/`;
* `systemctl` is a STUB first on PATH: it records every call and emulates a
  user manager well enough to run a unit's ExecStart (from a CLEAN environment
  plus EnvironmentFile= and Environment=, as systemd would) and stop it again.
  `loginctl` is a stub too. The real ones are never reached;
* curl, ss, python3 are the host's — talking only to the fakes, on loopback.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT / "deploy" / "single"

_DEBRIS = shutil.ignore_patterns(
    "NUL", "nul", "CON", "con", "AUX", "aux", "PRN", "prn",
    ".env", ".env.*", "__pycache__", "*.pyc",
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="drives POSIX process groups and a Linux stub systemd")

TOKEN = "MARKER_RUNNER_TOKEN_0123456789abcdef"


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


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


# ── the fakes ───────────────────────────────────────────────────────────────

# One fake for both uvicorn apps and the cockpit's node: the role comes from
# argv. Every start is appended to $FAKE_DIR/starts.log ("<role> <pid>").
FAKE_SERVER = r'''#!/usr/bin/env python3
import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

fake = os.environ["FAKE_DIR"]
args = sys.argv[1:]
if args[:1] == ["-v"]:
    print("v22.11.0"); sys.exit(0)
if args and args[0] == "server-dist/index.js":
    role, port = "cockpit", int(os.environ["PORT"])
else:
    role = {"central_command.api.app:app": "api",
            "central_command.sandbox.runner:app": "runner"}[args[0]]
    port = int(args[args.index("--port") + 1])
lib = os.path.join(fake, "library.json")

def library():
    try:
        return json.load(open(lib))
    except Exception:
        return []

if role == "runner":
    with open(os.path.join(fake, "runner-env.json"), "w") as f:
        json.dump({"token": os.environ.get("CC_SANDBOX_RUNNER_TOKEN", ""),
                   "backend": os.environ.get("CC_SANDBOX_BACKEND", "")}, f)

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def auth_ok(self):
        tok = os.environ.get("CC_SANDBOX_RUNNER_TOKEN", "")
        return not tok or self.headers.get("authorization", "") == "Bearer " + tok
    def do_GET(self):
        if role == "runner":
            return self.send(404 if self.auth_ok() else 401, {"detail": "x"})
        if role == "cockpit":
            return self.send(200, {"ok": True})
        if self.path == "/health":
            return self.send(200, {"ok": True})
        if self.path.startswith("/api/agents"):
            return self.send(200, {"agents": [{"id": "ea"}, {"id": "triage"}]})
        if self.path.startswith("/api/skills"):
            return self.send(200, {"skills": [{"id": i} for i in library()]})
        return self.send(404, {"detail": "no"})
    def do_POST(self):
        n = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(n) or b"{}")
        if role == "api" and self.path == "/api/skills/import":
            with open(os.path.join(fake, "imports.log"), "a") as f:
                f.write(json.dumps(body) + "\n")
            if not os.path.isfile(os.path.join(body["path"], "SKILL.md")):
                return self.send(422, {"detail": "no SKILL.md in " + body["path"]})
            ids = library() + [body["skill_id"]]
            json.dump(ids, open(lib, "w"))
            return self.send(200, {"skill_id": body["skill_id"], "guidance": "guidance", "references": []})
        return self.send(404, {"detail": "no"})

srv = ThreadingHTTPServer(("127.0.0.1", port), H)
with open(os.path.join(fake, "starts.log"), "a") as f:
    f.write(f"{role} {os.getpid()}\n")
srv.serve_forever()
'''

# A user manager, as far as `boot` and `stop` can tell. `enable <path>` links,
# `restart`/`start` run ExecStart from a CLEAN environment + EnvironmentFile= +
# Environment= (EnvironmentFile wins, as in systemd), `stop` TERMs and waits.
STUB_SYSTEMCTL = r'''#!/usr/bin/env python3
import os, shlex, signal, subprocess, sys, time

fake = os.environ["FAKE_DIR"]
with open(os.path.join(fake, "systemctl.log"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
args = [a for a in sys.argv[1:] if a not in ("--user", "--quiet", "--now", "--value")]
quiet_now = "--now" in sys.argv
if os.path.exists(os.path.join(fake, "no-systemd")):
    print("Failed to connect to bus: No medium found", file=sys.stderr); sys.exit(1)
units = os.path.join(fake, "units"); run = os.path.join(fake, "run")
os.makedirs(units, exist_ok=True); os.makedirs(run, exist_ok=True)

def alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(") ", 1)[1][0] != "Z"
    except OSError:
        return False

def pid_of(u):
    try:
        return int(open(os.path.join(run, u + ".pid")).read())
    except Exception:
        return 0

def unesc(s):
    return s.replace("%%", "%").replace("$$", "$")

def parse(u):
    conf = {"env": []}
    for line in open(os.path.join(units, u)):
        line = line.rstrip("\n")
        if "=" not in line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        if k == "Environment":
            conf["env"] += [unesc(x) for x in shlex.split(v)]
        else:
            conf[k] = v
    return conf

def envfile(path):
    out = {}
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if v[:1] in "'\"" and v[-1:] == v[:1]:
            v = v[1:-1]
        out[k] = v
    return out

def stop(u):
    pid = pid_of(u)
    if pid and alive(pid):
        os.kill(pid, signal.SIGTERM)
        for _ in range(100):
            if not alive(pid):
                break
            time.sleep(0.05)
    try:
        os.remove(os.path.join(run, u + ".pid"))
    except OSError:
        pass

def start(u):
    c = parse(u)
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": os.environ.get("HOME", "/"), "FAKE_DIR": fake}
    for kv in c["env"]:
        k, v = kv.split("=", 1); env[k] = v
    env.update(envfile(unesc(c["EnvironmentFile"])))
    log = unesc(c["StandardOutput"].split(":", 1)[1])
    out = open(log, "a")
    p = subprocess.Popen([unesc(w) for w in shlex.split(c["ExecStart"])], cwd=unesc(c["WorkingDirectory"]),
                         env=env, stdout=out, stderr=out, stdin=subprocess.DEVNULL, start_new_session=True)
    open(os.path.join(run, u + ".pid"), "w").write(str(p.pid))

cmd, rest = (args[0], args[1:]) if args else ("", [])
if cmd == "show-environment":
    print("PATH=/usr/bin"); sys.exit(0)
if cmd == "enable":
    for p in rest:
        dst = os.path.join(units, os.path.basename(p))
        if os.path.lexists(dst):
            os.remove(dst)
        os.symlink(p, dst)
    sys.exit(0)
if cmd == "daemon-reload":
    sys.exit(0)
if cmd in ("start", "restart"):
    for u in rest:
        if cmd == "restart":
            stop(u)
        if not (pid_of(u) and alive(pid_of(u))):
            start(u)
    sys.exit(0)
if cmd == "stop":
    for u in rest:
        stop(u)
    sys.exit(0)
if cmd == "disable":
    for u in rest:
        if quiet_now:
            stop(u)
        try:
            os.remove(os.path.join(units, u))
        except OSError:
            pass
    sys.exit(0)
if cmd == "is-active":
    sys.exit(0 if all(pid_of(u) and alive(pid_of(u)) for u in rest) else 3)
if cmd == "show":
    pid = pid_of(rest[-1]); print(pid if pid and alive(pid) else 0); sys.exit(0)
sys.exit(0)
'''

STUB_LOGINCTL = r'''#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_DIR/loginctl.log"
case "$(cat "$FAKE_DIR/linger" 2>/dev/null)" in
  yes) echo "Linger=yes" ;;
  no)  echo "Linger=no" ;;
  outside) echo "Failed to get user: User ID 1000 is not logged in or lingering" >&2; exit 1 ;;
  *)   echo "Failed to connect to bus: No such file or directory" >&2; exit 1 ;;
esac
'''


def _exe(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _write_ledger(repo: Path, steps: list[str]) -> None:
    led = repo.parent / "state" / "ledger.tsv"
    body = ["# ledger.tsv — prepared by the test"]
    for step in steps:
        body.append(f"{step}\tdone\t{_version()}\t2026-10-02T00:00:00Z\tnone\t")
    led.write_text("\n".join(body) + "\n", encoding="utf-8")


class Install:
    """A temp install whose host processes are fakes, with stub supervisors."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.repo = tmp / "repo"
        self.state = tmp / "state"
        self.fake = tmp / "fake"
        self.stubs = tmp / "stubs"
        repo = self.repo
        repo.mkdir()
        shutil.copytree(ROOT / "deploy", repo / "deploy", ignore=_DEBRIS)
        shutil.copytree(ROOT / "skills", repo / "skills")
        for f in (".env.example", "VERSION", ".gitignore"):
            shutil.copy2(ROOT / f, repo / f)
        shutil.copy2(ROOT / ".env.example", repo / ".env")
        (repo / "central_command" / "db").mkdir(parents=True)
        shutil.copy2(ROOT / "central_command" / "db" / "schema.sql",
                     repo / "central_command" / "db" / "schema.sql")
        (repo / "web" / "server-dist").mkdir(parents=True)
        (repo / "web" / "server-dist" / "index.js").write_text("// fake\n", encoding="utf-8")
        _exe(repo / ".venv" / "bin" / "uvicorn", FAKE_SERVER)
        _exe(self.stubs / "node", FAKE_SERVER)
        _exe(self.stubs / "systemctl", STUB_SYSTEMCTL)
        _exe(self.stubs / "loginctl", STUB_LOGINCTL)
        (tmp / "home").mkdir()
        self.state.mkdir()
        self.fake.mkdir()
        self.api, self.cockpit, self.runner = _free_port(), _free_port(), _free_port()
        _set(repo / ".env", {
            "CC_STATE_DIR": str(self.state),
            "CC_API_PORT": str(self.api),
            "CC_COCKPIT_PORT": str(self.cockpit),
            "CC_SANDBOX_RUNNER_URL": f"http://127.0.0.1:{self.runner}",
            "CC_SANDBOX_RUNNER_TOKEN": TOKEN,
            "CC_ENABLE_SANDBOX": "1",
            "CC_EXECUTOR_MODE": "dry_run",
        })
        # Every cross-phase prerequisite of a boot row, recorded done.
        _write_ledger(repo, ["app/install", "app/mint-key", "verify/selfcheck", "app/cockpit"])

    def run(self, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        home = self.tmp / "home"
        env.update(HOME=str(home), XDG_STATE_HOME=str(home / "state"),
                   FAKE_DIR=str(self.fake), PATH=f"{self.stubs}:{env.get('PATH', '/usr/bin:/bin')}")
        for stale in ("CC_STATE_DIR", "CC_SETUP_UNLEDGERED", "CC_RUN_LOCK_PID",
                      "CC_SANDBOX_RUNNER_TOKEN", "CC_ENABLE_SANDBOX", "CC_API_PORT",
                      "CC_COCKPIT_PORT", "CC_SANDBOX_RUNNER_URL", "CC_EXECUTOR_MODE",
                      "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"):
            env.pop(stale, None)
        return subprocess.run([_bash_exe(), "setup.sh", *args], cwd=self.repo / "deploy" / "single",
                              capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=timeout, env=env)

    def install_id(self) -> str:
        r = subprocess.run([_bash_exe(), "-c", '. ./deploy/env-lib.sh; cc_install_id "$(cc_norm_path "$PWD")"'],
                           cwd=self.repo, capture_output=True, text=True, check=True)
        return r.stdout.strip()

    def unit(self, kind: str) -> str:
        return f"cc-{self.install_id()}-{kind}.service"

    def calls(self) -> list[str]:
        p = self.fake / "systemctl.log"
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    def starts(self) -> list[tuple[str, int]]:
        p = self.fake / "starts.log"
        if not p.exists():
            return []
        return [(l.split()[0], int(l.split()[1])) for l in p.read_text(encoding="utf-8").splitlines()]

    def ledger(self) -> dict[str, list[str]]:
        led = (self.state / "ledger.tsv").read_text(encoding="utf-8")
        return {l.split("\t")[0]: l.split("\t") for l in led.splitlines() if l and not l.startswith("#")}

    def cleanup(self) -> None:
        for _role, pid in self.starts():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


@pytest.fixture
def inst(tmp_path: Path):
    i = Install(tmp_path)
    yield i
    i.cleanup()


def _protocol(out: str) -> list[str]:
    return [l for l in out.splitlines() if l.startswith(("PASS ", "WARN ", "FAIL ", "USERACTION "))]


def _line(out: str, prefix: str) -> str:
    hits = [l for l in _protocol(out) if l.startswith(prefix)]
    assert hits, f"no line starting {prefix!r} in:\n" + "\n".join(_protocol(out))
    return hits[-1]


def _bundled() -> list[str]:
    return sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))


# ── the systemd path ────────────────────────────────────────────────────────


def test_boot_writes_three_units_and_starts_them_through_systemd_then_stop_and_boot_again(inst: Install):
    r = inst.run("boot")
    assert r.returncode == 0, r.stdout + r.stderr
    api, sbx, ckp = inst.unit("api"), inst.unit("sandbox"), inst.unit("cockpit")

    # The units are in the STATE dir, named with the install id, never in the
    # checkout — and they carry what makes them the same process boot starts
    # by hand.
    units = inst.state / "systemd"
    assert sorted(p.name for p in units.iterdir()) == sorted([api, sbx, ckp])
    assert not list(inst.repo.rglob(f"cc-{inst.install_id()}-*")), "a unit landed inside the checkout"
    api_text = (units / api).read_text(encoding="utf-8")
    assert f"EnvironmentFile={inst.repo}/.env" in api_text
    assert f"WorkingDirectory={inst.repo}\n" in api_text
    assert (f"ExecStart={inst.repo}/.venv/bin/uvicorn central_command.api.app:app "
            f"--host 127.0.0.1 --port {inst.api}") in api_text
    assert "KillMode=process" in api_text, "the updater the API spawns must outlive the API's stop"
    assert "Restart=on-failure" in api_text and "WantedBy=default.target" in api_text
    assert f"StandardOutput=append:{inst.state}/uvicorn.log" in api_text
    sbx_text = (units / sbx).read_text(encoding="utf-8")
    assert 'Environment="CC_SANDBOX_BACKEND=podman"' in sbx_text
    assert "central_command.sandbox.runner:app" in sbx_text
    assert TOKEN not in sbx_text, "the token reaches the runner through EnvironmentFile, never baked into a unit"
    ckp_text = (units / ckp).read_text(encoding="utf-8")
    assert f"WorkingDirectory={inst.repo}/web" in ckp_text and f"After={api}" in ckp_text
    assert f'Environment="PORT={inst.cockpit}"' in ckp_text
    assert f'Environment="GATEWAY_URL=http://127.0.0.1:{inst.api}"' in ckp_text
    assert (units / api).stat().st_mode & 0o777 == 0o600

    # Linked/enabled, reloaded, then each STARTED THROUGH the (stub) manager,
    # in order: api, sandbox, cockpit.
    calls = inst.calls()
    enable = [c for c in calls if c.startswith("--user enable ")]
    assert enable and all(str(units / u) in enable[0] for u in (api, sbx, ckp)), calls
    assert calls.index(enable[0]) < calls.index("--user daemon-reload")
    restarts = [c for c in calls if c.startswith("--user restart ")]
    assert restarts == [f"--user restart {api}", f"--user restart {sbx}", f"--user restart {ckp}"], calls
    assert [role for role, _ in inst.starts()] == ["api", "runner", "cockpit"]
    # ...and ONLY through it: every process that started is one the stub ran.
    stub_pids = {int(p.read_text()) for p in (inst.fake / "run").iterdir()}
    assert {pid for _, pid in inst.starts()} == stub_pids
    for port in (inst.api, inst.runner, inst.cockpit):
        assert _listening(port)

    # The runner got the SAME token the API reads — from .env, through
    # EnvironmentFile=, since the stub manager starts from a clean environment.
    seen = json.loads((inst.fake / "runner-env.json").read_text())
    assert seen == {"token": TOKEN, "backend": "podman"}

    # The rows, recorded done; the pid files hold the units' MainPIDs.
    rows = inst.ledger()
    for step in ("boot/boot-api", "boot/boot-sandbox", "boot/boot-roster",
                 "boot/skills-imported", "boot/boot-cockpit"):
        assert rows[step][1] == "done", rows[step]
    assert int((inst.state / "uvicorn.pid").read_text()) == dict((r, p) for r, p in inst.starts())["api"]
    assert "PASS boot-supervisor: systemd --user units" in r.stdout

    # STOP goes through the manager (or Restart= would revive what it killed)
    # and proves every port free.
    s = inst.run("stop")
    assert s.returncode == 0, s.stdout + s.stderr
    for u in (ckp, sbx, api):
        assert f"--user stop {u}" in inst.calls()
    for name in ("cockpit", "sandbox", "uvicorn"):
        assert _line(s.stdout, f"PASS stop-{name}:")
    for port in (inst.api, inst.runner, inst.cockpit):
        assert not _listening(port)

    # ...and boot again: three listeners, and the library is not empty.
    b = inst.run("boot")
    assert b.returncode == 0, b.stdout + b.stderr
    for port in (inst.api, inst.runner, inst.cockpit):
        assert _listening(port)
    assert sorted(json.loads((inst.fake / "library.json").read_text())) == _bundled()
    assert "already in the library and LEFT AS THEY ARE" in _line(b.stdout, "PASS skills-imported:")


def test_a_running_unit_is_left_alone_and_not_started_twice(inst: Install):
    assert inst.run("boot").returncode == 0
    before = len(inst.starts())
    r = inst.run("boot")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(inst.starts()) == before, "boot started a second copy of something already running"
    assert f"under {inst.unit('api')} — not starting a second one" in _line(r.stdout, "PASS boot-api:")


# ── no user manager: the detached fallback ──────────────────────────────────


def test_without_a_user_manager_boot_starts_detached_and_warns(inst: Install):
    (inst.fake / "no-systemd").touch()
    r = inst.run("boot")
    assert r.returncode == 2, r.stdout + r.stderr       # the WARN, and nothing else
    warn = _line(r.stdout, "WARN boot-supervisor:")
    assert "nothing restarts them after a crash or a reboot" in warn
    assert "./setup.sh again after a reboot" in warn
    # Nothing was enabled or started through systemd; no unit was written.
    assert not [c for c in inst.calls() if not c.endswith("show-environment")], inst.calls()
    assert not (inst.state / "systemd").exists()
    assert [role for role, _ in inst.starts()] == ["api", "runner", "cockpit"]
    # The pid file names the SERVER itself — not a wrapper shell around it
    # (the pre-v2.57.0 `( cd X && cmd & )` shape recorded the wrapper, so
    # `stop` signalled a shell and the server kept listening).
    started = dict(inst.starts())
    for name, role, port in (("uvicorn", "api", inst.api), ("sandbox", "runner", inst.runner),
                             ("cockpit", "cockpit", inst.cockpit)):
        assert int((inst.state / f"{name}.pid").read_text()) == started[role]
        assert _listening(port)
    # The token reached the hand-started runner too (load_env exports .env).
    assert json.loads((inst.fake / "runner-env.json").read_text())["token"] == TOKEN

    s = inst.run("stop")
    assert s.returncode == 0, s.stdout + s.stderr
    for port in (inst.api, inst.runner, inst.cockpit):
        assert not _listening(port)
    assert not list(inst.state.glob("*.pid"))


def test_with_the_sandbox_off_the_runner_row_is_done_and_nothing_starts(inst: Install):
    """And with no cockpit build: the import route is the API's own, so the
    skills are imported without the cockpit, which is a WARN."""
    _set(inst.repo / ".env", {"CC_ENABLE_SANDBOX": "0"})
    shutil.rmtree(inst.repo / "web" / "server-dist")
    r = inst.run("boot")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "not applicable" in _line(r.stdout, "PASS boot-sandbox:")
    assert "server-dist is missing" in _line(r.stdout, "WARN boot-cockpit:")
    assert [role for role, _ in inst.starts()] == ["api"]
    assert not (inst.state / "systemd" / inst.unit("sandbox")).exists()
    assert not _listening(inst.runner)
    rows = inst.ledger()
    assert rows["boot/boot-sandbox"][1] == "done"
    assert rows["boot/skills-imported"][1] == "done"
    assert sorted(json.loads((inst.fake / "library.json").read_text())) == _bundled()
    # stop says there is no runner to stop rather than probing for one.
    s = inst.run("stop")
    assert "nothing to stop" in _line(s.stdout, "PASS stop-sandbox:")


# ── stop proves the port ────────────────────────────────────────────────────


def test_stop_fails_when_a_port_still_answers(inst: Install):
    """A listener on the API port that this install has no record of starting
    (no pid file, no unit): stop cannot claim the port is free, so it FAILS —
    and it does not shoot it either."""
    foreign = subprocess.Popen(["python3", "-m", "http.server", str(inst.api), "--bind", "127.0.0.1"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if _listening(inst.api):
                break
            time.sleep(0.1)
        r = inst.run("stop")
        assert r.returncode == 1, r.stdout + r.stderr
        line = _line(r.stdout, "FAIL stop-uvicorn:")
        assert f"127.0.0.1:{inst.api}" in line and "no record of starting" in line
        assert foreign.poll() is None, "stop killed a process it did not start"
    finally:
        foreign.kill()
        foreign.wait()


def test_stop_fails_loudly_when_the_answer_file_cannot_be_loaded(inst: Install):
    """D5: `stop` used to swallow a load_env failure and probe the DEFAULT
    ports — a green stop about ports this install may not use."""
    (inst.repo / ".env").unlink()
    r = inst.run("stop")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "ports are unknown" in _line(r.stdout, "FAIL stop:")
    assert not any(l.startswith("PASS stop-") for l in _protocol(r.stdout)), r.stdout

    inst.repo.joinpath(".env").write_text(
        (ROOT / ".env.example").read_text(encoding="utf-8") + "CC_OPERATOR_NAME=Jane Doe\n",
        encoding="utf-8")
    r = inst.run("stop")
    assert r.returncode == 1, r.stdout + r.stderr
    assert _line(r.stdout, "FAIL stop:")


# ── create-only skills, and a new bundled folder ────────────────────────────


def test_skills_are_create_only_and_a_new_bundled_folder_is_imported(inst: Install):
    (inst.fake / "no-systemd").touch()
    (inst.fake / "library.json").write_text(json.dumps(["jira"]))
    new = inst.repo / "skills" / "zz-new"
    new.mkdir()
    (new / "SKILL.md").write_text("---\nname: zz-new\ndescription: test\n---\n# x\n", encoding="utf-8")

    r = inst.run("boot")
    assert r.returncode == 2, r.stdout + r.stderr
    imported = [json.loads(l)["skill_id"]
                for l in (inst.fake / "imports.log").read_text().splitlines()]
    assert "jira" not in imported, "an existing skill was re-imported"
    assert sorted(imported) == sorted([*(s for s in _bundled() if s != "jira"), "zz-new"])
    # The path the API is handed is the folder itself, absolute.
    first = json.loads((inst.fake / "imports.log").read_text().splitlines()[0])
    assert Path(first["path"]).is_absolute() and (Path(first["path"]) / "SKILL.md").is_file()
    line = _line(r.stdout, "PASS skills-imported:")
    assert "LEFT AS THEY ARE: jira" in line and "create-only" in line
    assert "never overwritten" in line
    assert inst.ledger()["boot/skills-imported"][1] == "done"


def test_a_failed_import_is_a_fail_naming_the_skill(inst: Install):
    """The API refusing one folder (422) is a FAIL that names it, and the row
    is recorded failed — never a PASS over a library missing a skill."""
    (inst.fake / "no-systemd").touch()
    bad = inst.repo / "skills" / "zz-bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("x\n", encoding="utf-8")
    _exe(inst.repo / ".venv" / "bin" / "uvicorn", FAKE_SERVER.replace(
        'if not os.path.isfile(os.path.join(body["path"], "SKILL.md")):',
        'if body["skill_id"] == "zz-bad" or not os.path.isfile(os.path.join(body["path"], "SKILL.md")):'))
    r = inst.run("boot")
    assert r.returncode == 1, r.stdout + r.stderr
    line = _line(r.stdout, "FAIL skills-imported:")
    assert "zz-bad" in line and "/api/skills/import" in line
    assert inst.ledger()["boot/skills-imported"][1] == "failed"


def test_every_bundled_folder_name_is_the_id_the_importer_would_derive():
    """boot passes the FOLDER name as skill_id. The importer derives an id from
    SKILL.md's `name:` when none is passed — so the two must agree, or a skill
    imported by hand from the same folder would be a second, different skill."""
    from central_command.skills.importer import _doc_key, parse_frontmatter

    for skill_md in sorted((ROOT / "skills").glob("*/SKILL.md")):
        meta, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        derived = _doc_key(meta.get("name") or skill_md.parent.name)
        assert derived == skill_md.parent.name, f"{skill_md.parent.name}: the importer would derive {derived!r}"


# ── lingering is a FAIL that names the command ──────────────────────────────


@pytest.mark.parametrize("linger, verdict", [
    ("no", "FAIL linger:"),
    ("outside", "FAIL linger:"),
    ("yes", "PASS linger:"),
    ("absent", "PASS linger:"),
])
def test_lingering_off_is_a_fail_naming_loginctl_enable_linger(inst: Install, linger: str, verdict: str):
    (inst.fake / "linger").write_text(linger)
    r = inst.run("preflight")
    line = _line(r.stdout, "PASS linger:" if verdict.startswith("PASS") else "FAIL linger:")
    if verdict.startswith("FAIL"):
        assert "loginctl enable-linger" in line, line
        assert r.returncode == 1
    elif linger == "absent":
        assert "not applicable" in line
    # Only ever ASKED: show-user, never enable-linger.
    asked = (inst.fake / "loginctl.log").read_text().splitlines()
    assert asked and all(a.startswith("show-user ") for a in asked), asked


# ═══ the pure renderers (supervise-lib.sh) and the tree input (ledger-lib.sh) ═══


def _lib(script: str, cwd: Path = SINGLE, env: dict | None = None) -> str:
    r = subprocess.run(
        [_bash_exe(), "-c", ". ../env-lib.sh; . ./ledger-lib.sh; . ./supervise-lib.sh\n" + script],
        cwd=cwd, capture_output=True, text=True,
        env={**os.environ, **(env or {})},
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def _render_unit(tmp: Path, exe: str) -> str:
    return _lib(f'''
      cc_render_unit cc-repo-1a2b3c4d-api.service "Central Command API, 127.0.0.1:18080 (install repo-1a2b3c4d)" \\
        "{tmp}/my repo" "{tmp}/my repo/.env" "{tmp}/state/uvicorn.log" - process \\
        -- PYTHONUTF8=1 'SSL_CERT_FILE={tmp}/ca 50%.pem' \\
        -- "{exe}" central_command.api.app:app --host 127.0.0.1 --port 18080 'a b' '$HOME'
    ''')


def test_the_unit_renders_exactly(tmp_path: Path):
    text = _render_unit(tmp_path, "/opt/venv/bin/uvicorn")
    lines = text.splitlines()
    for expected in (
        "[Unit]",
        "Description=Central Command API, 127.0.0.1:18080 (install repo-1a2b3c4d)",
        "[Service]",
        "Type=simple",
        f"WorkingDirectory={tmp_path}/my repo",
        f"EnvironmentFile={tmp_path}/my repo/.env",
        'Environment="PYTHONUTF8=1"',
        f'Environment="SSL_CERT_FILE={tmp_path}/ca 50%%.pem"',
        # a word with a space is quoted; `$` is doubled, or systemd expands it
        'ExecStart=/opt/venv/bin/uvicorn central_command.api.app:app --host 127.0.0.1 '
        '--port 18080 "a b" $$HOME',
        "Restart=on-failure",
        "RestartSec=5",
        "KillMode=process",
        f"StandardOutput=append:{tmp_path}/state/uvicorn.log",
        f"StandardError=append:{tmp_path}/state/uvicorn.log",
        "[Install]",
        "WantedBy=default.target",
    ):
        assert expected in lines, f"missing {expected!r} in:\n{text}"
    assert not any(l.startswith("After=") for l in lines), "no After= when none is asked"
    # EnvironmentFile comes before the Environment= lines it is documented
    # beside, and the unit never names a key's VALUE from .env.
    assert lines.index(f"EnvironmentFile={tmp_path}/my repo/.env") < lines.index('Environment="PYTHONUTF8=1"')


def test_a_relative_executable_is_refused(tmp_path: Path):
    r = subprocess.run([_bash_exe(), "-c", ". ./supervise-lib.sh; cc_render_unit u d /w /e /l - - -- -- uvicorn x"],
                       cwd=SINGLE, capture_output=True, text=True)
    assert r.returncode == 1 and "absolute path" in r.stderr


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze is not installed here")
def test_systemd_analyze_verifies_the_rendered_units(tmp_path: Path):
    """READ-ONLY: `systemd-analyze --user verify` parses the files it is handed
    and reports; it enables, starts and reloads nothing. The three units boot
    writes, rendered the way proc_spec renders them."""
    (tmp_path / "my repo").mkdir()
    (tmp_path / "my repo" / ".env").write_text("CC_API_PORT=18080\n")
    (tmp_path / "state").mkdir()
    exe = shutil.which("true") or "/bin/true"
    units = tmp_path / "units"
    units.mkdir()
    (units / "cc-repo-1a2b3c4d-api.service").write_text(_render_unit(tmp_path, exe))
    for kind, after, extra in (("sandbox", "-", "CC_SANDBOX_BACKEND=podman"),
                               ("cockpit", "cc-repo-1a2b3c4d-api.service", "PORT=13080")):
        (units / f"cc-repo-1a2b3c4d-{kind}.service").write_text(_lib(f'''
          cc_render_unit cc-repo-1a2b3c4d-{kind}.service "Central Command {kind}" "{tmp_path}/my repo" \\
            "{tmp_path}/my repo/.env" "{tmp_path}/state/{kind}.log" {after} - -- {extra} -- "{exe}" x
        '''))
    files = sorted(str(p) for p in units.iterdir())
    r = subprocess.run(["systemd-analyze", "--user", "verify", *files], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    complaints = [l for l in (r.stdout + r.stderr).splitlines() if "cc-repo-1a2b3c4d" in l]
    assert not complaints, complaints


def test_unit_names_are_per_install_and_safe():
    out = _lib('cc_sup_unit_name "central-command-1a2b3c4d" api; echo; cc_sup_unit_name "my repo@x-99" cockpit')
    assert out.splitlines() == ["cc-central-command-1a2b3c4d-api.service", "cc-my_repo_x-99-cockpit.service"]


def test_the_windows_wrapper_is_tiny_and_hands_one_path_to_bash():
    out = subprocess.run(
        [_bash_exe(), "-c", ". ./supervise-lib.sh; cc_render_logon_cmd 'C:\\Program Files\\Git\\bin\\bash.exe' "
                            "'C:/Users/jdoe/AppData/Local/central-command/repo-1a2b3c4d/boot-at-logon.sh'"],
        cwd=SINGLE, capture_output=True,
    ).stdout
    assert out == (b'@echo off\r\n'
                   b'"C:\\Program Files\\Git\\bin\\bash.exe" -lc '
                   b'"bash \'C:/Users/jdoe/AppData/Local/central-command/repo-1a2b3c4d/boot-at-logon.sh\'"\r\n')
    # A `%` in a path is doubled: a .cmd file expands %NAME% even inside quotes.
    pct = subprocess.run([_bash_exe(), "-c", ". ./supervise-lib.sh; cc_render_logon_cmd 'C:\\b%x\\bash.exe' 'C:/s%1/r.sh'"],
                         cwd=SINGLE, capture_output=True).stdout
    assert b"C:\\b%%x\\bash.exe" in pct and b"C:/s%%1/r.sh" in pct


def test_the_retry_script_text():
    text = _lib('cc_render_logon_retry "/c/Users/jdoe/my repo/deploy/single" "/c/state/boot-at-logon.log"')
    lines = text.splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    assert "log=/c/state/boot-at-logon.log" in lines
    assert "attempts=10" in lines and "delay=60" in lines
    assert any(l.startswith("cd /c/Users/jdoe/my\\ repo/deploy/single || ") for l in lines), text
    assert "  ./setup.sh --accept-warnings >>\"$log\" 2>&1" in lines
    assert "  sleep \"$delay\"" in lines


@pytest.mark.parametrize("codes, attempts, final", [
    ([0], 1, 0),
    ([2], 1, 2),
    ([3], 1, 3),                 # waiting on the operator: retrying cannot help
    ([1, 1, 0], 3, 0),           # the podman machine came up on the third try
    ([1] * 12, 10, 1),           # ten attempts, then it gives up
    ([1, 5], 2, 5),              # a code it does not understand stops it
])
def test_the_retry_script_runs_the_resume_command_until_it_can_stop(tmp_path: Path, codes, attempts, final):
    """The REAL rendered script, run against a fake ./setup.sh that exits with
    the given codes in turn — with the delay rendered as 0."""
    single = tmp_path / "single"
    single.mkdir()
    seq = tmp_path / "codes"
    seq.write_text("\n".join(str(c) for c in codes) + "\n")
    _exe(single / "setup.sh",
         "#!/usr/bin/env bash\n"
         f'echo "args: $*"\n'
         f'n=$(head -1 "{seq}"); sed -i 1d "{seq}"; exit "$n"\n')
    log = tmp_path / "boot-at-logon.log"
    script = tmp_path / "boot-at-logon.sh"
    script.write_text(_lib(f'cc_render_logon_retry "{single}" "{log}" 10 0'))
    r = subprocess.run([_bash_exe(), str(script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == final, log.read_text()
    text = log.read_text()
    assert text.count("args: --accept-warnings") == attempts, text
    assert text.count(": ./setup.sh --accept-warnings") == attempts
    assert f"attempt {attempts}/10" in text
    if final == 3:
        assert "waiting on the operator; not retrying" in text
    if codes[:10] == [1] * 10:
        assert "giving up" in text


@pytest.mark.parametrize("rc, out, verdict", [
    (0, "Linger=yes", "PASS"),
    (0, "Linger=no", "FAIL"),
    (1, "Failed to get user: User ID 1000 is not logged in or lingering", "FAIL"),
    (1, "Failed to connect to bus: No such file or directory", "NA"),
    (1, "System has not been booted with systemd as init system (PID 1). Can't operate.", "NA"),
    (1, "", "NA"),
])
def test_the_linger_verdict(rc, out, verdict):
    got = _lib(f'cc_linger_verdict {rc} "$OUT" jdoe', env={"OUT": out})
    assert got.split(" ", 1)[0] == verdict, got
    if verdict == "FAIL":
        assert got.endswith("run: loginctl enable-linger jdoe"), got


# ── the tree input `@skills` ────────────────────────────────────────────────


def _fp(root: Path, reads: str) -> str:
    return _lib(f'cc_fingerprint "{root}/.env" "{reads}"')


def test_a_tree_input_moves_the_fingerprint_only_when_the_tree_moves(tmp_path: Path):
    (tmp_path / ".env").write_text("CC_API_PORT=8080\n")
    skill = tmp_path / "skills" / "jira"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: Jira\n---\nline one\nline two\n")
    (skill / "references" / "jql.md").write_text("jql\n")
    (tmp_path / "elsewhere.md").write_text("not in the tree\n")
    base = _fp(tmp_path, "CC_API_PORT,@skills")
    assert len(base) == 64

    assert _fp(tmp_path, "CC_API_PORT,@skills") == base, "stable"
    (tmp_path / "elsewhere.md").write_text("changed, but outside skills/\n")
    assert _fp(tmp_path, "CC_API_PORT,@skills") == base
    # CRLF-insensitive: a Windows checkout's line endings are not the release changing.
    (skill / "SKILL.md").write_bytes(b"---\r\nname: Jira\r\n---\r\nline one\r\nline two\r\n")
    assert _fp(tmp_path, "CC_API_PORT,@skills") == base

    (skill / "references" / "jql.md").write_text("jql, edited\n")
    edited = _fp(tmp_path, "CC_API_PORT,@skills")
    assert edited != base
    (skill / "references" / "jql.md").write_text("jql\n")
    assert _fp(tmp_path, "CC_API_PORT,@skills") == base

    new = tmp_path / "skills" / "zz-new"
    new.mkdir()
    (new / "SKILL.md").write_text("x\n")
    assert _fp(tmp_path, "CC_API_PORT,@skills") != base, "a new bundled folder re-runs the row"
    # A renamed file is a different tree even with the same bytes.
    (new / "SKILL.md").rename(new / "OTHER.md")
    assert _fp(tmp_path, "CC_API_PORT,@skills") not in (base, edited)


def test_a_tree_input_needs_no_git_and_an_absent_tree_is_stable(tmp_path: Path):
    (tmp_path / ".env").write_text("CC_API_PORT=8080\n")
    assert not (tmp_path / ".git").exists()
    a = _fp(tmp_path, "@skills")
    assert a == _fp(tmp_path, "@skills") and len(a) == 64
    assert _lib(f'cc_tree_hash "{tmp_path}" skills') == "absent"


def test_the_manifest_refuses_a_tree_input_that_leaves_the_checkout(tmp_path: Path):
    for bad in ("@../etc", "@/etc", "@"):
        steps = tmp_path / "steps.tsv"
        steps.write_text(f"boot\tx\trun\t-\t{bad}\t-\tp_always\tdoc\n")
        r = subprocess.run([_bash_exe(), "-c", f'. ../env-lib.sh; . ./ledger-lib.sh; cc_steps_load "{steps}"'],
                           cwd=SINGLE, capture_output=True, text=True)
        assert r.returncode == 1 and "tree input" in r.stderr, (bad, r.stderr)


def test_the_plan_names_a_tree_input_in_words():
    out = _lib('''
      PHASE_VERDICT=run PHASE_CODE=inputs PHASE_STEP=boot/skills-imported
      PHASE_READS=CC_API_PORT,@skills PHASE_SAME=0 PHASE_DETAIL="" PHASE_PROBE=p
      cc_phase_plan_text boot 2.57.0
    ''')
    assert out == ("boot: WILL RUN — inputs changed: boot/skills-imported reads one of "
                   "CC_API_PORT, the files under skills/")


def test_the_skills_row_reads_the_tree():
    rows = [l.split("\t") for l in (SINGLE / "steps.tsv").read_text(encoding="utf-8").splitlines()
            if l.startswith("boot\tskills-imported\t")]
    assert rows and "@skills" in rows[0][4].split(","), rows


# ── make-secrets.sh generates the runner token, and never overwrites one ────


def _secrets_tree(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    shutil.copytree(ROOT / "deploy" / "single", repo / "deploy" / "single", ignore=_DEBRIS)
    shutil.copy2(ROOT / "deploy" / "env-lib.sh", repo / "deploy" / "env-lib.sh")
    shutil.copy2(ROOT / ".env.example", repo / ".env")
    (tmp_path / "state").mkdir()
    _set(repo / ".env", {"CC_STATE_DIR": str(tmp_path / "state")})
    return repo


def _get(path: Path, key: str) -> str:
    out = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            out = line.split("=", 1)[1]
    return out


def _make_secrets(repo: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CC_", "N8N_", "LITELLM_"))}
    env["HOME"] = str(repo.parent)
    return subprocess.run([_bash_exe(), "make-secrets.sh"], cwd=repo / "deploy" / "single",
                          capture_output=True, text=True, env=env, timeout=60)


def test_make_secrets_generates_the_runner_token(tmp_path: Path):
    repo = _secrets_tree(tmp_path)
    assert _get(repo / ".env", "CC_SANDBOX_RUNNER_TOKEN") == ""
    r = _make_secrets(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    tok = _get(repo / ".env", "CC_SANDBOX_RUNNER_TOKEN")
    assert len(tok) == 48 and all(c in "0123456789abcdef" for c in tok), tok
    assert "generated CC_SANDBOX_RUNNER_TOKEN" in r.stdout
    assert tok not in r.stdout + r.stderr, "a generated value is never printed"


def test_make_secrets_never_overwrites_a_runner_token_that_is_set(tmp_path: Path):
    repo = _secrets_tree(tmp_path)
    _set(repo / ".env", {"CC_SANDBOX_RUNNER_TOKEN": "operator-chose-this"})
    r = _make_secrets(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _get(repo / ".env", "CC_SANDBOX_RUNNER_TOKEN") == "operator-chose-this"
    assert "generated CC_SANDBOX_RUNNER_TOKEN" not in r.stdout


def test_the_report_redacts_the_runner_token_by_its_glob():
    rows = [l.split("\t") for l in (SINGLE / "redact.tsv").read_text(encoding="utf-8").splitlines()
            if l.startswith("key\t")]
    import fnmatch
    assert any(fnmatch.fnmatchcase("CC_SANDBOX_RUNNER_TOKEN", r[1]) for r in rows), rows
