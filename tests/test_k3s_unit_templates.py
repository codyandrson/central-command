"""The k3s units that name the checkout or the account are templates (R6).

Seven units are `deploy/k3s/<name>.in`, rendered by ONE function
(`cc_k3s_render_unit`, instance-lib.sh) at install (setup.sh) and at update
(cc-update.sh); four hold no instance value and stay plain. These run the real
function and the real setup.sh and cc-update.sh functions, lifted out and never
copied, against temp trees with temp destinations:

* every template renders on a fake layout with no token left, and the render
  changes nothing but the tokens, so a personal layout's render is its unit;
* every path a unit runs from lies under the checkout, on a layout where the
  checkout sits in the owner's home and on one where it does not;
* the group the templates render is the owner's PRIMARY group, never the
  checkout's: /run/cc-update takes it at 0770 and a trigger written there starts
  the root updater, so a shared checkout group must not be able to write one;
* the render refuses what it cannot render, and a refusal installs nothing;
* setup's path check reads the RENDERED text and still refuses a stray path;
* the installed updater, which runs the release that introduces the templates,
  installs none of them (its old pathspec, over a real rename).

The directives each unit pins stay in their own tests, read through
tests/k3s_units.py. That the render equals the units installed on a live host is
a read-only diff run by hand, never here: nothing in the suite reads /etc.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.k3s_units import CHECKOUT, GROUP, HOME, K3S, OWNER, render_unit, run_render
from tests.test_no_instance_facts import shapes_of

TEMPLATES = ("cc-backup.service.in", "cc-nerve.service.in", "cc-sandbox-runner.service.in",
             "cc-update-stage.service.in", "cc-update-tmpfiles.conf.in", "cc-update.service.in",
             "cc-uvicorn.service.in")
PLAIN = ("cc-backup.timer", "cc-graph-bolt.service", "cc-update-stage.path", "cc-update.path")
TOKENS = ("@CC_CHECKOUT@", "@CC_OWNER@", "@CC_OWNER_GROUP@", "@CC_OWNER_HOME@")

_PATH_KEY = re.compile(r"^(?:WorkingDirectory|ExecStart|ExecStartPre|EnvironmentFile)=(.*)$", re.M)
_ABS_NOT_SYSTEM = re.compile(r"(?<![\w.])-?(/(?!bin/|usr/|run/|dev/|var/|tcp/)[\w./-]+)")

SETUP_SH = (K3S / "setup.sh").read_text(encoding="utf-8")
UPDATER_SH = (K3S / "cc-update.sh").read_text(encoding="utf-8")


def _fn(text: str, name: str) -> str:
    start = text.index(f"\n{name}() {{") + 1
    return text[start:text.index("\n}\n", start) + 3]


def _posix_only() -> None:
    if os.name == "nt" or not shutil.which("bash") or not shutil.which("getent"):
        pytest.skip("POSIX bash, GNU stat and getent needed")


# -- the set of files --------------------------------------------------------

def test_seven_templates_and_four_plain_units_and_no_unit_in_both_forms():
    assert tuple(sorted(p.name for p in K3S.glob("*.in"))) == TEMPLATES
    plain = sorted(p.name for g in ("*.service", "*.path", "*.timer", "*-tmpfiles.conf") for p in K3S.glob(g))
    assert tuple(plain) == PLAIN


@pytest.mark.parametrize("name", PLAIN)
def test_a_plain_unit_holds_no_instance_value(name):
    text = (K3S / name).read_text(encoding="utf-8")
    assert "/home/" not in text, name
    for value in re.findall(r"^(?:User|Group)=(.*)$", text, flags=re.M):
        assert value == "root", name


# -- the render --------------------------------------------------------------

@pytest.mark.parametrize("name", TEMPLATES)
def test_a_template_renders_with_no_token_left(name):
    assert "@CC_" not in render_unit(K3S / name)


@pytest.mark.parametrize("name", TEMPLATES)
def test_the_render_changes_the_tokens_and_nothing_else(name):
    """What makes the personal layout's render its installed unit, byte for byte."""
    raw = (K3S / name).read_bytes().decode("utf-8")
    want = raw
    for token, value in zip(TOKENS, (CHECKOUT, OWNER, "staff", HOME), strict=True):
        want = want.replace(token, value)
    assert render_unit(K3S / name, group="staff") == want
    assert want.endswith("\n")


