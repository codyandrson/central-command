"""The bundled skill docs at the start of a release (DL-232, amending DL-198):
`skills/refresh.py sync` compares the stored base, the library and the files on
disk, and the operator decides each flagged doc in the Skills page.

Each test builds a temp checkout (a `VERSION` and a `skills/` tree, no git) at
release 1, imports it into the library so its rows carry real provenance, then
changes the tree to release 2 and runs the comparison.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from central_command.api import nerve_gateway, routes
from central_command.config import settings
from central_command.db import repo
from central_command.skills import refresh
from central_command.skills.importer import bundled_text_hash, import_skill_folder
from tests.conftest import needs_pg, sweep_test_skills

ALPHA = "cc-test-alpha"
BETA = "cc-test-beta"
WORKTREE = Path(__file__).resolve().parents[1]

ONE_V1 = "# One\nline 1\nline 2\n"
ONE_V2 = "# One\nline 1\nline two, as release 2 says\n"
MINE = "# One\nline 1\nthe operator's own line\n"


def _write(root: Path, rel: str, text: str | bytes) -> None:
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))


def _release(root: Path, version: str) -> None:
    _write(root, "VERSION", f"version={version}\nmin_upgrade_from=0\n")


@pytest.fixture()
def checkout(tmp_path, monkeypatch):
    """Release 1 on disk, with the root seam and an empty overlay on it."""
    root = (tmp_path / "checkout").resolve()
    a = f"skills/{ALPHA}"
    _release(root, "1")
    _write(
        root,
        f"{a}/SKILL.md",
        "---\nname: alpha\ndescription: a test skill\n---\n\n# Alpha\nsteps\n",
    )
    _write(root, f"{a}/references/one.md", "---\ntitle: a reference\n---\n\n" + ONE_V1)
    _write(root, f"{a}/references/two.md", "# Two\nstays the same\n")
    _write(root, f"{a}/references/gone.md", "# Gone\nremoved by release 2\n")
    monkeypatch.setattr(settings, "repo_root", str(root))
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    monkeypatch.setattr(settings, "skill_overlay_dir", str(overlay))
    return root


async def _sweep() -> None:
    """The shared cleanup plus the events this file writes. Flags cascade with
    their skill."""
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


async def _current(doc_key: str, skill_id: str = ALPHA) -> dict | None:
    docs = await repo.list_skill_docs(skill_id)
    return next((d for d in docs if d["doc_key"] == doc_key), None)


async def _versions(doc_key: str, skill_id: str = ALPHA) -> int:
    return len(await repo.skill_doc_history(skill_id, doc_key))


async def _flags(skill_id: str = ALPHA) -> list[dict]:
    conn = await repo._conn()
    try:
        rows = await conn.fetch(
            "select * from skill_doc_flag where skill_id = $1 order by doc_key, release",
            skill_id,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def _base(doc_key: str) -> str | None:
    library = await repo.skill_review_library()
    prov = library["provenance"].get((ALPHA, doc_key))
    return prov["bundled_text"] if prov else None


def _mine(labels: list) -> list:
    """This file's docs only: the test database may hold other skills, and the
    comparison reads the whole library."""
    return [x for x in labels if (x if isinstance(x, str) else x["doc"]).startswith(f"{ALPHA}/")]


def _to_release_2(root: Path) -> None:
    _release(root, "2")
    _write(root, f"skills/{ALPHA}/references/one.md", "---\ntitle: a reference\n---\n\n" + ONE_V2)
    (root / "skills" / ALPHA / "references" / "gone.md").unlink()


# --- the three cases --------------------------------------------------------


@needs_pg
@pytest.mark.asyncio
async def test_a_doc_nobody_edited_follows_the_release(clean_skills, checkout):
    await _seed(checkout)
    _to_release_2(checkout)
    result = await refresh.sync()
    one = await _current("one")
    assert one["content"] == ONE_V2
    assert one["added_by"] == "updater"
    assert one["bundled_release"] == "2" and one["bundled_hash"] == bundled_text_hash(ONE_V2)
    assert await _base("one") == ONE_V2  # the base moved with it
    assert _mine(result["flagged"]) == [] and _mine(result["failed"]) == []
    assert await _flags() == []


@needs_pg
@pytest.mark.asyncio
async def test_an_edited_doc_the_release_did_not_change_is_left_alone(clean_skills, checkout):
    await _seed(checkout)
    await _hand_post("two", "# Two\nmy version\n")
    _to_release_2(checkout)
    before = await _versions("two")
    await refresh.sync()
    assert (await _current("two"))["content"] == "# Two\nmy version\n"
    assert await _versions("two") == before
    assert [f for f in await _flags() if f["doc_key"] == "two"] == []


@needs_pg
@pytest.mark.asyncio
async def test_an_edited_doc_the_release_changed_is_kept_based_anew_and_flagged(
    clean_skills, checkout
):
    await _seed(checkout)
    await _hand_post("one", MINE)
    _to_release_2(checkout)
    result = await refresh.sync()

    one = await _current("one")
    assert one["content"] == MINE  # an edit is never overwritten
    assert one["bundled_hash"] == bundled_text_hash(ONE_V2)  # the new base is recorded
    assert await _base("one") == ONE_V2
    [flag] = [f for f in await _flags() if f["doc_key"] == "one"]
    assert flag["change"] == "changed" and flag["release"] == "2" and flag["decision"] is None
    assert flag["base_hash"] == bundled_text_hash(ONE_V1)
    assert flag["release_hash"] == bundled_text_hash(ONE_V2)
    assert f"{ALPHA}/one" in result["flagged"]


@needs_pg
@pytest.mark.asyncio
async def test_the_new_base_stops_the_comparison_flagging_it_again(clean_skills, checkout):
    await _seed(checkout)
    await _hand_post("one", MINE)
    _to_release_2(checkout)
    await refresh.sync()
    versions, flags = await _versions("one"), await _flags()

    # The next start of the same release, and a later release that leaves the doc alone.
    again = await refresh.sync()
    _release(checkout, "3")
    later = await refresh.sync()
    assert await _versions("one") == versions
    assert await _flags() == flags
    assert _mine(again["flagged"]) == [] and _mine(later["flagged"]) == []


@needs_pg
@pytest.mark.asyncio
async def test_a_later_release_that_changes_the_doc_again_flags_it_again(clean_skills, checkout):
    await _seed(checkout)
    await _hand_post("one", MINE)
    _to_release_2(checkout)
    await refresh.sync()
    _release(checkout, "3")
    _write(checkout, f"skills/{ALPHA}/references/one.md", "# One\nrelease three\n")
    await refresh.sync()
    assert (await _current("one"))["content"] == MINE
    assert [f["release"] for f in await _flags() if f["doc_key"] == "one"] == ["2", "3"]
    # The page shows the newest flag of the doc only.
    shown = [f for f in await repo.list_open_skill_doc_flags() if f["skill_id"] == ALPHA]
    assert [(f["doc_key"], f["release"]) for f in shown] == [("one", "3")]


# --- additions, removals, retirements -----------------------------------------


@needs_pg
@pytest.mark.asyncio
async def test_a_new_doc_and_a_whole_new_skill_are_added(clean_skills, checkout):
    await _seed(checkout)
    _to_release_2(checkout)
    _write(checkout, f"skills/{ALPHA}/references/born.md", "# Born\nadded by release 2\n")
    _write(checkout, f"skills/{BETA}/SKILL.md", "---\nname: beta\n---\n\n# Beta\n")
    _write(checkout, f"skills/{BETA}/references/b.md", "# B\n")
    await refresh.sync()
    born = await _current("born")
    assert born["content"] == "# Born\nadded by release 2\n" and born["bundled_release"] == "2"
    beta = await repo.get_skill(BETA)
    assert beta is not None and beta["title"] == "beta"
    assert {d["doc_key"] for d in await repo.list_skill_docs(BETA)} == {"guidance", "b"}


@needs_pg
@pytest.mark.asyncio
async def test_a_removed_doc_nobody_edited_is_retired(clean_skills, checkout):
    await _seed(checkout)
    _to_release_2(checkout)
    await refresh.sync()
    assert await _current("gone") is None
    history = await repo.skill_doc_history(ALPHA, "gone")
    assert history[0]["retired_reason"] == "removed in v2"


@needs_pg
@pytest.mark.asyncio
async def test_a_removed_doc_that_was_edited_is_kept_and_flagged_once(clean_skills, checkout):
    await _seed(checkout)
    await _hand_post("gone", "# Gone\nI still need this\n")
    _to_release_2(checkout)
    await refresh.sync()
    await refresh.sync()
    assert (await _current("gone"))["content"] == "# Gone\nI still need this\n"
    flags = [f for f in await _flags() if f["doc_key"] == "gone"]
    assert len(flags) == 1 and flags[0]["change"] == "removed" and flags[0]["release_hash"] is None


@needs_pg
@pytest.mark.asyncio
async def test_a_doc_retired_here_that_the_release_changed_stays_retired_and_is_flagged_once(
    clean_skills, checkout
):
    await _seed(checkout)
    await repo.retire_skill_doc(ALPHA, "one", "not wanted here")
    _to_release_2(checkout)
    await refresh.sync()
    await refresh.sync()
    assert await _current("one") is None
    assert [f["change"] for f in await _flags() if f["doc_key"] == "one"] == ["changed"]


@needs_pg
@pytest.mark.asyncio
async def test_a_retired_skill_is_left_alone(clean_skills, checkout):
    await _seed(checkout)
    await repo.retire_skill(ALPHA, "not wanted")
    _to_release_2(checkout)
    await refresh.sync()
    assert (await _current("one"))["content"] == ONE_V1
    assert await _flags() == []


@needs_pg
@pytest.mark.asyncio
async def test_a_doc_already_at_the_release_text_catches_its_base_up_without_a_flag(
    clean_skills, checkout
):
    await _seed(checkout)
    await _hand_post("one", ONE_V2)  # taken by hand before the release
    _to_release_2(checkout)
    await refresh.sync()
    assert (await _current("one"))["content"] == ONE_V2
    assert await _base("one") == ONE_V2
    assert await _flags() == []


# --- what is read, and failing safe -------------------------------------------


@needs_pg
@pytest.mark.asyncio
async def test_a_crlf_file_reads_as_the_import_read_it(clean_skills, checkout):
    _write(checkout, f"skills/{ALPHA}/references/two.md", b"# Two\r\nstays the same\r\n")
    await _seed(checkout)
    before = await _versions("two")
    await refresh.sync()
    assert await _versions("two") == before


@needs_pg
@pytest.mark.asyncio
async def test_an_overlay_render_is_what_is_compared(clean_skills, checkout, tmp_path):
    _write(checkout, f"skills/{ALPHA}/references/two.md", "# Two\n<!-- overlay: site -->\n")
    (tmp_path / "overlay" / "site.md").write_text("the first site\n")
    await _seed(checkout)
    assert "the first site" in (await _current("two"))["content"]
    (tmp_path / "overlay" / "site.md").write_text("the second site\n")
    await refresh.sync()  # nobody edited it: it follows
    assert "the second site" in (await _current("two"))["content"]


@needs_pg
@pytest.mark.asyncio
async def test_a_tree_with_no_bundled_skills_refuses_rather_than_retiring_everything(
    clean_skills, checkout
):
    await _seed(checkout)
    for f in sorted((checkout / "skills").rglob("*"), reverse=True):
        f.unlink() if f.is_file() else f.rmdir()
    with pytest.raises(ValueError, match="no bundled skills"):
        await refresh.sync()
    assert (await _current("one"))["content"] == ONE_V1


@needs_pg
@pytest.mark.asyncio
async def test_an_edit_landing_while_it_runs_is_never_overwritten(
    clean_skills, checkout, monkeypatch
):
    """The read said nobody edited `one`; an edit lands before the write. The
    write re-checks under its lock and refuses, and the rest go on."""
    await _seed(checkout)
    _to_release_2(checkout)
    real = repo.skill_review_library

    async def then_an_edit():
        snapshot = await real()
        await _hand_post("one", MINE)
        return snapshot

    monkeypatch.setattr(repo, "skill_review_library", then_an_edit)
    result = await refresh.sync()
    assert (await _current("one"))["content"] == MINE
    assert [f["doc"] for f in _mine(result["failed"])] == [f"{ALPHA}/one"]
    assert await _current("gone") is None  # the others went on


@needs_pg
@pytest.mark.asyncio
async def test_the_event_counts_and_names_never_carry_doc_text(clean_skills, checkout):
    await _seed(checkout)
    await _hand_post("one", "# One\nSECRET-LOOKING TEXT\n")
    _to_release_2(checkout)
    await refresh.sync()
    conn = await repo._conn()
    try:
        payload = await conn.fetchval(
            "select payload::text from audit_event where kind = 'skill.review_computed' "
            "order by id desc limit 1"
        )
    finally:
        await conn.close()
    assert f"{ALPHA}/one" in payload and "SECRET-LOOKING" not in payload


def test_two_files_on_one_doc_key_raise_as_an_import_does(checkout):
    _write(checkout, f"skills/{ALPHA}/references/one_.md", "# clash\n")  # slugs to `one` too
    with pytest.raises(ValueError, match="collide"):
        refresh.read_disk(checkout, {})


def test_the_real_bundled_tree_reads_and_renders():
    docs = refresh.read_disk(WORKTREE, {})
    assert docs and all(d.path.startswith("skills/") for d in docs.values())
    assert all(key[1] == "guidance" or d.kind == "reference" for key, d in docs.items())


# --- the operator's decision ----------------------------------------------------


async def _flagged(checkout: Path) -> None:
    await _seed(checkout)
    await _hand_post("one", MINE)
    _to_release_2(checkout)
    await refresh.sync()


async def _open_item(doc_key: str = "one") -> dict:
    items = (await routes.list_skill_release_flags_route())["items"]
    return next(i for i in items if (i["skill_id"], i["doc_key"]) == (ALPHA, doc_key))


async def _decide(decision: str, content: str | None = None, doc_key: str = "one") -> dict:
    item = await _open_item(doc_key)
    return await routes.resolve_skill_release_flag_route(
        ALPHA,
        doc_key,
        routes.SkillFlagResolveIn(decision=decision, content=content, ours_hash=item["ours_hash"]),
    )


@needs_pg
@pytest.mark.asyncio
async def test_the_page_reads_both_texts_of_an_open_flag(clean_skills, checkout):
    await _flagged(checkout)
    item = await _open_item()
    assert item["ours"] == MINE and item["theirs"] == ONE_V2
    assert item["release"] == "2" and item["change"] == "changed"
    assert item["ours_hash"] == bundled_text_hash(MINE)


@needs_pg
@pytest.mark.asyncio
async def test_take_release_posts_the_release_text_and_the_doc_follows_from_then_on(
    clean_skills, checkout
):
    await _flagged(checkout)
    await _decide("take_release")
    one = await _current("one")
    assert one["content"] == ONE_V2 and one["added_by"] == "operator"
    assert one["bundled_hash"] == bundled_text_hash(ONE_V2)
    assert [f["decision"] for f in await _flags()] == ["take_release"]
    # Nobody's edit any more: the next release's change follows.
    _release(checkout, "3")
    _write(checkout, f"skills/{ALPHA}/references/one.md", "# One\nrelease three\n")
    await refresh.sync()
    assert (await _current("one"))["content"] == "# One\nrelease three\n"


@needs_pg
@pytest.mark.asyncio
async def test_keep_mine_writes_nothing_but_the_decision(clean_skills, checkout):
    await _flagged(checkout)
    before = await _versions("one")
    await _decide("keep_mine")
    assert (await _current("one"))["content"] == MINE
    assert await _versions("one") == before
    assert [f["decision"] for f in await _flags()] == ["keep_mine"]
    assert [
        i
        for i in (await routes.list_skill_release_flags_route())["items"]
        if i["skill_id"] == ALPHA
    ] == []


@needs_pg
@pytest.mark.asyncio
async def test_edit_posts_the_operators_text(clean_skills, checkout):
    await _flagged(checkout)
    await _decide("edit", "# One\nboth, merged by hand\n")
    one = await _current("one")
    assert one["content"] == "# One\nboth, merged by hand\n" and one["added_by"] == "operator"
    assert await _base("one") == ONE_V2  # the base stays the release's text


@needs_pg
@pytest.mark.asyncio
async def test_take_release_on_a_removed_doc_retires_it(clean_skills, checkout):
    await _seed(checkout)
    await _hand_post("gone", "# Gone\nmine\n")
    _to_release_2(checkout)
    await refresh.sync()
    await _decide("take_release", doc_key="gone")
    assert await _current("gone") is None


@needs_pg
@pytest.mark.asyncio
async def test_a_decision_over_a_moved_text_is_a_409_and_writes_nothing(clean_skills, checkout):
    await _flagged(checkout)
    item = await _open_item()
    await _hand_post("one", "# One\nchanged again meanwhile\n")
    with pytest.raises(HTTPException) as e:
        await routes.resolve_skill_release_flag_route(
            ALPHA,
            "one",
            routes.SkillFlagResolveIn(decision="take_release", ours_hash=item["ours_hash"]),
        )
    assert e.value.status_code == 409
    assert (await _current("one"))["content"] == "# One\nchanged again meanwhile\n"
    assert [f["decision"] for f in await _flags()] == [None]


@needs_pg
@pytest.mark.asyncio
async def test_take_release_refuses_when_the_disk_no_longer_ships_the_flagged_text(
    clean_skills, checkout
):
    await _flagged(checkout)
    _write(checkout, f"skills/{ALPHA}/references/one.md", "# One\nsomething else\n")
    with pytest.raises(HTTPException) as e:
        await _decide("take_release")
    assert e.value.status_code == 409
    assert (await _current("one"))["content"] == MINE


@needs_pg
@pytest.mark.asyncio
async def test_a_doc_with_no_open_flag_is_a_404_and_a_bad_shape_a_422(clean_skills, checkout):
    await _flagged(checkout)
    with pytest.raises(HTTPException) as e:
        await routes.resolve_skill_release_flag_route(
            ALPHA,
            "two",
            routes.SkillFlagResolveIn(decision="keep_mine", ours_hash=None),
        )
    assert e.value.status_code == 404
    # A bad shape never reaches the handler: the request model refuses it (over
    # HTTP that is FastAPI's 422; the RPC path's `from_params` answers 422 too).
    for params in (
        {"decision": "merge", "ours_hash": None},
        {"decision": "edit", "ours_hash": None},
        {"decision": "keep_mine", "content": "x", "ours_hash": None},
        {"ours_hash": None},
    ):
        with pytest.raises(ValidationError):
            routes.SkillFlagResolveIn(**params)
        with pytest.raises(HTTPException) as e:
            routes.SkillFlagResolveIn.from_params(params)
        assert e.value.status_code == 422
    assert "only with an edit" in str(
        pytest.raises(
            HTTPException,
            routes.SkillFlagResolveIn.from_params,
            {"decision": "edit", "ours_hash": None},
        ).value.detail
    )


@needs_pg
@pytest.mark.asyncio
async def test_the_rpc_methods_answer_like_the_routes(clean_skills, checkout):
    await _flagged(checkout)

    async def rpc(method: str, params: dict | None = None):
        return await nerve_gateway._dispatch(method, params or {}, notify=lambda *a, **k: None)

    listed = await rpc("skills.release_flags")
    assert [i["doc_key"] for i in listed["items"] if i["skill_id"] == ALPHA] == ["one"]
    item = listed["items"][0]
    out = await rpc(
        "skills.release_flag_resolve",
        {
            "skill_id": ALPHA,
            "doc_key": "one",
            "decision": "keep_mine",
            "ours_hash": item["ours_hash"],
        },
    )
    assert out["decision"] == "keep_mine"


# --- the command line -------------------------------------------------------------


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
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


@needs_pg
@pytest.mark.asyncio
async def test_sync_exits_0_naming_flagged_docs_and_no_doc_text(clean_skills, checkout, tmp_path):
    await _seed(checkout)
    await _hand_post("one", "# One\nSECRET-LOOKING TEXT\n")
    _to_release_2(checkout)
    done = _cli(checkout, "sync", cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert "keep=" in done.stdout and await _current("gone") is None
    assert f"kept and flagged for the operator: {ALPHA}/one" in done.stdout
    assert "SECRET-LOOKING TEXT" not in done.stdout + done.stderr


@needs_pg
@pytest.mark.asyncio
async def test_the_old_updaters_apply_command_runs_the_comparison(clean_skills, checkout, tmp_path):
    """What v2.126.0's updater calls after the merge (`refresh._LEGACY`)."""
    await _seed(checkout)
    _to_release_2(checkout)
    done = _cli(
        checkout, "apply", "--target", "2", "--ref", "v2", "--actor", "updater", cwd=tmp_path
    )
    assert done.returncode == 0, done.stderr
    assert (await _current("one"))["content"] == ONE_V2


def test_sync_exits_2_when_the_database_is_down(checkout, tmp_path):
    done = _cli(
        checkout,
        "sync",
        cwd=tmp_path,
        database_url="postgresql://nobody:nothing@127.0.0.1:1/nothing",
    )
    assert done.returncode == 2, done.stdout + done.stderr


def test_a_bad_command_line_exits_2(checkout, tmp_path):
    assert _cli(checkout, "apply", "--target", "1", cwd=tmp_path).returncode == 2
