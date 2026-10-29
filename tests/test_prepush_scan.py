"""scripts/prepush-scan.sh scans every commit a push publishes (R9, PROCESS-030).

The guard used to diff `rsha..lsha`, and for a NEW ref (rsha all zeros) it
scanned `lsha` alone -- so a new tag, or a new branch, was scanned for nothing
beyond its tip, and a secret added and reverted inside one push was never seen.
Now every published commit is scanned one at a time, an existing ref keeps the
old net diff on top, an annotated tag has its MESSAGE scanned (never its tagger
line, which carries the operator's identity and would trip the private list on
every release tag), and a git failure refuses instead of passing.

Every case runs the real script with git's stdin protocol
(`<lref> <lsha> <rref> <rsha>`) in a clone of a temp bare remote, with
CC_PRIVATE_IDENTIFIERS pointed at a temp list. Secret shapes are BUILT AT
RUNTIME: a literal one here would make the guard refuse the push that adds this
file. A couple of cases also drive a real `git push` with the hook installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCAN = ROOT / "scripts" / "prepush-scan.sh"
ZERO = "0" * 40
IDENT = "zq7-operator-identity"

# Built from parts so no line of this file matches SECRET_SHAPES.
SHAPE = "gh" + "p_" + "A1b2C3d4E5" * 3
SHAPE2 = "AK" + "IA" + "Q" * 16


@pytest.fixture
def repo(tmp_path: Path):
    if os.name == "nt" or not shutil.which("bash") or not shutil.which("git"):
        pytest.skip("POSIX bash and git needed")
    bare, work = tmp_path / "remote.git", tmp_path / "work"
    ident = tmp_path / "identifiers"
    ident.write_text(f"# test list\n{IDENT}\n")
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(work)], check=True)
    r = Repo(work, ident)
    r.git("remote", "add", "origin", str(bare))
    r.commit("README.md", "hello\n", "base")
    r.git("push", "-q", "origin", "master")
    return r


class Repo:
    def __init__(self, work: Path, ident: Path):
        self.work, self.ident = work, ident

    def git(self, *args: str, env: dict | None = None) -> str:
        e = {**os.environ, "GIT_AUTHOR_NAME": "Test Author", "GIT_AUTHOR_EMAIL": "t@example.com",
             "GIT_COMMITTER_NAME": "Test Author", "GIT_COMMITTER_EMAIL": "t@example.com", **(env or {})}
        return subprocess.run(["git", "-C", str(self.work), *args], check=True, capture_output=True,
                              text=True, env=e).stdout.strip()

    def commit(self, path: str, text: str, msg: str) -> str:
        p = self.work / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        self.git("add", path)
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def scan(self, *refs: tuple[str, str, str, str]) -> subprocess.CompletedProcess:
        stdin = "".join(" ".join(r) + "\n" for r in refs)
        return subprocess.run(["bash", str(SCAN)], input=stdin, capture_output=True, text=True,
                              cwd=self.work, timeout=60,
                              env={**os.environ, "CC_PRIVATE_IDENTIFIERS": str(self.ident)})

    @property
    def tip(self) -> str:
        return self.git("rev-parse", "HEAD")


def _new(ref: str, sha: str):
    return (ref, sha, ref, ZERO)


# ── new refs ────────────────────────────────────────────────────────────────

def test_new_branch_carrying_a_shape_is_refused(repo: Repo):
    repo.git("checkout", "-q", "-b", "feat")
    sha = repo.commit("a.txt", f"token = {SHAPE}\n", "add")
    r = repo.scan(_new("refs/heads/feat", sha))
    assert r.returncode == 1 and "SECRET SHAPE" in r.stderr


def test_new_lightweight_tag_on_a_commit_carrying_a_shape_is_refused(repo: Repo):
    sha = repo.commit("a.txt", f"token = {SHAPE}\n", "add")
    repo.git("tag", "v1")
    assert repo.scan(_new("refs/tags/v1", sha)).returncode == 1


def test_new_annotated_tag_on_a_commit_carrying_a_shape_is_refused(repo: Repo):
    repo.commit("a.txt", f"token = {SHAPE}\n", "add")
    repo.git("tag", "-a", "v1", "-m", "release")
    assert repo.scan(_new("refs/tags/v1", repo.git("rev-parse", "v1"))).returncode == 1


def test_new_lightweight_tag_on_published_commits_passes(repo: Repo):
    repo.git("tag", "v1")
    assert repo.scan(_new("refs/tags/v1", repo.tip)).returncode == 0


def test_new_annotated_tag_on_published_commits_passes(repo: Repo):
    repo.git("tag", "-a", "v1", "-m", "release 1")
    assert repo.scan(_new("refs/tags/v1", repo.git("rev-parse", "v1"))).returncode == 0


def test_new_annotated_tag_whose_message_carries_a_shape_is_refused(repo: Repo):
    repo.git("tag", "-a", "v1", "-m", f"release\n\nkey {SHAPE2}")
    r = repo.scan(_new("refs/tags/v1", repo.git("rev-parse", "v1")))
    assert r.returncode == 1 and "SECRET SHAPE" in r.stderr


def test_new_annotated_tag_whose_tagger_matches_the_list_but_message_is_clean_passes(repo: Repo):
    repo.git("tag", "-a", "v1", "-m", "release 1",
             env={"GIT_COMMITTER_NAME": IDENT, "GIT_COMMITTER_EMAIL": f"{IDENT}@example.com"})
    obj = repo.git("rev-parse", "v1")
    assert IDENT in repo.git("cat-file", "-p", obj), "the tagger line should carry the identifier"
    assert repo.scan(_new("refs/tags/v1", obj)).returncode == 0


def test_new_branch_carrying_a_private_identifier_is_refused(repo: Repo):
    repo.git("checkout", "-q", "-b", "feat")
    sha = repo.commit("a.txt", f"host {IDENT}\n", "add")
    r = repo.scan(_new("refs/heads/feat", sha))
    assert r.returncode == 1 and "PRIVATE IDENTIFIER" in r.stderr


def test_new_branch_with_a_secret_added_then_reverted_is_refused(repo: Repo):
    repo.git("checkout", "-q", "-b", "feat")
    repo.commit("a.txt", f"token = {SHAPE}\n", "add")
    sha = repo.commit("a.txt", "clean\n", "revert")
    assert SHAPE not in repo.git("diff", "master", sha), "the net diff must be clean for this case"
    assert repo.scan(_new("refs/heads/feat", sha)).returncode == 1


def test_new_branch_with_a_merged_in_side_branch_carrying_a_shape_is_refused(repo: Repo):
    repo.git("checkout", "-q", "-b", "side")
    repo.commit("s.txt", f"token = {SHAPE}\n", "side")
    repo.git("checkout", "-q", "master")
    repo.commit("m.txt", "m\n", "main work")
    repo.git("merge", "-q", "--no-ff", "-m", "merge side", "side")
    assert repo.scan(_new("refs/heads/feat", repo.tip)).returncode == 1


def test_new_branch_whose_added_line_itself_starts_with_a_plus_is_refused(repo: Repo):
    # The old filter (`^\+[^+]`) dropped an added line whose content began "+".
    repo.git("checkout", "-q", "-b", "feat")
    sha = repo.commit("a.txt", f"+{SHAPE}\n", "add")
    assert repo.scan(_new("refs/heads/feat", sha)).returncode == 1


def test_new_branch_shape_under_docs_vendor_is_still_excluded(repo: Repo):
    repo.git("checkout", "-q", "-b", "feat")
    sha = repo.commit("docs/vendor/x.md", f"{SHAPE}\n", "vendor")
    assert repo.scan(_new("refs/heads/feat", sha)).returncode == 0


# ── existing refs ───────────────────────────────────────────────────────────

def test_existing_branch_with_a_secret_added_then_reverted_is_refused(repo: Repo):
    old = repo.tip
    repo.commit("a.txt", f"token = {SHAPE}\n", "add")
    new = repo.commit("a.txt", "clean\n", "revert")
    assert SHAPE not in repo.git("diff", old, new), "the net diff must be clean for this case"
    r = repo.scan(("refs/heads/master", new, "refs/heads/master", old))
    assert r.returncode == 1 and "SECRET SHAPE" in r.stderr


def test_existing_branch_with_a_secret_in_the_net_diff_is_refused(repo: Repo):
    old = repo.tip
    new = repo.commit("a.txt", f"token = {SHAPE}\n", "add")
    assert repo.scan(("refs/heads/master", new, "refs/heads/master", old)).returncode == 1


def test_existing_branch_with_an_unknown_remote_sha_scans_the_range_and_refuses(repo: Repo):
    new = repo.commit("a.txt", f"token = {SHAPE}\n", "add")
    unknown = "ab" * 20
    assert repo.scan(("refs/heads/master", new, "refs/heads/master", unknown)).returncode == 1


def test_existing_branch_with_an_unknown_remote_sha_and_clean_commits_passes(repo: Repo):
    new = repo.commit("a.txt", "clean\n", "add")
    assert repo.scan(("refs/heads/master", new, "refs/heads/master", "ab" * 20)).returncode == 0


def test_existing_branch_clean_push_passes(repo: Repo):
    old = repo.tip
    new = repo.commit("a.txt", "clean\n", "add")
    assert repo.scan(("refs/heads/master", new, "refs/heads/master", old)).returncode == 0


def test_existing_annotated_tag_moved_with_a_shape_in_its_message_is_refused(repo: Repo):
    repo.git("tag", "-a", "v1", "-m", "one")
    old = repo.git("rev-parse", "v1")
    repo.git("tag", "-f", "-a", "v1", "-m", f"two {SHAPE2}")
    new = repo.git("rev-parse", "v1")
    assert repo.scan(("refs/tags/v1", new, "refs/tags/v1", old)).returncode == 1


# ── deletes, failures, several refs ─────────────────────────────────────────

def test_branch_delete_passes(repo: Repo):
    assert repo.scan(("(delete)", ZERO, "refs/heads/feat", repo.tip)).returncode == 0


def test_clean_new_branch_passes(repo: Repo):
    repo.git("checkout", "-q", "-b", "feat")
    sha = repo.commit("a.txt", "clean\n", "add")
    assert repo.scan(_new("refs/heads/feat", sha)).returncode == 0


def test_a_git_failure_refuses_instead_of_passing(repo: Repo):
    r = repo.scan(_new("refs/heads/feat", "cd" * 20))
    assert r.returncode == 1 and "REFUSING refs/heads/feat" in r.stderr


def test_one_bad_ref_among_several_refuses_the_push(repo: Repo):
    clean = repo.tip
    repo.git("checkout", "-q", "-b", "feat")
    bad = repo.commit("a.txt", f"token = {SHAPE}\n", "add")
    r = repo.scan(_new("refs/tags/ok", clean), _new("refs/heads/feat", bad))
    assert r.returncode == 1 and "(refs/heads/feat)" in r.stderr


def test_the_refusal_output_names_the_ref(repo: Repo):
    repo.git("checkout", "-q", "-b", "feat")
    sha = repo.commit("a.txt", f"token = {SHAPE}\n", "add")
    assert "refs/heads/feat" in repo.scan(_new("refs/heads/feat", sha)).stderr


# ── the hook, driven by a real push ─────────────────────────────────────────

def _install(repo: Repo):
    hook = repo.work / ".git" / "hooks" / "pre-push"
    hook.write_text(f'#!/usr/bin/env bash\nexec bash "{SCAN}"\n')
    hook.chmod(0o755)


def _push(repo: Repo, *args: str):
    return subprocess.run(["git", "-C", str(repo.work), "push", *args], capture_output=True, text=True,
                          env={**os.environ, "CC_PRIVATE_IDENTIFIERS": str(repo.ident)})


def test_real_push_of_a_new_tag_on_an_unpublished_commit_with_a_shape_is_refused(repo: Repo):
    _install(repo)
    repo.commit("a.txt", f"token = {SHAPE}\n", "add")
    repo.git("tag", "-a", "v1", "-m", "release")
    r = _push(repo, "origin", "v1")
    assert r.returncode != 0 and "SECRET SHAPE" in r.stderr
    assert "v1" not in subprocess.run(["git", "-C", str(repo.work), "ls-remote", "--tags", "origin"],
                                      capture_output=True, text=True).stdout


def test_real_push_of_a_clean_new_tag_goes_through(repo: Repo):
    _install(repo)
    repo.git("tag", "-a", "v1", "-m", "release 1")
    assert _push(repo, "origin", "v1").returncode == 0