@pytest.mark.parametrize("name", TEMPLATES)
def test_the_guard_passes_for_every_template(name):
    assert shapes_of(f"deploy/k3s/{name}", (K3S / name).read_text(encoding="utf-8")) == set()


@pytest.mark.parametrize(("checkout", "home"), [(CHECKOUT, HOME), ("/opt/cc", "/home/doe")])
@pytest.mark.parametrize("name", [t for t in TEMPLATES if ".service" in t])
def test_every_path_a_unit_runs_from_lies_under_the_checkout(name, checkout, home):
    text = render_unit(K3S / name, checkout=checkout, home=home)
    for value in _PATH_KEY.findall(text):
        for path in _ABS_NOT_SYSTEM.findall(value):
            assert path == checkout or path.startswith(checkout + "/"), f"{name}: {path}"


def test_the_sandbox_runners_environment_is_under_the_checkout_and_the_owners_home():
    text = render_unit(K3S / "cc-sandbox-runner.service.in", checkout="/opt/cc", home="/home/doe")
    assert "Environment=CC_SANDBOX_KUBECONFIG=/home/doe/.cc-sandbox-runner.kubeconfig\n" in text
    assert "Environment=CC_SANDBOX_COPY_ROOT=/opt/cc\n" in text
    assert "EnvironmentFile=-/opt/cc/.env\n" in text


def test_the_group_is_its_own_value():
    """Its own argument: a primary group need not carry its owner's name."""
    text = render_unit(K3S / "cc-uvicorn.service.in", group="staff")
    assert "\nUser=doe\nGroup=staff\n" in text
    tmpfiles = render_unit(K3S / "cc-update-tmpfiles.conf.in", group="staff")
    assert "d /run/cc-update      0770 doe staff -\n" in tmpfiles


@pytest.mark.parametrize("planted", ["x=@CC_NOT_A_TOKEN@\n", "x=@CC_lower@\n", "x=@CC_@\n",
                                     "x=@CC_CHECKOUT\n", "x=@CC_\n"])
def test_the_render_refuses_a_token_it_does_not_know_or_a_malformed_one(tmp_path, planted):
    t = tmp_path / "x.service.in"
    t.write_text("User=@CC_OWNER@\n" + planted)
    r = run_render(t, CHECKOUT, OWNER, GROUP, HOME)
    assert r.returncode != 0 and r.stdout == b""
    assert b"instance-lib:" in r.stderr


def test_the_refusal_names_the_unknown_token(tmp_path):
    t = tmp_path / "x.service.in"
    t.write_text("x=@CC_NOT_A_TOKEN@\n")
    assert b"@CC_NOT_A_TOKEN@" in run_render(t, CHECKOUT, OWNER, GROUP, HOME).stderr


@pytest.mark.parametrize("position", range(4))
def test_the_render_refuses_an_empty_value(position):
    values = [CHECKOUT, OWNER, GROUP, HOME]
    values[position] = ""
    r = run_render(K3S / "cc-uvicorn.service.in", *values)
    assert r.returncode != 0 and r.stdout == b""


def test_the_render_refuses_a_missing_argument_and_a_missing_template(tmp_path):
    r = run_render(K3S / "cc-uvicorn.service.in", CHECKOUT, OWNER, GROUP)
    assert r.returncode != 0 and r.stdout == b""
    r = run_render(tmp_path / "absent.service.in", CHECKOUT, OWNER, GROUP, HOME)
    assert r.returncode != 0 and r.stdout == b""


def test_a_value_is_literal(tmp_path):
    t = tmp_path / "x.service.in"
    t.write_text("WorkingDirectory=@CC_CHECKOUT@/web\n")
    weird = r"/home/doe/a&b\c*d"
    assert render_unit(t, checkout=weird) == f"WorkingDirectory={weird}/web\n"


def test_a_value_holding_a_token_is_refused_rather_than_written_out(tmp_path):
    t = tmp_path / "x.service.in"
    t.write_text("WorkingDirectory=@CC_CHECKOUT@\n")
    r = run_render(t, "/home/doe/@CC_X", OWNER, GROUP, HOME)
    assert r.returncode != 0 and r.stdout == b""


# -- setup.sh ----------------------------------------------------------------

