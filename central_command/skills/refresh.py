"""The review of a target release's bundled skills against the library
(epic-skills-refresh-on-update R2).

One module computes, for a target ref, the status of every bundled doc, the
three-way merge of the ones both sides changed, and stores the result keyed to
the target. The API route, the updaters' stage step and their gate all call it.

The command line is the contract the two updaters build to. It opens the
database directly, so it answers with the API up or down, and takes its settings
from the checkout's `.env` as the API does:

    python -m central_command.skills.refresh review --target <version> --ref <ref>
        compute and store; prints the counts, never doc text.
        exit 0 stored; exit 2 could not be computed or stored.

    python -m central_command.skills.refresh check --target <version> --ref <ref>
        the gate; recomputes first, so it answers for the library as it is now
        and not as it was when the release was staged (a verdict survives where
        its hashes did).
        exit 0 nothing unresolved; exit 3 docs unresolved, each named on stdout;
        exit 2 could not be computed, which leaves the gate closed.

    python -m central_command.skills.refresh apply --target <version> --ref <ref> [--actor updater]
        post the STORED review, never a recomputed one: the updater runs this
        after the merge, when HEAD is already the target. Each row is its own
        transaction, a row that fails records `apply_error` and the run goes on,
        and a row already applied is skipped, so a re-run posts only what is
        missing. `--actor` is who posts what nobody decided; a verdict's version
        is always the operator's.
        exit 0 every row applied or nothing was due; exit 1 a row failed, each
        named on stderr with its reason and no doc text; exit 2 could not run
        (no review stored, the ref unreadable, the database down), nothing
        posted; exit 3 docs unresolved, each named on stdout, nothing posted.

`<ref>` is what the stage names: `v<target>` on k3s, `upstream` on single-node.

Everything read from git passes `importer.normalise_doc_text` with the live
overlay before it is hashed or merged, so every comparison is between texts as
the library stores them (A5). A missing ref, an unreadable repo, a file the
importer would refuse or a database error raises, and nothing is stored: the
computation finishes before the one transaction that writes.

API tier: this module imports nothing from `gateway/` or `executor`, and nothing
under `runtime/` imports it (`tests/test_skills_update_review.py` walks both).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from central_command import events
from central_command.config import settings
from central_command.db import repo
from central_command.skills import importer
from central_command.skills import overlay as skill_overlay
from central_command.skills.ids import validate_skill_id

# The doc_key of a row about a skill as a whole (`skill-retired`). A slug from
# `importer._doc_key` can never be this.
WHOLE_SKILL_KEY = "*"

# Who may post what nobody decided. An apply never posts as the operator on its
# own account: a verdict is what makes a version the operator's.
APPLY_ACTORS = ("updater",)

# A checkout's git variables would point a read at the wrong repository when
# this runs under a git hook.
_GIT_ENV_DROPPED = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


@dataclass(frozen=True)
class BundledDoc:
    """One bundled file at one ref, as the library would store it."""

    path: str   # relative to the checkout: 'skills/jira/references/api.md'
    kind: str   # 'guidance' | 'reference'
    text: str
    meta: dict  # frontmatter fields, as written


def _git(root: Path, *args: str) -> bytes:
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_DROPPED}
    done = subprocess.run(["git", *args], cwd=root, env=env, capture_output=True)
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip()
        raise ValueError(f"git {' '.join(args)} failed in {root}: {detail}")
    return done.stdout


def _decode(raw: bytes, source: str) -> str:
    """Bytes from git -> the text an import reads from the same file. An import
    goes through `Path.read_text`, whose universal newlines turn CRLF and lone CR
    into LF; skipping that here would make a CRLF file read as a release change."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"{source} is not valid UTF-8: {e}") from e
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_tree(root: Path, ref: str, overlay: dict[str, str]) -> dict[tuple[str, str], BundledDoc]:
    """Every bundled doc at `ref`, keyed (skill_id, doc_key).

    The same shape an import walks: a skill is a folder directly under `skills/`
    holding a SKILL.md, its docs are that file and `references/*.md`, and the
    keys are the importer's. Two files on one key raise, as an import does.
    """
    commit = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
    listed = _git(root, "ls-tree", "-r", "--name-only", "-z", commit, "--", "skills/")
    paths = [PurePosixPath(p) for p in listed.decode("utf-8").split("\0") if p]
    skills = {p.parts[1] for p in paths if len(p.parts) == 3 and p.name == "SKILL.md"}

    docs: dict[tuple[str, str], BundledDoc] = {}
    for p in sorted(paths):
        if len(p.parts) < 3 or p.parts[1] not in skills:
            continue
        if len(p.parts) == 3 and p.name == "SKILL.md":
            kind, key = "guidance", "guidance"
        elif len(p.parts) == 4 and p.parts[2] == "references" and p.suffix == ".md":
            kind, key = "reference", importer._doc_key(p.name)
        else:
            continue
        skill_id = validate_skill_id(p.parts[1])
        source = f"{ref}:{p}"
        if (skill_id, key) in docs:
            raise ValueError(
                f"{source} and {docs[(skill_id, key)].path} collide on doc_key {key!r}"
            )
        raw = _git(root, "show", f"{commit}:{p}")
        doc = importer.normalise_doc_text(kind, _decode(raw, source), source, overlay=overlay)
        docs[(skill_id, key)] = BundledDoc(str(p), kind, doc.text, doc.meta)
    return docs


