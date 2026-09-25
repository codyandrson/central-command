"""podman-compose 1.6.0 is a FLOOR, and setup.sh decides it in one function.

The work site (air-gapped Windows, 2026-09-25) ran podman-compose 1.5.0. Two
things this profile relies on first shipped in 1.6.0 (2026-06-03): ``up
--wait`` — the deploy phases wait on ``compose.yaml``'s healthchecks instead of
polling — and the config-hash change that made a second ``up -d`` idempotent;
under 1.5.0 every re-run died with ``container name ... is already in use``.
``check``'s host section reported PASS on that provider, so the gate passed a
substrate that could not run the install.

The decision is ``compose_version_floor_ok``: pure, prints nothing, returns
0 (ok) / 1 (too old) / 2 (unparseable) and leaves what it read in
``COMPOSE_FLAVOUR`` / ``COMPOSE_VERSION``. ``setup.sh`` ends in ``main "$@"``
so it cannot be sourced — the block is lifted out and run, the same way the CA
check is exercised in ``tests/test_single_airgap_seams.py``.

The parsing trap this pins: ``podman compose version`` prints THREE lines (the
external-provider banner, ``podman version``, then ``podman-compose version``),
so neither ``head -1`` nor "the first number on the page" is the answer.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

# The bash that can run this repo's shell scripts. On Windows a bare "bash" is
# System32's WSL launcher (its error reads "The RPC call contains a handle
# that differs from the declared handle type") — 26 tests failed that way on
# the 2026-09-25 testbed run, all of them in files that shelled out with the
# bare name. `update._bash()` resolves Git Bash from git's own install.
from central_command.api.update import _bash as _resolve_bash  # noqa: E402
BASH = _resolve_bash() or "bash"


ROOT = pathlib.Path(__file__).resolve().parent.parent
SETUP = ROOT / "deploy" / "single" / "setup.sh"

# The real outputs, as measured on the work site's podman 5.8.3 (2026-09-25).
PODMAN_THREE_LINE = (
    ">>>> Executing external compose provider "
    '"/usr/local/bin/podman-compose". Please see podman-compose(1) for how '
    "to disable this message. <<<<\n"
    "podman version 5.8.3\n"
    "podman-compose version 1.6.0\n"
)
PODMAN_THREE_LINE_OLD = PODMAN_THREE_LINE.replace("1.6.0", "1.5.0")
DOCKER = "Docker Compose version v2.39.1\n"


def _floor_block() -> str:
    text = SETUP.read_text(encoding="utf-8")
    start = text.index("# ── the compose FLOOR")
    end = text.index("\n# Every compose call goes through here", start)
    return text[start:end]


def floor(provider: str, output: str) -> tuple[int, str, str]:
    """-> (return code, flavour, version)."""
    script = f"""
set -uo pipefail
{_floor_block()}
compose_version_floor_ok "$1" "$2"; rc=$?
printf '%s|%s|%s\\n' "$rc" "$COMPOSE_FLAVOUR" "$COMPOSE_VERSION"
"""
    r = subprocess.run([BASH, "-c", script, "_", provider, output],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    # PURE: the function itself prints nothing — only the harness's one line.
    assert len(r.stdout.strip().splitlines()) == 1, r.stdout
    rc, flavour, version = r.stdout.strip().split("|")
    return int(rc), flavour, version


def test_podman_compose_1_5_0_is_too_old():
    assert floor("podman", "podman-compose version 1.5.0\n") == (1, "podman-compose", "1.5.0")


def test_podman_compose_1_6_0_is_the_floor_itself():
    assert floor("podman", "podman-compose version 1.6.0\n") == (0, "podman-compose", "1.6.0")


def test_a_later_podman_compose_compares_numerically_not_lexically():
    """1.10.2 > 1.6.0 — a string compare says otherwise, which is why
    version_ge exists at all."""
    assert floor("podman", "podman-compose version 1.10.2\n") == (0, "podman-compose", "1.10.2")


def test_docker_compose_has_no_floor():
    assert floor("docker", DOCKER) == (0, "docker-compose", "2.39.1")


def test_garbage_is_unparseable_and_never_a_silent_pass():
    rc, flavour, version = floor("podman", "podman: 'compose' is not a podman command\n")
    assert rc == 2 and version == ""


def test_the_real_three_line_podman_output_reads_the_provider_not_the_banner():
    """`podman version 5.8.3` is on line 2 and the banner is on line 1: a
    `head -1` or a first-number parse would report 5.8.3 and pass anything."""
    assert floor("podman", PODMAN_THREE_LINE) == (0, "podman-compose", "1.6.0")
    assert floor("podman", PODMAN_THREE_LINE_OLD) == (1, "podman-compose", "1.5.0")


@pytest.mark.parametrize(
    "a,b,expected",
    [("1.6.0", "1.6.0", 0), ("1.10.2", "1.6.0", 0), ("1.5.0", "1.6.0", 1),
     ("2.0", "1.6.0", 0), ("1.6", "1.6.0", 0), ("1.6.0rc1", "1.6.0", 0)],
)
def test_version_ge(a, b, expected):
    script = f"""
set -uo pipefail
{_floor_block()}
version_ge "$1" "$2"
"""
    r = subprocess.run([BASH, "-c", script, "_", a, b], capture_output=True, text=True)
    assert r.stdout == "", r.stdout
    assert r.returncode == expected, f"version_ge {a} {b} -> {r.returncode}"


def test_the_host_section_reports_the_floor_as_its_own_check_line():
    """One provider line and one VERSION line: an operator who reads
    `compose-provider: podman compose` learns nothing about 1.5.0."""
    text = SETUP.read_text(encoding="utf-8")
    assert 'pass "compose-version"' in text
    assert 'fail "compose-version"' in text
    assert 'warn "compose-version"' in text
    # The FAIL has to name BOTH the reason and the way out.
    assert "up --wait" in text and "config-hash" in text
    assert "podman-compose==1.6.0" in text