@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A checkout owned by the test's own account, holding the real units."""
    _posix_only()
    t = tmp_path / "checkout"
    k3s = t / "deploy" / "k3s"
    k3s.mkdir(parents=True)
    shutil.copy(K3S / "instance-lib.sh", k3s)
    for name in TEMPLATES + PLAIN:
        shutil.copy(K3S / name, k3s)
    return t


def _stub_bin(tmp_path: Path) -> Path:
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    sudo = stubs / "sudo"
    sudo.write_text('#!/usr/bin/env bash\nexec "$@"\n')
    sudo.chmod(0o755)
    return stubs


def _setup_script(tree: Path, body: str) -> str:
    fns = "".join(_fn(SETUP_SH, n) for n in ("load_unit_values", "unit_text", "install_unit", "unit_paths_check"))
    return "\n".join([
        "set -uo pipefail",
        f'HERE="{tree / "deploy" / "k3s"}"',
        'pass() { printf "PASS %s: %s\\n" "$1" "$2"; }',
        'fail() { printf "FAIL %s: %s\\n" "$1" "$2"; }',
        fns,
        body,
    ])


def _run_setup(tree: Path, tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    env = {"PATH": f"{_stub_bin(tmp_path)}:{os.environ['PATH']}"}
    return subprocess.run(["bash", "-c", _setup_script(tree, body)], env=env,
                          capture_output=True, text=True, timeout=60)


def _stray() -> str:
    return "/home/" + "zq7acct/elsewhere"


def test_setup_path_check_passes_on_the_real_units(tree, tmp_path):
    r = _run_setup(tree, tmp_path, "unit_paths_check")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS unit-paths" in r.stdout


@pytest.mark.parametrize("name", ["cc-uvicorn.service.in", "cc-graph-bolt.service"])
def test_setup_path_check_refuses_a_path_outside_the_checkout(tree, tmp_path, name):
    f = tree / "deploy" / "k3s" / name
    f.write_text(f.read_text() + f"\n[Service]\nExecStartPre={_stray()}/bin/x\n")
    r = _run_setup(tree, tmp_path, "unit_paths_check")
    assert r.returncode != 0
    assert "FAIL unit-paths" in r.stdout and name in r.stdout


def _run_setup_for_layout(tree: Path) -> subprocess.CompletedProcess:
    """The check with the values given, as they are on a /home-rooted checkout."""
    script = _setup_script(tree, ". \"$HERE/instance-lib.sh\"\n"
                                 "UNIT_CHECKOUT=/home/doe/cc UNIT_OWNER=doe UNIT_GROUP=doe UNIT_HOME=/home/doe\n"
                                 "load_unit_values() { :; }\nunit_paths_check")
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                          env={"PATH": os.environ["PATH"]})


def test_setup_path_check_reads_the_rendered_text(tree):
    """Raw, a token is no /home path at all; rendered, each is under /home/doe/cc."""
    r = _run_setup_for_layout(tree)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "under /home/doe/cc" in r.stdout


def test_setup_path_check_refuses_a_sibling_of_the_checkout(tree):
    (tree / "deploy" / "k3s" / "cc-update.service.in").write_text("[Service]\nExecStart=/home/doe/cc2/x\n")
    r = _run_setup_for_layout(tree)
    assert r.returncode != 0 and "cc-update.service.in" in r.stdout


def test_setup_path_check_refuses_a_template_that_does_not_render(tree, tmp_path):
    (tree / "deploy" / "k3s" / "cc-nerve.service.in").write_text("x=@CC_NOT_A_TOKEN@\n")
    r = _run_setup(tree, tmp_path, "unit_paths_check")
    assert r.returncode != 0
    assert "FAIL unit-paths: cc-nerve.service.in does not render" in r.stdout


def test_setup_install_renders_a_template_and_copies_a_plain_unit(tree, tmp_path):
    k3s, dest = tree / "deploy" / "k3s", tmp_path / "dest"
    dest.mkdir()
    r = _run_setup(tree, tmp_path, f'''load_unit_values || exit 9
install_unit "$HERE/cc-update.service.in" "{dest}/cc-update.service" || exit 1
install_unit "$HERE/cc-update.path" "{dest}/cc-update.path" || exit 2
''')
    assert r.returncode == 0, r.stdout + r.stderr
    rendered = (dest / "cc-update.service").read_text()
    assert f"ExecStart={tree.resolve()}/deploy/k3s/cc-update.sh\n" in rendered and "@CC_" not in rendered
    assert (dest / "cc-update.service").stat().st_mode & 0o777 == 0o644
    assert (dest / "cc-update.path").read_bytes() == (k3s / "cc-update.path").read_bytes()


