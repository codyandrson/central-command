"""Apply the two graphiti-core fixes we carry (D7) to the INSTALLED package.

The patches are unified diffs under `deploy/graphiti-patches/`; `patch` is not
guaranteed on every substrate, so this applies them with nothing but the
standard library. The rules are the point:

* exact context, no fuzz — the package version is pinned, so a hunk that
  matches neither before nor after is a real finding, not noise;
* idempotent — a hunk whose NEW block is already present is skipped;
* all-or-nothing — every hunk of every file is validated before anything is
  written;
* atomic and hardlink-safe — a temp file in the same directory, then
  `os.replace`. `uv` hardlinks installed files from its cache; writing in place
  would patch the cache and every other environment sharing it.

STDLIB ONLY, and it must never import `graphiti_core` (importing it loads
dotenv and starts telemetry). The package is located with `find_spec`, which
does not execute a top-level package. The CLI (`scripts/apply_graphiti_patches.py`)
loads this file by path, so no sibling imports either.
"""

from __future__ import annotations

import importlib.util
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

# The one definition of the pinned version; a pyproject pin test reads it.
PINNED_VERSION = "0.30.2"

PATCH_FILES = ("1729-invalidation-scope.patch", "1666-reasoning-first-dedupe.patch")

# parents[2] is the repo root (central_command/integrations/<this file>). A
# non-editable install has no `deploy/` beside the package: the patches are
# then not found and every check reports 'unknown' rather than guessing.
PATCH_DIR = Path(__file__).resolve().parents[2] / "deploy" / "graphiti-patches"

_HUNK = re.compile(r"^@@ -(\d+)(,(\d+))? \+(\d+)(,(\d+))? @@")


class PatchError(Exception):
    """A patch could not be located, parsed or matched. Nothing was written."""


@dataclass(frozen=True)
class Hunk:
    old: tuple[str, ...]
    new: tuple[str, ...]
    hint: int  # header's old start, 0-based; a tiebreak only


@dataclass(frozen=True)
class FilePatch:
    path: str  # relative to site-packages, -p1 applied
    hunks: tuple[Hunk, ...]


def parse_patch(text: str) -> list[FilePatch]:
    """Hunk bodies are read by the header's line counts, so a trailing newline
    or a blank separator between files is never mistaken for context."""
    files: list[FilePatch] = []
    path: str | None = None
    hunks: list[Hunk] = []
    src = text.split("\n")
    i = 0
    while i < len(src):
        line = src[i]
        i += 1
        if line.startswith("--- "):
            if path is not None:
                files.append(FilePatch(path, tuple(hunks)))
            path, hunks = None, []
        elif line.startswith("+++ "):
            raw = line[4:].split("\t")[0].strip()
            parts = raw.split("/", 1)  # -p1
            if len(parts) != 2:
                raise PatchError(f"unusable path in patch header: {raw!r}")
            p = Path(parts[1])
            if p.is_absolute() or ".." in p.parts:
                raise PatchError(f"unsafe path in patch header: {raw!r}")
            path = parts[1]
        elif (m := _HUNK.match(line)) is not None:
            if path is None:
                raise PatchError("hunk before any file header")
            want_old, want_new = int(m.group(3) or 1), int(m.group(6) or 1)
            old: list[str] = []
            new: list[str] = []
            while len(old) < want_old or len(new) < want_new:
                if i >= len(src):
                    raise PatchError(f"{path}: hunk at line {m.group(1)} is truncated")
                body = src[i]
                i += 1
                if body.startswith("\\"):
                    continue  # "\ No newline at end of file"
                tag, rest = (body[:1], body[1:])
                if tag == "+":
                    new.append(rest)
                elif tag == "-":
                    old.append(rest)
                elif tag == " " or body == "":
                    # A bare "" is an empty context line whose space an editor stripped.
                    old.append(rest)
                    new.append(rest)
                else:
                    raise PatchError(f"{path}: unrecognised hunk line {body!r}")
            hunks.append(Hunk(tuple(old), tuple(new), int(m.group(1)) - 1))
    if path is not None:
        files.append(FilePatch(path, tuple(hunks)))
    if not files or any(not f.hunks for f in files):
        raise PatchError("patch has no file section with hunks")
    return files


def _find(lines: list[str], block: tuple[str, ...]) -> list[int]:
    n = len(block)
    if n == 0:
        return []
    first = block[0]
    return [
        i
        for i in range(len(lines) - n + 1)
        if lines[i] == first and tuple(lines[i : i + n]) == block
    ]


def _classify(lines: list[str], hunk: Hunk) -> tuple[str, int]:
    """('pending'|'applied', index) or raise. Old checked against new so an
    edge-anchored hunk (context on one side only) that matches both ways is
    refused as ambiguous rather than guessed."""
    old = _find(lines, hunk.old)
    new = _find(lines, hunk.new)
    if old and not new:
        return "pending", min(old, key=lambda i: abs(i - hunk.hint))
    if new and not old:
        return "applied", min(new, key=lambda i: abs(i - hunk.hint))
    if old and new:
        raise PatchError("ambiguous: both the old and the new block are present")
    raise PatchError("neither the old nor the new block is present")


