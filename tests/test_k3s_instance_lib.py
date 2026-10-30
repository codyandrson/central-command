"""The k3s scripts derive the checkout, its owner, group and home (R5, INSTANCE-001).

No script under deploy/k3s/ names a checkout path, an account or a home path: each
asks deploy/k3s/instance-lib.sh from its own location. A mistake here surfaces only
at the NEXT update, run by the new cc-update.sh as root from its unit, so these run
the real functions and the real scripts against a temp checkout:

  * the helper resolves that tree, that user, that group and that user's home, with
    a path or from the calling script, with no git call (root on another user's
    checkout: git's safe.directory check refuses it);
  * a script inside a second worktree resolves to that worktree, as the updater's
    build scripts do, and the updater's REPO is the script the unit ran, never a
    build worktree;
  * anything that cannot be resolved fails loud, naming the role;
  * cc-update.sh's own head, backup.sh, the three make-*-kubeconfig.sh and
    setup.sh's three rows use those values.

Anything that would write under a home gets a `getent` stub pointing at a temp one
first: these tests never touch the real home of the account running them.

The helper also holds the unit render and the owner's primary group it is handed
(R6), the render tested in tests/test_k3s_unit_templates.py, and the compute node's
ssh target (R7, INSTANCE-001), tested in tests/test_k3s_compute_ssh.py. An owner of
root is refused here: the updater rebuilds as the owner and never as root.
"""

from __future__ import annotations

import grp
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not shutil.which("bash") or not shutil.which("getent"),
    reason="POSIX bash, GNU stat and getent needed",
)

ROOT = Path(__file__).resolve().parents[1]
K3S = ROOT / "deploy" / "k3s"
LIB = K3S / "instance-lib.sh"
UPDATER = (K3S / "cc-update.sh").read_text(encoding="utf-8")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(cwd), *args],
                   check=True, capture_output=True)


def _clean_env(stubs: Path | None = None, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("REPO", "CC_BACKUP_DIR")}
    if stubs is not None:
        env["PATH"] = f"{stubs}{os.pathsep}{env['PATH']}"
    env.update(extra)
    return env


