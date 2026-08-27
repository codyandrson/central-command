"""Import a `SKILL.md` + `references/` folder into the library.

The Anthropic Skill layout is used as the AUTHORING convention (spec §"What a
skill is"): it is proven, it is what the operator already thinks in, and any
existing skill folder imports without being rewritten. Delivery is still
pydantic-ai capabilities and storage is still the database — this module only
reads files.

It lives in `central_command/skills/`, NOT in `runtime/`: the agent tier has no
filesystem access and must not gain any. Only the API tier calls this.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from central_command.db import repo
from central_command.skills.ids import validate_skill_id

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Split simple `key: value` YAML frontmatter from the body.

    Deliberately not a YAML parser: skill frontmatter is flat `key: value`, and
    taking a YAML dependency to read two fields would be the larger mistake.
    Nested or list-valued keys are ignored rather than guessed at.
    """
    m = _FRONTMATTER.match(raw)
    if not m:
        return {}, raw
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if line.startswith((" ", "\t", "#")) or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip("'\"")
    return meta, raw[m.end():].lstrip("\n")


def _doc_key(filename: str) -> str:
    """A stable slug per reference file, so re-import VERSIONS rather than
    duplicates. Derived from the filename, which is what stays constant upstream."""
    stem = Path(filename).stem.lower()
    return re.sub(r"[^a-z0-9]+", "-", stem).strip("-") or "reference"


def _read_text(f: Path) -> str:
    """Decode one file, raising a message that names the offending file.

    Read-everything-then-write-everything relies on this raising BEFORE any
    database write happens, so a bad file fails loud instead of leaving a
    half-imported skill behind.
    """
    try:
        return f.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"{f} is not valid UTF-8: {e}") from e


async def import_skill_folder(
    path: str | Path,
    skill_id: str | None = None,
    source_url: str | None = None,
    describes: str | None = None,
) -> dict:
    """Import one skill folder. Returns what was written.

    Re-importing the same folder creates NEW VERSIONS of each document, because
    `doc_key` is derived from the filename and `add_skill_doc` supersedes. That
    makes "refresh the docs after an upstream release" the same action as the
    first import — but ONLY when the caller names the target with `skill_id`.

    A DERIVED id that already exists is refused (`ValueError` → 422 at the
    route). The derivation is lossy — `Pydantic AI`, `pydantic-ai` and
    `pydantic_ai` all yield `pydantic-ai` — and `repo.create_skill` is an
    upsert, so without this check importing a second vendor's `Jira` folder
    beside an existing `jira` skill produced no error and no 409: one skill
    with two vendors' documentation interleaved, silently re-pointing every
    agent that holds it. Passing `skill_id` explicitly is the operator saying
    "yes, this folder updates THAT skill", which is the only way the two cases
    can be told apart from here.

    Read-everything-then-write-everything on purpose: every file is opened,
    decoded, and slugged into a doc_key BEFORE the first database write. That
    way a decode failure or a doc_key collision raises before anything is
    persisted, instead of leaving a half-imported skill (some references
    written, others lost or erroring mid-loop) for the operator to find later.
    """
    folder = Path(path).expanduser().resolve()
    skill_md = folder / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"no SKILL.md in {folder}")

    # --- discover + decode everything first; write nothing yet -------------
    meta, body = parse_frontmatter(_read_text(skill_md))
    derived = skill_id is None
    sid = validate_skill_id(skill_id or _doc_key(meta.get("name") or folder.name))
    title = meta.get("name") or folder.name
    summary = meta.get("description") or f"Knowledge about {title}"

    ref_dir = folder / "references"
    ref_files = sorted(ref_dir.glob("*.md")) if ref_dir.is_dir() else []

    # The guidance doc always occupies the "guidance" key, so a reference file
    # that slugs to "guidance" (e.g. references/guidance.md) collides with it
    # just as surely as two references colliding with each other — it must
    # be caught in the SAME map, not a second, narrower check.
    key_to_files: dict[str, list[Path]] = {"guidance": [skill_md]}
    for f in ref_files:
        key_to_files.setdefault(_doc_key(f.name), []).append(f)
    collisions = {k: v for k, v in key_to_files.items() if len(v) > 1}
    if collisions:
        detail = "; ".join(
            f"{key!r} <- {', '.join(str(f) for f in files)}"
            for key, files in sorted(collisions.items())
        )
        raise ValueError(f"filenames collide on the same doc_key: {detail}")

    if derived and await repo.get_skill(sid) is not None:
        raise ValueError(
            f"the id derived from this folder, {sid!r}, is already in the "
            "library. If this folder updates that skill, re-import with "
            f"skill_id={sid!r} to say so; if it is a different skill, give it "
            "a distinct skill_id."
        )

    references_to_write: list[tuple[str, Path, str]] = []
    for f in ref_files:
        key = _doc_key(f.name)
        _, ref_body = parse_frontmatter(_read_text(f))
        references_to_write.append((key, f, ref_body))

    # --- everything decoded and slug-checked; now write -------------------
    # ONE capture instant for the whole folder: these documents were copied
    # together, and stamping each row with its own `now()` would imply a
    # precision the import does not have. The spec's rule is that ALL paths
    # record `captured_at`, because freshness metadata nobody enters is
    # freshness metadata nobody has — and this path recorded none until
    # 2026-07-26, so every imported document (i.e. every document) reached the
    # agent with the `Captured:` line omitted by `_guidance_text`/`format_hits`.
    captured_at = datetime.now(timezone.utc)

    await repo.create_skill(sid, title, summary)
    await repo.add_skill_doc(
        sid, "guidance", "guidance", title, body,
        source_url=source_url, describes=describes, captured_at=captured_at,
    )

    references: list[str] = []
    for key, f, ref_body in references_to_write:
        await repo.add_skill_doc(
            sid, key, "reference", f.stem, ref_body,
            source_url=source_url, describes=describes, captured_at=captured_at,
        )
        references.append(key)

    return {"skill_id": sid, "guidance": "guidance", "references": references}
