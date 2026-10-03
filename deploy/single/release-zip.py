#!/usr/bin/env python3
"""Read and unpack a Central Command source zip — the same way on every host.

`update.sh import` used to drive `unzip`, and the 2026-10-02 Windows acceptance
run (finding F6) measured what that cost: Git for Windows' UnZip 6.00 does not
let `*` cross `/`, so the vendor skip `-x <prefix>docs/vendor/*` excluded 3 of
49,809 entries; all of docs/vendor was unpacked (104 s), its four symlink
members failed with `symlink error`, and NO release zip could be imported on
Windows at all. A glob dialect is a property of one build of one tool. This is
Python's `zipfile` instead — the interpreter the install already resolves
(deploy/env-lib.sh's cc_resolve_py) — and a skip that is a PATH PREFIX, so it
means the same thing everywhere.

    release-zip.py find    <zip> <suffix>    the first entry name ending in <suffix>
    release-zip.py cat     <zip> <entry>     one entry's bytes on stdout
    release-zip.py extract <zip> <dest> [--skip <prefix>] [--links <file>]

`extract` rules, each decided BEFORE anything is written — a zip that breaks
one writes nothing at all:

* an entry under `--skip` (e.g. `central-command-2.58.0/docs/vendor/`) is not
  read, written or checked — the skip stays a skip, symlinks included;
* an entry whose name would land outside <dest> (absolute, a drive letter,
  a `..` component, a control character) is refused, naming it;
* a SYMLINK entry (git archive records mode 120000 in the external attributes)
  is never written to disk — a link is exactly what Windows cannot create
  without privileges, and what unzip turned into a failure. Its path and
  target go to `--links` as `<path>\\t<target>` lines, and update.sh records
  each one into the import commit as a git symlink (mode 120000), so the
  commit is the release on every host. A link whose target is absolute or
  climbs out of the tree is refused, naming it; with no `--links` file any
  symlink entry is refused, naming it;
* a regular file gets the Unix permission bits the zip recorded (git archive
  writes 0755/0644), so an extracted `setup.sh` is executable — on Linux, git
  then records it as 100755.

Exit 0 on success; 1 with ONE line on stderr, `release-zip: <reason>`, on any
failure. Standard library only.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import sys
import zipfile

PREFIX = "release-zip: "
_DRIVE = re.compile(r"^[A-Za-z]:")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class Refused(Exception):
    pass


def _mode(info: zipfile.ZipInfo) -> int:
    """The Unix st_mode the zip recorded, or 0 when it recorded none."""
    if info.create_system != 3:          # 3 = Unix; anything else carries no mode
        return 0
    return (info.external_attr >> 16) & 0xFFFF


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(_mode(info))


def _parts(name: str) -> list[str]:
    """An entry name's components, splitting on BOTH separators: on Windows a
    backslash in a name is a directory boundary when it is written."""
    return [p for p in re.split(r"[\\/]", name) if p not in ("", ".")]


def _shown(info: zipfile.ZipInfo) -> str:
    """The entry's name AS THE ARCHIVE SPELLS IT, for a refusal. On Windows
    zipfile rewrites every backslash in `filename` to `/` (os.sep is `\\`), so
    `a\\..\\..\\x` was refused as `a/../../x` — a name that is not in the
    archive. `orig_filename` is the recorded one on every host; the DECISIONS
    still read `filename`, unchanged."""
    return info.orig_filename or info.filename


def _check_name(name: str, shown: str) -> None:
    if _CONTROL.search(name):
        raise Refused(f"entry {shown!r} has a control character in its name — refusing the archive")
    if name.startswith(("/", "\\")) or _DRIVE.match(name):
        raise Refused(f"entry {shown!r} is an absolute path and would land outside the destination — refusing the archive")
    if ".." in _parts(name):
        raise Refused(f"entry {shown!r} climbs out with '..' and would land outside the destination — refusing the archive")


def _target_of(name: str, target: str, shown: str) -> str:
    """The link target, checked to stay inside the tree the zip unpacks to."""
    if not target or _CONTROL.search(target):
        raise Refused(f"symlink entry {shown!r} has an empty or unprintable target — refusing the archive")
    if target.startswith(("/", "\\")) or _DRIVE.match(target):
        raise Refused(f"symlink entry {shown!r} points at an absolute path ({target}) — refusing the archive")
    here = _parts(name)[:-1]
    for p in _parts(target):
        if p == "..":
            if not here:
                raise Refused(f"symlink entry {shown!r} points outside the tree ({target}) — refusing the archive")
            here.pop()
        else:
            here.append(p)
    return target


def _inside(dest_real: str, path: str) -> bool:
    real = os.path.realpath(path)
    return real == dest_real or real.startswith(dest_real.rstrip(os.sep) + os.sep)


def _skipped(name: str, skip: str) -> bool:
    return bool(skip) and (name.startswith(skip) or name == skip.rstrip("/"))


def extract(zip_path: str, dest: str, skip: str = "", links: str | None = None) -> None:
    os.makedirs(dest, exist_ok=True)
    dest_real = os.path.realpath(dest)
    files: list[zipfile.ZipInfo] = []
    dirs: list[str] = []
    link_rows: list[tuple[str, str]] = []
    with zipfile.ZipFile(zip_path) as z:
        # ── decide everything first: nothing is written for a refused archive ──
        for info in z.infolist():
            name, shown = info.filename, _shown(info)
            if _skipped(name, skip):
                continue
            _check_name(name, shown)
            out = os.path.join(dest, *_parts(name))
            if not _inside(dest_real, out):
                raise Refused(f"entry {shown!r} would land outside the destination — refusing the archive")
            if _is_symlink(info):
                if links is None:
                    raise Refused(f"entry {shown!r} is a symlink, and no link list was asked for — refusing the archive")
                target = z.read(info).decode("utf-8", errors="strict")
                link_rows.append(("/".join(_parts(name)), _target_of(name, target, shown)))
            elif info.is_dir():
                dirs.append(out)
            else:
                files.append(info)
        # ── then write ──
        for d in dirs:
            os.makedirs(d, exist_ok=True)
        for info in files:
            out = os.path.join(dest, *_parts(info.filename))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(info) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            perm = stat.S_IMODE(_mode(info)) & 0o777
            if perm:
                os.chmod(out, perm | stat.S_IRUSR | stat.S_IWUSR)
    if links is not None:
        with open(links, "w", encoding="utf-8", newline="\n") as f:
            for path, target in link_rows:
                f.write(f"{path}\t{target}\n")


def find(zip_path: str, suffix: str) -> None:
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if name.endswith(suffix):
                sys.stdout.write(name + "\n")
                return
    raise Refused(f"no entry ending in {suffix}")


def cat(zip_path: str, entry: str) -> None:
    with zipfile.ZipFile(zip_path) as z:
        try:
            data = z.read(entry)
        except KeyError:
            raise Refused(f"no entry {entry}") from None
    sys.stdout.buffer.write(data)


def main(argv: list[str]) -> int:
    try:
        if len(argv) >= 3 and argv[0] == "find":
            find(argv[1], argv[2])
        elif len(argv) >= 3 and argv[0] == "cat":
            cat(argv[1], argv[2])
        elif len(argv) >= 3 and argv[0] == "extract":
            skip, links, rest = "", None, argv[3:]
            while rest:
                if rest[0] == "--skip" and len(rest) > 1:
                    skip, rest = rest[1], rest[2:]
                elif rest[0] == "--links" and len(rest) > 1:
                    links, rest = rest[1], rest[2:]
                else:
                    raise Refused(f"unknown argument {rest[0]!r}")
            extract(argv[1], argv[2], skip, links)
        else:
            raise Refused("usage: release-zip.py find|cat|extract … (see the module docstring)")
    except Refused as e:
        print(PREFIX + str(e), file=sys.stderr)
        return 1
    except (zipfile.BadZipFile, OSError, UnicodeDecodeError, ValueError) as e:
        print(PREFIX + f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
