"""The bundled skill docs at the start of a release (DL-232, amending DL-198).

Per bundled doc, three texts: the stored BASE (the newest version's
`bundled_hash`, its text in `skill_bundled_text`: what the last release shipped
to this library), the LIBRARY's text (what agents are served now) and the DISK
text (the release's file under the checkout's `skills/`, rendered through the
skill overlay exactly as an import renders it). Then:

    library == base            nobody edited it: library and base become the disk
                               text (a new doc is added, a removed one retired);
    disk == base               the release did not change it: nothing changes;
    otherwise                  edited here AND changed by the release: the
                               library's text is KEPT, the new base is recorded
                               (a version carrying the library's text with the
                               release's provenance), and the doc is FLAGGED in
                               `skill_doc_flag` for the operator to decide in the
                               Skills page: take the release's text, keep theirs,
                               or edit. Nothing merges automatically.

An edit is never overwritten: every write re-checks the library's text under a
row lock (`repo.apply_bundled_skill_doc`). A retired skill is left alone; a doc
retired here counts as edited. A flag is written once per (doc, change, base,
release text), so a flag the operator decided is not raised again for the same
release text. No git is read: the disk is the release.

It runs where the updaters already loaded skills after an update: the `skills`
phase of `deploy/k3s/cc-update.sh` and `refresh_bundled_skills` in
`deploy/single/update.sh`'s `deploy_current_tree`, with the API stopped. It
opens the database directly and takes its settings from the checkout's `.env`:

    python -m central_command.skills.refresh sync
        exit 0 every doc compared; exit 1 a doc's write failed (each named on
        stderr with its reason, never doc text; the rest went on, and a re-run
        retries it); exit 2 could not run (no bundled skills on disk, a file the
        importer would refuse, the database down) and nothing was written.

The older command lines `review`, `check` and `apply --target --ref [--actor]`
are kept for ONE release only (see `_LEGACY`): v2.126.0's updater drives the
update into this release and calls `apply` on the merged tree.

API tier: this module imports nothing from `gateway/` or `executor`, and nothing
under `runtime/` imports it (`tests/test_skills_refresh_boundary.py`).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, get_args

from central_command import events
from central_command.config import settings
from central_command.db import repo
from central_command.skills import importer
from central_command.skills import overlay as skill_overlay
from central_command.skills.ids import validate_skill_id

# What the operator may decide on a flag. The request body
# (`api/routes.SkillFlagResolveIn`) validates a decision and its `content`
# against this before `resolve_flag` is reached.
Decision = Literal["take_release", "keep_mine", "edit"]
DECISIONS: tuple[str, ...] = get_args(Decision)


@dataclass(frozen=True)
class BundledDoc:
    """One bundled file on disk, as the library would store it."""

    path: str  # relative to the checkout: 'skills/jira/references/api.md'
    kind: str  # 'guidance' | 'reference'
    text: str
    meta: dict  # frontmatter fields, as written


def read_disk(root: Path, overlay: dict[str, str]) -> dict[tuple[str, str], BundledDoc]:
    """Every bundled doc under `root/skills/`, keyed (skill_id, doc_key).

    The shape an import walks: a skill is a folder directly under `skills/`
    holding a SKILL.md, its docs are that file and `references/*.md`, the id is
    the folder's name (what the install's import passes) and the keys are the
    importer's. Two files on one key raise, as an import does, and so does a
    file the importer would refuse: everything is read before anything is written.
    """
    docs: dict[tuple[str, str], BundledDoc] = {}
    skills_dir = root / "skills"
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        folder = skill_md.parent
        skill_id = validate_skill_id(folder.name)
        files = [("guidance", "guidance", skill_md)]
        ref_dir = folder / "references"
        if ref_dir.is_dir():
            files += [
                ("reference", importer._doc_key(f.name), f)
                for f in sorted(ref_dir.glob("*.md"))
                if f.is_file()
            ]
        for kind, key, f in files:
            rel = f.relative_to(root).as_posix()
            if (skill_id, key) in docs:
                raise ValueError(
                    f"{rel} and {docs[(skill_id, key)].path} collide on doc_key {key!r}"
                )
            doc = importer.normalise_doc_text(kind, importer._read_text(f), str(f), overlay=overlay)
            docs[(skill_id, key)] = BundledDoc(rel, kind, doc.text, doc.meta)
    return docs


def _hash(text: str | None) -> str | None:
    return importer.bundled_text_hash(text) if text is not None else None


def decide(
    disk_doc: BundledDoc | None,
    lib: dict | None,
    prov: dict | None,
    seen: set[tuple],
    key: tuple,
) -> dict | None:
    """One doc's act, or None when nothing changes. Pure: the rule in one place.

    Acts: `follow` (post the release text with its provenance, which also adds a
    doc the library never held), `retire` (the release removed a doc nobody
    edited), `keep` (record the new base under the library's text and flag it),
    `flag` (flag only: the doc is retired here, or the release removed an edited
    doc, so there is no version to carry a new base and the flag row records it).
    """
    theirs = disk_doc.text if disk_doc else None
    if lib is None:
        return {"act": "follow"} if theirs is not None else None
    ours = lib["content"] if lib["is_current"] else None
    base = None
    if prov is not None:
        if prov["bundled_text"] is None:
            raise ValueError(f"no bundled text is stored for hash {prov['bundled_hash']}")
        base = prov["bundled_text"]

    if theirs is not None and theirs == base:
        return None  # the release did not change it
    if ours is not None and ours == base:  # nobody edited it
        return {"act": "follow"} if theirs is not None else {"act": "retire"}
    if theirs is None:
        if ours is None or base is None:
            return None  # retired on both sides, or never bundled
        change = "removed"
    else:
        if ours == theirs:
            return {"act": "follow"}  # already the release's text: the base catches up
        change = "changed"
    flag = {
        "change": change,
        "bundled_path": disk_doc.path if disk_doc else prov["bundled_path"],
        "base_hash": prov["bundled_hash"] if prov else None,
        "release_hash": _hash(theirs),
    }
    if (*key, change, flag["base_hash"], flag["release_hash"]) in seen:
        return None  # already put to the operator
    act = "keep" if change == "changed" and ours is not None else "flag"
    return {"act": act, "flag": flag}


def _release_post(
    key: tuple, doc: BundledDoc, disk: dict, release: str, actor: str, *, is_new_skill: bool
) -> tuple[dict, dict | None]:
    """The version that takes the release's text, and the skill fields that go
    with it: the skill's title and summary follow the guidance doc's frontmatter
    (A2), and a new skill takes its default holders."""
    skill_id, doc_key = key
    is_guidance = doc_key == "guidance"
    skill = None
    if is_new_skill or is_guidance:
        meta = disk[(skill_id, "guidance")].meta
        title, summary = importer.skill_title_summary(meta, skill_id)
        skill = {
            "title": title,
            "summary": summary,
            "follow": is_guidance,
            "default_agents": importer.parse_id_list(meta.get("default_agents", "")),
        }
    post = {
        "kind": doc.kind,
        "title": skill["title"] if is_guidance else Path(doc.path).stem,
        "content": doc.text,
        "added_by": actor,
        "captured_at": datetime.now(UTC),
        **importer.provenance(release, doc.path, doc.text),
        "bundled_text": doc.text,
    }
    return post, skill


async def _emit_written(
    key: tuple, out: dict, skill: dict | None, release: str, actor: str, *, kept: bool = False
) -> None:
    skill_id, doc_key = key
    if out["created_skill"]:
        await events.emit(
            "skill.created",
            ref_id=skill_id,
            payload={"title": skill["title"], "summary": skill["summary"]},
            actor=actor,
        )
        for agent_id in out["grants"]["applied"]:
            await events.emit(
                "skill.granted",
                ref_id=agent_id,
                payload={"skill_id": skill_id, "default": True},
                actor="seed:skill",
            )
    if out["doc"] is not None:
        await events.emit(
            "skill.doc_added",
            ref_id=skill_id,
            payload={
                "doc_key": doc_key,
                "kind": out["doc"]["kind"],
                "version": out["doc"]["version"],
                "describes": out["doc"]["describes"],
                "bundled_release": out["doc"]["bundled_release"],
                **({"kept": True} if kept else {}),
            },
            actor=actor,
        )
    if out["retired"] is not None:
        await events.emit(
            "skill.doc_retired",
            ref_id=skill_id,
            payload={
                "doc_key": doc_key,
                "version": out["retired"]["version"],
                "reason": out["retired"]["retired_reason"],
            },
            actor=actor,
        )


async def _write(key, act, disk, library, release, actor="updater") -> None:
    skill_id, doc_key = key
    lib = library["docs"].get(key)
    ours_hash = _hash(lib["content"]) if lib and lib["is_current"] else None
    doc = disk.get(key)
    if act["act"] == "follow":
        post, skill = _release_post(
            key,
            doc,
            disk,
            release,
            actor,
            is_new_skill=skill_id not in library["skills"],
        )
        out = await repo.apply_bundled_skill_doc(
            skill_id,
            doc_key,
            ours_hash=ours_hash,
            post=post,
            skill=skill,
        )
        await _emit_written(key, out, skill, release, actor)
    elif act["act"] == "retire":
        out = await repo.apply_bundled_skill_doc(
            skill_id,
            doc_key,
            ours_hash=ours_hash,
            retire_reason=f"removed in v{release}",
        )
        await _emit_written(key, out, None, release, actor)
    else:
        post = None
        if act["act"] == "keep":
            post = {
                "kind": doc.kind,
                "title": None,
                "content": None,
                "added_by": actor,
                "captured_at": datetime.now(UTC),
                **importer.provenance(release, doc.path, doc.text),
                "bundled_text": doc.text,
            }
        out = await repo.apply_bundled_skill_doc(
            skill_id,
            doc_key,
            ours_hash=ours_hash,
            post=post,
            flag={**act["flag"], "release": release},
        )
        await _emit_written(key, out, None, release, actor, kept=True)


async def sync() -> dict:
    """Compare every bundled doc on disk with the library and act on each.

    Returns {release, counts, flagged, failed}: `flagged` and `failed` name docs
    (`skill/doc`), `failed` with the reason; never doc text. Reading the disk
    and the library raises before anything is written; a doc whose write fails
    is recorded in `failed` and the rest go on.
    """
    root = importer.checkout_root()
    overlay = skill_overlay.load(settings.resolved_skill_overlay_dir())
    release = importer.checkout_release(root)
    disk = await asyncio.to_thread(read_disk, root, overlay)
    if not disk:
        # Every bundled doc would read as removed, most of them silently.
        raise ValueError(f"{root} carries no bundled skills under skills/")
    library = await repo.skill_review_library()
    seen = await repo.skill_doc_flags_seen()

    keys = sorted({*disk, *library["provenance"]})
    plan: list[tuple[tuple, dict]] = []
    for key in keys:
        if library["skills"].get(key[0]) == "RETIRED":
            continue
        act = decide(
            disk.get(key), library["docs"].get(key), library["provenance"].get(key), seen, key
        )
        if act is not None:
            plan.append((key, act))

    counts = {"follow": 0, "retire": 0, "keep": 0, "flag": 0, "failed": 0}
    flagged: list[str] = []
    failed: list[dict] = []
    for key, act in plan:
        label = f"{key[0]}/{key[1]}"
        try:
            await _write(key, act, disk, library, release)
        except Exception as e:  # noqa: BLE001 — one failed doc is counted, the rest still run
            counts["failed"] += 1
            failed.append({"doc": label, "error": str(e) or type(e).__name__})
            continue
        counts[act["act"]] += 1
        if act["act"] in ("keep", "flag"):
            flagged.append(label)
    counts["unchanged"] = len(keys) - len(plan)
    await events.emit(
        "skill.review_computed",
        release,
        {
            "release": release,
            "counts": counts,
            "flagged": flagged,
            "failed": [f["doc"] for f in failed],
        },
    )
    return {"release": release, "counts": counts, "flagged": flagged, "failed": failed}


# --- the operator's decision on a flag ------------------------------------------


class FlagMoved(ValueError):
    """What the operator was shown is no longer what is there."""


async def resolve_flag(
    skill_id: str,
    doc_key: str,
    decision: Decision,
    *,
    ours_hash: str | None,
    content: str | None = None,
) -> dict:
    """Carry out the operator's decision on a doc's open flag, and close it.

    take_release: post what the release ships (read from disk, and only while it
    still renders to the flagged text), or retire a doc the release removed;
    keep_mine: nothing is written but the decision; edit: post `content` as the
    operator's version. `ours_hash` is the library text the operator was shown,
    re-checked under the lock. Raises `repo.SkillDocFlagClosed` when no flag is
    open and `FlagMoved` (a ValueError) when a text moved. The decision's shape
    (one of `DECISIONS`, `content` with an edit and only with one) is the
    request body's to check (`api/routes.SkillFlagResolveIn`).
    """
    key = (skill_id, doc_key)
    flags = {(f["skill_id"], f["doc_key"]): f for f in await repo.list_open_skill_doc_flags()}
    flag = flags.get(key)
    if flag is None:
        raise repo.SkillDocFlagClosed(f"{skill_id}/{doc_key} has no open flag")
    release = flag["release"]
    post = retire = skill = None
    if decision == "take_release" and flag["change"] == "removed":
        retire = f"removed in v{release}"
    elif decision == "take_release":
        root = importer.checkout_root()
        overlay = skill_overlay.load(settings.resolved_skill_overlay_dir())
        disk = await asyncio.to_thread(read_disk, root, overlay)
        doc = disk.get(key)
        if doc is None or _hash(doc.text) != flag["release_hash"]:
            raise FlagMoved(
                f"the bundled file for {skill_id}/{doc_key} no longer renders to the text this "
                "flag showed (the release on disk or the overlay changed)"
            )
        post, skill = _release_post(key, doc, disk, release, "operator", is_new_skill=False)
    elif decision == "edit":
        post = {"kind": None, "title": None, "content": content, "added_by": "operator"}
    try:
        out = await repo.apply_bundled_skill_doc(
            skill_id,
            doc_key,
            ours_hash=ours_hash,
            post=post,
            retire_reason=retire,
            skill=skill,
            decision=decision,
        )
    except repo.SkillDocFlagClosed:
        raise
    except ValueError as e:
        raise FlagMoved(str(e)) from e
    await _emit_written(key, out, skill, release, "operator")
    await events.emit(
        "skill.review_resolved",
        ref_id=skill_id,
        payload={"release": release, "skill_id": skill_id, "doc_key": doc_key, "verdict": decision},
        actor="operator",
    )
    return {"skill_id": skill_id, "doc_key": doc_key, "release": release, "decision": decision}


# --- command line -------------------------------------------------------------

# The command lines of the pre-update review (DL-198), kept for ONE release so
# the update INTO this release, which v2.126.0's updater drives, cannot fail:
# its stage and its gate run the INSTALLED tree's copy of this module (they
# never reach this one), and after the merge its `skills` phase (k3s) or
# `refresh_bundled_skills` (single-node) calls `apply --target T --ref R
# --actor updater` here. `apply` runs the comparison, which is this release's
# own step at that same point; `review` and `check` answer "nothing to review".
# Delete `_LEGACY` in the first release whose `min_upgrade_from` is at least
# the release that ships DL-232.
_LEGACY = ("review", "check", "apply")


async def _run_sync() -> int:
    try:
        result = await sync()
    finally:
        await repo.close_pool()
    counts = " ".join(f"{k}={v}" for k, v in result["counts"].items())
    print(f"bundled skills for {result['release']}: {counts}")
    for label in result["flagged"]:
        print(f"kept and flagged for the operator: {label}")
    for f in result["failed"]:
        print(f"failed: {f['doc']}: {f['error']}", file=sys.stderr)
    return 1 if result["failed"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m central_command.skills.refresh",
        description="Compare the bundled skill docs on disk with the library (DL-232).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="compare and act; exit 0 done, 1 a doc failed, 2 could not run")
    for name in _LEGACY:
        p = sub.add_parser(name, help="kept for v2.126.0's updater for one release (DL-232)")
        p.add_argument("--target", required=True)
        p.add_argument("--ref", required=True)
        if name == "apply":
            p.add_argument("--actor", choices=("updater",), default="updater")
    args = parser.parse_args(argv)
    if args.command in ("review", "check"):
        print(
            f"nothing to review for {args.target}: bundled skill docs are compared at the start of the release (DL-232)"
        )
        return 0
    try:
        return asyncio.run(_run_sync())
    except Exception as e:  # noqa: BLE001 — CLI boundary: printed, exit 2
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