def test_setup_install_of_a_refused_template_installs_nothing(tree, tmp_path):
    (tree / "deploy" / "k3s" / "cc-nerve.service.in").write_text("x=@CC_NOT_A_TOKEN@\n")
    dest = tmp_path / "out.service"
    r = _run_setup(tree, tmp_path, f'load_unit_values || exit 9\ninstall_unit "$HERE/cc-nerve.service.in" "{dest}"')
    assert r.returncode == 1 and not dest.exists()


def test_setup_renders_the_owners_primary_group_never_the_checkouts(tree, tmp_path):
    """A checkout owned by doe, group `users`: a member of `users` must not be able
    to write the trigger that starts the root updater."""
    stubs = _stub_bin(tmp_path)
    for name, body in (("stat", 'case "$2" in %U) echo doe ;; %G) echo users ;; esac\n'),
                       ("id", 'echo doeprimary\n'),
                       ("getent", 'printf "doe:x:1000:1000::/home/doe:/bin/bash\\n"\n')):
        (stubs / name).write_text("#!/usr/bin/env bash\n" + body)
        (stubs / name).chmod(0o755)
    dest = tmp_path / "dest"
    dest.mkdir()
    r = _run_setup(tree, tmp_path, f"""load_unit_values || exit 9
install_unit "$HERE/cc-uvicorn.service.in" "{dest}/cc-uvicorn.service" || exit 1
install_unit "$HERE/cc-update-tmpfiles.conf.in" "{dest}/cc-update.conf" || exit 2
""")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "\nUser=doe\nGroup=doeprimary\n" in (dest / "cc-uvicorn.service").read_text()
    tmpfiles = (dest / "cc-update.conf").read_text()
    assert "d /run/cc-update      0770 doe doeprimary -\n" in tmpfiles and "users" not in tmpfiles


def test_cc_k3s_group_is_for_the_chown_and_never_reaches_a_render():
    assert "cc_k3s_group" not in SETUP_SH
    assert '"$OWNER" "$OWNER_PRIMARY_GROUP" "$OWNER_HOME"' in _fn(UPDATER_SH, "install_changed_unit")
    assert 'UNIT_GROUP="$(cc_k3s_owner_primary_group "$HERE/setup.sh")"' in _fn(SETUP_SH, "load_unit_values")


def test_setup_installs_the_fixed_list_and_not_the_stage_pair():
    app = _fn(SETUP_SH, "phase_app")
    loop = re.search(r"for u in (.*?); do", app, re.S)
    assert loop
    assert loop.group(1).split() == ["cc-uvicorn.service", "cc-sandbox-runner.service", "cc-graph-bolt.service",
                                     "\\", "cc-backup.service", "cc-backup.timer", "cc-update.service", "cc-update.path"]
    assert '[[ -f "$src.in" ]] && src="$src.in"' in app
    assert 'install_unit "$HERE/cc-nerve.service.in" /etc/systemd/system/cc-nerve.service' in app
    assert "cc-update-stage" not in app
    tmpfiles = _fn(SETUP_SH, "install_update_tmpfiles")
    assert '"$HERE/cc-update-tmpfiles.conf.in" /etc/tmpfiles.d/cc-update.conf' in tmpfiles


# -- cc-update.sh ------------------------------------------------------------

def _run_updater(tree: Path, tmp_path: Path, rel: str) -> tuple[subprocess.CompletedProcess, Path]:
    etc = tmp_path / "etc"
    (etc / "systemd" / "system").mkdir(parents=True, exist_ok=True)
    (etc / "tmpfiles.d").mkdir(parents=True, exist_ok=True)
    script = "\n".join([
        "set -uo pipefail",
        f'. "{K3S / "instance-lib.sh"}"',
        f'REPO="{tree}" OWNER=doe OWNER_GROUP=users OWNER_PRIMARY_GROUP=staff OWNER_HOME=/home/doe',
        _fn(UPDATER_SH, "install_changed_unit").replace("/etc/", f"{etc}/"),
        f'install_changed_unit "{rel}"',
    ])
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60), etc


