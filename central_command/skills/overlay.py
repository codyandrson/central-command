"""The instance overlay for bundled skills (epic-instance-facts-relocation,
R1 and R2, INSTANCE-008).

A skill that would otherwise name a fact only one deployment has carries a
marker where the fact goes:

    <!-- overlay: model_server_layout -->

The deployment's text for that field lives in `<overlay dir>/<field>.md`,
OUTSIDE the checkout (`settings.resolved_skill_overlay_dir()`), and is written
into the library at IMPORT time by `importer.py`. So the agent tier stays
file-free: what an agent reads is the rendered text already stored in the
database, and an overlay edit reaches the library only through a re-import.

An absent directory, or a field with no file, is not an error: the marker is
removed (an agent never reads one) and the field is reported unresolved. A
MALFORMED marker — anything starting `<!-- overlay` that is not the grammar
below — refuses the whole import, naming the file and line, because a
half-written marker would otherwise reach an agent as noise or, worse, hide a
fact that was meant to be resolved.

API tier only: nothing under `central_command/runtime/` may import this
module (`tests/test_skill_overlay.py` walks the source). No value is ever
logged or placed in an event; callers record field NAMES.
"""

from __future__ import annotations

import re
from pathlib import Path

_FIELD = r"[a-z0-9_]+"
# `<!-- overlay: field -->`, or, until the tree is normalised, the long form
# `<!-- overlay: field — moved to the instance overlay by epic-instance-facts-
# relocation -->`, which names the same field.
_MARKER = re.compile(
    rf"<!-- overlay: (?P<field>{_FIELD})"
    r"(?: — moved to the instance overlay by epic-instance-facts-relocation)? -->"
)
# Anything that STARTS like a marker. A match here that `_MARKER` does not
# cover at the same position is malformed.
_OPENER = re.compile(r"<!--\s*overlay")


def load(directory: str | Path) -> dict[str, str]:
    """Read `<directory>/<field>.md` for every field-named file.

    A missing directory is an empty overlay. A file whose stem is not a valid
    field name is ignored (a README beside the fields is fine). An unreadable
    or non-UTF-8 file raises `ValueError` naming it. The text's trailing
    newlines are trimmed; nothing else is changed.
    """
    root = Path(directory).expanduser()
    if not root.is_dir():
        return {}
    out: dict[str, str] = {}
    for f in sorted(root.glob("*.md")):
        if not re.fullmatch(_FIELD, f.stem) or not f.is_file():
            continue
        try:
            out[f.stem] = f.read_text(encoding="utf-8").rstrip("\r\n")
        except UnicodeDecodeError as e:
            raise ValueError(f"overlay file {f} is not valid UTF-8: {e}") from e
        except OSError as e:
            raise ValueError(f"overlay file {f} is unreadable: {e.strerror or e}") from e
    return out


def render(
    text: str, overlay: dict[str, str], *, source: str
) -> tuple[str, list[str], list[str]]:
    """Replace each marker in `text` with its field's text.

    Returns `(rendered, resolved, unresolved)`: field names, sorted and
    deduplicated. Placement:

    - a marker ALONE on its line is replaced by the value, and removed WITH its
      line when the field is unresolved (no blank line is left behind);
    - a multi-line value inserted at an indented marker takes the marker's
      leading whitespace on every line after the first (markers sit inside
      list items);
    - a marker mid-sentence is replaced in place, and removed in place when
      unresolved.

    A malformed marker raises `ValueError` naming `source:line`.
    """
    resolved: set[str] = set()
    unresolved: set[str] = set()
    out_lines: list[str] = []

    for lineno, line in enumerate(text.splitlines(keepends=True), start=1):
        ending = line[len(line.rstrip("\r\n")):]
        body = line[: len(line) - len(ending)]

        # Every opener on the line must be a well-formed marker.
        for m in _OPENER.finditer(body):
            if not _MARKER.match(body, m.start()):
                raise ValueError(
                    f"{source}:{lineno}: malformed overlay marker "
                    f"{body[m.start(): m.start() + 60]!r}; expected "
                    "'<!-- overlay: <field> -->' with field [a-z0-9_]+"
                )

        if not _MARKER.search(body):
            out_lines.append(line)
            continue

        indent = body[: len(body) - len(body.lstrip(" \t"))]
        alone = _MARKER.fullmatch(body.strip()) is not None

        pieces: list[str] = []
        pos = 0
        for m in _MARKER.finditer(body):
            field = m.group("field")
            pieces.append(body[pos: m.start()])
            pos = m.end()
            if field in overlay:
                resolved.add(field)
                # Every line after the first takes the marker's line
                # indentation (blank lines stay blank), so a value inside a
                # list item stays in the list item.
                first, *rest = overlay[field].replace("\r\n", "\n").split("\n")
                pieces.append("\n".join([first, *((indent + r) if r else r for r in rest)]))
                continue
            unresolved.add(field)
            # Removed in place, tidily: the space that set the marker off from
            # the text before it goes too when nothing but punctuation or the
            # line's end follows, and two spaces around it become one.
            before = "".join(pieces)
            after = body[pos:]
            if before.endswith((" ", "\t")) and (
                after.strip() == "" or after[:1] in ",.;:)]"
            ):
                pieces = [before.rstrip(" \t")]
            elif before.endswith(" ") and after.startswith(" "):
                pos += 1
        pieces.append(body[pos:])
        rendered = "".join(pieces)
        if alone and rendered.strip() == "":
            continue  # unresolved own-line marker: drop the line itself
        out_lines.append(rendered + ending)

    return "".join(out_lines), sorted(resolved), sorted(unresolved)
