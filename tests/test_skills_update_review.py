"""The bundled-skill review (epic-skills-refresh-on-update R2): the rehearsal's R3
fixture, every row of its table, the invalidation rules, the never-partial rule
and the command line the two updaters build to.

Each test builds its own git repo: `skills/cc-test-alpha` at `v0` and `v1`, plus
`cc-test-beta` new at `v1`, with HEAD at `v0` and the library seeded from that
tree by the importer, so its rows carry real provenance.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.skills import importer, refresh
from central_command.skills.importer import bundled_text_hash, import_skill_folder
from tests.conftest import needs_pg

ALPHA = "cc-test-alpha"
BETA = "cc-test-beta"
WORKTREE = Path(__file__).resolve().parents[1]

# Far enough apart that an edit at line 2 and one at line 10 merge cleanly.
ONE_V0 = "".join(f"line {i}\n" for i in range(1, 13))
ONE_V1 = ONE_V0.replace("line 10\n", "line ten, as the release says\n")
FRONTMATTER = "---\ntitle: a reference\n---\n\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        cwd=root, check=True, capture_output=True,
    )


def _write(root: Path, rel: str, text: str | bytes) -> None:
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))


def _commit(root: Path, tag: str) -> None:
    _git(root, "add", "--all")
    _git(root, "commit", "-q", "--allow-empty", "-m", tag)
    _git(root, "tag", tag)


@pytest.fixture()
def fixture_repo(tmp_path, monkeypatch):
    """The R3 repo, HEAD at v0, with the root seam and an empty overlay on it."""
    root = (tmp_path / "checkout").resolve()
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "core.autocrlf", "false")
    a = f"skills/{ALPHA}"
    _write(root, "VERSION", "version=0\n")
    _write(root, f"{a}/SKILL.md", "---\nname: alpha\ndescription: a test skill\n---\n\n# Alpha\nsteps\n")
    _write(root, f"{a}/references/one.md", FRONTMATTER + ONE_V0)
    _write(root, f"{a}/references/two.md", "# Two\nstays the same\n")
    _write(root, f"{a}/references/gone.md", "# Gone\ndeleted by v1\n")
    _commit(root, "v0")

    _write(root, "VERSION", "version=1\n")
    _write(root, f"{a}/references/one.md", FRONTMATTER + ONE_V1)
    (root / a / "references" / "gone.md").unlink()
    _write(root, f"{a}/references/born.md", "# Born\nadded by v1\n")
    _write(root, f"skills/{BETA}/SKILL.md", "---\nname: beta\n---\n\n# Beta\n")
    _write(root, f"skills/{BETA}/references/b.md", "# B\n")
    _commit(root, "v1")

    _git(root, "checkout", "-q", "--detach", "v0")
    monkeypatch.setattr(settings, "repo_root", str(root))
    _overlay(tmp_path, monkeypatch)
    return root


def _overlay(tmp_path: Path, monkeypatch, **fields: str) -> None:
    d = tmp_path / "overlay"
    d.mkdir(exist_ok=True)
    for name, text in fields.items():
        (d / f"{name}.md").write_text(text + "\n")
    monkeypatch.setattr(settings, "skill_overlay_dir", str(d))


async def _sweep() -> None:
    conn = await repo._conn()
    try:
        await conn.execute("delete from skill_doc_review")
        await conn.execute("delete from skill where id like 'cc-test-%'")
        # A bundled text nobody references any more is a leftover of a test's
        # import (the same sweep as the import and store test files).
        await conn.execute(
            "delete from skill_bundled_text where hash not in "
            "(select bundled_hash from skill_doc where bundled_hash is not null)"
        )
        await conn.execute("delete from audit_event where kind = 'skill.review_computed'")
    finally:
        await conn.close()


@pytest.fixture()
async def clean_skills():
    await _sweep()
    yield
    await _sweep()


async def _seed(root: Path) -> None:
    await import_skill_folder(root / "skills" / ALPHA, skill_id=ALPHA)


async def _hand_post(doc_key: str, content: str) -> None:
    """A version with no provenance, as a session's or the operator's post."""
    await repo.add_skill_doc(ALPHA, doc_key, "reference", doc_key, content)


async def _review(target: str = "1", ref: str = "v1") -> dict:
    review = await refresh.compute_review(target, ref)
    return {(r["skill_id"], r["doc_key"]): r for r in review["items"]}


async def _resolve(target: str, doc_key: str, verdict: str = "keep_mine") -> None:
    """The stored half of a verdict; the route that checks it is story 3's."""
    conn = await repo._conn()
    try:
        await conn.execute(
            "update skill_doc_review set verdict = $3, resolved_at = now() "
            "where target = $1 and skill_id = $2 and doc_key = $4",
            target, ALPHA, verdict, doc_key,
        )
    finally:
        await conn.close()