def test_the_updater_renders_a_changed_template_to_its_installed_name(tree, tmp_path):
    r, etc = _run_updater(tree, tmp_path, "deploy/k3s/cc-uvicorn.service.in")
    assert r.returncode == 0, r.stderr
    text = (etc / "systemd" / "system" / "cc-uvicorn.service").read_text()
    assert "\nUser=doe\nGroup=staff\n" in text and f"WorkingDirectory={tree}\n" in text and "@CC_" not in text
    assert not (etc / "systemd" / "system" / "cc-uvicorn.service.in").exists()


def test_the_updater_renders_the_tmpfiles_template_to_the_runbooks_name(tree, tmp_path):
    r, etc = _run_updater(tree, tmp_path, "deploy/k3s/cc-update-tmpfiles.conf.in")
    assert r.returncode == 0, r.stderr
    text = (etc / "tmpfiles.d" / "cc-update.conf").read_text()
    assert "0770 doe staff -" in text and "users" not in text


def test_the_updater_installs_a_plain_unit_verbatim(tree, tmp_path):
    r, etc = _run_updater(tree, tmp_path, "deploy/k3s/cc-graph-bolt.service")
    assert r.returncode == 0, r.stderr
    assert (etc / "systemd" / "system" / "cc-graph-bolt.service").read_bytes() == (K3S / "cc-graph-bolt.service").read_bytes()


def test_a_refused_render_leaves_the_installed_unit_alone(tree, tmp_path):
    (tree / "deploy" / "k3s" / "cc-nerve.service.in").write_text("x=@CC_NOT_A_TOKEN@\n")
    installed = tmp_path / "etc" / "systemd" / "system" / "cc-nerve.service"
    installed.parent.mkdir(parents=True)
    installed.write_text("the unit systemd has\n")
    r, _ = _run_updater(tree, tmp_path, "deploy/k3s/cc-nerve.service.in")
    assert r.returncode != 0
    assert installed.read_text() == "the unit systemd has\n"


def test_the_updater_lists_templates_and_still_skips_a_deleted_unit():
    block = UPDATER_SH[UPDATER_SH.index('changed_units="$(changed_between'):]
    assert "'deploy/k3s/*.in'" in block[:block.index(')"')]
    assert '[[ -f "$REPO/$f" ]] || continue' in block
    assert """grep -qx 'deploy/k3s/cc-graph-bolt.service' <<<"$changed_units\"""" in block


def test_the_release_that_introduces_the_templates_installs_nothing(tmp_path):
    """The installed updater runs this release's apply with the pathspec it has.
    Over a real rename it lists the deleted plain name and no template, and it
    skips a path that is gone; so no raw token reaches /etc/systemd/system."""
    if not shutil.which("git"):
        pytest.skip("git needed")
    k3s = tmp_path / "deploy" / "k3s"
    k3s.mkdir(parents=True)
    unit = k3s / "cc-x.service"
    body = "".join(f"# comment line {i}\n" for i in range(40))
    unit.write_text(body + "User=doe\n")

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.com",
                               *args], check=True, capture_output=True, text=True).stdout

    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "old")
    unit.rename(k3s / "cc-x.service.in")
    (k3s / "cc-x.service.in").write_text(body + "User=@CC_OWNER@\n")
    git("add", "-A")
    git("commit", "-qm", "new")
    old_pathspec = ("deploy/k3s/*.service", "deploy/k3s/*.path", "deploy/k3s/*.timer", "deploy/k3s/*-tmpfiles.conf")
    listed = git("diff", "--name-only", "HEAD~1", "HEAD", "--", *old_pathspec).split()
    assert listed == ["deploy/k3s/cc-x.service"]
    assert not (tmp_path / listed[0]).exists()
    # The updater from this release on sees the template.
    assert git("diff", "--name-only", "HEAD~1", "HEAD", "--", *old_pathspec, "deploy/k3s/*.in").split() == [
        "deploy/k3s/cc-x.service.in"]


def test_the_updater_says_why_this_release_installs_nothing():
    comment = UPDATER_SH[:UPDATER_SH.index('changed_units="$(changed_between')]
    comment = comment[comment.rindex("# A unit that names the checkout"):]
    assert "previous" in comment and "'*.in'" in comment and "byte" in comment
