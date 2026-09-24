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

import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIB = ROOT / "deploy" / "single" / "machine-lib.sh"
ENV_LIB = ROOT / "deploy" / "env-lib.sh"


def bash() -> str:
    """The bash that can actually run this repo's shell scripts.

    A bare `"bash"` is NOT it on Windows: PATH's first `bash` is System32's WSL
    launcher, a shell in a different filesystem where this checkout's paths mean
    nothing. With no WSL distribution registered it does not even start — it
    exits 1 with `Bash/Service/0x8007072c` and writes UTF-16LE to stdout, which
    is what the assertion below then reports as gibberish. The product already
    knows this (`central_command.api.update._bash` resolves Git Bash from git's
    own install); the tests have to ask the same way.

    Found on the 2026-09-24 Windows testbed run: 9 tests here and 3 in
    test_register_models_upstream.py failed on it, and since `./setup.sh test`
    is the install GATE, the gate was red on the platform this profile targets.
    """
    from central_command.api.update import _bash

    resolved = _bash()
    if not resolved:
        pytest.skip("no usable bash on this host")
    return resolved


def run(func: str, env: dict[str, str]) -> str:
    """Call one machine-lib function with a clean environment."""
    # env -i equivalent: only what the test sets, plus PATH so coreutils exist.
    # On Windows the inherited PATH is kept instead — Git Bash resolves its own
    # coreutils through it, and a POSIX-only PATH leaves it without `printf`.
    # What matters for the test's intent is that no CC_* leaks in, and none does.
    base = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
    if sys.platform == "win32":
        base = {k: v for k, v in os.environ.items()
                if k.upper() in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP")}
    # cwd= + a RELATIVE source path: the script is sourced as `./machine-lib.sh`
    # so no absolute Windows path ever reaches bash.
    r = subprocess.run(
        [bash(), "-c", f". ./machine-lib.sh; {func}"],
        cwd=LIB.parent,
        env={**base, **env},
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
            [bash(), "-c", f". ./machine-lib.sh; cc_machine_restart_needed {item}"],
            cwd=LIB.parent, capture_output=True, text=True,
        )
        assert r.returncode == 1, f"{item} must NOT require a restart: {r.stdout}"
        assert r.stdout == ""
    r = subprocess.run(
        [bash(), "-c", ". ./machine-lib.sh; cc_machine_restart_needed registries import-native-ca"],
        cwd=LIB.parent, capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "podman machine stop && podman machine start" in r.stdout


@pytest.mark.parametrize("script", [LIB, ENV_LIB], ids=lambda p: p.name)
def test_the_libraries_parse(script: pathlib.Path):
    subprocess.run([bash(), "-n", script.name], cwd=script.parent, check=True)


# ── two Windows-only seams, guarded by a source walk ────────────────────────
# Both were found on the 2026-09-24 Windows testbed run, and neither can be
# exercised on Linux: one needs `podman machine`, the other needs MSYS. The
# guard is a walk over `setup.sh` — narrow, and pinned to the exact reason.

SETUP = ROOT / "deploy" / "single" / "setup.sh"


def test_the_machine_name_strips_podmans_default_marker():
    """`podman machine list --format '{{.Name}}'` prints the DEFAULT machine as
    `podman-machine-default*`. That star is a marker, not part of the name:
    `podman machine ssh podman-machine-default*` does not match a machine, so
    podman takes the starred word as the COMMAND and every machine probe reports
    "does not answer 'podman machine ssh'" on a machine that is running.
    Measured on podman 5.8.3, where it FAILed check's whole machine section —
    and the default machine is the normal case on Windows and macOS.
    """
    src = SETUP.read_text(encoding="utf-8")
    body = src[src.index("machine_name() {"):]
    body = body[: body.index("\n}\n")]
    assert "podman machine list --format '{{.Name}}'" in body
    assert '"${n%\\*}"' in body or "${n%\\*}" in body, (
        "machine_name must strip a trailing '*' (podman's default marker) "
        f"before the name is used as an ssh target:\n{body}"
    )


def test_compose_is_invoked_with_msys_path_conversion_disabled():
    """MSYS rewrites POSIX-looking values in the environment of a NATIVE Windows
    process, and `podman-compose.exe` is one. Measured on the 2026-09-24 run:
    `/dev/null` arrived as `nul` (RuntimeError: volume [nul] not defined in top
    level — the compose render FAILED on every Windows install) and
    `/etc/pki/ca-trust/source/anchors/cc-ca.pem` arrived as
    `C:/Program Files/Git/etc/pki/...` (ValueError: could not parse mount, and
    the same rewrite would have put a Windows path into SSL_CERT_FILE inside a
    Linux container). `MSYS2_ENV_CONV_EXCL` is the documented opt-out.
    """
    src = SETUP.read_text(encoding="utf-8")
    assert "MSYS2_ENV_CONV_EXCL" in src, (
        "every compose invocation must exclude the container-side POSIX paths "
        "from MSYS path conversion"
    )
    body = src[src.index("compose() {"):]
    body = body[: body.index("\n}\n")]
    assert "MSYS2_ENV_CONV_EXCL" in body, f"the compose wrapper itself:\n{body}"
    # Both derived CA variables, because both carry a container-side path.
    for key in ("CC_CA_BUNDLE_MOUNT_SRC", "CC_CA_BUNDLE_IN_CONTAINER"):
        assert key in src[src.index("COMPOSE_ENV_CONV_EXCL="):][:200], key
