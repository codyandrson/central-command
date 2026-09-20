"""Guards `docs/decisions/` — the log that explains WHY a rule exists, not
what it says.

The log's whole value is that its citations are real: a `Rule:` link that
points at a bullet the target file no longer has, or an `Enforced: test:`
that names a function nobody wrote, is worse than no citation at all — it
tells a reader "checked" about something nobody checked. This walks every
entry in every `docs/decisions/*.md` area file and verifies, by reading the
actual files on disk, that:

- entry ids are the six required bullets, a known `Status:`, a parseable
  `Date:`, and are numbered DL-001, DL-002, ... with no gaps or repeats
  (in the order the README's index lists them);
- every `Rule:` link names a file that exists, and the quoted lead phrase is
  a verbatim (whitespace-normalised) substring of that file — so a phrase
  that gets edited or removed from AGENTS.md/.claude/rules/*.md fails this
  test instead of silently going stale;
- every `Enforced: test:` path exists and actually defines the named test
  function (a source-level check, not a pytest collection run — this suite
  must not need a database);
- every `Enforced: script:`/`code structure only` backticked path exists;
- the README's index table lists exactly the ids and titles the area files
  define, in the same order.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DECISIONS_DIR = ROOT / "docs" / "decisions"
README = DECISIONS_DIR / "README.md"

AREA_FILES = [
    "trust-gate.md",
    "runtime.md",
    "events-data.md",
    "ingest-integrations.md",
    "graph.md",
    "models.md",
    "deploy.md",
    "cockpit.md",
    "process.md",
]

REQUIRED_BULLETS = ["Status", "Date", "Rule", "Why", "Enforced", "Source"]
VALID_STATUSES = {"active", "superseded", "retired"}

ENTRY_HEADING_RE = re.compile(r"^### (DL-\d+) — (.+)$", re.MULTILINE)
BULLET_RE = re.compile(r"^- \*\*([^:*]+):\*\*\s?(.*)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalize(text: str) -> str:
    """Collapse whitespace/newlines so a wrapped bullet still substring-matches
    a one-line (or differently wrapped) source sentence."""
    return re.sub(r"\s+", " ", text).strip()


def _split_entries(md_text: str) -> list[tuple[str, str, str]]:
    """Return (id, title, body) for every '### DL-NNN — title' block."""
    matches = list(ENTRY_HEADING_RE.finditer(md_text))
    entries = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        entries.append((m.group(1), m.group(2).strip(), md_text[start:end]))
    return entries


def _parse_bullets(body: str) -> dict[str, str]:
    """Parse the '- **Label:** text' bullets of one entry, folding indented
    continuation lines into the preceding bullet's text."""
    bullets: dict[str, str] = {}
    order: list[str] = []
    current = None
    for line in body.splitlines():
        m = BULLET_RE.match(line)
        if m:
            label, rest = m.group(1).strip(), m.group(2)
            current = label
            bullets[current] = rest
            order.append(current)
        elif current is not None and line.startswith("  ") and line.strip():
            bullets[current] += " " + line.strip()
        elif line.strip() == "":
            current = None
    return bullets


def _all_entries() -> list[tuple[str, str, str, dict[str, str], str]]:
    """(id, title, area_file, bullets, raw_body) for every entry, across all
    area files, in AREA_FILES order (which is also the required id order)."""
    out = []
    for area in AREA_FILES:
        path = DECISIONS_DIR / area
        assert path.exists(), f"docs/decisions/{area} is listed as an area but does not exist"
        text = path.read_text(encoding="utf-8")
        for entry_id, title, body in _split_entries(text):
            out.append((entry_id, title, area, _parse_bullets(body), body))
    return out


ENTRIES = _all_entries()


