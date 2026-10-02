"""`deploy/single/CHECKLIST.md` is GENERATED from the manifest, and stays so.

Design record `docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`,
D8. The 2026-10-01 investigation found the install defined in code once and
described in prose six times, one copy wrong, and no operator-facing document
that was a numbered checklist at all. So the checklist is rendered from
`deploy/single/steps.tsv` by `scripts/render_checklist.py`, committed, and held
to the manifest here:

* the committed file is byte-for-byte a fresh render (run the script to fix);
* every manifest row appears, by `<phase>/<step>`, in manifest order;
* the phase headings are the manifest's phases, in order;
* every `human` / `gate` row is set in bold and says WHERE the operator acts —
  an operator row with no derivable place and no entry in
  `WHERE_BY_STEP` fails here, so a new human row cannot ship without one;
* the file says it is generated.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = ROOT / "deploy" / "single" / "CHECKLIST.md"
SCRIPT = ROOT / "scripts" / "render_checklist.py"


def _renderer():
    spec = importlib.util.spec_from_file_location("render_checklist", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R = _renderer()
ROWS = R.load_rows()
TEXT = CHECKLIST.read_bytes().decode("utf-8")


def _steps_section() -> str:
    m = re.search(r"^## The steps\n(.*?)^## ", TEXT, flags=re.S | re.M)
    assert m, "CHECKLIST.md has no '## The steps' section followed by another section"
    return m.group(1)


def test_the_committed_checklist_is_a_fresh_render():
    assert "\r" not in TEXT, "CHECKLIST.md must have LF line endings"
    assert TEXT == R.render(), (
        "deploy/single/CHECKLIST.md differs from a fresh render of "
        "deploy/single/steps.tsv — run: python scripts/render_checklist.py "
        "(and never edit the file by hand)"
    )


def test_the_check_flag_agrees():
    assert R.main(["--check"]) == 0


def test_every_row_appears_in_manifest_order():
    section = _steps_section()
    pos = -1
    for r in ROWS:
        name = f"`{r['phase']}/{r['step']}`"
        at = section.find(name, pos + 1)
        assert at > pos, f"{name} is missing from CHECKLIST.md, or out of manifest order"
        pos = at


def test_phase_headings_are_the_manifest_phases_in_order():
    headings = re.findall(r"^### `(\w+)`$", _steps_section(), flags=re.M)
    assert headings == R.phases_in_order(ROWS)


def test_operator_rows_are_bold_and_say_where():
    section = _steps_section().splitlines()
    defaults = R.question_defaults()
    operator_rows = [r for r in ROWS if r["kind"] in R.OPERATOR_KINDS]
    assert operator_rows, "the manifest declares no human/gate rows — D1 says it must"
    missing = [f"{r['phase']}/{r['step']}" for r in operator_rows
               if not R.where_for(r, defaults)]
    assert not missing, (
        f"human/gate row(s) with no WHERE: {missing}. Name the place in the "
        "row's reads/doc, or add it to WHERE_BY_STEP in scripts/render_checklist.py"
    )
    for r in operator_rows:
        name = f"`{r['phase']}/{r['step']}`"
        i = next(i for i, line in enumerate(section) if name in line)
        assert re.match(r"^\d+\. \*\*" + re.escape(name), section[i]), (
            f"{name} is the operator's move and must be set in bold: {section[i]!r}"
        )
        assert section[i].rstrip().endswith("**"), section[i]
        assert section[i + 1].lstrip().startswith("Where: "), (
            f"{name} must be followed by its Where: line, got {section[i + 1]!r}"
        )
    for r in ROWS:
        if r["kind"] not in R.OPERATOR_KINDS:
            name = f"`{r['phase']}/{r['step']}`"
            line = next(line for line in section if name in line)
            assert "**" not in line.split(":", 1)[0], f"{name} is not the operator's: {line!r}"


def test_the_checklist_says_it_is_generated():
    head = TEXT[:1500]
    assert "GENERATED" in head and "deploy/single/steps.tsv" in head
    assert "scripts/render_checklist.py" in head


def test_an_unmapped_operator_row_still_renders_without_a_where():
    """A new human row the renderer cannot place renders with its sentence (the
    test above is what fails), rather than crashing the render."""
    row = dict(phase="boot", step="zz-new", kind="human", requires="", reads="",
               writes="", probe="p_always", doc="Do a thing nobody located.")
    out = R.render_steps([row], R.question_defaults())
    assert "**`boot/zz-new` (yours): Do a thing nobody located.**" in out
    assert "Where:" not in out
    assert R.where_for(row, R.question_defaults()) is None


def test_where_is_derived_for_the_rows_the_record_names():
    """D1's operator rows that P4 adds (the CA on disk, the n8n credential)
    derive their place from what they read, so their names need no mapping."""
    d = R.question_defaults()
    ca = dict(phase="machine", step="ca-on-disk", kind="human", requires="",
              reads="CC_CA_BUNDLE", writes="", probe="p_always", doc="Place the CA.")
    n8n = dict(phase="boot", step="n8n-credential", kind="human", requires="",
               reads="CC_ENABLE_N8N", writes="", probe="p_always",
               doc="Create the Gmail account credential in n8n, when CC_ENABLE_N8N=1.")
    assert "CC_CA_BUNDLE" in R.where_for(ca, d)
    assert "n8n UI" in R.where_for(n8n, d)