def _splice(lines: list[str], hunks: list[tuple[Hunk, int]]) -> list[str]:
    out = list(lines)
    prev_start = len(lines) + 1
    # Bottom to top so earlier indices stay valid.
    for hunk, idx in sorted(hunks, key=lambda t: t[1], reverse=True):
        if idx + len(hunk.old) > prev_start:
            raise PatchError("overlapping hunks")
        out[idx : idx + len(hunk.old)] = list(hunk.new)
        prev_start = idx
    return out


def installed_version(site: Path) -> str:
    """Read the version from dist-info METADATA — importing the package would
    run its import-time side effects."""
    versions = set()
    for d in site.glob("*.dist-info"):
        name = re.split(r"-\d", d.name[: -len(".dist-info")], maxsplit=1)[0]
        if re.sub(r"[-_.]+", "-", name).lower() != "graphiti-core":
            continue
        meta = d / "METADATA"
        try:
            for ln in meta.read_text(encoding="utf-8").splitlines():
                if ln.startswith("Version:"):
                    versions.add(ln.split(":", 1)[1].strip())
                    break
                if not ln.strip():
                    break
        except OSError:
            continue
    if not versions:
        raise PatchError(f"no graphiti-core dist-info under {site}")
    if len(versions) > 1:
        raise PatchError(f"several graphiti-core versions under {site}: {sorted(versions)}")
    return versions.pop()


def find_site_packages() -> Path:
    spec = importlib.util.find_spec("graphiti_core")
    if spec is None or not spec.submodule_search_locations:
        raise PatchError("graphiti_core is not installed in this interpreter")
    return Path(list(spec.submodule_search_locations)[0]).resolve().parent


def _load_patches(patch_dir: Path) -> list[tuple[str, list[FilePatch]]]:
    out = []
    for name in PATCH_FILES:
        p = patch_dir / name
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as e:
            raise PatchError(f"patch file {p} unreadable ({e.strerror}); "
                             "a non-editable install does not ship deploy/") from e
        try:
            out.append((name, parse_patch(text)))
        except PatchError as e:
            raise PatchError(f"{name}: {e}") from e
    return out


@dataclass
class Plan:
    # relpath -> new text, only for files that change
    writes: dict[str, str]
    # (patch name, relpath, 'patched'|'already patched')
    lines: list[tuple[str, str, str]]
    # patch name -> counts of hunks
    counts: dict[str, tuple[int, int]]  # (pending, applied)


def build_plan(site: Path, patch_dir: Path | None = None) -> Plan:
    """Validate every hunk of every patch against the files on disk. Raises
    PatchError naming the file and hunk on the first mismatch; writes nothing."""
    patches = _load_patches(patch_dir or PATCH_DIR)
    texts: dict[str, str] = {}
    writes: dict[str, str] = {}
    lines_out: list[tuple[str, str, str]] = []
    counts: dict[str, tuple[int, int]] = {}
    for name, files in patches:
        pend = done = 0
        for fp in files:
            if fp.path not in texts:
                target = site / fp.path
                try:
                    texts[fp.path] = target.read_bytes().decode("utf-8")
                except OSError as e:
                    raise PatchError(f"{name}: {fp.path}: cannot read ({e.strerror})") from e
            lines = texts[fp.path].split("\n")
            todo: list[tuple[Hunk, int]] = []
            file_done = 0
            for n, h in enumerate(fp.hunks, 1):
                try:
                    state, idx = _classify(lines, h)
                except PatchError as e:
                    raise PatchError(f"{name}: {fp.path}: hunk {n} (near line {h.hint + 1}): {e}") from e
                if state == "pending":
                    todo.append((h, idx))
                else:
                    file_done += 1
            if todo:
                try:
                    lines = _splice(lines, todo)
                except PatchError as e:
                    raise PatchError(f"{name}: {fp.path}: {e}") from e
                texts[fp.path] = "\n".join(lines)
                writes[fp.path] = texts[fp.path]
            pend += len(todo)
            done += file_done
            lines_out.append((name, fp.path, "patched" if todo else "already patched"))
        counts[name] = (pend, done)
    return Plan(writes, lines_out, counts)


def patch_state(site_packages: Path | None = None, patch_dir: Path | None = None) -> dict[str, str]:
    """Per patch: 'patched' (every hunk present), 'pristine' (none), or
    'unknown' (mixed, mismatched, unreadable, or no package). Read-only."""
    try:
        site = site_packages or find_site_packages()
        plan = build_plan(Path(site), patch_dir)
    except PatchError:
        return {name: "unknown" for name in PATCH_FILES}
    out = {}
    for name in PATCH_FILES:
        pend, done = plan.counts[name]
        out[name] = "patched" if pend == 0 else "pristine" if done == 0 else "unknown"
    return out


def _atomic_write(target: Path, text: str) -> None:
    mode = stat.S_IMODE(target.stat().st_mode)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def apply_patches(
    site_packages: Path | None = None,
    patch_dir: Path | None = None,
    *,
    force_version: bool = False,
) -> list[tuple[str, str, str]]:
    """Apply what is pending; returns (patch, file, status) per file. Raises
    PatchError before writing anything if the version is wrong or any hunk fails."""
    site = Path(site_packages) if site_packages else find_site_packages()
    version = installed_version(site)
    if version != PINNED_VERSION and not force_version:
        raise PatchError(
            f"graphiti-core {version} is installed; the patches are written for "
            f"{PINNED_VERSION} (pass --force-version to try anyway)"
        )
    plan = build_plan(site, patch_dir)
    for rel, text in plan.writes.items():
        _atomic_write(site / rel, text)
    return plan.lines