def merge_three_way(ours: str, base: str, theirs: str, installed: str, target: str) -> tuple[str, int]:
    """`git merge-file --diff3` over the three texts -> (merged, conflict count),
    markers kept. Git reports an error as a negative exit status, which a process
    sees as 255, so anything above the documented cap of 127 raises."""
    with tempfile.TemporaryDirectory(prefix="cc-skill-merge-") as d:
        files = []
        for name, text in (("ours", ours), ("base", base), ("theirs", theirs)):
            f = Path(d) / name
            f.write_bytes(text.encode("utf-8"))
            files.append(str(f))
        done = subprocess.run(
            [
                "git", "merge-file", "-p", "--diff3",
                "-L", "mine (library)",
                "-L", f"base (v{installed})",
                "-L", f"release (v{target})",
                *files,
            ],
            cwd=d, capture_output=True,
        )
    if not 0 <= done.returncode <= 127:
        detail = done.stderr.decode("utf-8", "replace").strip()
        raise ValueError(f"git merge-file failed (exit {done.returncode}): {detail}")
    return done.stdout.decode("utf-8"), done.returncode


def _classify(
    key: tuple[str, str],
    installed: dict[tuple[str, str], BundledDoc],
    theirs_tree: dict[tuple[str, str], BundledDoc],
    library: dict,
    labels: tuple[str, str],
) -> dict | None:
    """One doc's row, or None when nothing anywhere holds it any more (a file of
    the installed tree the library never received and the target dropped)."""
    skill_id, doc_key = key
    theirs_doc, installed_doc = theirs_tree.get(key), installed.get(key)
    lib, prov = library["docs"].get(key), library["provenance"].get(key)
    theirs = theirs_doc.text if theirs_doc else None
    ours = lib["content"] if lib and lib["is_current"] else None

    if prov is not None:
        if prov["bundled_text"] is None:
            raise ValueError(
                f"{skill_id}/{doc_key}: no bundled text is stored for hash {prov['bundled_hash']}"
            )
        base, base_hash, base_origin = prov["bundled_text"], prov["bundled_hash"], "library"
    elif installed_doc is not None:
        base = installed_doc.text
        base_hash, base_origin = importer.bundled_text_hash(base), "installed-tree"
    else:
        base, base_hash, base_origin = None, None, "installed-tree"
    ours_hash = importer.bundled_text_hash(ours) if ours is not None else None
    theirs_hash = importer.bundled_text_hash(theirs) if theirs is not None else None

    merged, conflicts = None, 0
    if lib is None:
        if theirs is None:
            return None
        status = "new"
    elif theirs is None:
        # Retired upstream means the installed release shipped the file and the
        # target does not. Absent from both, a doc is the library's own, which
        # is also what a keep-mine of an earlier retirement leaves behind; and a
        # doc both sides retired has nothing left to decide.
        status = "retired-upstream" if ours is not None and installed_doc else "local-only"
    elif theirs_hash == base_hash:
        status = "unchanged-upstream"
    elif ours is None:
        status = "retired-locally"
    elif ours_hash == base_hash:
        status = "unmodified"
    else:
        status = "modified"
        merged, conflicts = merge_three_way(ours, base or "", theirs, *labels)

    path = theirs_doc or installed_doc
    return {
        "skill_id": skill_id,
        "doc_key": doc_key,
        "status": status,
        "bundled_path": path.path if path else (prov["bundled_path"] if prov else ""),
        "base_hash": base_hash,
        "base_origin": base_origin,
        "ours_hash": ours_hash,
        "theirs_hash": theirs_hash,
        "merged": merged,
        "conflicts": conflicts,
    }


