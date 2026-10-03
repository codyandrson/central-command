"""A phase's FAIL and USERACTION lines name ITS ROWS (P5, the first laptop run).

`deploy/single/steps.tsv`'s rule is that a step's name IS the check-name the
phase prints, and since v2.56.0 the ledger records a row from what its OWN name
said this run: FAIL is `failed`, USERACTION is `gate`, and (since P5) the rows
after a stopped one are `pending` — the phase never reached them. A stop
printed under a name that is no row marks NO row. The first acceptance run on
the Windows laptop measured what that costs: the llm catalog pause said
`USERACTION llm-models`, so `llm/catalog-filled` was recorded `done` at the
very stop, the probe rows after it `done` though they never ran, and the status
`gate` never appeared at all — the plan then gave the wrong reason to resume.

So every `fail`, `useraction` and `step` (which prints FAIL on failure) in a
phase file whose check-name is a LITERAL must name a row of that phase, or be
on the short allowlist below with its reason. A name built from a variable
(`fail "$check" …`, `fail "$PROC_CHECK" …`) is the caller's business and is not
read here. PASS and WARN may use any name: they mark no stop.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.installer_source import SINGLE, phase_files

STEPS = SINGLE / "steps.tsv"

# (phase file, check-name) -> why it is not a row. "*" exempts the whole file.
# Keep it short: every entry is a stop the ledger cannot attribute to a row.
ALLOWLIST: dict[tuple[str, str], str] = {
    # The dry GATE. Its sections report under the table's own check-names
    # (`./setup.sh check --list`), and its verdict is check_gate's, not its
    # rows': a FAIL anywhere in it stops the run before any phase that changes
    # something. Its three rows (tree-pristine, ca-bundle, linger) print their
    # own lines.
    ("check.sh", "*"): "the dry gate's table of checks",
    # A missing images.txt stops `fetch` before its first row — release
    # content that failed to ship. phases/fetch.sh is outside the P5 change
    # that added this guard; naming it `resolve-images` is the obvious fix.
    ("fetch.sh", "images-txt"): "outside the P5 change (fetch.sh)",
    # The air-gapped half of app/install (`uv pip install -e . --no-deps`
    # after the lock). phases/app.sh is outside the P5 change that added this
    # guard; printing it under `install` is the obvious fix.
    ("app.sh", "install-editable"): "outside the P5 change (app.sh)",
    # The interactive wait timing out AFTER demo-approve's USERACTION: the row
    # is the gate it already said it was, and a FAIL under its name would turn
    # "waiting on the operator" into "broken".
    ("demo.sh", "demo"): "the wait after demo-approve's gate",
}

_CALL = re.compile(
    r"(?:^|[;&|({]|\bthen|\belse|\bdo|&&|\|\|)\s*(fail|useraction|step)\s+"
    r"(?:\"([^\"$`\\]+)\"|'([^'$]+)'|([A-Za-z0-9][A-Za-z0-9_.-]*))(?=\s)"
)


def _rows() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for line in STEPS.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("\t")
        out.setdefault(f[0], set()).add(f[1])
    return out


def unattributed(phase: str, file_name: str, text: str, rows: dict[str, set[str]]) -> list[str]:
    """`<file>:<line>: <reporter> "<name>"` for every literal check-name in a
    stopping reporter call that is neither a row of `phase` nor allowlisted."""
    if (file_name, "*") in ALLOWLIST:
        return []
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for m in _CALL.finditer(line):
            verb, name = m.group(1), m.group(2) or m.group(3) or m.group(4)
            if name in rows.get(phase, set()) or (file_name, name) in ALLOWLIST:
                continue
            found.append(f"{file_name}:{n}: {verb} \"{name}\"")
    return found


def test_every_stop_in_a_phase_file_names_one_of_its_rows():
    rows = _rows()
    bad = []
    for path in phase_files():
        bad += unattributed(path.stem, path.name, path.read_text(encoding="utf-8"), rows)
    assert not bad, (
        "a phase prints FAIL/USERACTION under a check-name that is no row of that phase, so the "
        "ledger marks no row for the stop (and a USERACTION never becomes `gate`). Print it under "
        "the row it stops — the step name IS the check-name (steps.tsv) — or, if it truly is no "
        "row's, allowlist it here with the reason:\n  " + "\n  ".join(bad)
    )


def test_the_allowlist_has_no_dead_entries():
    """An entry whose call is gone is a hole left open for the next one."""
    texts = {p.name: p.read_text(encoding="utf-8") for p in phase_files()}
    dead = []
    for (file_name, name) in ALLOWLIST:
        if name == "*":
            if file_name not in texts:
                dead.append(file_name)
            continue
        if not re.search(r"\b(?:fail|useraction|step)\s+[\"']?" + re.escape(name) + r"[\"']?\s",
                         texts.get(file_name, "")):
            dead.append(f"{file_name}: {name}")
    assert not dead, f"allowlisted, but no such call any more: {dead}"


def test_the_guard_catches_a_planted_stop_under_a_name_that_is_no_row():
    rows = _rows()
    llm = (SINGLE / "phases" / "llm.sh").read_text(encoding="utf-8")
    # The very line P5 found, planted back, plus the other two shapes.
    planted = llm + "\n".join([
        "",
        'x() {',
        '  useraction "llm-models" "the catalog needs you — re-run: ./setup.sh"',
        '  [[ -n "$y" ]] || fail not-a-row "unquoted"',
        '  step "also-not-a-row" "ran" true || return 1',
        '  fail "catalog-filled" "a row: fine"',
        '  fail "$dynamic" "a variable: the caller\'s business"',
        '}',
    ])
    found = unattributed("llm", "llm.sh", planted, rows)
    assert [f.split(": ", 1)[1] for f in found] == [
        'useraction "llm-models"', 'fail "not-a-row"', 'step "also-not-a-row"'], found
    # ...and the shipped file, alone, is clean: the pause is the row's now.
    assert unattributed("llm", "llm.sh", llm, rows) == []
    assert re.search(r'useraction "catalog-filled"', llm), "llm_gate no longer pauses under the row"