def test_entry_ids_are_contiguous_from_dl_001_with_no_repeats():
    ids = [int(e[0].split("-")[1]) for e in ENTRIES]
    assert len(ids) == len(set(ids)), (
        f"duplicate DL- ids found: {sorted({i for i in ids if ids.count(i) > 1})}"
    )
    expected = list(range(1, len(ids) + 1))
    assert ids == expected, (
        "DL- ids must run DL-001, DL-002, ... with no gaps, in area-file order "
        f"(trust-gate, runtime, events-data, ingest-integrations, graph, models, "
        f"deploy, cockpit, process); got {ids[:10]}... expected {expected[:10]}..."
    )


@pytest.mark.parametrize("entry_id,title,area,bullets,body", ENTRIES, ids=[e[0] for e in ENTRIES])
def test_entry_has_all_required_bullets(entry_id, title, area, bullets, body):
    missing = [b for b in REQUIRED_BULLETS if b not in bullets]
    assert not missing, (
        f"{entry_id} ({area}) is missing required bullet(s) {missing} — "
        f"every entry needs Status, Date, Rule, Why, Enforced, Source"
    )


@pytest.mark.parametrize("entry_id,title,area,bullets,body", ENTRIES, ids=[e[0] for e in ENTRIES])
def test_entry_status_is_in_vocabulary(entry_id, title, area, bullets, body):
    if "Status" not in bullets:
        pytest.skip("missing-bullet case covered by test_entry_has_all_required_bullets")
    status = bullets["Status"].strip()
    assert status in VALID_STATUSES, (
        f"{entry_id} ({area}) has Status {status!r}, must be one of {sorted(VALID_STATUSES)}"
    )


@pytest.mark.parametrize("entry_id,title,area,bullets,body", ENTRIES, ids=[e[0] for e in ENTRIES])
def test_entry_date_is_iso_or_undated(entry_id, title, area, bullets, body):
    if "Date" not in bullets:
        pytest.skip("missing-bullet case covered by test_entry_has_all_required_bullets")
    date = bullets["Date"].strip()
    assert date == "undated" or DATE_RE.match(date), (
        f"{entry_id} ({area}) has Date {date!r}, must be 'undated' or YYYY-MM-DD"
    )


def _rule_targets(rule_text: str) -> list[tuple[Path, str]]:
    """Extract (target_file, quoted_phrase) pairs from a Rule: bullet. A
    '(recorded here)' rule cites no external file and returns []."""
    if rule_text.strip().startswith("(recorded here)"):
        return []
    targets = []
    # [label](relative/link) — "quoted phrase"
    for link_match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", rule_text):
        rel = link_match.group(1)
        # links are written relative to docs/decisions/
        target = (DECISIONS_DIR / rel).resolve()
        remainder = rule_text[link_match.end():]
        # The quote may itself contain nested straight quotes (e.g. a rule
        # that quotes a phrase like "the operator"), so take everything
        # between the FIRST and LAST '"' rather than stopping at the first
        # closing quote.
        first = remainder.find('"')
        last = remainder.rfind('"')
        if first != -1 and last > first:
            targets.append((target, remainder[first + 1:last]))
    return targets


@pytest.mark.parametrize("entry_id,title,area,bullets,body", ENTRIES, ids=[e[0] for e in ENTRIES])
def test_rule_link_target_exists_and_quote_is_verbatim(entry_id, title, area, bullets, body):
    if "Rule" not in bullets:
        pytest.skip("missing-bullet case covered by test_entry_has_all_required_bullets")
    rule_text = bullets["Rule"]
    for target, quote in _rule_targets(rule_text):
        assert target.exists(), (
            f"{entry_id} ({area}): Rule: link target {target} does not exist"
        )
        content = _normalize(target.read_text(encoding="utf-8"))
        needle = _normalize(quote)
        assert needle in content, (
            f"{entry_id} ({area}): Rule: quoted phrase {quote!r} is not a verbatim "
            f"substring of {target.relative_to(ROOT)} (whitespace-normalised)"
        )


def _backticked_paths(text: str) -> list[str]:
    return re.findall(r"`([^`]+)`", text)