def _classify_all(installed, theirs_tree, library, labels) -> list[dict]:
    bundled = {skill for skill, _ in (*installed, *theirs_tree)}
    retired = {s for s in bundled if library["skills"].get(s) == "RETIRED"}
    rows = [
        {
            "skill_id": s, "doc_key": WHOLE_SKILL_KEY, "status": "skill-retired",
            "bundled_path": f"skills/{s}", "base_hash": None, "base_origin": "library",
            "ours_hash": None, "theirs_hash": None, "merged": None, "conflicts": 0,
        }
        for s in sorted(retired)
    ]
    keys = {*installed, *theirs_tree, *library["docs"]}
    for key in sorted(k for k in keys if k[0] not in retired):
        row = _classify(key, installed, theirs_tree, library, labels)
        if row is not None:
            rows.append(row)
    return rows


def needs_verdict(row: dict) -> bool:
    """Whether the operator must decide this row. A clean merge still must: a
    `modified` doc is never resolved automatically."""
    if row["status"] == "retired-upstream":
        return row["ours_hash"] != row["base_hash"]
    return row["status"] in ("modified", "retired-locally")


def unresolved_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if needs_verdict(r) and r.get("verdict") is None]


def review_counts(rows: list[dict]) -> dict[str, int]:
    def count(status: str) -> int:
        return sum(r["status"] == status for r in rows)

    return {
        "unmodified": count("unmodified"),
        "new": count("new"),
        "modified": count("modified"),
        "unresolved": len(unresolved_rows(rows)),
        "retired_upstream": count("retired-upstream"),
        "retired_locally": count("retired-locally"),
        "skill_retired": count("skill-retired"),
    }


async def compute_review(target: str, ref: str) -> dict:
    """Compute `target`'s review from `ref`, store it whole, record the event.
    Returns `{target, ref, counts, items}`, items being the stored rows."""
    root = importer.checkout_root()
    overlay = skill_overlay.load(settings.resolved_skill_overlay_dir())

    def _read_trees():
        return (
            read_tree(root, "HEAD", overlay),
            read_tree(root, ref, overlay),
            importer.checkout_release(root),
        )

    installed, theirs_tree, release = await asyncio.to_thread(_read_trees)
    if not theirs_tree:
        # Every doc would read as retired upstream, most of them silently.
        raise ValueError(f"{ref} carries no bundled skills under skills/ in {root}")

    bundled = sorted({skill for skill, _ in (*installed, *theirs_tree)})
    library = await repo.skill_review_library(bundled)
    computed = await asyncio.to_thread(
        _classify_all, installed, theirs_tree, library, (release, target),
    )
    items = await repo.replace_skill_doc_review(target, ref, computed)
    counts = review_counts(items)
    await events.emit("skill.review_computed", target, {"target": target, "counts": counts})
    return {"target": target, "ref": ref, "counts": counts, "items": items}


# --- apply -------------------------------------------------------------------


class ReviewNotStored(ValueError):
    """Nothing to apply: no review is stored for the target."""


