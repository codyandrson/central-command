"""What the `machine` phase would write, tested where no machine exists.

A podman machine is a Windows/macOS thing, so the WRITER can only be exercised
on the Windows testbed. Its DECISIONS are pure text transformations of `.env`
— which mirror, which host is insecure, what the drop-in says, whether a
restart is due, and whether a proxy value can leak into a printed diff — and
those live in `deploy/single/machine-lib.sh` precisely so they can be tested
here, on Linux, by invoking bash (design record
``2026-09-23-airgap-check-configure-setup-design.md``, D4).

The syntax the renderers emit is doc-verified against
``containers-registries.conf(5)``, ``containers-registries.conf.d(5)`` and
``containers.conf(5)`` — see the file's own header. These tests pin the SHAPE
(a drop-in that podman would accept, and nothing more) and the rules that
matter operationally: a mirror onto itself is not written, an operator's image
PIN host is marked insecure too, and a proxy value is never printed.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIB = ROOT / "deploy" / "single" / "machine-lib.sh"
ENV_LIB = ROOT / "deploy" / "env-lib.sh"


def run(func: str, env: dict[str, str]) -> str:
    """Call one machine-lib function with a clean environment."""
    # env -i equivalent: only what the test sets, plus PATH so coreutils exist.
    # cwd= + basename, not a full path: on Windows the first `bash` on PATH may
    # be WSL's launcher, which cannot open a Windows path.
    r = subprocess.run(
        ["bash", "-c", f". ./machine-lib.sh; {func}"],
        cwd=LIB.parent,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", **env},
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_no_mirrors_renders_nothing():
    """Nothing configured must produce an EMPTY file, not an empty drop-in.

    The caller reads empty as "there is nothing to declare" — writing a
    commented-out husk into the machine would make every later diff noisy.
    """
    assert run("cc_render_registries_conf", {}) == ""
    assert run("cc_render_registries_conf", {"CC_REGISTRY_DOCKERIO": "docker.io"}) == "", (
        "a seam set to the CANONICAL host is not a mirror — mirroring a host "
        "onto itself is a loop, not a configuration"
    )


def test_one_mirror_renders_a_registry_and_a_mirror_table():
    out = run("cc_render_registries_conf", {"CC_REGISTRY_DOCKERIO": "mirror.corp.example"})
    assert '[[registry]]' in out
    assert 'prefix = "docker.io"' in out
    assert '[[registry.mirror]]' in out
    assert 'location = "mirror.corp.example"' in out
    # Verification is ON by default, so nothing may say otherwise.
    assert "insecure" not in out
    # Only the configured seam appears; the other two registries are untouched.
    assert "ghcr.io" not in out and "mcr.microsoft.com" not in out


def test_a_url_shaped_seam_is_reduced_to_a_host():
    """A discovery mirror is a URL; registries.conf takes a host."""
    out = run("cc_render_registries_conf",
              {"CC_REGISTRY_GHCR": "https://mirror.corp.example/v2/"})
    assert 'location = "mirror.corp.example"' in out
    assert "https://" not in out


def test_insecure_marks_the_prefix_the_mirror_and_the_mirror_host_directly():
    out = run("cc_render_registries_conf",
              {"CC_REGISTRY_DOCKERIO": "mirror.corp.example", "CC_TLS_INSECURE": "1"})
    assert out.count("insecure = true") == 3, out
    # The third block is the one that actually matters: CC_REGISTRY_* rewrites
    # every ref to <mirror>/<path>, so the pull never goes through docker.io.
    assert '[[registry]]\nlocation = "mirror.corp.example"\ninsecure = true' in out


def test_an_operator_pin_host_is_marked_insecure_too():
    """A re-namespacing mirror's pin may name a host no seam mentions."""
    env = {
        "CC_REGISTRY_DOCKERIO": "mirror.corp.example",
        "CC_TLS_INSECURE": "1",
        "CC_IMG_POSTGRES": "other.corp.example/mirrored/library/postgres:16",
        "CC_IMG_NEO4J": "mirror.corp.example/library/neo4j:5.26.2",
    }
    out = run("cc_render_registries_conf", env)
    assert 'location = "other.corp.example"' in out
    # ...exactly once, and not twice for the host the seam already covered.
    assert out.count('location = "mirror.corp.example"') == 2, out
    assert out.count('location = "other.corp.example"') == 1, out


def test_a_localhost_or_hostless_pin_is_not_a_registry():
    """`localhost/cc-sandbox:1` and a bare name have no registry to configure."""
    out = run("cc_pin_hosts", {
        "CC_TLS_INSECURE": "1",
        "CC_IMG_A": "localhost/cc-graphiti:1.0.2-anthropic",
        "CC_IMG_B": "postgres:16",
        "CC_IMG_C": "mirror.corp.example:5000/library/redis:7-alpine",
    })
    assert out.strip() == "mirror.corp.example:5000"


def test_no_proxy_means_no_proxy_dropin():
    assert run("cc_render_proxy_conf", {}) == ""


def test_the_proxy_dropin_is_an_engine_env_table():
    out = run("cc_render_proxy_conf", {"CC_PROXY": "http://proxy.corp.example:8080"})
    assert "[engine]" in out
    assert 'env = ["http_proxy=http://proxy.corp.example:8080"' in out
    # Loopback must bypass it: every phase curls 127.0.0.1.
    assert "no_proxy=127.0.0.1,localhost,host.containers.internal" in out


def test_a_proxy_credential_never_survives_the_printed_diff():
    """The diff the operator sees must not carry a password."""
    secret = "http://svc-account:s3cr3t-p4ss@proxy.corp.example:8080"
    out = run('cc_redact_proxy "$(cc_render_proxy_conf)"', {"CC_PROXY": secret})
    assert "s3cr3t-p4ss" not in out
    assert "svc-account" not in out
    assert "CC_PROXY" in out, "the KEY NAME should still be there — it is the seam"
    # The table shape survives redaction, so the operator still sees WHAT changes.
    assert "[engine]" in out


def test_only_import_native_ca_needs_a_machine_restart():
    """podman re-reads its configuration per invocation; only the CA import
    is applied at machine START (podman-machine-set(1))."""
    for item in ("registries", "proxy", "ca"):
        r = subprocess.run(
            ["bash", "-c", f". ./machine-lib.sh; cc_machine_restart_needed {item}"],
            cwd=LIB.parent, capture_output=True, text=True,
        )
        assert r.returncode == 1, f"{item} must NOT require a restart: {r.stdout}"
        assert r.stdout == ""
    r = subprocess.run(
        ["bash", "-c", ". ./machine-lib.sh; cc_machine_restart_needed registries import-native-ca"],
        cwd=LIB.parent, capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "podman machine stop && podman machine start" in r.stdout


@pytest.mark.parametrize("script", [LIB, ENV_LIB], ids=lambda p: p.name)
def test_the_libraries_parse(script: pathlib.Path):
    subprocess.run(["bash", "-n", script.name], cwd=script.parent, check=True)