def _exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)
    return path


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A temp git checkout owned by the test user, holding the helper."""
    if not shutil.which("git"):
        pytest.skip("git needed")
    t = tmp_path / "checkout"
    (t / "deploy" / "k3s").mkdir(parents=True)
    shutil.copy(LIB, t / "deploy" / "k3s" / "instance-lib.sh")
    _git(t, "init", "-q")
    _git(t, "add", "deploy/k3s/instance-lib.sh")
    _git(t, "commit", "-q", "-m", "seed")
    return t.resolve()


@pytest.fixture
def stubs(tmp_path: Path) -> Path:
    d = tmp_path / "stubs"
    d.mkdir()
    return d


@pytest.fixture
def fake_home(tmp_path: Path, stubs: Path) -> Path:
    """`getent passwd` answers with a temp home, whatever account it is asked about."""
    home = tmp_path / "owner-home"
    home.mkdir()
    _exe(stubs / "getent", f'printf "%s:x:1000:1000::{home}:/bin/bash\\n" "$2"\n')
    return home


def _script(tree: Path) -> Path:
    return tree / "deploy" / "k3s" / "some-script.sh"


def _call(fn: str, path: object, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", '. "$1"; "$2" "$3"', "_", str(LIB), fn, str(path)],
                          env=env if env is not None else _clean_env(), capture_output=True, text=True, timeout=30)


def _me(tree: Path) -> tuple[str, str, str]:
    st = tree.stat()
    entry = pwd.getpwuid(st.st_uid)
    return entry.pw_name, grp.getgrgid(st.st_gid).gr_name, entry.pw_dir


# ── the resolvers ───────────────────────────────────────────────────────────


def test_each_resolver_returns_that_tree_user_group_and_home(tree: Path):
    owner, group, home = _me(tree)
    for fn, want in (("cc_k3s_checkout", str(tree)), ("cc_k3s_owner", owner),
                     ("cc_k3s_group", group), ("cc_k3s_home", home)):
        r = _call(fn, _script(tree))
        assert (r.returncode, r.stdout.strip()) == (0, want), fn + ": " + r.stderr


@pytest.mark.parametrize("relative", [False, True])
def test_with_no_argument_each_resolver_uses_the_calling_script(tree: Path, relative: bool):
    owner, group, home = _me(tree)
    probe = _exe(_script(tree), '. "$(dirname "${BASH_SOURCE[0]}")/instance-lib.sh"\n'
                                "cc_k3s_checkout; cc_k3s_owner; cc_k3s_group; cc_k3s_home; cc_k3s_backup_dir\n")
    arg, cwd = ("deploy/k3s/some-script.sh", tree) if relative else (str(probe), Path("/"))
    r = subprocess.run(["bash", arg], cwd=cwd, env=_clean_env(), capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split("\n")[:5] == [str(tree), owner, group, home, f"{home}/cc-backups"]


def test_a_second_worktree_resolves_to_itself_and_the_checkout_to_itself(tree: Path, tmp_path: Path):
    # The shape of cc-update.sh's build worktree: detached, beside the checkout.
    wt = tmp_path / "build-worktree"
    _git(tree, "worktree", "add", "-q", "--detach", str(wt))
    wt = wt.resolve()
    assert _call("cc_k3s_checkout", wt / "deploy" / "k3s" / "x.sh").stdout.strip() == str(wt)
    assert _call("cc_k3s_checkout", _script(tree)).stdout.strip() == str(tree)
    # The owner of that worktree is still the test user, read from the worktree.
    assert _call("cc_k3s_owner", wt / "deploy" / "k3s" / "x.sh").stdout.strip() == _me(tree)[0]


def test_the_checkout_is_physical(tree: Path, tmp_path: Path):
    link = tmp_path / "via-a-link"
    link.symlink_to(tree)
    assert _call("cc_k3s_checkout", link / "deploy" / "k3s" / "x.sh").stdout.strip() == str(tree)


def test_a_caller_with_cdpath_set_gets_one_clean_line(tree: Path):
    r = _call("cc_k3s_checkout", _script(tree), _clean_env(CDPATH=str(tree.parent)))
    assert r.stdout == f"{tree}\n"


def test_no_git_is_ever_called(tree: Path, stubs: Path):
    log = tree.parent / "git-called"
    _exe(stubs / "git", f'echo called >> "{log}"\nexit 99\n')
    for fn in ("cc_k3s_checkout", "cc_k3s_owner", "cc_k3s_group", "cc_k3s_home", "cc_k3s_backup_dir"):
        assert _call(fn, _script(tree), _clean_env(stubs)).returncode == 0, fn
    assert not log.exists()


def test_the_group_is_read_never_assumed_equal_to_the_owner(tree: Path, stubs: Path):
    _exe(stubs / "stat", 'case "$2" in %U) echo jane ;; %G) echo rivers ;; esac\n')
    env = _clean_env(stubs)
    assert _call("cc_k3s_owner", _script(tree), env).stdout.strip() == "jane"
    assert _call("cc_k3s_group", _script(tree), env).stdout.strip() == "rivers"


def test_the_primary_group_is_the_owners_not_the_checkouts(tree: Path, stubs: Path):
    _exe(stubs / "stat", 'case "$2" in %U) echo doe ;; %G) echo users ;; esac\n')
    _exe(stubs / "id", '[[ "$*" == "-gn -- doe" ]] && echo doeprimary\n')
    r = _call("cc_k3s_owner_primary_group", _script(tree), _clean_env(stubs))
    assert (r.returncode, r.stdout.strip()) == (0, "doeprimary"), r.stderr


def test_the_primary_group_of_the_real_owner_is_read_with_no_stub(tree: Path):
    r = _call("cc_k3s_owner_primary_group", _script(tree))
    assert (r.returncode, r.stdout.strip()) == (0, grp.getgrgid(pwd.getpwuid(tree.stat().st_uid).pw_gid).gr_name)


@pytest.mark.parametrize("answer", ["exit 1\n", "exit 0\n"])
def test_an_account_with_no_primary_group_fails_loud(tree: Path, stubs: Path, answer: str):
    _exe(stubs / "id", answer)
    r = _call("cc_k3s_owner_primary_group", _script(tree), _clean_env(stubs))
    assert r.returncode != 0 and r.stdout == ""
    assert "the primary group of account" in r.stderr, r.stderr


def test_an_unreadable_owner_fails_the_primary_group_naming_the_owner(tree: Path, stubs: Path):
    _exe(stubs / "stat", "echo UNKNOWN\n")
    r = _call("cc_k3s_owner_primary_group", _script(tree), _clean_env(stubs))
    assert r.returncode != 0 and r.stdout == ""
    assert "the owner of" in r.stderr, r.stderr


def test_the_backup_dir_is_the_key_when_set_else_under_the_owners_home(tree: Path, fake_home: Path, stubs: Path):
    assert _call("cc_k3s_backup_dir", _script(tree), _clean_env(stubs)).stdout.strip() == f"{fake_home}/cc-backups"
    r = _call("cc_k3s_backup_dir", _script(tree), _clean_env(stubs, CC_BACKUP_DIR="/srv/backups"))
    assert r.stdout.strip() == "/srv/backups"


def test_the_key_needs_no_account_at_all(tree: Path, stubs: Path):
    _exe(stubs / "stat", "exit 1\n")
    r = _call("cc_k3s_backup_dir", _script(tree), _clean_env(stubs, CC_BACKUP_DIR="/srv/backups"))
    assert (r.returncode, r.stdout.strip()) == (0, "/srv/backups")


# ── failing loud ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("fn", "body", "named"), [
    # GNU stat prints UNKNOWN and exits 0 for a uid with no account.
    ("cc_k3s_owner", "echo UNKNOWN\n", "owner"),
    ("cc_k3s_group", "echo UNKNOWN\n", "group"),
    ("cc_k3s_home", "echo UNKNOWN\n", "owner"),
    ("cc_k3s_owner", "exit 1\n", "owner"),
    ("cc_k3s_group", "exit 1\n", "group"),
    ("cc_k3s_backup_dir", "exit 1\n", "owner"),
])
def test_an_unreadable_owner_or_group_fails_loud(tree: Path, stubs: Path, fn: str, body: str, named: str):
    _exe(stubs / "stat", body)
    r = _call(fn, _script(tree), _clean_env(stubs))
    assert r.returncode != 0 and r.stdout == ""
    assert f"the {named} of" in r.stderr, r.stderr


@pytest.mark.parametrize("fn", ["cc_k3s_owner", "cc_k3s_owner_primary_group", "cc_k3s_home", "cc_k3s_backup_dir"])
def test_a_root_owned_checkout_is_refused(tree: Path, stubs: Path, fn: str):
    # The updater's RUNAS is this owner: a root-owned checkout would make it root.
    _exe(stubs / "stat", "echo root\n")
    r = _call(fn, _script(tree), _clean_env(stubs))
    assert r.returncode != 0 and r.stdout == ""
    assert "owned by root" in r.stderr, r.stderr


def test_the_updater_stops_at_resolve_on_a_root_owned_checkout(tree: Path, stubs: Path, tmp_path: Path):
    script = _updater_head(tree, tmp_path / "state")
    _exe(stubs / "stat", "echo root\n")
    r = subprocess.run([str(script)], env=_clean_env(stubs), capture_output=True, text=True, timeout=30)
    assert r.returncode == 1, r.stdout
    assert "runuser" not in r.stdout
    status = json.loads((tmp_path / "state" / "status.json").read_text())
    assert (status["state"], status["phase"]) == ("failed", "resolve")


@pytest.mark.parametrize("answer", ["exit 2\n", 'printf "jane:x:1:1::::\\n"\n', 'printf "\\n"\n'])
def test_an_account_with_no_home_fails_loud(tree: Path, stubs: Path, answer: str):
    _exe(stubs / "getent", answer)
    r = _call("cc_k3s_home", _script(tree), _clean_env(stubs))
    assert r.returncode != 0 and r.stdout == ""
    assert "the home of account" in r.stderr, r.stderr


def test_a_path_that_does_not_exist_fails_naming_the_checkout(tmp_path: Path):
    r = _call("cc_k3s_checkout", tmp_path / "no" / "such" / "x.sh")
    assert r.returncode != 0 and r.stdout == ""
    assert "the checkout" in r.stderr


def test_no_path_and_no_calling_script_fails_rather_than_resolving_the_library():
    r = subprocess.run(["bash", "-c", '. "$1"; cc_k3s_checkout', "_", str(LIB)],
                       env=_clean_env(), capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and r.stdout == ""
    assert "no script path" in r.stderr


# ── what the old updater sources stays as it is ─────────────────────────────


def test_env_path_does_not_carry_the_helper_and_the_helper_sets_nothing(tmp_path: Path):
    # The installed updater sources the MERGED env-path.sh after the stop.
    assert "instance-lib" not in (K3S / "env-path.sh").read_text(encoding="utf-8")
    # The first pipeline sets PIPESTATUS itself, so it runs before the first snapshot.
    script = (': | :; compgen -v | sort > "$2/a"; declare -F | sort > "$2/fa"; . "$1"; '
              'compgen -v | sort > "$2/b"; declare -F | sort > "$2/fb"')
    subprocess.run(["bash", "-c", script, "_", str(LIB), str(tmp_path)], check=True, timeout=30)
    assert (tmp_path / "a").read_text() == (tmp_path / "b").read_text()
    new = set((tmp_path / "fb").read_text().splitlines()) - set((tmp_path / "fa").read_text().splitlines())
    assert {line.split()[-1] for line in new} == {
        "cc_k3s_checkout", "cc_k3s_owner", "cc_k3s_group", "cc_k3s_home", "cc_k3s_backup_dir", "_cc_k3s_stat",
        "cc_k3s_owner_primary_group", "cc_k3s_render_unit",  # R6, tests/test_k3s_unit_templates.py
        "cc_k3s_compute_ssh",  # R7, tests/test_k3s_compute_ssh.py
    }


def test_each_value_has_one_resolver_across_the_k3s_scripts():
    code = {p.name: [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]
            for p in K3S.glob("*.sh")}
    for name, lines in code.items():
        if name == LIB.name:
            continue
        for line in lines:
            assert not re.search(r"stat -c %[UG]|getent passwd|id -gn|SUDO_USER|\$\{?USER\b|/cc-backups", line), f"{name}: {line}"
            if "BASH_SOURCE" in line:
                assert line.startswith(("REPO_ROOT=", "HERE=", "cd ", 'SELF="${BASH_SOURCE[0]}"')), f"{name}: {line}"
    for name in ("setup.sh", "cc-update.sh"):
        assert sum("cc_k3s_render_unit " in line for line in code[name]) == 1, name


# ── cc-update.sh's own head ─────────────────────────────────────────────────


def _updater_head(tree: Path, statedir: Path) -> Path:
    """The real text of cc-update.sh down to phase(), run from `tree`'s script path."""
    head = UPDATER[:UPDATER.index("\nphase() {")]
    assert "STATEDIR=/var/lib/cc-update\n" in head
    head = head.replace("STATEDIR=/var/lib/cc-update\n", f"STATEDIR='{statedir}'\n")
    k3s = tree / "deploy" / "k3s"
    shutil.copy(K3S / "env-path.sh", k3s / "env-path.sh")
    shutil.copy(LIB, k3s / "instance-lib.sh")
    script = k3s / "cc-update.sh"
    script.write_text(head + '\nprintf "%s\\n" "$REPO" "${RUNAS[*]}" "$OWNER" "$OWNER_GROUP" "$OWNER_HOME" "$BACKUPS"\n')
    script.chmod(0o755)
    return script