async def _versions(skill_id: str) -> int:
    conn = await repo._conn()
    try:
        return await conn.fetchval("select count(*) from skill_doc where skill_id = $1", skill_id)
    finally:
        await conn.close()


# --- every row of the R3 table ------------------------------------------------


@needs_pg
@pytest.mark.asyncio
async def test_one_untouched_is_unmodified(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    row = (await _review())[(ALPHA, "one")]
    assert row["status"] == "unmodified"
    assert row["base_origin"] == "library"
    assert row["ours_hash"] == row["base_hash"] == bundled_text_hash(ONE_V0)
    assert row["theirs_hash"] == bundled_text_hash(ONE_V1)
    assert row["bundled_path"] == f"skills/{ALPHA}/references/one.md"
    assert row["merged"] is None
    assert not refresh.needs_verdict(row)


@needs_pg
@pytest.mark.asyncio
async def test_one_with_a_non_overlapping_edit_is_modified_and_merges_clean(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 2\n", "line two, by hand\n"))
    row = (await _review())[(ALPHA, "one")]
    assert row["status"] == "modified"
    assert row["conflicts"] == 0
    assert "line two, by hand\n" in row["merged"]
    assert "line ten, as the release says\n" in row["merged"]
    assert "<<<<<<<" not in row["merged"]
    # A clean automatic merge still asks: nothing resolves itself.
    assert refresh.needs_verdict(row)


@needs_pg
@pytest.mark.asyncio
async def test_one_with_an_overlapping_edit_conflicts_with_diff3_markers(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "line ten, by hand\n"))
    row = (await _review())[(ALPHA, "one")]
    assert row["status"] == "modified"
    assert row["conflicts"] == 1
    merged = row["merged"]
    assert "<<<<<<< mine (library)\nline ten, by hand\n" in merged
    assert "||||||| base (v0)\nline 10\n" in merged
    assert "=======\nline ten, as the release says\n>>>>>>> release (v1)\n" in merged


@needs_pg
@pytest.mark.asyncio
async def test_two_re_posted_by_hand_is_unchanged_upstream(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _hand_post("two", "# Two\nmine now\n")
    row = (await _review())[(ALPHA, "two")]
    assert row["status"] == "unchanged-upstream"
    assert row["ours_hash"] != row["base_hash"]
    assert not refresh.needs_verdict(row)


@needs_pg
@pytest.mark.asyncio
async def test_gone_untouched_is_retired_upstream_and_unlisted(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    row = (await _review())[(ALPHA, "gone")]
    assert row["status"] == "retired-upstream"
    assert row["theirs_hash"] is None
    assert row["merged"] is None
    assert not refresh.needs_verdict(row)


@needs_pg
@pytest.mark.asyncio
async def test_gone_re_posted_is_retired_upstream_and_listed(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _hand_post("gone", "# Gone\nI still want this\n")
    row = (await _review())[(ALPHA, "gone")]
    assert row["status"] == "retired-upstream"
    assert row["merged"] is None
    assert refresh.needs_verdict(row)


@needs_pg
@pytest.mark.asyncio
async def test_one_retired_by_the_operator_is_retired_locally_and_listed(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await repo.retire_skill_doc(ALPHA, "one", "not wanted")
    row = (await _review())[(ALPHA, "one")]
    assert row["status"] == "retired-locally"
    assert row["ours_hash"] is None
    assert row["theirs_hash"] == bundled_text_hash(ONE_V1)
    assert row["merged"] is None
    assert refresh.needs_verdict(row)


@needs_pg
@pytest.mark.asyncio
async def test_born_and_a_whole_new_skill_are_new(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    rows = await _review()
    assert rows[(ALPHA, "born")]["status"] == "new"
    assert rows[(ALPHA, "born")]["ours_hash"] is None
    assert rows[(ALPHA, "born")]["base_hash"] is None
    assert rows[(BETA, "guidance")]["status"] == "new"
    assert rows[(BETA, "b")]["status"] == "new"
    assert rows[(BETA, "guidance")]["bundled_path"] == f"skills/{BETA}/SKILL.md"
    assert not refresh.needs_verdict(rows[(ALPHA, "born")])


@needs_pg
@pytest.mark.asyncio
async def test_a_hand_made_doc_under_a_bundled_skill_is_local_only(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _hand_post("notes", "# My notes\n")
    row = (await _review())[(ALPHA, "notes")]
    assert row["status"] == "local-only"
    assert row["bundled_path"] == ""
    assert row["base_hash"] is None
    assert row["theirs_hash"] is None
    assert not refresh.needs_verdict(row)


@needs_pg
@pytest.mark.asyncio
async def test_a_retired_skill_is_one_informational_row_and_nothing_is_posted(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await repo.retire_skill(ALPHA, "not wanted")
    before = await _versions(ALPHA)
    review = await refresh.compute_review("1", "v1")
    alpha = [r for r in review["items"] if r["skill_id"] == ALPHA]
    assert [(r["doc_key"], r["status"]) for r in alpha] == [
        (refresh.WHOLE_SKILL_KEY, "skill-retired")
    ]
    assert not refresh.needs_verdict(alpha[0])
    assert review["counts"]["skill_retired"] == 1
    assert await _versions(ALPHA) == before


@needs_pg
@pytest.mark.asyncio
async def test_one_with_no_provenance_anywhere_takes_the_installed_tree_as_base(
    clean_skills, fixture_repo
):
    await repo.create_skill(ALPHA, "alpha", "a test skill")
    await _hand_post("one", ONE_V0)
    rows = await _review()
    row = rows[(ALPHA, "one")]
    assert row["status"] == "unmodified"
    assert row["base_origin"] == "installed-tree"
    assert row["base_hash"] == bundled_text_hash(ONE_V0)


# --- the edges the table leaves to the rule -----------------------------------


@needs_pg
@pytest.mark.asyncio
async def test_a_retired_doc_the_release_did_not_change_is_unchanged_upstream(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await repo.retire_skill_doc(ALPHA, "two", "not wanted")
    row = (await _review())[(ALPHA, "two")]
    assert row["status"] == "unchanged-upstream"
    assert not refresh.needs_verdict(row)


@needs_pg
@pytest.mark.asyncio
async def test_a_doc_retired_on_both_sides_asks_nothing(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await repo.retire_skill_doc(ALPHA, "gone", "not wanted")
    row = (await _review())[(ALPHA, "gone")]
    assert row["status"] == "local-only"
    assert not refresh.needs_verdict(row)


@needs_pg
@pytest.mark.asyncio
async def test_a_doc_kept_after_its_retirement_upstream_is_not_listed_again(
    clean_skills, fixture_repo
):
    """Keep-mine leaves a current doc with provenance and no file at installed
    or target: the library's own now, until the file returns upstream."""
    await _seed(fixture_repo)
    await _hand_post("gone", "# Gone\nI still want this\n")
    _git(fixture_repo, "checkout", "-q", "--detach", "v1")
    _write(fixture_repo, "VERSION", "version=2\n")
    _commit(fixture_repo, "v2")
    row = (await _review("2", "v2"))[(ALPHA, "gone")]
    assert row["status"] == "local-only"
    assert not refresh.needs_verdict(row)


@needs_pg
@pytest.mark.asyncio
async def test_a_hand_made_doc_the_release_now_ships_needs_a_verdict(clean_skills, fixture_repo):
    """No base at all: the two texts have no common ancestor, and overwriting
    the operator's doc silently is the one thing the review exists to prevent."""
    await _seed(fixture_repo)
    await _hand_post("born", "# Born\nmine first\n")
    row = (await _review())[(ALPHA, "born")]
    assert row["status"] == "modified"
    assert row["base_hash"] is None
    assert row["conflicts"] == 1
    assert refresh.needs_verdict(row)


@needs_pg
@pytest.mark.asyncio
async def test_modified_is_decided_against_base_even_when_ours_equals_theirs(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V1)
    row = (await _review())[(ALPHA, "one")]
    assert row["status"] == "modified"
    assert row["conflicts"] == 0
    assert row["merged"] == ONE_V1


@needs_pg
@pytest.mark.asyncio
async def test_base_is_the_newest_provenance_row_not_the_current_one(clean_skills, fixture_repo):
    """Hand posts move ours, never the base; an installed tree that moved past
    what the library received does not move it either."""
    await _seed(fixture_repo)
    await _hand_post("one", "# by hand, once\n")
    await _hand_post("one", ONE_V0)
    row = (await _review())[(ALPHA, "one")]
    assert row["status"] == "unmodified"
    assert row["base_origin"] == "library"


@needs_pg
@pytest.mark.asyncio
async def test_a_provenance_hash_with_no_stored_text_raises(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    conn = await repo._conn()
    try:
        await conn.execute(
            "delete from skill_bundled_text where hash = $1", bundled_text_hash(ONE_V0),
        )
    finally:
        await conn.close()
    with pytest.raises(ValueError, match="no bundled text is stored"):
        await refresh.compute_review("1", "v1")
    assert await repo.list_skill_doc_review("1") == []


@needs_pg
@pytest.mark.asyncio
async def test_a_crlf_file_reads_as_the_import_read_it(clean_skills, fixture_repo):
    """An import reads through universal newlines, so the library holds LF. The
    same blob from git must not look like a release change."""
    _git(fixture_repo, "checkout", "-q", "--detach", "v1")
    _write(fixture_repo, f"skills/{ALPHA}/references/crlf.md", b"# CR\r\nLF\r\n")
    _commit(fixture_repo, "v3")
    await _seed(fixture_repo)
    row = (await _review("3", "v3"))[(ALPHA, "crlf")]
    assert row["status"] == "unchanged-upstream"
    assert row["theirs_hash"] == bundled_text_hash("# CR\nLF\n")


def test_a_file_that_is_not_utf8_raises_naming_it(tmp_path, fixture_repo):
    _git(fixture_repo, "checkout", "-q", "--detach", "v1")
    _write(fixture_repo, f"skills/{ALPHA}/references/bad.md", b"\xff\xfe not text\n")
    _commit(fixture_repo, "v3")
    with pytest.raises(ValueError, match=r"v3:skills/cc-test-alpha/references/bad\.md"):
        refresh.read_tree(fixture_repo, "v3", {})


def test_two_files_on_one_doc_key_raise_as_an_import_does(fixture_repo):
    _git(fixture_repo, "checkout", "-q", "--detach", "v1")
    _write(fixture_repo, f"skills/{ALPHA}/references/One.md", "# Same slug\n")
    _commit(fixture_repo, "v3")
    with pytest.raises(ValueError, match="collide on doc_key 'one'"):
        refresh.read_tree(fixture_repo, "v3", {})


def test_only_what_an_import_would_read_is_a_bundled_doc(fixture_repo):
    _git(fixture_repo, "checkout", "-q", "--detach", "v1")
    _write(fixture_repo, "skills/README.md", "# not a skill\n")
    _write(fixture_repo, "skills/no-skill-md/references/x.md", "# no SKILL.md\n")
    _write(fixture_repo, f"skills/{ALPHA}/notes.md", "# beside SKILL.md\n")
    _write(fixture_repo, f"skills/{ALPHA}/references/deep/x.md", "# below references\n")
    _write(fixture_repo, f"skills/{ALPHA}/references/data.json", "{}\n")
    _commit(fixture_repo, "v3")
    keys = set(refresh.read_tree(fixture_repo, "v3", {}))
    assert keys == {
        (ALPHA, "guidance"), (ALPHA, "one"), (ALPHA, "two"), (ALPHA, "born"),
        (BETA, "guidance"), (BETA, "b"),
    }


def test_the_real_bundled_tree_reads_and_renders():
    docs = refresh.read_tree(WORKTREE, "HEAD", {})
    assert (("litellm", "guidance")) in docs
    assert all("<!-- overlay" not in d.text for d in docs.values())


@needs_pg
@pytest.mark.asyncio
async def test_a_ref_with_no_bundled_skills_refuses_rather_than_retiring_everything(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    _git(fixture_repo, "checkout", "-q", "--detach", "v1")
    _git(fixture_repo, "rm", "-rq", "skills")
    _commit(fixture_repo, "v3")
    _git(fixture_repo, "checkout", "-q", "--detach", "v0")
    with pytest.raises(ValueError, match="carries no bundled skills"):
        await refresh.compute_review("3", "v3")


def test_a_git_merge_error_raises_and_is_never_read_as_a_conflict_count():
    """Git's negative exit status reaches a parent as 255."""
    with pytest.raises(ValueError, match="git merge-file failed"):
        refresh.merge_three_way("ours\n", "base\n", "bin\0ary\n", "0", "1")


# --- the overlay: comparison is over the rendered text ------------------------


def _marked(root: Path) -> None:
    """Move the repo to a v0/v1 pair whose `marked` doc holds an overlay marker."""
    body = "# Marked\nbefore\n<!-- overlay: model_server_layout -->\nafter\n"
    _git(root, "checkout", "-q", "--detach", "v0")
    _write(root, f"skills/{ALPHA}/references/marked.md", body)
    _commit(root, "m0")
    _write(root, f"skills/{ALPHA}/references/marked.md", body.replace("after", "after, in v1"))
    _commit(root, "m1")
    _git(root, "checkout", "-q", "--detach", "m0")


@needs_pg
@pytest.mark.asyncio
async def test_a_marked_doc_compares_rendered_text(
    clean_skills, fixture_repo, tmp_path, monkeypatch
):
    _marked(fixture_repo)
    _overlay(tmp_path, monkeypatch, model_server_layout="THE LAYOUT")
    await _seed(fixture_repo)
    rendered = "# Marked\nbefore\nTHE LAYOUT\nafter\n"

    # The same file at the target reads as unchanged. Hashed raw, it would
    # differ from what the library holds.
    row = (await _review("m0", "m0"))[(ALPHA, "marked")]
    assert row["status"] == "unchanged-upstream"
    assert row["theirs_hash"] == row["base_hash"] == bundled_text_hash(rendered)

    # A real change merges rendered text: no marker reaches the operator.
    await _hand_post("marked", rendered.replace("before", "before, by hand"))
    row = (await _review("m1", "m1"))[(ALPHA, "marked")]
    assert row["status"] == "modified"
    assert row["conflicts"] == 0
    assert row["merged"] == "# Marked\nbefore, by hand\nTHE LAYOUT\nafter, in v1\n"


@needs_pg
@pytest.mark.asyncio
async def test_an_overlay_edit_alone_is_never_a_conflict(
    clean_skills, fixture_repo, tmp_path, monkeypatch
):
    _marked(fixture_repo)
    _overlay(tmp_path, monkeypatch, model_server_layout="THE LAYOUT")
    await _seed(fixture_repo)
    _overlay(tmp_path, monkeypatch, model_server_layout="ANOTHER LAYOUT")
    row = (await _review("m0", "m0"))[(ALPHA, "marked")]
    assert row["status"] == "unmodified"
    assert not refresh.needs_verdict(row)
    assert row["theirs_hash"] == bundled_text_hash("# Marked\nbefore\nANOTHER LAYOUT\nafter\n")


# --- invalidation -------------------------------------------------------------


@needs_pg
@pytest.mark.asyncio
async def test_a_review_for_another_target_drops_every_row_of_the_old_one(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    await _review()
    await _resolve("1", "one")
    assert await repo.list_skill_doc_review("1")

    await _review("1b", "v1")

    assert await repo.list_skill_doc_review("1") == []
    rows = {r["doc_key"]: r for r in await repo.list_skill_doc_review("1b")}
    assert rows["one"]["verdict"] is None


@needs_pg
@pytest.mark.asyncio
async def test_a_recompute_keeps_a_verdict_whose_hashes_did_not_move(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    await _review()
    await _resolve("1", "one", "take_release")
    row = (await _review())[(ALPHA, "one")]
    assert row["verdict"] == "take_release"
    assert row["resolved_at"] is not None


@needs_pg
@pytest.mark.asyncio
async def test_a_hand_post_voids_that_docs_verdict_only(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    await _hand_post("gone", "# Gone\nmine\n")
    await _review()
    await _resolve("1", "one")
    await _resolve("1", "gone")

    await _hand_post("one", ONE_V0.replace("line 10\n", "mine, later\n"))

    review = await refresh.compute_review("1", "v1")
    rows = {r["doc_key"]: r for r in review["items"] if r["skill_id"] == ALPHA}
    assert rows["one"]["verdict"] is None
    assert rows["one"]["resolved_at"] is None
    assert rows["gone"]["verdict"] == "keep_mine"
    assert [r["doc_key"] for r in refresh.unresolved_rows(review["items"])] == ["one"]
    assert review["counts"]["unresolved"] == 1


@needs_pg
@pytest.mark.asyncio
async def test_a_moved_target_text_voids_the_verdict(clean_skills, fixture_repo):
    """The same target from a ref whose file changed: theirs_hash moved."""
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    await _review()
    await _resolve("1", "one")

    _git(fixture_repo, "checkout", "-q", "--detach", "v1")
    _write(fixture_repo, f"skills/{ALPHA}/references/one.md", ONE_V1 + "and more\n")
    _commit(fixture_repo, "v1-again")
    _git(fixture_repo, "checkout", "-q", "--detach", "v0")

    row = (await _review("1", "v1-again"))[(ALPHA, "one")]
    assert row["verdict"] is None
    assert row["ref"] == "v1-again"


# --- never partially stored ---------------------------------------------------


@needs_pg
@pytest.mark.asyncio
async def test_a_failure_in_the_computation_leaves_the_stored_review_alone(
    clean_skills, fixture_repo, monkeypatch
):
    await _seed(fixture_repo)
    await _hand_post("two", "# Two\nmine\n")
    await _review()
    before = await repo.list_skill_doc_review("1")

    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))

    def boom(*a, **k):
        raise ValueError("merge exploded")

    monkeypatch.setattr(refresh, "merge_three_way", boom)
    with pytest.raises(ValueError, match="merge exploded"):
        await refresh.compute_review("1b", "v1")

    assert await repo.list_skill_doc_review("1") == before
    assert await repo.list_skill_doc_review("1b") == []


@needs_pg
@pytest.mark.asyncio
async def test_a_failure_in_the_write_rolls_the_whole_review_back(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _review()
    before = await repo.list_skill_doc_review("1")
    computed = [
        {k: r[k] for k in repo._SKILL_REVIEW_COMPUTED} for r in before
    ]
    # Postgres refuses a NUL in text, and this row is written last.
    computed[-1]["merged"] = "not\0text"
    with pytest.raises(Exception):
        await repo.replace_skill_doc_review("1b", "v1", computed)
    assert await repo.list_skill_doc_review("1") == before
    assert await repo.list_skill_doc_review("1b") == []


@needs_pg
@pytest.mark.asyncio
async def test_a_missing_ref_raises_and_leaves_the_stored_review_alone(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _review()
    before = await repo.list_skill_doc_review("1")
    with pytest.raises(ValueError, match="no-such-ref"):
        await refresh.compute_review("1", "no-such-ref")
    assert await repo.list_skill_doc_review("1") == before


# --- the event ----------------------------------------------------------------


@needs_pg
@pytest.mark.asyncio
async def test_the_event_carries_counts_and_never_doc_text(clean_skills, fixture_repo):
    import json

    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    review = await refresh.compute_review("1", "v1")
    assert review["counts"] == {
        "unmodified": 0, "new": 3, "modified": 1, "unresolved": 1,
        "retired_upstream": 1, "retired_locally": 0, "skill_retired": 0,
    }
    conn = await repo._conn()
    try:
        rows = await conn.fetch(
            "select ref_id, payload from audit_event where kind = 'skill.review_computed'"
        )
    finally:
        await conn.close()
    assert len(rows) == 1
    assert rows[0]["ref_id"] == "1"
    payload = json.loads(rows[0]["payload"])
    assert payload == {"target": "1", "counts": review["counts"]}


# --- the root seam and the schema ---------------------------------------------


def test_the_setting_moves_the_one_root_seam(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "repo_root", str(tmp_path))
    assert importer.checkout_root() == tmp_path
    monkeypatch.setattr(settings, "repo_root", "")
    assert importer.checkout_root() == WORKTREE


@needs_pg
@pytest.mark.asyncio
async def test_the_review_table_has_the_contracts_columns_and_key():
    """The table reached the test database through `schema.sql`, the file the
    updater's schema phase applies."""
    conn = await repo._conn()
    try:
        cols = {
            r["column_name"]: r["is_nullable"]
            for r in await conn.fetch(
                "select column_name, is_nullable from information_schema.columns "
                "where table_name = 'skill_doc_review'"
            )
        }
        pk = await conn.fetch(
            "select a.attname from pg_index i join pg_attribute a "
            "on a.attrelid = i.indrelid and a.attnum = any(i.indkey) "
            "where i.indrelid = 'skill_doc_review'::regclass and i.indisprimary"
        )
    finally:
        await conn.close()
    assert cols == {
        "target": "NO", "ref": "NO", "skill_id": "NO", "doc_key": "NO", "status": "NO",
        "bundled_path": "NO", "base_hash": "YES", "base_origin": "NO", "ours_hash": "YES",
        "theirs_hash": "YES", "merged": "YES", "conflicts": "NO", "verdict": "YES",
        "resolved_content": "YES", "resolved_at": "YES", "computed_at": "NO",
        "applied_at": "YES", "apply_error": "YES",
    }
    assert {r["attname"] for r in pk} == {"target", "skill_id", "doc_key"}


def test_the_review_table_is_created_idempotently():
    schema = (WORKTREE / "central_command" / "db" / "schema.sql").read_text()
    assert "create table if not exists skill_doc_review" in schema


# --- the tier ------------------------------------------------------------------


def test_nothing_under_runtime_imports_the_module():
    runtime = WORKTREE / "central_command" / "runtime"
    for f in runtime.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"skills\.refresh|skills import .*\brefresh\b", text), f


def test_the_module_loads_neither_the_gateway_nor_the_runtime():
    code = (
        "import sys, central_command.skills.refresh\n"
        "print([m for m in sys.modules if m.startswith("
        "('central_command.gateway', 'central_command.executor', 'central_command.runtime'))])"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(WORKTREE)}, cwd=WORKTREE,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"


# --- the command line the updaters build to -----------------------------------


def _cli(root: Path, *args: str, cwd: Path, database_url: str | None = None):
    """The real `python -m`, from another directory, against the test database."""
    env = {
        **os.environ,
        "PYTHONPATH": str(WORKTREE),
        "CC_REPO_ROOT": str(root),
        "CC_SKILL_OVERLAY_DIR": settings.skill_overlay_dir,
        "CC_DATABASE_URL": database_url or str(settings.database_url),
    }
    return subprocess.run(
        [sys.executable, "-m", "central_command.skills.refresh", *args],
        capture_output=True, text=True, env=env, cwd=cwd,
    )


@needs_pg
@pytest.mark.asyncio
async def test_review_exits_0_printing_counts_and_no_doc_text(clean_skills, fixture_repo, tmp_path):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "SECRET-LOOKING TEXT\n"))
    done = _cli(fixture_repo, "review", "--target", "1", "--ref", "v1", cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert "modified=1" in done.stdout
    assert "unresolved=1" in done.stdout
    assert "SECRET-LOOKING TEXT" not in done.stdout + done.stderr
    # `review` reports; only `check` is the gate.
    assert {r["doc_key"] for r in await repo.list_skill_doc_review("1")} >= {"one", "two"}


@needs_pg
@pytest.mark.asyncio
async def test_check_exits_3_naming_unresolved_docs(clean_skills, fixture_repo, tmp_path):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    await _hand_post("gone", "# Gone\nmine\n")
    done = _cli(fixture_repo, "check", "--target", "1", "--ref", "v1", cwd=tmp_path)
    assert done.returncode == 3, done.stdout + done.stderr
    assert f"{ALPHA}/one (modified)" in done.stdout
    assert f"{ALPHA}/gone (retired-upstream)" in done.stdout
    assert f"{ALPHA}/two" not in done.stdout


@needs_pg
@pytest.mark.asyncio
async def test_check_exits_0_after_verdict(clean_skills, fixture_repo, tmp_path):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    assert _cli(fixture_repo, "check", "--target", "1", "--ref", "v1", cwd=tmp_path).returncode == 3
    await _resolve("1", "one")
    done = _cli(fixture_repo, "check", "--target", "1", "--ref", "v1", cwd=tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "unresolved=0" in done.stdout


@needs_pg
@pytest.mark.asyncio
async def test_check_exits_0_when_nothing_was_ever_modified(clean_skills, fixture_repo, tmp_path):
    await _seed(fixture_repo)
    done = _cli(fixture_repo, "check", "--target", "1", "--ref", "v1", cwd=tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr


@needs_pg
@pytest.mark.asyncio
async def test_check_answers_for_the_library_now_not_at_stage_time(
    clean_skills, fixture_repo, tmp_path
):
    """A doc re-posted after the stage's review closes the gate without anyone
    recomputing by hand."""
    await _seed(fixture_repo)
    assert _cli(fixture_repo, "review", "--target", "1", "--ref", "v1", cwd=tmp_path).returncode == 0
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    done = _cli(fixture_repo, "check", "--target", "1", "--ref", "v1", cwd=tmp_path)
    assert done.returncode == 3
    assert f"{ALPHA}/one" in done.stdout


@needs_pg
@pytest.mark.asyncio
async def test_check_exits_2_on_missing_ref(clean_skills, fixture_repo, tmp_path):
    await _seed(fixture_repo)
    done = _cli(fixture_repo, "check", "--target", "1", "--ref", "no-such-ref", cwd=tmp_path)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "no-such-ref" in done.stderr


def test_check_exits_2_on_an_unreadable_repo(tmp_path):
    (tmp_path / "overlay").mkdir()
    not_a_repo = tmp_path / "nothing"
    not_a_repo.mkdir()
    done = _cli(not_a_repo, "check", "--target", "1", "--ref", "v1", cwd=tmp_path)
    assert done.returncode == 2, done.stdout + done.stderr


def test_check_exits_2_when_the_database_is_down(fixture_repo, tmp_path):
    done = _cli(
        fixture_repo, "check", "--target", "1", "--ref", "v1", cwd=tmp_path,
        database_url="postgresql://nobody:nothing@127.0.0.1:1/nothing",
    )
    assert done.returncode == 2, done.stdout + done.stderr


def test_a_bad_command_line_exits_2(fixture_repo, tmp_path):
    assert _cli(fixture_repo, "check", "--target", "1", cwd=tmp_path).returncode == 2


def test_settings_come_from_the_checkouts_env_whatever_the_directory(tmp_path):
    """A7: the updaters run this from wherever they are, and the file is found
    from the package's location. A key set only in that file proves it."""
    env_file = WORKTREE / ".env"
    if not env_file.is_file():
        pytest.skip("this checkout has no .env")
    code = (
        "from central_command.config import settings\n"
        "from dotenv import dotenv_values\n"
        f"want = dotenv_values({str(env_file)!r}).get('CC_DATABASE_URL')\n"
        "print(want is None or settings.database_url == want)"
    )
    env = {k: v for k, v in os.environ.items() if k != "CC_DATABASE_URL"}
    env["PYTHONPATH"] = str(WORKTREE)
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "True"
