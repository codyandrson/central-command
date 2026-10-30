"""The k3s units as systemd is given them, for the tests that pin their directives (R6).

The seven units that name the checkout or the account are templates
(`deploy/k3s/<name>.in`) rendered by `cc_k3s_render_unit` in instance-lib.sh, and
four stay plain. So there is ONE way for a test to read a shipped unit, and it
runs the real render: a test that opened the template raw would pin a token, and
one that kept opening the plain path would fail on a file that no longer exists.

Not a test module (no `test_` prefix): pytest does not collect it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
K3S = ROOT / "deploy" / "k3s"
LIB = K3S / "instance-lib.sh"

# The fake layout: the example cast, and a checkout under the owner's home.
CHECKOUT, OWNER, GROUP, HOME = "/home/doe/cc", "doe", "doe", "/home/doe"


def run_render(template: Path, *values: str) -> subprocess.CompletedProcess:
    """The real function, given exactly `values`; the caller reads the verdict."""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash needed to run the unit render")
    return subprocess.run(
        [bash, "-c", '. "$1" && shift && cc_k3s_render_unit "$@"', "render",
         LIB.as_posix(), template.as_posix(), *values],
        capture_output=True, timeout=30,
    )


def render_unit(template: Path, checkout: str = CHECKOUT, owner: str = OWNER,
                group: str = GROUP, home: str = HOME) -> str:
    r = run_render(template, checkout, owner, group, home)
    assert r.returncode == 0, f"{template.name} did not render: {r.stderr.decode()}"
    return r.stdout.decode("utf-8")


def shipped_text(rel: str) -> str:
    """The repo-relative file `rel` as installed: rendered for the fake layout
    when `<rel>.in` exists, the file itself otherwise."""
    path = ROOT / rel
    template = path.with_name(path.name + ".in")
    if template.exists():
        return render_unit(template)
    return path.read_text(encoding="utf-8")