def _test_targets(enforced_text: str) -> list[tuple[str, str]]:
    """Extract (relative_path, function_name) for every 'test: `path::func`'
    reference in an Enforced: bullet. A path with no '::func' (e.g. a whole
    test file, not one function) is skipped by design of the entry writer —
    but if one is given, it is checked."""
    out = []
    for m in re.finditer(r"test:\s*`([^`]+)`", enforced_text):
        ref = m.group(1)
        if "::" in ref:
            path, func = ref.split("::", 1)
            out.append((path, func))
    return out


@pytest.mark.parametrize("entry_id,title,area,bullets,body", ENTRIES, ids=[e[0] for e in ENTRIES])
def test_enforced_test_paths_and_functions_exist(entry_id, title, area, bullets, body):
    if "Enforced" not in bullets:
        pytest.skip("missing-bullet case covered by test_entry_has_all_required_bullets")
    enforced_text = bullets["Enforced"]
    for rel_path, func_name in _test_targets(enforced_text):
        target = (ROOT / rel_path).resolve()
        assert target.exists(), (
            f"{entry_id} ({area}): Enforced test path {rel_path!r} does not exist"
        )
        source = target.read_text(encoding="utf-8")
        pattern = re.compile(
            rf"^\s*(?:async\s+)?def {re.escape(func_name)}\s*\(", re.MULTILINE
        )
        assert pattern.search(source), (
            f"{entry_id} ({area}): Enforced test names `{func_name}` but "
            f"{rel_path} defines no such function"
        )


@pytest.mark.parametrize("entry_id,title,area,bullets,body", ENTRIES, ids=[e[0] for e in ENTRIES])
def test_enforced_script_and_structure_paths_exist(entry_id, title, area, bullets, body):
    if "Enforced" not in bullets:
        pytest.skip("missing-bullet case covered by test_entry_has_all_required_bullets")
    enforced_text = bullets["Enforced"]
    kind = enforced_text.strip().split(" ", 1)[0].rstrip(":").lower()
    if kind not in ("script", "code"):
        return
    # Only a backticked token that plainly names a real repo FILE is checked:
    # it must end in a recognised extension (with an optional ":line" or
    # ":line,line,..." suffix). This deliberately excludes function/attribute
    # references (`gateway.executor.execute()`, `runtime/tools._validate_proposal`),
    # shell fragments (`grep -qx`), and IP:port strings
    # (`127.0.0.1:5442/4000/8000`) — none of those are filesystem paths, even
    # though some contain a "/".
    path_re = re.compile(
        r"^([\w./-]+\.(?:py|sh|yaml|yml|md|sql|ts|tsx|js|json))(?::[\d,]+)?$"
    )
    for backticked in _backticked_paths(enforced_text):
        m = path_re.match(backticked)
        if not m:
            continue
        candidate = (ROOT / m.group(1)).resolve()
        assert candidate.exists(), (
            f"{entry_id} ({area}): Enforced {kind} path `{backticked}` does not exist"
        )


def test_readme_index_matches_area_file_entries():
    readme_text = README.read_text(encoding="utf-8")
    table_rows = re.findall(
        r"^\|\s*(DL-\d+)\s*\|\s*(.+?)\s*\|\s*\w+\s*\|", readme_text, re.MULTILINE
    )
    assert table_rows, "README.md has no parseable index table rows"

    readme_ids = [r[0] for r in table_rows]
    readme_titles = {r[0]: r[1] for r in table_rows}

    entry_ids = [e[0] for e in ENTRIES]
    entry_titles = {e[0]: e[1] for e in ENTRIES}

    assert readme_ids == entry_ids, (
        "README.md's index table order/ids do not match the area files' entries.\n"
        f"README:      {readme_ids}\n"
        f"Area files:  {entry_ids}"
    )
    mismatched = [
        eid for eid in entry_ids if _normalize(readme_titles[eid]) != _normalize(entry_titles[eid])
    ]
    assert not mismatched, (
        f"README.md's titles differ from the area files' titles for: {mismatched}"
    )