def test_the_updater_derives_everything_from_the_script_the_unit_ran(tree: Path, fake_home: Path, stubs: Path, tmp_path: Path):
    script = _updater_head(tree, tmp_path / "state")
    r = subprocess.run([str(script)], cwd="/", env=_clean_env(stubs), capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    owner, group, _home = _me(tree)
    assert r.stdout.splitlines() == [
        str(tree), f"runuser -u {owner} --", owner, group, str(fake_home), f"{fake_home}/cc-backups",
    ]


def test_the_updater_reads_the_backup_key_from_the_env_file(tree: Path, fake_home: Path, stubs: Path, tmp_path: Path):
    script = _updater_head(tree, tmp_path / "state")
    (tree / "deploy" / "k3s" / ".env").write_text("CC_BACKUP_DIR=/srv/from-the-file\n")
    r = subprocess.run([str(script)], env=_clean_env(stubs), capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[-1] == "/srv/from-the-file"


@pytest.mark.parametrize(("args", "written", "untouched"), [
    ([], "status.json", "stage.json"),
    (["stage"], "stage.json", "status.json"),
])
def test_a_failed_derivation_stops_the_updater_and_reaches_the_cockpit(
        tree: Path, stubs: Path, tmp_path: Path, args: list[str], written: str, untouched: str):
    import json

    state = tmp_path / "state"
    script = _updater_head(tree, state)
    _exe(stubs / "stat", "echo UNKNOWN\n")
    r = subprocess.run([str(script), *args], env=_clean_env(stubs), capture_output=True, text=True, timeout=30)
    assert r.returncode == 1
    assert "the owner of" in r.stderr, r.stderr            # the journal names the role
    assert "runuser" not in r.stdout                       # it never reached the print
    status = json.loads((state / written).read_text())
    assert (status["state"], status["phase"]) == ("failed", "resolve")
    assert not (state / untouched).exists()


def test_a_missing_helper_stops_the_updater(tree: Path, stubs: Path, tmp_path: Path):
    script = _updater_head(tree, tmp_path / "state")
    (tree / "deploy" / "k3s" / "instance-lib.sh").unlink()
    r = subprocess.run([str(script)], env=_clean_env(stubs), capture_output=True, text=True, timeout=30)
    assert r.returncode == 1 and "instance-lib.sh could not be read" in r.stdout


def _fn(name: str) -> str:
    start = UPDATER.index(f"\n{name}() {{") + 1
    return UPDATER[start:UPDATER.index("\n}\n", start) + 3]


def test_the_rebuild_finds_uv_under_the_owners_home(tmp_path: Path, stubs: Path):
    repo, home, log = tmp_path / "r", tmp_path / "h", tmp_path / "called"
    (repo / "web").mkdir(parents=True)
    _exe(home / ".local" / "bin" / "uv", f'echo "uv $VIRTUAL_ENV" >> "{log}"\n')
    _exe(stubs / "npm", f'echo "npm $1" >> "{log}"\n')
    script = "\n".join([
        "set -uo pipefail",
        'note() { printf "%s\\n" "$*"; }',
        "RUNAS=()",
        f"REPO='{repo}'",
        f"OWNER_HOME='{home}'",
        _fn("patch_graphiti"),
        _fn("rebuild"),
        "rebuild",
    ])
    r = subprocess.run(["bash", "-c", script], env=_clean_env(stubs), capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert log.read_text().splitlines() == [f"uv {repo}/.venv", "npm ci", "npm run"]


def test_a_build_worktree_can_never_become_repo():
    assert len(re.findall(r"^\s*REPO=", UPDATER, re.M)) == 1
    assert 'REPO="$(cc_k3s_checkout "$SELF")"' in UPDATER
    assert 'SELF="${BASH_SOURCE[0]}"' in UPDATER
    worktrees = re.findall(r'^\s*WT="([^"]*)"', UPDATER, re.M)
    assert sorted(w.split("/.")[0] for w in worktrees if w) == ["$OWNER_HOME", "$OWNER_HOME"]
    # The target's OWN build script runs from the worktree, with its own REPO_ROOT.
    assert UPDATER.count('bash "$WT/deploy/k3s/$script"') == 2
    # Both dumps' owner and group are read, not assumed equal.
    assert UPDATER.count('chown "$OWNER:$OWNER_GROUP"') == 2
    assert "chown " not in UPDATER.replace('chown "$OWNER:$OWNER_GROUP"', "")


# ── backup.sh, for real ─────────────────────────────────────────────────────


@pytest.fixture
def backup_tree(tree: Path, stubs: Path) -> Path:
    if not shutil.which("flock"):
        pytest.skip("flock needed")
    k3s = tree / "deploy" / "k3s"
    for name in ("backup.sh", "env-path.sh", "instance-lib.sh"):
        shutil.copy(K3S / name, k3s / name)
    (k3s / ".env").write_text("N8N_ENCRYPTION_KEY=fake\nLITELLM_SALT_KEY=fake\n")
    (tree / ".env").write_text("X=1\n")
    (tree / "web").mkdir()
    (tree / "web" / ".env").write_text("X=1\n")
    _exe(stubs / "k3s", "exit 1\n")    # every store fails; the run still reaches the env copies
    return tree


def _backup(tree: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    # An absolute path from `/`, as cc-backup.service runs it, and no REPO in the env.
    return subprocess.run([str(tree / "deploy" / "k3s" / "backup.sh")], cwd="/", env=env,
                          capture_output=True, text=True, timeout=120)


def test_backup_with_nothing_set_derives_the_checkout_and_the_owners_dir(backup_tree: Path, fake_home: Path, stubs: Path):
    (backup_tree / "deploy" / "k3s" / "backup.sh").chmod(0o755)
    r = _backup(backup_tree, _clean_env(stubs))
    assert r.returncode != 0                               # the k3s stub fails every store
    out = fake_home / "cc-backups"
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    # The three copies were read from the derived checkout.
    for label in ("env", "env-app", "env-web"):
        assert list(out.glob(f"{label}_*.env")), f"{label}: {r.stdout}{r.stderr}"


def test_backup_reads_the_key_from_the_env_file_and_the_environment_beats_it(
        backup_tree: Path, fake_home: Path, stubs: Path, tmp_path: Path):
    (backup_tree / "deploy" / "k3s" / "backup.sh").chmod(0o755)
    from_file, from_env = tmp_path / "from-file", tmp_path / "from-env"
    with (backup_tree / "deploy" / "k3s" / ".env").open("a") as f:
        f.write(f"CC_BACKUP_DIR={from_file}\n")
    _backup(backup_tree, _clean_env(stubs))
    assert list(from_file.glob("env_*.env")) and not (fake_home / "cc-backups").exists()
    _backup(backup_tree, _clean_env(stubs, CC_BACKUP_DIR=str(from_env)))
    assert list(from_env.glob("env_*.env"))


def test_backup_fails_loud_when_the_owner_cannot_be_resolved(backup_tree: Path, stubs: Path, fake_home: Path):
    (backup_tree / "deploy" / "k3s" / "backup.sh").chmod(0o755)
    _exe(stubs / "stat", "echo UNKNOWN\n")
    r = _backup(backup_tree, _clean_env(stubs))
    assert r.returncode != 0
    assert "the owner of" in r.stderr, r.stderr
    assert not (fake_home / "cc-backups").exists()


def test_backup_takes_the_owner_from_repo_when_repo_is_given(backup_tree: Path, fake_home: Path, stubs: Path, tmp_path: Path):
    # The script is run from one tree, REPO names another: the env it copies and
    # the owner it asks about both follow REPO.
    other = tmp_path / "elsewhere" / "deploy" / "k3s"
    other.mkdir(parents=True)
    shutil.copy(K3S / "env-path.sh", other / "env-path.sh")
    repo = other.parent.parent
    (other / ".env").write_text("A=1\n")
    (repo / ".env").write_text("X=1\n")
    (repo / "web").mkdir()
    (repo / "web" / ".env").write_text("X=1\n")
    (backup_tree / "deploy" / "k3s" / "backup.sh").chmod(0o755)
    _backup(backup_tree, _clean_env(stubs, REPO=str(repo)))
    (copy,) = (fake_home / "cc-backups").glob("env_*.env")
    assert copy.read_text() == "A=1\n"


# ── the kubeconfig scripts, for real ────────────────────────────────────────


@pytest.mark.parametrize(("script", "outputs"), [
    ("make-litellm-kubeconfig.sh", [".cc-litellm-operator.kubeconfig", ".cc-litellm-logreader.kubeconfig"]),
    ("make-mcp-kubeconfig.sh", [".cc-mcp-deployer.kubeconfig"]),
    ("make-sandbox-kubeconfig.sh", [".cc-sandbox-runner.kubeconfig"]),
])
def test_a_kubeconfig_lands_in_the_owners_home_at_0600(tree: Path, stubs: Path, fake_home: Path, script: str, outputs: list[str]):
    k3s = tree / "deploy" / "k3s"
    shutil.copy(K3S / script, k3s / script)
    _exe(stubs / "sudo", 'echo Zm9v\n')                    # the Secret reads; base64 for the CA
    _exe(stubs / "kubectl", "exit 0\n")
    owner = _me(tree)[0]
    r = subprocess.run(["bash", str(k3s / script)], cwd="/", env=_clean_env(stubs, USER=owner, SUDO_USER=owner),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    for name in outputs:
        assert stat.S_IMODE((fake_home / name).stat().st_mode) == 0o600, name


def test_a_kubeconfig_script_stops_when_the_home_cannot_be_resolved(tree: Path, stubs: Path):
    k3s = tree / "deploy" / "k3s"
    shutil.copy(K3S / "make-mcp-kubeconfig.sh", k3s / "make-mcp-kubeconfig.sh")
    called = tree.parent / "sudo-called"
    _exe(stubs / "sudo", f'echo called >> "{called}"\n')
    _exe(stubs / "getent", "exit 2\n")
    r = subprocess.run(["bash", str(k3s / "make-mcp-kubeconfig.sh")], env=_clean_env(stubs),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and "the home of account" in r.stderr
    assert not called.exists()


# ── setup.sh's three rows ───────────────────────────────────────────────────


def _setup_rows() -> str:
    text = (K3S / "setup.sh").read_text(encoding="utf-8")
    start = text.index("  local owner_home\n")
    end = text.index("\n", text.index('"app-litellm-log-kubeconfig"', start)) + 1
    return text[start:end]


def _run_rows(tree: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    script = "\n".join([
        "set -uo pipefail",
        f"HERE='{tree}/deploy/k3s'",
        'APP_ENV=unused',
        'fail() { printf "FAIL %s\\n" "$1"; }',
        'set_kv_if_unset() { printf "%s=%s\\n" "$2" "$3"; }',
        "rows() {",
        _setup_rows(),
        "}",
        'rows; echo "rc=$?"',
    ])
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)


def test_setup_writes_the_three_paths_under_the_owners_home(tree: Path, stubs: Path, fake_home: Path):
    r = _run_rows(tree, _clean_env(stubs))
    assert r.stdout.splitlines() == [
        f"CC_MCP_DEPLOY_KUBECONFIG={fake_home}/.cc-mcp-deployer.kubeconfig",
        f"CC_LITELLM_KUBECONFIG={fake_home}/.cc-litellm-operator.kubeconfig",
        f"CC_LITELLM_LOG_KUBECONFIG={fake_home}/.cc-litellm-logreader.kubeconfig",
        "rc=0",
    ], r.stderr


def test_setup_fails_the_phase_and_writes_no_row_when_the_home_cannot_be_resolved(tree: Path, stubs: Path):
    _exe(stubs / "getent", "exit 2\n")
    r = _run_rows(tree, _clean_env(stubs))
    assert r.stdout.splitlines() == ["FAIL app-owner-home", "rc=1"]
    assert "the home of account" in r.stderr
