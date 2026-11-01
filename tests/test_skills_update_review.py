"""The bundled-skill review (epic-skills-refresh-on-update R2): the rehearsal's R3
fixture, every row of its table, the invalidation rules, the never-partial rule
and the command line the two updaters build to.

Each test builds its own git repo: `skills/cc-test-alpha` at `v0` and `v1`, plus
`cc-test-beta` new at `v1`, with HEAD at `v0` and the library seeded from that
tree by the importer, so its rows carry real provenance.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from fastapi import HTTPException

from central_command.api import nerve_gateway, routes
from central_command.config import settings
from central_command.db import repo
from central_command.skills import importer, refresh
from central_command.skills.importer import bundled_text_hash, import_skill_folder
from tests.conftest import needs_pg, sweep_test_skills

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
    """The shared `clean_skills` cleanup plus this file's own tables: the stored
    review and the events a review, a verdict or an apply write. That is why the
    file keeps its own `clean_skills` fixture instead of the conftest one."""
    conn = await repo._conn()
    try:
        await conn.execute("delete from skill_doc_review")
    finally:
        await conn.close()
    await sweep_test_skills()
    conn = await repo._conn()
    try:
        await conn.execute(
            "delete from audit_event where kind in ('skill.review_computed', 'skill.review_resolved') "
            "or (kind like 'skill.%' and ref_id like 'cc-test-%') "
            "or (kind = 'skill.granted' and payload->>'skill_id' like 'cc-test-%')"
        )
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


# The module's tier and the schema's shapes: tests/test_skills_refresh_boundary.py.


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


# --- the review over the API (story 2.3; review-contract.md §Routes) ----------

COUNT_KEYS = {
    "unmodified", "new", "modified", "unresolved",
    "retired_upstream", "retired_locally", "skill_retired",
}
ITEM_KEYS = {
    "skill_id", "doc_key", "status", "bundled_path", "base_origin", "base", "ours",
    "theirs", "merged", "conflicts", "ours_hash", "theirs_hash", "needs_verdict",
    "verdict", "resolved_content", "resolved_at", "applied_at", "apply_error",
}


async def _post_review(target: str = "1", ref: str = "v1") -> dict:
    return await routes.compute_skill_review_route(routes.SkillReviewIn(target=target, ref=ref))


def _items(shape: dict) -> dict:
    return {(i["skill_id"], i["doc_key"]): i for i in shape["items"]}


async def _verdict(
    item: dict, verdict: str = "keep_mine", content: str | None = None, *,
    target: str = "1", **hashes,
) -> dict:
    body = routes.SkillReviewResolveIn(
        verdict=verdict, content=content,
        ours_hash=hashes.get("ours_hash", item["ours_hash"]),
        theirs_hash=hashes.get("theirs_hash", item["theirs_hash"]),
    )
    return await routes.resolve_skill_review_item_route(
        target, item["skill_id"], item["doc_key"], body
    )


async def _status_of(coro) -> int:
    with pytest.raises(HTTPException) as e:
        await coro
    return e.value.status_code


@needs_pg
@pytest.mark.asyncio
async def test_get_is_404_until_a_post_computes_and_then_serves_the_same_shape(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    assert await _status_of(routes.get_skill_review_route("1")) == 404

    posted = await _post_review()
    fetched = await routes.get_skill_review_route("1")

    assert posted == fetched
    assert set(fetched) == {"target", "ref", "computed_at", "counts", "items"}
    assert (fetched["target"], fetched["ref"]) == ("1", "v1")
    assert set(fetched["counts"]) == COUNT_KEYS
    assert all(set(i) == ITEM_KEYS for i in fetched["items"])
    assert fetched["counts"]["new"] == 3 and fetched["counts"]["unmodified"] == 1


@needs_pg
@pytest.mark.asyncio
async def test_each_item_carries_the_texts_its_hashes_name(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    hand = ONE_V0.replace("line 10\n", "line ten, by hand\n")
    await _hand_post("one", hand)
    items = _items(await _post_review())

    one = items[(ALPHA, "one")]
    assert (one["base"], one["ours"], one["theirs"]) == (ONE_V0, hand, ONE_V1)
    assert one["base_origin"] == "library" and one["status"] == "modified"
    assert one["conflicts"] == 1 and "<<<<<<< mine (library)" in one["merged"]
    assert one["needs_verdict"] and one["verdict"] is None and one["resolved_at"] is None
    for text, key in ((one["base"], "base_hash"), (one["ours"], "ours_hash"), (one["theirs"], "theirs_hash")):
        if key != "base_hash":
            assert bundled_text_hash(text) == one[key]

    born = items[(ALPHA, "born")]
    assert (born["status"], born["ours"], born["base"]) == ("new", None, None)
    assert born["theirs"] == "# Born\nadded by v1\n"
    gone = items[(ALPHA, "gone")]
    assert (gone["status"], gone["theirs"]) == ("retired-upstream", None)
    assert gone["ours"] == gone["base"] == "# Gone\ndeleted by v1\n"
    assert not gone["needs_verdict"]


@needs_pg
@pytest.mark.asyncio
async def test_a_doc_with_no_provenance_is_based_on_the_installed_tree(
    clean_skills, fixture_repo
):
    await repo.create_skill(ALPHA, "alpha", "a test skill")
    await repo.add_skill_doc(ALPHA, "one", "reference", "one", ONE_V0)
    one = _items(await _post_review())[(ALPHA, "one")]
    assert one["base_origin"] == "installed-tree"
    assert (one["base"], one["ours"], one["theirs"]) == (ONE_V0, ONE_V0, ONE_V1)
    assert one["status"] == "unmodified"


@needs_pg
@pytest.mark.asyncio
async def test_a_review_that_cannot_be_computed_stores_nothing_and_says_why(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    status = await _status_of(_post_review("9", "no-such-ref"))
    assert status == 422
    with pytest.raises(HTTPException) as e:
        await _post_review("9", "no-such-ref")
    assert "no-such-ref" in e.value.detail
    assert await _status_of(routes.get_skill_review_route("9")) == 404


@needs_pg
@pytest.mark.asyncio
async def test_a_library_that_moved_since_the_compute_is_a_409_not_a_stale_diff(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    await _post_review()
    await _hand_post("one", "someone edited it again\n")
    with pytest.raises(HTTPException) as e:
        await routes.get_skill_review_route("1")
    assert e.value.status_code == 409 and "recompute" in e.value.detail


@needs_pg
@pytest.mark.asyncio
async def test_a_verdict_is_stored_counted_and_announced(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    shape = await _post_review()
    assert shape["counts"]["unresolved"] == 1
    one = _items(shape)[(ALPHA, "one")]

    answer = await _verdict(one, "keep_mine")

    assert (answer["target"], answer["skill_id"], answer["doc_key"], answer["verdict"]) == (
        "1", ALPHA, "one", "keep_mine",
    )
    assert answer["resolved_at"] is not None and answer["counts"]["unresolved"] == 0
    again = _items(await routes.get_skill_review_route("1"))[(ALPHA, "one")]
    assert again["verdict"] == "keep_mine" and again["resolved_at"] == answer["resolved_at"]
    assert again["resolved_content"] is None
    (event,) = [
        e for e in await repo.list_events_of_kinds(["skill.review_resolved"]) if e["ref_id"] == ALPHA
    ]
    assert event["payload"] == {
        "target": "1", "skill_id": ALPHA, "doc_key": "one", "verdict": "keep_mine",
    }


@needs_pg
@pytest.mark.asyncio
async def test_a_verdict_replaces_a_verdict_and_there_is_no_way_back_to_none(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    one = _items(await _post_review())[(ALPHA, "one")]

    await _verdict(one, "edit", "my own text\n")
    stored = _items(await routes.get_skill_review_route("1"))[(ALPHA, "one")]
    assert (stored["verdict"], stored["resolved_content"]) == ("edit", "my own text\n")

    await _verdict(one, "take_release")
    stored = _items(await routes.get_skill_review_route("1"))[(ALPHA, "one")]
    assert (stored["verdict"], stored["resolved_content"]) == ("take_release", None)
    assert await _status_of(_verdict(one, "none")) == 422
    assert (await routes.get_skill_review_route("1"))["counts"]["unresolved"] == 0
    assert len(await repo.list_events_of_kinds(["skill.review_resolved"])) == 2


@needs_pg
@pytest.mark.asyncio
async def test_a_verdict_over_stale_hashes_is_a_409_and_stores_nothing(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    one = _items(await _post_review())[(ALPHA, "one")]

    assert await _status_of(_verdict(one, ours_hash="0" * 64)) == 409
    assert await _status_of(_verdict(one, theirs_hash="0" * 64)) == 409
    assert await _status_of(_verdict(one, theirs_hash=None)) == 409
    assert _items(await routes.get_skill_review_route("1"))[(ALPHA, "one")]["verdict"] is None
    assert await repo.list_events_of_kinds(["skill.review_resolved"]) == []


@needs_pg
@pytest.mark.asyncio
async def test_an_applied_row_takes_no_new_verdict(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    one = _items(await _post_review())[(ALPHA, "one")]
    conn = await repo._conn()
    try:
        await conn.execute("update skill_doc_review set applied_at = now() where doc_key = 'one'")
    finally:
        await conn.close()
    assert await _status_of(_verdict(one, "keep_mine")) == 409


@needs_pg
@pytest.mark.asyncio
async def test_the_store_itself_refuses_a_verdict_on_an_applied_row(clean_skills, fixture_repo):
    """The route refuses an applied row before it writes, but an apply can land
    between the route's read and its write: the one conditional UPDATE is the
    guard that holds then, so it is proven on its own, with matching hashes."""
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    one = _items(await _post_review())[(ALPHA, "one")]
    conn = await repo._conn()
    try:
        await conn.execute("update skill_doc_review set applied_at = now() where doc_key = 'one'")
    finally:
        await conn.close()
    saved = await repo.resolve_skill_doc_review(
        "1", ALPHA, "one", "keep_mine", None, one["ours_hash"], one["theirs_hash"]
    )
    assert saved is None
    row = await repo.get_skill_doc_review_row("1", ALPHA, "one")
    assert row["verdict"] is None


MARKER_LINES = [
    "<<<<<<< mine (library)",
    "||||||| base (v0)",
    ">>>>>>> release (v1)",
    "=======",
    "<<<<<<< x",
]


@needs_pg
@pytest.mark.asyncio
@pytest.mark.parametrize("marker", MARKER_LINES)
async def test_an_edit_holding_a_conflict_marker_line_is_refused(
    clean_skills, fixture_repo, marker
):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    one = _items(await _post_review())[(ALPHA, "one")]
    assert await _status_of(_verdict(one, "edit", f"fine\n{marker}\nmore\n")) == 422
    # CRLF from a browser textarea does not hide it.
    assert await _status_of(_verdict(one, "edit", f"fine\r\n{marker}\r\nmore\r\n")) == 422
    assert _items(await routes.get_skill_review_route("1"))[(ALPHA, "one")]["verdict"] is None


@needs_pg
@pytest.mark.asyncio
async def test_a_setext_underline_or_prose_that_looks_like_a_marker_is_not_one(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    one = _items(await _post_review())[(ALPHA, "one")]
    text = (
        "Title\n=====\n\nSub\n==========\n\n<<<<<<<\n>>>>>>>>\n<<<<<<<< x\n"
        "a ======= b\n ======= \n||||||| \n"
    )
    answer = await _verdict(one, "edit", text)
    assert answer["verdict"] == "edit"
    stored = _items(await routes.get_skill_review_route("1"))[(ALPHA, "one")]
    assert stored["resolved_content"] == text


@needs_pg
@pytest.mark.asyncio
async def test_the_verdict_shapes_the_contract_refuses(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    await _hand_post("gone", "# Gone\nedited by hand\n")
    items = _items(await _post_review())
    one, gone = items[(ALPHA, "one")], items[(ALPHA, "gone")]
    assert gone["status"] == "retired-upstream" and gone["needs_verdict"]

    assert await _status_of(_verdict(one, "edit", None)) == 422       # no text
    assert await _status_of(_verdict(one, "keep_mine", "text")) == 422  # content is for edit
    assert await _status_of(_verdict(one, "merge")) == 422            # unknown verdict
    assert await _status_of(_verdict(gone, "edit", "text\n")) == 422   # edit on a retired-* row
    assert (await _verdict(gone, "take_release"))["verdict"] == "take_release"
    # Nothing to decide on a doc that follows the release or is new.
    assert await _status_of(_verdict(items[(ALPHA, "born")], "keep_mine")) == 422


@needs_pg
@pytest.mark.asyncio
async def test_a_skill_retired_row_and_an_unknown_item_are_not_resolvable(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await repo.retire_skill(ALPHA, "no longer used")
    shape = await _post_review()
    whole = _items(shape)[(ALPHA, refresh.WHOLE_SKILL_KEY)]
    assert whole["status"] == "skill-retired" and not whole["needs_verdict"]
    assert whole["ours"] is None and whole["theirs"] is None
    # The row exists but has nothing to decide: 422. A row that does not exist: 404.
    assert await _status_of(_verdict(whole, "keep_mine")) == 422
    ghost = dict(whole, doc_key="no-such-doc")
    assert await _status_of(_verdict(ghost, "keep_mine")) == 404
    assert await _status_of(_verdict(whole, "keep_mine", target="nope")) == 404


@needs_pg
@pytest.mark.asyncio
async def test_a_recompute_keeps_a_verdict_over_unchanged_hashes(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))
    one = _items(await _post_review())[(ALPHA, "one")]
    await _verdict(one, "keep_mine")
    again = _items(await _post_review())[(ALPHA, "one")]
    assert again["verdict"] == "keep_mine"


@needs_pg
@pytest.mark.asyncio
async def test_the_rpc_methods_answer_like_the_routes(clean_skills, fixture_repo):
    noop = lambda *a, **k: None  # noqa: E731
    await _seed(fixture_repo)
    await _hand_post("one", ONE_V0.replace("line 10\n", "mine\n"))

    with pytest.raises(HTTPException) as e:
        await nerve_gateway._dispatch("skills.update_review_get", {"target": "1"}, notify=noop)
    assert e.value.status_code == 404

    posted = await nerve_gateway._dispatch(
        "skills.update_review", {"target": "1", "ref": "v1"}, notify=noop
    )
    got = await nerve_gateway._dispatch("skills.update_review_get", {"target": "1"}, notify=noop)
    assert posted == got == await routes.get_skill_review_route("1")

    one = _items(got)[(ALPHA, "one")]
    answer = await nerve_gateway._dispatch(
        "skills.update_review_resolve",
        {
            "target": "1", "skill_id": ALPHA, "doc_key": "one", "verdict": "edit",
            "content": "rpc text\n", "ours_hash": one["ours_hash"], "theirs_hash": one["theirs_hash"],
        },
        notify=noop,
    )
    assert answer["verdict"] == "edit" and answer["counts"]["unresolved"] == 0
    with pytest.raises(HTTPException) as e:
        await nerve_gateway._dispatch(
            "skills.update_review_resolve",
            {
                "target": "1", "skill_id": ALPHA, "doc_key": "one", "verdict": "keep_mine",
                "ours_hash": "0" * 64, "theirs_hash": one["theirs_hash"],
            },
            notify=noop,
        )
    assert e.value.status_code == 409


# --- apply: the contract's table, row by row (story 2.4) ------------------------


MINE = ONE_V0.replace("line 10\n", "mine\n")


async def _apply(target: str = "1") -> dict:
    return await routes.apply_skill_review_route(target, routes.SkillReviewApplyIn(actor="updater"))


async def _current(doc_key: str, skill_id: str = ALPHA) -> dict | None:
    docs = await repo.list_skill_docs(skill_id)
    return next((d for d in docs if d["doc_key"] == doc_key), None)


async def _bundled_text(text: str) -> dict | None:
    rows = await repo.get_skill_bundled_texts([bundled_text_hash(text)])
    return rows[0] if rows else None


async def _events(kind: str, skill_id: str = ALPHA) -> list[dict]:
    import json

    conn = await repo._conn()
    try:
        rows = await conn.fetch(
            "select actor, payload from audit_event where kind = $1 and ref_id = $2 order by id",
            kind, skill_id,
        )
    finally:
        await conn.close()
    return [{"actor": r["actor"], **json.loads(r["payload"])} for r in rows]


def _assert_provenance(doc: dict, *, added_by: str, content: str, theirs: str, path: str) -> None:
    assert doc["is_current"]
    assert doc["content"] == content
    assert doc["added_by"] == added_by
    assert doc["bundled_release"] == "1"
    assert doc["bundled_path"] == path
    assert doc["bundled_hash"] == bundled_text_hash(theirs)


ONE_PATH = f"skills/{ALPHA}/references/one.md"
GONE_PATH = f"skills/{ALPHA}/references/gone.md"


@needs_pg
@pytest.mark.asyncio
async def test_apply_unmodified_posts_theirs_as_the_updater(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    before = await _current("one")
    await _post_review()
    result = await _apply()

    doc = await _current("one")
    assert doc["version"] == before["version"] + 1
    _assert_provenance(doc, added_by="updater", content=ONE_V1, theirs=ONE_V1, path=ONE_PATH)
    assert (await _bundled_text(ONE_V1))["content"] == ONE_V1
    item = next(i for i in result["items"] if i["doc_key"] == "one")
    assert item["applied_at"] is not None and item["apply_error"] is None
    assert result["failed"] == 0
    # Nothing is edited in place: the version it superseded still reads as it did.
    history = await repo.skill_doc_history(ALPHA, "one")
    assert {d["content"] for d in history} == {ONE_V0, ONE_V1}
    # The new version is what search finds.
    conn = await repo._conn()
    try:
        chunks = await conn.fetch("select content from skill_chunk where doc_id = $1", doc["id"])
    finally:
        await conn.close()
    assert chunks and "line ten, as the release says" in "".join(c["content"] for c in chunks)


@needs_pg
@pytest.mark.asyncio
async def test_apply_new_posts_as_the_updater_and_creates_a_whole_new_skill(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await _post_review()
    assert await repo.get_skill(BETA) is None
    await _apply()

    _assert_provenance(
        await _current("born"), added_by="updater", content="# Born\nadded by v1\n",
        theirs="# Born\nadded by v1\n", path=f"skills/{ALPHA}/references/born.md",
    )
    skill = await repo.get_skill(BETA)
    assert (skill["title"], skill["summary"]) == ("beta", "Knowledge about beta")
    guidance = await _current("guidance", BETA)
    assert guidance["title"] == "beta" and guidance["added_by"] == "updater"
    assert guidance["content"] == "# Beta\n" and guidance["bundled_release"] == "1"
    b = await _current("b", BETA)
    assert (b["kind"], b["title"], b["content"]) == ("reference", "b", "# B\n")
    assert [e["title"] for e in await _events("skill.created", BETA)] == ["beta"]


@needs_pg
@pytest.mark.asyncio
async def test_apply_a_new_doc_leaves_its_existing_skill_alone(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await repo.create_skill(ALPHA, "the operator's title", "the operator's summary")
    await _post_review()
    await _apply()
    skill = await repo.get_skill(ALPHA)
    assert skill["title"] == "the operator's title"


@needs_pg
@pytest.mark.asyncio
async def test_a_whole_new_skills_default_grants_apply_once_and_create_only(
    clean_skills, fixture_repo
):
    """What the installers' import does for a new folder: an update that imports
    the skill first must not leave it with no holders."""
    conn = await repo._conn()
    try:
        holder = await conn.fetchval(
            "select id from agent where role <> '' and status = 'ACTIVE' order by id limit 1"
        )
    finally:
        await conn.close()
    _git(fixture_repo, "checkout", "-q", "--detach", "v1")
    _write(
        fixture_repo, f"skills/{BETA}/SKILL.md",
        f"---\nname: beta\ndefault_agents: {holder}, cc-test-nobody\n---\n\n# Beta\n",
    )
    _write(fixture_repo, f"skills/{ALPHA}/SKILL.md", f"---\nname: alpha\ndefault_agents: {holder}\n---\n\n# Alpha\nsteps\n")
    _commit(fixture_repo, "v1g")
    _git(fixture_repo, "checkout", "-q", "--detach", "v0")
    await _seed(fixture_repo)

    await _post_review("1", "v1g")
    await _apply()

    assert {r["skill_id"] for r in await repo.list_agent_skills(holder)} >= {BETA}
    assert ALPHA not in {r["skill_id"] for r in await repo.list_agent_skills(holder)}
    assert await _events("skill.granted", holder) == [
        {"actor": "seed:skill", "skill_id": BETA, "default": True}
    ]


@needs_pg
@pytest.mark.asyncio
async def test_apply_take_release_posts_theirs_as_the_operator(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _hand_post("one", MINE)
    items = _items(await _post_review())
    await _verdict(items[(ALPHA, "one")], "take_release")
    await _apply()
    _assert_provenance(
        await _current("one"), added_by="operator", content=ONE_V1, theirs=ONE_V1, path=ONE_PATH,
    )


@needs_pg
@pytest.mark.asyncio
async def test_apply_keep_mine_posts_a_new_version_of_ours_and_the_base_moves(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await _hand_post("one", MINE)
    before = await _current("one")
    items = _items(await _post_review())
    await _verdict(items[(ALPHA, "one")], "keep_mine")
    await _apply()

    doc = await _current("one")
    assert doc["version"] == before["version"] + 1
    assert doc["content"] == before["content"] == MINE
    _assert_provenance(doc, added_by="operator", content=MINE, theirs=ONE_V1, path=ONE_PATH)
    # The text row is the release's, not the posted content: it is the next base.
    assert (await _bundled_text(ONE_V1))["content"] == ONE_V1
    assert await _bundled_text(MINE) is None
    row = (await _review())[(ALPHA, "one")]
    assert row["status"] == "unchanged-upstream"
    assert row["base_hash"] == bundled_text_hash(ONE_V1)


@needs_pg
@pytest.mark.asyncio
async def test_apply_edit_posts_the_resolved_content_as_the_operator(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _hand_post("one", MINE)
    items = _items(await _post_review())
    await _verdict(items[(ALPHA, "one")], "edit", "the two together\n")
    await _apply()
    _assert_provenance(
        await _current("one"), added_by="operator", content="the two together\n",
        theirs=ONE_V1, path=ONE_PATH,
    )
    assert (await _bundled_text(ONE_V1))["content"] == ONE_V1


@needs_pg
@pytest.mark.asyncio
async def test_apply_retired_upstream_untouched_is_retired_by_the_updater(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    before = await repo.skill_doc_history(ALPHA, "gone")
    await _post_review()
    await _apply()

    assert await _current("gone") is None
    history = await repo.skill_doc_history(ALPHA, "gone")
    assert len(history) == len(before)
    assert max(history, key=lambda d: d["version"])["retired_reason"] == "removed in v1"
    assert await _events("skill.doc_retired") == [
        {"actor": "updater", "doc_key": "gone", "version": 1, "reason": "removed in v1"}
    ]


@needs_pg
@pytest.mark.asyncio
async def test_apply_retired_upstream_take_release_is_retired_by_the_operator(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await _hand_post("gone", "# Gone\nmine\n")
    items = _items(await _post_review())
    await _verdict(items[(ALPHA, "gone")], "take_release")
    await _apply()
    assert await _current("gone") is None
    assert [e["actor"] for e in await _events("skill.doc_retired")] == ["operator"]


@needs_pg
@pytest.mark.asyncio
async def test_apply_retired_upstream_keep_mine_does_nothing_and_closes_the_row(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await _hand_post("gone", "# Gone\nmine\n")
    before = await _versions(ALPHA)
    items = _items(await _post_review())
    await _verdict(items[(ALPHA, "gone")], "keep_mine")
    result = await _apply()

    assert (await _current("gone"))["content"] == "# Gone\nmine\n"
    assert await _events("skill.doc_retired") == []
    assert next(i for i in result["items"] if i["doc_key"] == "gone")["applied_at"] is not None
    # one + born are the only posts under alpha.
    assert await _versions(ALPHA) == before + 2


@needs_pg
@pytest.mark.asyncio
async def test_apply_retired_locally_take_release_reactivates_with_theirs(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await repo.retire_skill_doc(ALPHA, "one", "not wanted")
    items = _items(await _post_review())
    await _verdict(items[(ALPHA, "one")], "take_release")
    await _apply()
    doc = await _current("one")
    _assert_provenance(doc, added_by="operator", content=ONE_V1, theirs=ONE_V1, path=ONE_PATH)
    assert doc["version"] == 2


@needs_pg
@pytest.mark.asyncio
async def test_apply_retired_locally_keep_mine_stays_retired(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await repo.retire_skill_doc(ALPHA, "one", "not wanted")
    items = _items(await _post_review())
    await _verdict(items[(ALPHA, "one")], "keep_mine")
    await _apply()
    assert await _current("one") is None
    assert len(await repo.skill_doc_history(ALPHA, "one")) == 1


@needs_pg
@pytest.mark.asyncio
async def test_apply_posts_nothing_for_a_row_with_nothing_due(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _hand_post("two", "# Two\nmine\n")
    await _hand_post("notes", "# My notes\n")
    two, notes = await _current("two"), await _current("notes")
    await _post_review()
    result = await _apply()
    assert (await _current("two"))["id"] == two["id"]
    assert (await _current("notes"))["id"] == notes["id"]
    closed = {i["doc_key"]: i for i in result["items"] if i["skill_id"] == ALPHA}
    assert closed["two"]["applied_at"] and closed["notes"]["applied_at"]


@needs_pg
@pytest.mark.asyncio
async def test_apply_leaves_a_retired_skill_alone(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await repo.retire_skill(ALPHA, "not wanted")
    before = await _versions(ALPHA)
    await _post_review()
    result = await _apply()
    assert await _versions(ALPHA) == before
    assert result["failed"] == 0
    assert (await repo.get_skill(ALPHA))["status"] == "RETIRED"


# --- apply: title and summary follow the guidance doc's verdict (A2) ------------


def _release_with_new_frontmatter(root: Path, *, body: str = "# Alpha\nsteps\n") -> None:
    _git(root, "checkout", "-q", "--detach", "v1")
    _write(
        root, f"skills/{ALPHA}/SKILL.md",
        f"---\nname: alpha renamed\ndescription: a renamed skill\n---\n\n{body}",
    )
    _commit(root, "v1g")
    _git(root, "checkout", "-q", "--detach", "v0")


@needs_pg
@pytest.mark.asyncio
async def test_an_unmodified_guidance_doc_brings_the_title_and_summary(clean_skills, fixture_repo):
    _release_with_new_frontmatter(fixture_repo, body="# Alpha\nnew steps\n")
    await _seed(fixture_repo)
    await _post_review("1", "v1g")
    await _apply()
    skill = await repo.get_skill(ALPHA)
    assert (skill["title"], skill["summary"]) == ("alpha renamed", "a renamed skill")
    guidance = await _current("guidance")
    assert guidance["title"] == "alpha renamed" and guidance["added_by"] == "updater"


@needs_pg
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdict,content,follows",
    [("take_release", None, True), ("keep_mine", None, False), ("edit", "# Merged\n", False)],
)
async def test_a_modified_guidance_doc_moves_the_title_only_on_take_release(
    clean_skills, fixture_repo, verdict, content, follows
):
    _release_with_new_frontmatter(fixture_repo, body="# Alpha\nnew steps\n")
    await _seed(fixture_repo)
    await repo.add_skill_doc(ALPHA, "guidance", "guidance", "alpha", "# Alpha\nmy steps\n")
    items = _items(await _post_review("1", "v1g"))
    await _verdict(items[(ALPHA, "guidance")], verdict, content)
    await _apply()

    skill = await repo.get_skill(ALPHA)
    want = ("alpha renamed", "a renamed skill") if follows else ("alpha", "a test skill")
    assert (skill["title"], skill["summary"]) == want
    guidance = await _current("guidance")
    assert guidance["title"] == want[0]
    assert guidance["added_by"] == "operator"
    assert guidance["bundled_release"] == "1"


# --- apply: re-verify, record, continue, re-run ----------------------------------


@needs_pg
@pytest.mark.asyncio
async def test_a_hand_post_after_the_review_leaves_the_doc_alone_and_the_rest_go_on(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await _post_review()
    await _hand_post("one", MINE)
    posted = await _current("one")

    result = await _apply()

    assert (await _current("one"))["id"] == posted["id"]
    assert result["failed"] == 1
    items = {i["doc_key"]: i for i in result["items"] if i["skill_id"] == ALPHA}
    assert "ours_hash" in items["one"]["apply_error"] and items["one"]["applied_at"] is None
    assert MINE not in items["one"]["apply_error"]
    assert items["born"]["applied_at"] is not None
    assert await _current("born") is not None


@needs_pg
@pytest.mark.asyncio
async def test_a_failed_row_retries_and_a_success_clears_its_error(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _post_review()
    await _hand_post("one", MINE)
    await _apply()
    await _hand_post("one", ONE_V0)  # the operator puts it back

    result = await _apply()

    assert result["failed"] == 0 and result["applied"] == 1
    item = next(i for i in result["items"] if i["doc_key"] == "one")
    assert item["apply_error"] is None and item["applied_at"] is not None
    _assert_provenance(
        await _current("one"), added_by="updater", content=ONE_V1, theirs=ONE_V1, path=ONE_PATH,
    )


@needs_pg
@pytest.mark.asyncio
async def test_a_release_text_that_moved_leaves_the_doc_alone(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _post_review()
    _git(fixture_repo, "checkout", "-q", "--detach", "v1")
    _write(fixture_repo, f"skills/{ALPHA}/references/one.md", ONE_V1 + "and more\n")
    _commit(fixture_repo, "v1-again")

    result = await refresh.apply_review("1", "v1-again")

    assert (await _current("one"))["content"] == ONE_V0
    item = next(i for i in result["items"] if i["doc_key"] == "one")
    assert "theirs_hash" in item["apply_error"] and item["applied_at"] is None


@needs_pg
@pytest.mark.asyncio
async def test_a_skill_retired_after_the_review_is_not_touched(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _post_review()
    before = await _versions(ALPHA)
    await repo.retire_skill(ALPHA, "not wanted")
    result = await _apply()
    assert await _versions(ALPHA) == before
    item = next(i for i in result["items"] if i["doc_key"] == "one")
    assert "retired" in item["apply_error"]


@needs_pg
@pytest.mark.asyncio
async def test_a_failure_inside_a_row_rolls_that_whole_row_back(
    clean_skills, fixture_repo, monkeypatch
):
    """The skill a `new` row would have created goes with the version it failed
    to post, and the row stays open for the re-run."""
    await _seed(fixture_repo)
    await _post_review()
    real = repo._insert_skill_doc

    async def refuse_beta(conn, skill_id, *a, **k):
        if skill_id == BETA:
            raise RuntimeError("the insert failed")
        return await real(conn, skill_id, *a, **k)

    monkeypatch.setattr(repo, "_insert_skill_doc", refuse_beta)
    result = await _apply()
    assert result["failed"] == 2
    assert await repo.get_skill(BETA) is None
    assert await _current("born") is not None
    errors = {i["doc_key"]: i["apply_error"] for i in result["items"] if i["skill_id"] == BETA}
    assert errors == {"b": "the insert failed", "guidance": "the insert failed"}

    monkeypatch.setattr(repo, "_insert_skill_doc", real)
    result = await _apply()
    assert result["failed"] == 0 and result["applied"] == 2
    assert await _current("b", BETA) is not None


@needs_pg
@pytest.mark.asyncio
async def test_a_second_apply_posts_nothing(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _hand_post("one", MINE)
    items = _items(await _post_review())
    await _verdict(items[(ALPHA, "one")], "keep_mine")
    first = await _apply()
    versions = await _versions(ALPHA)
    events_before = len(await _events("skill.doc_added"))

    second = await _apply()

    assert second["applied"] == 0 and second["failed"] == 0
    assert second["skipped"] == first["applied"]
    assert await _versions(ALPHA) == versions
    assert len(await _events("skill.doc_added")) == events_before


@needs_pg
@pytest.mark.asyncio
async def test_a_row_applied_while_the_run_was_going_is_skipped_not_posted_twice(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await _post_review()
    stale = await repo.get_skill_doc_review_row("1", ALPHA, "one")
    await _apply()
    versions = await _versions(ALPHA)
    assert await repo.apply_skill_doc_review_row(stale) is None
    assert await _versions(ALPHA) == versions


@needs_pg
@pytest.mark.asyncio
async def test_a_verdict_changed_while_the_run_was_going_refuses_the_plan(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _hand_post("one", MINE)
    items = _items(await _post_review())
    await _verdict(items[(ALPHA, "one")], "take_release")
    stale = await repo.get_skill_doc_review_row("1", ALPHA, "one")
    await _verdict(items[(ALPHA, "one")], "keep_mine")
    with pytest.raises(ValueError, match="changed while the apply ran"):
        await repo.apply_skill_doc_review_row(stale)


@needs_pg
@pytest.mark.asyncio
async def test_after_the_apply_a_recompute_on_the_merged_tree_finds_nothing_to_decide(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await _hand_post("one", MINE)
    items = _items(await _post_review())
    await _verdict(items[(ALPHA, "one")], "keep_mine")
    await _apply()
    _git(fixture_repo, "checkout", "-q", "--detach", "v1")

    review = await refresh.compute_review("1", "v1")

    assert review["counts"]["unresolved"] == 0
    assert review["counts"]["modified"] == 0
    assert review["counts"]["unmodified"] == 0 and review["counts"]["new"] == 0


@needs_pg
@pytest.mark.asyncio
async def test_apply_with_unresolved_docs_is_409_and_posts_nothing(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _hand_post("one", MINE)
    await _post_review()
    before = await _versions(ALPHA)
    assert await _status_of(_apply()) == 409
    assert await _versions(ALPHA) == before
    assert await repo.get_skill(BETA) is None


@needs_pg
@pytest.mark.asyncio
async def test_apply_with_no_review_is_404_and_a_bad_actor_is_422(clean_skills, fixture_repo):
    assert await _status_of(_apply("nothing-stored")) == 404
    await _seed(fixture_repo)
    await _post_review()
    assert await _status_of(
        routes.apply_skill_review_route("1", routes.SkillReviewApplyIn(actor="operator"))
    ) == 422
    assert (await _current("born")) is None


@needs_pg
@pytest.mark.asyncio
async def test_the_doc_events_carry_the_actor_and_never_the_text(clean_skills, fixture_repo):
    await _seed(fixture_repo)
    await _hand_post("two", "# Two\nmine\n")
    await _hand_post("one", MINE)
    items = _items(await _post_review())
    await _verdict(items[(ALPHA, "one")], "take_release")
    await _apply()
    added = {e["doc_key"]: e for e in await _events("skill.doc_added")}
    assert set(added) == {"one", "born"}
    assert added["one"]["actor"] == "operator" and added["born"]["actor"] == "updater"
    assert added["one"]["bundled_release"] == "1" and added["one"]["kind"] == "reference"
    assert all("content" not in e for e in added.values())


@needs_pg
@pytest.mark.asyncio
async def test_a_refreshed_version_keeps_what_the_doc_said_about_its_source(
    clean_skills, fixture_repo
):
    await _seed(fixture_repo)
    await repo.add_skill_doc(
        ALPHA, "one", "reference", "one", ONE_V0,
        source_url="https://example.com/one", describes="the one thing",
    )
    await _post_review()
    await _apply()
    doc = await _current("one")
    assert (doc["source_url"], doc["describes"]) == ("https://example.com/one", "the one thing")
    assert doc["captured_at"] is not None


@needs_pg
@pytest.mark.asyncio
async def test_the_rpc_apply_answers_like_the_route(clean_skills, fixture_repo):
    noop = lambda *a, **k: None  # noqa: E731
    await _seed(fixture_repo)
    await _post_review()
    answer = await nerve_gateway._dispatch(
        "skills.update_review_apply", {"target": "1", "actor": "updater"}, notify=noop,
    )
    assert answer["applied"] > 0 and answer["failed"] == 0
    again = await nerve_gateway._dispatch("skills.update_review_apply", {"target": "1"}, notify=noop)
    assert again["items"] == (await _apply())["items"]
    assert again["applied"] == 0
    with pytest.raises(HTTPException) as e:
        await nerve_gateway._dispatch(
            "skills.update_review_apply", {"target": "nothing-stored"}, notify=noop,
        )
    assert e.value.status_code == 404


# --- add_skill_doc's separate bundled text ---------------------------------------


@needs_pg
@pytest.mark.asyncio
async def test_a_version_may_name_a_bundled_text_that_is_not_its_content(clean_skills):
    await repo.create_skill(ALPHA, "alpha", "a test skill")
    theirs = "the release's text\n"
    doc = await repo.add_skill_doc(
        ALPHA, "one", "reference", "one", "my text\n",
        bundled_release="1", bundled_path="skills/x.md",
        bundled_hash=bundled_text_hash(theirs), bundled_text=theirs,
    )
    assert doc["content"] == "my text\n"
    stored = await _bundled_text(theirs)
    assert stored["content"] == theirs
    assert await _bundled_text("my text\n") is None


@needs_pg
@pytest.mark.asyncio
async def test_a_bundled_text_that_does_not_hash_to_the_hash_raises_before_any_write(clean_skills):
    await repo.create_skill(ALPHA, "alpha", "a test skill")
    with pytest.raises(ValueError, match="is not the hash"):
        await repo.add_skill_doc(
            ALPHA, "one", "reference", "one", "my text\n",
            bundled_release="1", bundled_path="skills/x.md",
            bundled_hash="0" * 64, bundled_text="the release's text\n",
        )
    assert await _versions(ALPHA) == 0
    assert await _bundled_text("the release's text\n") is None


# --- apply: the command line, as the updaters call it, after the merge -----------


async def _library_for_cli(root: Path) -> None:
    """One doc of every verdict, the verdicts in, and HEAD on the target: where
    the updater's refresh step finds it."""
    await _seed(root)
    await _hand_post("one", MINE)
    await _hand_post("gone", "# Gone\nmine\n")
    await _hand_post("two", "# Two\nmine\n")
    items = _items(await _post_review())
    await _verdict(items[(ALPHA, "one")], "keep_mine")
    await _verdict(items[(ALPHA, "gone")], "take_release")
    _git(root, "checkout", "-q", "--detach", "v1")


