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

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from central_command.config import settings
from central_command.db import repo
from central_command.skills import overlay as skill_overlay
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


def parse_id_list(value: str) -> list[str]:
    """`[a, b]` or `a, b` -> ["a", "b"]. Flat frontmatter has no list syntax, so
    `default_agents` is a comma list, optionally bracketed."""
    return [p.strip().strip("'\"") for p in value.strip().strip("[]").split(",") if p.strip()]


def _doc_key(filename: str) -> str:
    """A stable slug per reference file, so re-import VERSIONS rather than
    duplicates. Derived from the filename, which is what stays constant upstream."""
    stem = Path(filename).stem.lower()
    return re.sub(r"[^a-z0-9]+", "-", stem).strip("-") or "reference"


@dataclass(frozen=True)
class NormalisedDoc:
    """One document as the library stores it, with what its render touched."""

    meta: dict          # frontmatter fields, parsed as written (never rendered)
    text: str           # the stored content: frontmatter stripped, overlay rendered
    resolved: list[str]    # overlay field NAMES filled in
    unresolved: list[str]  # overlay field NAMES with no text (marker removed)


def normalise_doc_text(
    kind: str,
    raw_text: str,
    source: str,
    *,
    overlay: dict[str, str] | None = None,
) -> NormalisedDoc:
    """Raw bundled-file text -> the text the library stores. ONE path.

    The frontmatter strip, then the overlay render of the BODY only. Every
    caller that turns a bundled file into a library document goes through
    here, so the stored text, the hash taken over it (`bundled_text_hash`) and
    the text a refresh compares against can never differ in how they were made
    (epic-skills-refresh-on-update A5: the base is the RENDERED text the
    library received, so an overlay edit alone never reads as a release change).

    Frontmatter is parsed as written; a marker in it is refused (it would reach
    the skill's id, title and summary), and reported lines are lines of the
    source. `kind` is 'guidance' (SKILL.md) or 'reference'; both take the same
    steps today, and an unknown kind raises rather than being guessed at.
    `source` names the text in errors (a path on disk, or `<ref>:<path>` for
    a file read from git). `overlay` is the loaded field map; None loads it
    from the configured directory, which a caller normalising many files
    loads once and passes.
    """
    if kind not in ("guidance", "reference"):
        raise ValueError(f"{source}: unknown document kind {kind!r}")
    if overlay is None:
        overlay = skill_overlay.load(settings.resolved_skill_overlay_dir())
    meta, _ = parse_frontmatter(raw_text)
    m = _FRONTMATTER.match(raw_text)
    head, rest = (raw_text[: m.end()], raw_text[m.end():]) if m else ("", raw_text)
    skill_overlay.refuse_in(head, source=source)
    rendered, got, missed = skill_overlay.render(
        rest, overlay, source=source, first_line=head.count("\n") + 1
    )
    return NormalisedDoc(meta, rendered.lstrip("\n"), got, missed)


def bundled_text_hash(text: str) -> str:
    """sha256 (hex) of the STORED text, as UTF-8 — the key of
    `skill_bundled_text` and the `bundled_hash` of a version posted from it."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def checkout_root() -> Path:
    """The checkout the bundled skills are read from: `CC_REPO_ROOT` when set,
    else the one this code runs from (`central_command/skills/` is two levels
    below it). The ONE root seam: the import's stamp and the review's git reads
    both come through here, and a test points it at a tmp checkout."""
    if settings.repo_root:
        return Path(settings.repo_root).expanduser()
    return Path(__file__).resolve().parents[2]


def checkout_release(root: Path) -> str:
    """The checkout's release: the `version=` line of its `VERSION` file, the
    one the updaters call current. Unreadable or missing raises, because a
    stamp of a guessed release would put a wrong base into every later review."""
    version_file = root / "VERSION"
    try:
        text = version_file.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"{version_file} is unreadable: {e.strerror or e}") from e
    m = re.search(r"^version=(\S+)\s*$", text, re.M)
    if not m:
        raise ValueError(f"{version_file} has no version= line")
    return m.group(1)


def _bundled_stamp(folder: Path) -> tuple[Path, str] | None:
    """`(checkout root, release)` when `folder` is a skill folder under the
    checkout's `skills/` tree, else None: provenance is a claim that a file
    came from this release, so a folder from anywhere else makes none."""
    root = checkout_root().resolve()
    if (root / "skills") not in folder.parents:
        return None
    return root, checkout_release(root)


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
    apply_default_grants: bool = False,
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

    The instance overlay (`skills/overlay.py`) is rendered in the same decode-
    first phase: every `<!-- overlay: <field> -->` marker in SKILL.md and every
    reference becomes this deployment's text (or is removed), so the library
    holds what agents read and a malformed marker refuses before any write.
    The result's `overlay` lists field NAMES only, never a value.

    A folder under the checkout's `skills/` tree is stamped: each version it
    posts records the checkout's release, the file's path relative to the
    checkout and the sha256 of the text as stored (`bundled_text_hash`), and the
    library keeps that text content-addressed. A folder from anywhere else
    stamps nothing, and the result's `bundled_release` is None.

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
    # Provenance (epic-skills-refresh-on-update R1): read the checkout's release
    # here, before any write, so an unreadable VERSION refuses the import whole.
    stamp = _bundled_stamp(folder)
    release = stamp[1] if stamp else None

    def _provenance(f: Path, text: str) -> dict:
        if stamp is None:
            return {}
        rel = folder.relative_to(stamp[0]) / f.relative_to(folder)
        return {
            "bundled_release": release,
            "bundled_path": rel.as_posix(),
            "bundled_hash": bundled_text_hash(text),
        }

    fields = skill_overlay.load(settings.resolved_skill_overlay_dir())
    resolved: set[str] = set()
    unresolved: set[str] = set()

    def _split_render(kind: str, f: Path) -> tuple[dict, str]:
        doc = normalise_doc_text(kind, _read_text(f), str(f), overlay=fields)
        resolved.update(doc.resolved)
        unresolved.update(doc.unresolved)
        return doc.meta, doc.text

    meta, body = _split_render("guidance", skill_md)
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

    default_agents = (
        parse_id_list(meta.get("default_agents", "")) if apply_default_grants else []
    )

    references_to_write: list[tuple[str, Path, str]] = []
    for f in ref_files:
        key = _doc_key(f.name)
        _, ref_body = _split_render("reference", f)
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
        **_provenance(skill_md, body),
    )

    references: list[str] = []
    for key, f, ref_body in references_to_write:
        await repo.add_skill_doc(
            sid, key, "reference", f.stem, ref_body,
            source_url=source_url, describes=describes, captured_at=captured_at,
            **_provenance(f, ref_body),
        )
        references.append(key)

    # Default holders (front-matter `default_agents`), applied CREATE-ONLY after
    # the skill exists: a revoked grant stays revoked, an unknown agent is a
    # note. The route emits the events; this only writes the rows.
    default_grants = await repo.seed_skill_grants(sid, default_agents)

    return {
        "skill_id": sid, "guidance": "guidance", "references": references,
        "default_grants": default_grants,
        "overlay": {"resolved": sorted(resolved), "unresolved": sorted(unresolved)},
        # The release the docs were stamped with; None when the folder is not
        # under the checkout's skills/ tree and nothing was stamped.
        "bundled_release": release,
    }