class ReviewUnresolved(Exception):
    """Docs still need a verdict, so nothing was applied."""

    def __init__(self, rows: list[dict]) -> None:
        super().__init__(f"{len(rows)} docs still need a verdict")
        self.rows = rows


def _apply_act(row: dict, actor: str) -> tuple[str, str | None]:
    """The contract's apply table: (act, who posts it). `actor` posts what nobody
    decided; a verdict is the operator's."""
    status, verdict = row["status"], row["verdict"]
    if status in ("unmodified", "new"):
        return "post_theirs", actor
    if status == "retired-upstream" and verdict is None and not needs_verdict(row):
        return "retire", actor
    act = {
        ("modified", "take_release"): "post_theirs",
        ("modified", "keep_mine"): "post_ours",
        ("modified", "edit"): "post_edit",
        ("retired-upstream", "take_release"): "retire",
        ("retired-locally", "take_release"): "post_theirs",
    }.get((status, verdict))
    if act is not None:
        return act, "operator"
    if needs_verdict(row) and verdict != "keep_mine":
        raise ValueError(f"no apply rule for a {status} doc with verdict {verdict!r}")
    return "nothing", None


async def _apply_row(row: dict, tree: dict, target: str, actor: str) -> dict | None:
    key = (row["skill_id"], row["doc_key"])
    act, added_by = _apply_act(row, actor)
    if act == "nothing":
        return await repo.apply_skill_doc_review_row(row)

    theirs = tree.get(key)
    if (importer.bundled_text_hash(theirs.text) if theirs else None) != row["theirs_hash"]:
        raise ValueError(f"the release text changed since the review (theirs_hash, {key[1]})")
    if act == "retire":
        reason = f"removed in v{target}"
        out = await repo.apply_skill_doc_review_row(row, retire_reason=reason)
        if out is not None:
            await events.emit(
                "skill.doc_retired", ref_id=key[0],
                payload={"doc_key": key[1], "version": out["retired"]["version"], "reason": reason},
                actor=added_by,
            )
        return out

    # A2: the skill's title and summary follow the guidance doc's text, so they
    # move with the release's text and stay with a kept or edited one. A new row
    # may be the first of a whole new skill, in any order, so it carries them.
    takes_release = act == "post_theirs"
    is_guidance = key[1] == "guidance"
    skill = None
    if row["status"] == "new" or (is_guidance and takes_release):
        meta = tree[(key[0], "guidance")].meta
        title, summary = importer.skill_title_summary(meta, key[0])
        skill = {
            "title": title, "summary": summary, "follow": is_guidance,
            "default_agents": importer.parse_id_list(meta.get("default_agents", "")),
        }
    doc_title = None
    if takes_release:
        doc_title = skill["title"] if is_guidance else PurePosixPath(theirs.path).stem
    content = {"post_theirs": theirs.text, "post_ours": None, "post_edit": row["resolved_content"]}[act]
    if act == "post_edit" and content is None:
        raise ValueError("an edit verdict with no text to post")
    out = await repo.apply_skill_doc_review_row(
        row, skill=skill,
        post={
            "kind": theirs.kind, "title": doc_title, "content": content,
            "added_by": added_by, "captured_at": datetime.now(timezone.utc),
            "bundled_release": target, "bundled_path": theirs.path,
            "bundled_hash": row["theirs_hash"], "bundled_text": theirs.text,
        },
    )
    if out is None:
        return None
    if out["created_skill"]:
        await events.emit(
            "skill.created", ref_id=key[0],
            payload={"title": skill["title"], "summary": skill["summary"]}, actor=added_by,
        )
        for agent_id in out["grants"]["applied"]:
            await events.emit(
                "skill.granted", ref_id=agent_id,
                payload={"skill_id": key[0], "default": True}, actor="seed:skill",
            )
    await events.emit(
        "skill.doc_added", ref_id=key[0],
        payload={
            "doc_key": key[1], "kind": theirs.kind, "version": out["doc"]["version"],
            "describes": out["doc"]["describes"], "bundled_release": target,
        },
        actor=added_by,
    )
    return out


