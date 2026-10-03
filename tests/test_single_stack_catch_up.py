"""An update DEPLOYS what it rebuilt: the image catch-up after `compose up` (v2.57.0, P3).

Since this release a changed Dockerfile REBUILDS the local image under its
fixed tag (`tests/test_single_local_image_label.py`). But `compose up -d`
converges on the SERVICE DEFINITION: podman-compose 1.6.0 — this profile's
floor — recreates a container only when the hash of its service dict changed
(`compose_up` in podman_compose.py at the v1.6.0 tag), and the image ID behind
an unchanged ref is not in that dict. So the rebuilt image, or a re-pulled
third-party tag, sat in local storage while `up` reported the OLD container
healthy — and `update.sh` did not even run `stack`.

`setup.sh` now asks, per enabled service, whether the container runs the image
ID its configured ref resolves to NOW (`image_drift`), recreates exactly the
ones that do not (`catch_up_images`, after `up` in both the stack and the llm
phase), and the `stack/up-stack` and `llm/up-litellm` probes ask the same
question. A stateful service is recreated only because its data is on a NAMED
volume — checked against compose.yaml every time, not assumed.

These run the REAL setup.sh functions in a temp copy of the tree (the
append-a-hook pattern of tests/test_single_local_image_label.py) against a stub
`podman` whose `ps`, `images` and `compose` read and write small text files:
no real podman, no compose, nothing in the checkout.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.installer_source import (
    drives_installer,
    installer_source,
    python_shim,
    run_driver,
    with_stub_path,
    write_lf,
)

ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT / "deploy" / "single"
COMPOSE = SINGLE / "compose.yaml"

_DEBRIS = shutil.ignore_patterns("NUL", "nul", ".env", ".env.*", "__pycache__", "*.pyc")

OLD = "a" * 64
NEW = "b" * 64


def _bash() -> str:
    from central_command.api.update import _bash as resolve

    b = resolve()
    if not b:
        pytest.skip("no usable bash on this host")
    return b


# The stub. `ps` prints <state>/containers ("<service> <image-id>" per line, the
# shape of the real call's --format); `images` prints <state>/images ("<ref>
# <digest> <id>"); `compose up … --force-recreate … <services>` gives each named
# service the id in <state>/recreate/<service> (what a real recreate would land
# on) — unless <state>/fail-recreate exists, or no such file was prepared (a
# recreate that "succeeded" without changing anything). <state>/fail-ps makes
# `ps` fail. Every call is logged.
_PODMAN = r'''#!/usr/bin/env bash
echo "podman $*" >> "$STUB/log"
case "$1" in
  ps)     [[ -f "$STUB/fail-ps" ]] && exit 125
          cat "$STUB/containers" 2>/dev/null; exit 0 ;;
  images) cat "$STUB/images" 2>/dev/null; exit 0 ;;
  compose)
    [[ "${2:-}" == version ]] && { echo "podman-compose version 1.6.0"; exit 0; }
    [[ " $* " == *" up "* ]] || exit 0
    [[ " $* " == *" --force-recreate "* ]] || exit 0
    [[ -f "$STUB/fail-recreate" ]] && exit 1
    svcs=(); after=0
    for a in "$@"; do
      (( after )) && svcs+=("$a")
      [[ "$a" == --wait ]] && after=1
    done
    for s in "${svcs[@]}"; do
      [[ -f "$STUB/recreate/$s" ]] || continue
      grep -v "^$s " "$STUB/containers" > "$STUB/containers.new"
      echo "$s $(cat "$STUB/recreate/$s")" >> "$STUB/containers.new"
      mv "$STUB/containers.new" "$STUB/containers"
    done
    exit 0 ;;
esac
exit 0
'''


class Stack:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.repo = tmp / "repo"
        self.stub = tmp / "stub"
        self.single = self.repo / "deploy" / "single"
        self.repo.mkdir()
        self.stub.mkdir()
        (self.stub / "recreate").mkdir()
        (tmp / "state").mkdir()
        (tmp / "home").mkdir()
        shutil.copytree(ROOT / "deploy", self.repo / "deploy", ignore=_DEBRIS)
        for f in (".env.example", "VERSION", ".gitignore"):
            shutil.copy2(ROOT / f, self.repo / f)
        self.env_lines = {
            # Forward slashes: setup.sh SOURCES .env (bash drops the backslashes).
            "CC_STATE_DIR": (tmp / "state").as_posix(),
            "CC_ENABLE_N8N": "0", "CC_ENABLE_CRAWLER": "1", "CC_ENABLE_SPEECH": "0",
            "CC_ENABLE_SANDBOX": "0",
            "CC_IMG_POSTGRES": "docker.io/library/postgres:16",
            "CC_IMG_REDIS": "docker.io/library/redis:7-alpine",
            "CC_IMG_NEO4J": "docker.io/library/neo4j:5.26.2",
            "CC_IMG_BERRIAI_LITELLM_DATABASE": "ghcr.io/berriai/litellm-database:main-stable",
        }
        self.write_env()
        setup = self.single / "setup.sh"
        text = setup.read_text(encoding="utf-8")
        tail = 'main "$@"'
        assert text.rstrip().endswith(tail)
        # __drift: image_drift, with the verdict it leaves in DRIFT/DRIFT_KIND
        # printed, so a test can read which services and why.
        hook = ('__drift() { local rc=0 s; image_drift "$@" || rc=$?; '
                'for s in "${DRIFT[@]-}"; do [[ -n "$s" ]] && echo "DRIFT $s ${DRIFT_KIND[$s]}"; done; '
                'return $rc; }\n'
                'if [[ -n "${__CALL:-}" ]]; then "$@"; exit $?; fi\n')
        write_lf(setup, text.rstrip()[: -len(tail)] + hook + tail + "\n")
        b = tmp / "bin"
        b.mkdir()
        write_lf(b / "podman", _PODMAN, mode=0o755)
        python_shim(b)   # $PY must be a real interpreter (no python3 in Git's /usr/bin)
        # The proxy answers /health/liveliness: p_litellm_live is not under test.
        write_lf(b / "curl", "#!/usr/bin/env bash\nexit 0\n", mode=0o755)
        # Every enabled service, on the image its ref names, everything current.
        self.refs = {
            "postgres": "docker.io/library/postgres:16",
            "litellm-db": "docker.io/library/postgres:16",
            "litellm-redis": "docker.io/library/redis:7-alpine",
            "litellm": "ghcr.io/berriai/litellm-database:main-stable",
            "neo4j": "docker.io/library/neo4j:5.26.2",
            "graphiti": "localhost/cc-graphiti:1.0.2-anthropic",
            "crawler": "localhost/cc-crawler:1",
        }
        self.containers = {svc: OLD for svc in self.refs}
        self.images = {ref: ("sha256:" + "d" * 64, OLD) for ref in set(self.refs.values())}
        self.sync()

    def write_env(self) -> None:
        env = (ROOT / ".env.example").read_text(encoding="utf-8")
        env += "\n" + "".join(f"{k}={v}\n" for k, v in self.env_lines.items())
        write_lf(self.repo / ".env", env)

    def sync(self) -> None:
        write_lf(self.stub / "containers",
                 "".join(f"{s} {i}\n" for s, i in self.containers.items()))
        write_lf(self.stub / "images",
                 "".join(f"{r} {d} {i}\n" for r, (d, i) in self.images.items()))

    def rebuild(self, ref: str, new: str = NEW) -> None:
        """A new image behind an UNCHANGED ref: a fetch rebuild, or a re-pull."""
        d, _ = self.images[ref]
        self.images[ref] = (d, new)
        self.sync()
        for svc, r in self.refs.items():
            if r == ref:
                write_lf(self.stub / "recreate" / svc, new)

    def container_ids(self) -> dict[str, str]:
        out = {}
        for line in (self.stub / "containers").read_text().splitlines():
            if line.strip():
                s, i = line.split()
                out[s] = i
        return out

    def log(self) -> str:
        f = self.stub / "log"
        return f.read_text() if f.exists() else ""

    def recreates(self) -> list[list[str]]:
        out = []
        for line in self.log().splitlines():
            if " up " in line and "--force-recreate" in line:
                out.append(line.split("--wait", 1)[1].split())
        return out

    def call(self, *args: str, **extra: str) -> subprocess.CompletedProcess:
        env = with_stub_path(
            {"PATH": os.pathsep.join([str(self.tmp / "bin"), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
             "HOME": str(self.tmp / "home"), "XDG_STATE_HOME": str(self.tmp / "home" / "state"),
             "STUB": self.stub.as_posix(), "LANG": "C.UTF-8", "__CALL": "1", **extra},
            self.tmp / "bin")
        return run_driver([_bash(), str(self.single / "setup.sh"), *args], cwd=self.single, env=env)

    def drift(self, *services: str) -> tuple[int, dict[str, str]]:
        r = self.call("__drift", *services)
        kinds = {l.split()[1]: l.split()[2] for l in r.stdout.splitlines() if l.startswith("DRIFT ")}
        return r.returncode, kinds


# Skipped on Windows until the 2026-10-02 testbed run's second pass ("Git Bash
# prepends /usr/bin to PATH"): with_stub_path now puts the stub podman FIRST,
# and the stubs and their data are written LF.
@pytest.fixture
def stack(tmp_path: Path) -> Stack:
    return Stack(tmp_path)


def _lines(out: str, prefix: str) -> list[str]:
    return [l for l in out.splitlines() if l.startswith(prefix)]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ── compose.yaml is the one list, and the stateful services are safe ────────


def _yaml() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _table(stack: Stack) -> dict[str, list[str]]:
    script = ('stack_load || exit 1; echo "PROJECT $STACK_PROJECT"; '
              'for s in "${STACK_SVCS[@]}"; do printf "SVC %s\\t%s\\t%s\\t%s\\n" "$s" '
              '"${STACK_PROFILE[$s]}" "${STACK_IMAGE[$s]}" "${STACK_VOLS[$s]}"; done; '
              'for v in "${!STACK_NAMED_VOL[@]}"; do echo "VOL $v"; done')
    r = stack.call("eval", script)
    assert r.returncode == 0, r.stdout + r.stderr
    out: dict[str, list[str]] = {"svc": [], "vol": [], "project": []}
    for line in r.stdout.splitlines():
        kind, _, rest = line.partition(" ")
        out[{"SVC": "svc", "VOL": "vol", "PROJECT": "project"}[kind]].append(rest)
    return out


@drives_installer
def test_the_reader_sees_what_compose_sees(stack: Stack):
    """A second table of services would drift from compose.yaml; this reader
    must agree with a real YAML parser on every service, image, profile and
    named volume, or a new service escapes the catch-up."""
    y = _yaml()
    t = _table(stack)
    assert t["project"] == [y["name"]]
    got = {}
    for row in t["svc"]:
        svc, prof, img, vols = row.split("\t")
        got[svc] = (prof, img, vols)
    assert list(got) == list(y["services"]), "service list (or its order) differs"
    for svc, spec in y["services"].items():
        prof, img, vols = got[svc]
        assert img == spec["image"], svc
        assert prof == (",".join(spec["profiles"]) if spec.get("profiles") else "-"), svc
        want = " ".join(spec.get("volumes", [])) or "-"
        assert vols == want, svc
    assert sorted(t["vol"]) == sorted(y["volumes"])


@drives_installer
def test_a_crlf_checkout_reads_the_same(stack: Stack):
    """A Windows checkout may hold compose.yaml in CRLF; a stray CR in an image
    ref would make every service read `noimage` and the probe never pass."""
    before = _table(stack)
    compose = stack.single / "compose.yaml"
    compose.write_bytes(compose.read_bytes().replace(b"\n", b"\r\n"))
    assert _table(stack) == before
    assert stack.drift() == (0, {})


@drives_installer
def test_every_stateful_service_keeps_its_data_on_a_named_volume(stack: Stack):
    """The recreate is safe for a database ONLY because its data path is a named
    volume. Pinned both ways: every service that mounts a named volume is in
    STACK_STATEFUL (so a new stateful service must declare its data path), and
    every one listed has that path on a named volume in compose.yaml."""
    y = _yaml()
    named = set(y["volumes"])
    r = stack.call("eval", 'for e in "${STACK_STATEFUL[@]}"; do echo "$e"; done')
    stateful = dict(l.split(" ", 1) for l in r.stdout.splitlines())
    with_volume = {svc for svc, spec in y["services"].items()
                   if any(v.split(":", 1)[0] in named for v in spec.get("volumes", []))}
    assert with_volume == set(stateful), (
        "a service mounts a named volume but STACK_STATEFUL does not say where its data "
        f"lives (or lists one that has none): {sorted(with_volume ^ set(stateful))}"
    )
    for svc, path in stateful.items():
        mounts = [v for v in y["services"][svc]["volumes"] if v.split(":")[1] == path]
        assert mounts and mounts[0].split(":", 1)[0] in named, f"{svc}: {path} is not on a named volume"
        assert stack.call("stack_data_on_volume", svc).returncode == 0, svc
    # A stateless service is always safe to recreate.
    assert stack.call("stack_data_on_volume", "graphiti").returncode == 0


def _expand(stack: Stack, ref: str, **extra: str):
    """compose_expand <ref>, the ref handed over in the ENVIRONMENT: the MSYS
    runtime globs the argv a native parent passes, and an unquoted `${X:-y}`
    reached bash as `$X:-y` (the 2026-10-02 testbed run's second pass)."""
    return stack.call("eval", 'compose_expand "$__REF"', __REF=ref, **extra)


@drives_installer
def test_compose_expand_follows_compose_interpolation(stack: Stack):
    r = _expand(stack, "${CC_IMG_POSTGRES:-docker.io/library/postgres:16}")
    assert r.stdout == "docker.io/library/postgres:16"
    stack.env_lines["CC_IMG_POSTGRES"] = "mirror.example.com/library/postgres:16.9"
    stack.write_env()
    r = _expand(stack, "${CC_IMG_POSTGRES:-docker.io/library/postgres:16}")
    assert r.stdout == "mirror.example.com/library/postgres:16.9"
    r = _expand(stack, "localhost/cc-graphiti:${CC_GRAPHITI_TAG:-1.0.2-anthropic}")
    assert r.stdout == "localhost/cc-graphiti:1.0.2-anthropic"
    r = _expand(stack, "localhost/cc-graphiti:${CC_GRAPHITI_TAG:-1.0.2-anthropic}",
                CC_GRAPHITI_TAG="9.9-test")
    assert r.stdout == "localhost/cc-graphiti:9.9-test", "the shell's value wins, as in a phase"


# ── the question ────────────────────────────────────────────────────────────


@drives_installer
def test_everything_current_is_no_drift_in_two_podman_calls(stack: Stack):
    rc, kinds = stack.drift()
    assert rc == 0 and kinds == {}
    calls = [l for l in stack.log().splitlines() if l.startswith("podman ")]
    assert len(calls) == 2, f"the probe must cost two podman calls, not one per service: {calls}"
    ps = next(l for l in calls if l.startswith("podman ps"))
    assert "label=com.docker.compose.project=central-command" in ps
    assert '{{index .Labels "com.docker.compose.service"}} {{.ImageID}}' in ps
    assert any(l.startswith("podman images --no-trunc") for l in calls)


@drives_installer
def test_a_rebuilt_local_image_behind_its_fixed_tag_is_drift(stack: Stack):
    stack.rebuild("localhost/cc-graphiti:1.0.2-anthropic")
    rc, kinds = stack.drift()
    assert rc == 1 and kinds == {"graphiti": "differs"}, kinds


@drives_installer
def test_a_re_pulled_third_party_tag_is_drift_for_every_service_on_it(stack: Stack):
    stack.rebuild("docker.io/library/postgres:16")
    rc, kinds = stack.drift()
    assert rc == 1 and kinds == {"postgres": "differs", "litellm-db": "differs"}, kinds


@drives_installer
def test_absent_container_absent_image_and_unknown_service(stack: Stack):
    del stack.containers["neo4j"]
    del stack.images["localhost/cc-crawler:1"]
    stack.sync()
    rc, kinds = stack.drift()
    assert rc == 1 and kinds == {"neo4j": "absent", "crawler": "noimage"}, kinds
    rc, kinds = stack.drift("no-such-service")
    assert rc == 1 and kinds == {"no-such-service": "undeclared"}


@drives_installer
def test_a_disabled_service_is_not_asked_about(stack: Stack):
    """speech and n8n are off here and have no containers; crawler goes off too."""
    del stack.containers["crawler"]
    stack.sync()
    assert stack.drift()[1] == {"crawler": "absent"}
    stack.env_lines["CC_ENABLE_CRAWLER"] = "0"
    stack.write_env()
    assert stack.drift() == (0, {})


@drives_installer
def test_id_spellings_and_a_digest_pinned_ref_agree(stack: Stack):
    """docker's `sha256:`-prefixed IDs, podman's bare ones, a truncated one, and
    an operator pin by digest all resolve to the same answer."""
    stack.containers["postgres"] = "sha256:" + OLD
    stack.containers["neo4j"] = OLD[:12]
    pin = "docker.io/library/redis@sha256:" + "e" * 64
    stack.env_lines["CC_IMG_REDIS"] = pin
    stack.write_env()
    stack.images["docker.io/library/redis:<none>"] = ("sha256:" + "e" * 64, "sha256:" + OLD)
    stack.sync()
    assert stack.drift() == (0, {})


@drives_installer
def test_podman_that_cannot_be_asked_is_never_current(stack: Stack):
    write_lf(stack.stub / "fail-ps", "")
    rc, _ = stack.drift()
    assert rc == 2
    assert stack.call("p_up_litellm").returncode != 0


# ── the probes ──────────────────────────────────────────────────────────────


@drives_installer
def test_the_up_litellm_probe_reads_false_on_a_stale_trio_member(stack: Stack):
    assert stack.call("p_up_litellm").returncode == 0
    stack.rebuild("docker.io/library/redis:7-alpine")
    assert stack.call("p_up_litellm").returncode != 0, (
        "a done llm/up-litellm row hid a container on an image its ref no longer names"
    )


@drives_installer
def test_the_up_stack_probe_reads_false_on_any_stale_container(stack: Stack):
    """The whole p_up_stack: the proxy (stub curl), two listeners, and now the
    images. A stale graphiti behind healthy ports used to read done."""
    socks = []
    for key in ("CC_PG_PORT", "CC_GRAPHITI_PORT"):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen()
        socks.append(s)
        stack.env_lines[key] = str(s.getsockname()[1])
    stack.write_env()
    try:
        assert stack.call("p_up_stack").returncode == 0
        stack.rebuild("localhost/cc-crawler:1")
        assert stack.call("p_up_stack").returncode != 0
    finally:
        for s in socks:
            s.close()


def test_steps_tsv_names_the_drift_aware_probes():
    rows = {(l.split("\t")[0], l.split("\t")[1]): l.split("\t")
            for l in (SINGLE / "steps.tsv").read_text(encoding="utf-8").splitlines()
            if l and not l.startswith("#")}
    assert rows[("llm", "up-litellm")][6] == "p_up_litellm"
    assert rows[("stack", "up-stack")][6] == "p_up_stack"
    src = installer_source()
    body = src[src.index("\np_up_stack() {"):]
    body = body[: body.index("\n}\n")]
    assert "image_drift" in body


# ── the catch-up ────────────────────────────────────────────────────────────


@drives_installer
def test_nothing_stale_recreates_nothing(stack: Stack):
    r = stack.call("catch_up_images", "up-stack")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _lines(r.stdout, "PASS up-stack: every container runs the image its ref resolves to now")
    assert stack.recreates() == []


@drives_installer
def test_a_stale_local_image_is_recreated_by_name_and_said_so(stack: Stack):
    stack.rebuild("localhost/cc-graphiti:1.0.2-anthropic")

    r = stack.call("catch_up_images", "up-stack")

    assert r.returncode == 0, r.stdout + r.stderr
    assert stack.recreates() == [["graphiti"]], stack.log()
    up = next(l for l in stack.log().splitlines() if "--force-recreate" in l)
    # compose's own force-recreate, scoped to the one service, with the
    # enabled profiles, waiting on the healthchecks as `up --wait` does.
    assert "--env-file" in up and "--profile crawler" in up
    assert "up -d --force-recreate --no-deps --wait graphiti" in up
    passes = _lines(r.stdout, "PASS up-stack: graphiti recreated")
    assert len(passes) == 1, r.stdout
    assert OLD[:12] in passes[0] and NEW[:12] in passes[0]
    assert "localhost/cc-graphiti:1.0.2-anthropic now resolves to" in passes[0]
    assert stack.container_ids()["graphiti"] == NEW
    assert stack.drift() == (0, {})


@drives_installer
def test_stateful_services_on_named_volumes_are_recreated_in_one_call(stack: Stack):
    """A re-pulled postgres:16 moves the spine AND the LiteLLM database: both
    keep their data on named volumes, so both are recreated — together."""
    stack.rebuild("docker.io/library/postgres:16")
    r = stack.call("catch_up_images", "up-stack")
    assert r.returncode == 0, r.stdout + r.stderr
    assert [sorted(x) for x in stack.recreates()] == [["litellm-db", "postgres"]]
    assert len(_lines(r.stdout, "PASS up-stack: postgres recreated")) == 1
    assert len(_lines(r.stdout, "PASS up-stack: litellm-db recreated")) == 1


@drives_installer
def test_the_llm_phase_catches_up_only_its_trio(stack: Stack):
    stack.rebuild("docker.io/library/postgres:16")
    r = stack.call("catch_up_images", "up-litellm", "litellm-db", "litellm-redis", "litellm")
    assert r.returncode == 0, r.stdout + r.stderr
    assert stack.recreates() == [["litellm-db"]], "the llm phase touched a service it does not own"
    assert _lines(r.stdout, "PASS up-litellm: litellm-db recreated")
    assert stack.call("p_up_litellm").returncode == 0
    # ...and the spine is the stack phase's, which still sees it.
    assert stack.drift()[1] == {"postgres": "differs"}


@drives_installer
def test_a_stateful_service_without_a_named_volume_is_reported_never_recreated(stack: Stack):
    compose = stack.single / "compose.yaml"
    text = compose.read_text(encoding="utf-8")
    assert "      - pgdata:/var/lib/postgresql/data\n" in text
    write_lf(compose, text.replace("      - pgdata:/var/lib/postgresql/data\n",
                                    "      - ./pgdata:/var/lib/postgresql/data\n"))
    stack.rebuild("docker.io/library/postgres:16")

    r = stack.call("catch_up_images", "up-stack")

    assert r.returncode == 1, r.stdout + r.stderr
    fails = _lines(r.stdout, "FAIL up-stack: postgres")
    assert len(fails) == 1 and "NOT recreated" in fails[0] and "/var/lib/postgresql/data" in fails[0]
    # litellm-db's data IS on its named volume: it is still caught up.
    assert stack.recreates() == [["litellm-db"]]
    assert stack.container_ids()["postgres"] == OLD


@drives_installer
def test_absent_and_unfetched_are_fails_that_name_the_move(stack: Stack):
    del stack.containers["neo4j"]
    del stack.images["localhost/cc-crawler:1"]
    stack.sync()
    r = stack.call("catch_up_images", "up-stack")
    assert r.returncode == 1
    assert any("crawler's image localhost/cc-crawler:1 is not in local storage" in l
               and "./setup.sh (it resumes at fetch" in l for l in _lines(r.stdout, "FAIL up-stack:")), r.stdout
    assert any("neo4j has no container" in l for l in _lines(r.stdout, "FAIL up-stack:")), r.stdout
    assert stack.recreates() == []


@drives_installer
def test_a_failed_or_ineffective_recreate_is_a_fail_never_a_pass(stack: Stack):
    stack.rebuild("localhost/cc-graphiti:1.0.2-anthropic")
    write_lf(stack.stub / "fail-recreate", "")
    r = stack.call("catch_up_images", "up-stack")
    assert r.returncode == 1 and _lines(r.stdout, "FAIL up-stack: could not recreate graphiti"), r.stdout
    assert not _lines(r.stdout, "PASS up-stack: graphiti")

    (stack.stub / "fail-recreate").unlink()
    (stack.stub / "recreate" / "graphiti").unlink()       # "succeeds", changes nothing
    r = stack.call("catch_up_images", "up-stack")
    assert r.returncode == 1
    assert _lines(r.stdout, "FAIL up-stack: graphiti was recreated and still does not run"), r.stdout


@drives_installer
def test_podman_that_cannot_be_asked_fails_the_catch_up(stack: Stack):
    write_lf(stack.stub / "fail-ps", "")
    r = stack.call("catch_up_images", "up-stack")
    assert r.returncode == 1 and _lines(r.stdout, "FAIL up-stack: could not compare"), r.stdout


# ── where it runs ───────────────────────────────────────────────────────────


def _body(name: str) -> str:
    src = installer_source()
    start = src.index(f"\n{name}() {{")
    return src[start: src.index("\n}\n", start)]


def test_both_phases_catch_up_right_after_their_compose_up():
    stack_body = _body("phase_stack")
    up = stack_body.index('up -d --wait || return 1')
    assert stack_body.index('catch_up_images "up-stack" || return 1') > up
    llm_body = _body("phase_llm")
    up = llm_body.index("compose up -d --wait litellm || return 1")
    catch = llm_body.index('catch_up_images "up-litellm" litellm-db litellm-redis litellm || return 1')
    assert up < catch < llm_body.index('"litellm-live"')


def test_update_deploys_the_stack_between_llm_and_app():
    src = (SINGLE / "update.sh").read_text(encoding="utf-8")
    start = src.index("\ndeploy_current_tree() {")
    body = src[start: src.index("\n}\n", start)]
    phases = re.findall(r"^\s*deploy_phase (\w+) ", body, flags=re.M)
    assert phases == ["fetch", "llm", "stack", "app", "verify"], phases