APPLY = ("apply", "--target", "1", "--ref", "v1", "--actor", "updater")


@needs_pg
@pytest.mark.asyncio
async def test_apply_cli_posts_per_table(clean_skills, fixture_repo, tmp_path):
    await _library_for_cli(fixture_repo)
    done = _cli(fixture_repo, *APPLY, cwd=tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "failed=0" in done.stdout

    _assert_provenance(
        await _current("one"), added_by="operator", content=MINE, theirs=ONE_V1, path=ONE_PATH,
    )
    assert await _current("gone") is None
    assert (await _current("two"))["content"] == "# Two\nmine\n"
    assert (await _current("born"))["added_by"] == "updater"
    assert (await _current("guidance", BETA))["added_by"] == "updater"
    assert await repo.get_skill(BETA) is not None


@needs_pg
@pytest.mark.asyncio
async def test_apply_second_run_posts_nothing(clean_skills, fixture_repo, tmp_path):
    await _library_for_cli(fixture_repo)
    assert _cli(fixture_repo, *APPLY, cwd=tmp_path).returncode == 0
    versions = await _versions(ALPHA) + await _versions(BETA)
    done = _cli(fixture_repo, *APPLY, cwd=tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "applied=0" in done.stdout and "failed=0" in done.stdout
    assert await _versions(ALPHA) + await _versions(BETA) == versions


@needs_pg
@pytest.mark.asyncio
async def test_the_store_itself_skips_a_row_already_applied(clean_skills, fixture_repo):
    """apply_review leaves applied rows out before it posts anything, but two
    applies can run at once (the cockpit's and the updater's): the row lock and
    the applied_at check inside apply_skill_doc_review_row are what hold then,
    so they are proven on their own, on a row the first apply closed."""
    await _library_for_cli(fixture_repo)
    assert (await _apply())["failed"] == 0
    row = await repo.get_skill_doc_review_row("1", ALPHA, "one")
    assert row["applied_at"] is not None
    assert await repo.apply_skill_doc_review_row(row) is None
    again = await repo.get_skill_doc_review_row("1", ALPHA, "one")
    assert again["applied_at"] == row["applied_at"]


@needs_pg
@pytest.mark.asyncio
async def test_history_shows_actor_and_provenance(clean_skills, fixture_repo, tmp_path):
    await _library_for_cli(fixture_repo)
    assert _cli(fixture_repo, *APPLY, cwd=tmp_path).returncode == 0
    versions = (await routes.skill_doc_history_route(ALPHA, "one"))["versions"]
    shown = sorted(
        (v["version"], v["added_by"], v["bundled_release"], v["content"]) for v in versions
    )
    assert shown == [
        (1, "operator", "0", ONE_V0),
        (2, "operator", None, MINE),
        (3, "operator", "1", MINE),
    ]


@needs_pg
@pytest.mark.asyncio
async def test_apply_cli_exits_1_naming_the_failed_row_and_no_doc_text(
    clean_skills, fixture_repo, tmp_path
):
    await _seed(fixture_repo)
    await _post_review()
    await _hand_post("one", "MY PRIVATE TEXT\n")
    done = _cli(fixture_repo, *APPLY, cwd=tmp_path)
    assert done.returncode == 1, done.stdout + done.stderr
    assert f"failed: {ALPHA}/one: " in done.stderr and "ours_hash" in done.stderr
    assert "MY PRIVATE TEXT" not in done.stdout + done.stderr
    assert (await _current("one"))["content"] == "MY PRIVATE TEXT\n"
    # The rest went on, and the error is on the record for the cockpit.
    assert await _current("born") is not None
    row = await repo.get_skill_doc_review_row("1", ALPHA, "one")
    assert row["apply_error"] and row["applied_at"] is None


@needs_pg
@pytest.mark.asyncio
async def test_apply_cli_exits_3_naming_unresolved_docs_and_posts_nothing(
    clean_skills, fixture_repo, tmp_path
):
    await _seed(fixture_repo)
    await _hand_post("one", MINE)
    await _post_review()
    before = await _versions(ALPHA)
    done = _cli(fixture_repo, *APPLY, cwd=tmp_path)
    assert done.returncode == 3, done.stdout + done.stderr
    assert f"unresolved: {ALPHA}/one (modified)" in done.stdout
    assert await _versions(ALPHA) == before


@needs_pg
@pytest.mark.asyncio
async def test_apply_cli_exits_2_with_no_review_or_a_missing_ref(clean_skills, fixture_repo, tmp_path):
    await _seed(fixture_repo)
    done = _cli(fixture_repo, *APPLY, cwd=tmp_path)
    assert done.returncode == 2 and "no bundled-skill review" in done.stderr

    await _post_review()
    before = await _versions(ALPHA)
    done = _cli(fixture_repo, "apply", "--target", "1", "--ref", "no-such-ref", cwd=tmp_path)
    assert done.returncode == 2 and "no-such-ref" in done.stderr
    assert await _versions(ALPHA) == before
    assert await repo.get_skill(BETA) is None


def test_apply_cli_refuses_an_actor_it_does_not_know(fixture_repo, tmp_path):
    done = _cli(fixture_repo, "apply", "--target", "1", "--ref", "v1", "--actor", "operator", cwd=tmp_path)
    assert done.returncode == 2


@needs_pg
@pytest.mark.asyncio
async def test_a_skill_the_release_removed_has_every_doc_retired_and_stays_active(
    clean_skills, fixture_repo
):
    """The contract's retired-upstream row, guidance included: the skill row is
    the operator's to retire, and delivery drops a skill with no guidance."""
    await _seed(fixture_repo)
    _git(fixture_repo, "checkout", "-q", "--detach", "v1")
    _git(fixture_repo, "rm", "-rq", f"skills/{ALPHA}")
    _commit(fixture_repo, "v2")
    _git(fixture_repo, "checkout", "-q", "--detach", "v0")
    await _post_review("2", "v2")
    result = await _apply("2")
    assert result["failed"] == 0
    assert await repo.list_skill_docs(ALPHA) == []
    assert (await repo.get_skill(ALPHA))["status"] == "ACTIVE"


@needs_pg
@pytest.mark.asyncio
async def test_the_review_stays_readable_after_an_apply_with_its_failures_on_it(
    clean_skills, fixture_repo
):
    """The cockpit's retry banner reads the failed rows from the GET, though the
    apply's own posts and the hand post that failed have moved the library."""
    await _seed(fixture_repo)
    await _post_review()
    await _hand_post("one", MINE)
    await _apply()

    items = _items(await routes.get_skill_review_route("1"))

    one = items[(ALPHA, "one")]
    assert "ours_hash" in one["apply_error"] and one["applied_at"] is None
    assert one["ours"] is None and one["theirs"] == ONE_V1
    born = items[(ALPHA, "born")]
    assert born["applied_at"] is not None and born["apply_error"] is None


# --- the latest read the Skills page's retry banner polls (story 2.8) -----------

_LATEST_ITEM_KEYS = {
    "skill_id", "doc_key", "status", "verdict", "resolved_at", "applied_at",
    "apply_error", "pending_apply", "moved",
}


def _installed(monkeypatch, version: str) -> None:
    from central_command.api import update

    monkeypatch.setattr(update, "read_product_version", lambda: version)


@needs_pg
@pytest.mark.asyncio
async def test_latest_is_404_with_no_review_stored_over_the_route_and_the_rpc(clean_skills):
    noop = lambda *a, **k: None  # noqa: E731
    assert await _status_of(routes.get_latest_skill_review_route()) == 404
    with pytest.raises(HTTPException) as e:
        await nerve_gateway._dispatch("skills.update_review_latest", {}, notify=noop)
    assert e.value.status_code == 404


@needs_pg
@pytest.mark.asyncio
async def test_latest_names_the_stored_target_without_texts_and_the_rpc_answers_alike(
    clean_skills, fixture_repo, monkeypatch
):
    noop = lambda *a, **k: None  # noqa: E731
    _installed(monkeypatch, "0.9.0")
    await _seed(fixture_repo)
    await _hand_post("one", MINE)
    full = await _post_review()

    latest = await routes.get_latest_skill_review_route()

    assert latest["target"] == "1" and latest["ref"] == "v1"
    assert latest["computed_at"] == full["computed_at"]
    assert latest["installed"] == "0.9.0" and latest["finished"] is False
    assert latest["counts"] == {**full["counts"], "not_refreshed": 0}
    assert len(latest["items"]) == len(full["items"])
    for item in latest["items"]:
        assert set(item) == _LATEST_ITEM_KEYS     # hashes and texts stay on the by-target read
        assert item["pending_apply"] is False and item["moved"] is False
    assert latest == await nerve_gateway._dispatch("skills.update_review_latest", {}, notify=noop)


@needs_pg
@pytest.mark.asyncio
async def test_latest_marks_a_failed_row_pending_and_a_moved_text_as_not_retryable(
    clean_skills, fixture_repo, monkeypatch
):
    _installed(monkeypatch, "1.0.0")
    await _seed(fixture_repo)
    await _post_review()
    await _hand_post("one", MINE)          # the library moved after the review
    await _apply()

    latest = await routes.get_latest_skill_review_route()
    items = {i["doc_key"]: i for i in latest["items"] if i["skill_id"] == ALPHA}

    assert items["one"]["pending_apply"] is True and items["one"]["moved"] is True
    assert "ours_hash" in items["one"]["apply_error"] and MINE not in items["one"]["apply_error"]
    assert items["born"]["pending_apply"] is False and items["born"]["applied_at"] is not None
    assert latest["counts"]["not_refreshed"] == 1

    await _hand_post("one", ONE_V0)        # put back: a plain retry now succeeds
    await _apply()
    cleared = await routes.get_latest_skill_review_route()
    assert cleared["counts"]["not_refreshed"] == 0
    assert all(not i["pending_apply"] for i in cleared["items"])


@needs_pg
@pytest.mark.asyncio
async def test_latest_counts_a_decided_row_the_updater_never_applied_only_once_the_update_is_finished(
    clean_skills, fixture_repo, monkeypatch
):
    await _seed(fixture_repo)
    await _hand_post("one", MINE)
    one = _items(await _post_review())[(ALPHA, "one")]
    await _verdict(one, "keep_mine")

    _installed(monkeypatch, "0.9.0")        # the release is still only staged
    staged = await routes.get_latest_skill_review_route()
    assert staged["finished"] is False and staged["counts"]["not_refreshed"] == 0

    _installed(monkeypatch, "1.0.0")        # installed, and the refresh never ran
    done = await routes.get_latest_skill_review_route()
    flagged = {i["doc_key"]: i for i in done["items"] if i["pending_apply"]}
    assert set(flagged) == {"one"} and flagged["one"]["apply_error"] is None
    assert flagged["one"]["moved"] is False and done["counts"]["not_refreshed"] == 1