async def apply_review(target: str, ref: str | None = None, actor: str = "updater") -> dict:
    """Run the stored review of `target` through the apply table, row by row.

    Returns `{target, ref, applied, failed, skipped, counts, items}`: `applied`
    rows closed by this run, `failed` rows that recorded an `apply_error`,
    `skipped` rows an earlier run already closed; each item says what became of
    its row, and never holds doc text. Raises `ReviewNotStored`, or
    `ReviewUnresolved` before anything is posted. A failure to read the target
    tree raises too: nothing has been posted by then. `ref` defaults to the one
    the review was computed from.
    """
    rows = await repo.list_skill_doc_review(target)
    if not rows:
        raise ReviewNotStored(f"no bundled-skill review is stored for {target!r}")
    unresolved = unresolved_rows(rows)
    if unresolved:
        raise ReviewUnresolved(unresolved)

    ref = ref or rows[0]["ref"]
    due = [r for r in rows if r["applied_at"] is None]
    tree: dict = {}
    if due:
        root = importer.checkout_root()
        overlay = skill_overlay.load(settings.resolved_skill_overlay_dir())
        tree = await asyncio.to_thread(read_tree, root, ref, overlay)

    applied = failed = 0
    skipped = len(rows) - len(due)
    for row in due:
        try:
            done = await _apply_row(row, tree, target, actor)
        except Exception as e:
            failed += 1
            await repo.record_skill_doc_review_error(
                target, row["skill_id"], row["doc_key"], str(e) or type(e).__name__,
            )
            continue
        if done is None:
            skipped += 1
        else:
            applied += 1

    stored = await repo.list_skill_doc_review(target)
    return {
        "target": target, "ref": ref,
        "applied": applied, "failed": failed, "skipped": skipped,
        "counts": review_counts(stored),
        "items": [
            {k: r[k] for k in (
                "skill_id", "doc_key", "status", "verdict", "applied_at", "apply_error",
            )}
            for r in stored
        ],
    }


# --- command line -------------------------------------------------------------


async def _run_apply(args: argparse.Namespace) -> int:
    try:
        result = await apply_review(args.target, args.ref, args.actor)
    except ReviewUnresolved as e:
        for r in e.rows:
            print(f"unresolved: {r['skill_id']}/{r['doc_key']} ({r['status']})")
        print(f"error: {e}; decide them in the cockpit", file=sys.stderr)
        return 3
    finally:
        await repo.close_pool()
    print(
        f"skill refresh for {args.target} ({args.ref}): applied={result['applied']} "
        f"failed={result['failed']} skipped={result['skipped']}"
    )
    errors = [i for i in result["items"] if i["apply_error"]]
    for i in errors:
        print(f"failed: {i['skill_id']}/{i['doc_key']}: {i['apply_error']}", file=sys.stderr)
    return 1 if errors else 0


async def _run(args: argparse.Namespace) -> int:
    if args.command == "apply":
        return await _run_apply(args)
    try:
        review = await compute_review(args.target, args.ref)
    finally:
        await repo.close_pool()
    counts = " ".join(f"{k}={v}" for k, v in review["counts"].items())
    print(f"skill review for {args.target} ({args.ref}): {counts}")
    if args.command == "review":
        return 0
    unresolved = unresolved_rows(review["items"])
    for r in unresolved:
        print(f"unresolved: {r['skill_id']}/{r['doc_key']} ({r['status']})")
    return 3 if unresolved else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m central_command.skills.refresh",
        description="Review a target release's bundled skills against the library.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("review", "compute and store the review; exit 0, or 2 if it cannot be computed"),
        ("check", "the gate; exit 0 nothing unresolved, 3 docs unresolved, 2 cannot be computed"),
        ("apply", "post the stored review; exit 0 done, 1 a row failed, 2 could not run, 3 unresolved"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("--target", required=True, help="the release under review, e.g. 2.68.0")
        p.add_argument("--ref", required=True, help="the git ref holding it, e.g. v2.68.0 or upstream")
        if name == "apply":
            p.add_argument(
                "--actor", choices=APPLY_ACTORS, default="updater",
                help="who posts what nobody decided",
            )
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
