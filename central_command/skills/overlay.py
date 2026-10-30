"""The instance overlay for bundled skills (epic-instance-facts-relocation,
R1, R2 and R3, INSTANCE-008).

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

`FIELDS` is the index of every field the bundled skills mark, with a public
line on WHAT each holds (never a value). `python -m central_command.skills.overlay
check` reads it against an overlay directory and says which fields resolve; it
is read-only, prints no value, and opens no database.

API tier only: nothing under `central_command/runtime/` may import this
module (`tests/test_skill_overlay.py` walks the source). No value is ever
logged or placed in an event; callers record field NAMES.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_FIELD = r"[a-z0-9_]+"
_MARKER = re.compile(rf"<!-- overlay: (?P<field>{_FIELD}) -->")
# Anything that STARTS like a marker. A match here that `_MARKER` does not
# cover at the same position is malformed.
_OPENER = re.compile(r"<!--\s*overlay")

# Every field the bundled skills mark, in the order a reader meets the
# subjects: what the field HOLDS, never a value. `tests/test_skill_overlay.py`
# pins this dict to the markers under `skills/` in both directions.
FIELDS: dict[str, str] = {
    "agent_traffic_cost": "the measured cost of this deployment's agent LLM traffic",
    "compute_node_name": "the name of the compute node that builds and runs the sandboxes",
    "confluence_instances": "which Confluence site or sites this deployment is connected to",
    "confluence_plan_limits": "the Confluence plan tier's quotas (API points, users, storage)",
    "confluence_profile_cloud": "the macro profile this deployment ships for Confluence Cloud",
    "confluence_profile_server": "the macro profile this deployment ships for Confluence Server/DC",
    "embedder_upstream": "the upstream server behind the cc-embedding alias",
    "graph_extraction_latency": "the measured time for one episode to become searchable facts",
    "graph_main_group_origin": "where the inherited main graph group came from",
    "jira_hierarchy_field": "how this Jira instance expresses issue hierarchy (parent versus epic link)",
    "jira_installed_apps": "which marketplace apps are installed on this Jira instance",
    "jira_issue_types_present": "which issue types this Jira instance has",
    "jira_readonly_field_names": "the names of this Jira instance's plugin-owned read-only fields",
    "model_server_concurrency": "the local model server's per-model concurrency limit as deployed",
    "model_server_layout": "the local model servers' hosts, ports, slots and which model each serves",
    "model_server_max_output_tokens": "the output-token caps declared for this deployment's models",
    "model_server_timeouts": "the request timeouts declared for this deployment's models",
    "retired_local_models": "which local models were retired from the fleet, and when",
    "served_context_example": "an example of a served context length smaller than a model card's",
}


def _require_well_formed(body: str, source: str, lineno: int) -> None:
    """Every opener on the line must be a well-formed marker."""
    for m in _OPENER.finditer(body):
        if not _MARKER.match(body, m.start()):
            raise ValueError(
                f"{source}:{lineno}: malformed overlay marker "
                f"{body[m.start(): m.start() + 60]!r}; expected "
                "'<!-- overlay: <field> -->' with field [a-z0-9_]+"
            )


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


def refuse_in(text: str, *, source: str, first_line: int = 1) -> None:
    """Raise `ValueError` naming `source:line` if `text` holds any marker.

    For frontmatter: its fields become the skill's id, title and summary, which
    the catalog and the cockpit show everywhere, so a deployment value must
    never be rendered into them — and a marker left in them would reach an
    agent unrendered. Loud beats either.
    """
    for lineno, line in enumerate(text.splitlines(), start=first_line):
        if _OPENER.search(line):
            raise ValueError(
                f"{source}:{lineno}: an overlay marker is not allowed in the "
                "frontmatter (it becomes the skill's id, title and summary); "
                "put it in the body"
            )


def render(
    text: str, overlay: dict[str, str], *, source: str, first_line: int = 1
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

    A malformed marker raises `ValueError` naming `source:line`; `first_line`
    is the file line `text` starts at, when it is a slice of the file.
    """
    resolved: set[str] = set()
    unresolved: set[str] = set()
    out_lines: list[str] = []

    for lineno, line in enumerate(text.splitlines(keepends=True), start=first_line):
        ending = line[len(line.rstrip("\r\n")):]
        body = line[: len(line) - len(ending)]

        _require_well_formed(body, source, lineno)

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



# --- check (read-only) --------------------------------------------------------


def markers_under(skills_dir: str | Path) -> dict[str, list[str]]:
    """Map each field named by a marker in `<skills_dir>/**/*.md` to the files
    marking it (paths shown from the directory's own name, as `skills/jira/...`,
    sorted). A malformed marker
    raises `ValueError` naming `file:line`."""
    root = Path(skills_dir)
    found: dict[str, set[str]] = {}
    for f in sorted(root.rglob("*.md")):
        if not f.is_file():
            continue
        shown = f"{root.resolve().name}/{f.relative_to(root).as_posix()}"
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), start=1):
            _require_well_formed(line, shown, lineno)
            for m in _MARKER.finditer(line):
                found.setdefault(m.group("field"), set()).add(shown)
    return {k: sorted(v) for k, v in sorted(found.items())}


def check(directory: str | Path, skills_dir: str | Path) -> tuple[list[str], bool]:
    """Report which of `FIELDS` the overlay at `directory` resolves.

    Returns `(lines, ok)`; `ok` is True only when every field has a non-empty
    `<field>.md` and no marker names a field outside `FIELDS`. Lines carry
    field and file NAMES only, never an overlay value.
    """
    overlay = load(directory)
    marked = markers_under(skills_dir)
    lines = [f"overlay dir: {directory}" + ("" if Path(directory).expanduser().is_dir() else " (absent)")]
    unresolved = 0
    for field in FIELDS:
        text = overlay.get(field, "")
        status = "RESOLVED" if text.strip() else "UNRESOLVED"
        unresolved += status == "UNRESOLVED"
        note = " (empty file)" if field in overlay and not text.strip() else ""
        where = ", ".join(marked.get(field, [])) or "no marker"
        lines.append(f"{status:<11} {field}{note}  [{where}]")
    for field in sorted(set(overlay) - set(FIELDS)):
        lines.append(f"UNUSED      {field}.md  [names no field in FIELDS]")
    unknown = [f for f in marked if f not in FIELDS]
    for field in unknown:
        lines.append(f"UNKNOWN     {field}  [marked in {', '.join(marked[field])}; not in FIELDS]")
    lines.append(
        f"{len(FIELDS) - unresolved} of {len(FIELDS)} fields resolved"
        + (f", {unresolved} unresolved" if unresolved else "")
    )
    return lines, unresolved == 0 and not unknown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m central_command.skills.overlay",
        description="Read-only checks of the skill instance overlay.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="say which bundled-skill fields an overlay resolves")
    c.add_argument("--dir", help="overlay directory (default: the resolved skill overlay dir)")
    c.add_argument(
        "--skills",
        default=str(Path(__file__).resolve().parents[2] / "skills"),
        help="the bundled skills directory to scan for markers (default: the checkout's skills/)",
    )
    args = parser.parse_args(argv)

    directory = args.dir
    if directory is None:
        from central_command.config import settings

        directory = settings.resolved_skill_overlay_dir()
    if not Path(args.skills).is_dir():
        print(f"error: skills directory {args.skills} does not exist", file=sys.stderr)
        return 1
    try:
        lines, ok = check(directory, args.skills)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
