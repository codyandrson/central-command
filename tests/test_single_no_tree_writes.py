"""No command under deploy/single/ writes inside the checkout but `.env`.

The rule, from the 2026-09-23 design record's D7: everything an install
GENERATES — `setup-log.txt`, `setup-diagnostics.txt`, `installed.manifest`,
the uvicorn/cockpit logs and pid files, the cockpit-driven updater's working
directory, the generated `.curlrc`, the Windows logon wrapper, discovery's
report and evidence — lives in `$CC_STATE_DIR`, OUTSIDE the tree. Acceptance
for that phase was literally "`git status` clean after every command".

Before it, `deploy/single/.gitignore` and three root `.gitignore` entries kept
the litter invisible instead of absent, and an update had to merge around it.
Those entries are gone, so a regression here is a dirty checkout and a
conflicted update rather than a quiet line in a diff — which is exactly why it
is worth a test.

This is a deliberately PRAGMATIC source walk, not a sandbox: it reads every
write-shaped construct (`>`, `>>`, `tee`, `cp`, `install`, `mkdir`) in the
profile's scripts and fails when a target resolves under `$HERE`, `$REPO_ROOT`
or a literal `deploy/…` path. The one allowed target is the answer file.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = sorted((ROOT / "deploy" / "single").glob("*.sh")) + [
    ROOT / "deploy" / "discover.sh",
    ROOT / "deploy" / "env-lib.sh",
]

# The ONE thing a command may write inside the checkout: the answer file.
# `$ENV_FILE` is what the scripts call it; the literal forms cover the two
# places it is spelled out.
ALLOWED = (
    '"$ENV_FILE"',
    '"$REPO_ROOT/.env"',
    '"$1"',          # cc_set_kv/cc__absorb write "the file they were handed"
    '"$f"',          # ...the same, inside cc_set_kv's loop
)

# Redirections whose TARGET is what we care about. `2>&1` / `>&2` are fd dups,
# not files; `-->` in a progress line is not a redirection either, which is why
# `-` is in the lookbehind.
REDIRECT = re.compile(r"(?<![0-9&<>-])>{1,2}[ \t]*(\"[^\"\n]+\"|\$?[A-Za-z0-9_./${}-]+)")
# Copy-shaped commands, in COMMAND POSITION only, with a target that STARTS at
# a checkout-relative path. Both halves matter: `uv pip install
# "$REPO_ROOT/requirements.lock"` is a read, and a prose `cp` inside a message
# string mentions a path without writing it.
COPYLIKE = re.compile(
    r"(?:^|[;&|(]|&&|\|\|)[ \t]*(?:cp|install|mv|tee|mkdir)\b[^\n|]*?"
    r"(\"\$\{?(?:HERE|REPO_ROOT)\}?/[^\"\n]*\")"
)

# A target that lands in the tree. $HERE is deploy/single (or deploy/ for
# discover.sh); $REPO_ROOT is the checkout root; `deploy/…` is a literal path.
IN_TREE = re.compile(r"\$\{?(?:HERE|REPO_ROOT)\b|(?<![\w/])deploy/[A-Za-z0-9_.-]+/")


def _code_lines(path: pathlib.Path) -> list[tuple[int, str]]:
    """Source lines with comments and single-quoted literals removed.

    A `#` inside a string is not a comment, but every `#` this repo's scripts
    use mid-line IS one, and printf format strings are what carry the false
    positives (a report that PRINTS `deploy/single/...` writes nothing).
    """
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Drop single-quoted chunks: printf formats, sed programs, echo text.
        line = re.sub(r"'[^'\n]*'", "''", line)
        line = re.sub(r"(?<![$\w])#.*$", "", line)
        out.append((n, line))
    return out


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_nothing_is_written_inside_the_checkout(script: pathlib.Path):
    offenders = []
    for n, line in _code_lines(script):
        targets = [m.group(1) for m in REDIRECT.finditer(line)]
        targets += [m.group(1) for m in COPYLIKE.finditer(line)]
        for target in targets:
            if target in ALLOWED:
                continue
            if IN_TREE.search(target):
                offenders.append(f"{script.name}:{n}: writes {target}")
    assert not offenders, (
        "generated files must live in $CC_STATE_DIR, never inside the checkout "
        "(design record 2026-09-23, D7):\n  " + "\n  ".join(offenders)
    )


def test_the_state_dir_is_where_the_generated_files_went():
    """Each retired litter path is addressed through the state dir now."""
    setup = (ROOT / "deploy" / "single" / "setup.sh").read_text(encoding="utf-8")
    for name in ("setup-log.txt", "setup-diagnostics.txt", "uvicorn.log",
                 "uvicorn.pid", "cockpit.log", "cockpit.pid", "cc-boot.cmd",
                 "boot-at-logon.log"):
        assert f"$HERE/{name}" not in setup, f"setup.sh still writes $HERE/{name}"
    resolver = (ROOT / "deploy" / "single" / "resolve-images.sh").read_text(encoding="utf-8")
    assert '"$HERE/installed.manifest"' not in resolver
    assert "$STATE_DIR/installed.manifest" in resolver


def test_the_single_profile_has_no_gitignore_of_its_own():
    """It existed only to hide the litter; the litter is gone."""
    assert not (ROOT / "deploy" / "single" / ".gitignore").exists()
    root_ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for gone in ("deploy/discovery.out/", "deploy/discovery.conf"):
        assert f"\n{gone}\n" not in root_ignore, (
            f"{gone} is ignored again — nothing writes it any more, so the entry "
            "would only hide a regression"
        )
    assert "\n.env\n" in root_ignore, "the answer file must stay gitignored"
