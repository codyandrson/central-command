"""The single-node phase list is defined ONCE; documents follow it, never copy it.

History: the 2026-08-28 rework added test/boot/demo and every prose copy of the
phase list drifted (six-, seven- and nine-phase variants shipped for three
releases), so this file used to require four documents to CARRY the full list
and pinned each copy to setup.sh. The 2026-10-01 record (D8) took the other
way out: the install is declared once, in `deploy/single/steps.tsv`; the
operator's procedure is `deploy/single/CHECKLIST.md`, GENERATED from it
(`scripts/render_checklist.py`, guarded by `tests/test_single_checklist.py`);
and the documents LINK to the checklist instead of restating it. So, now:

* the READMEs and the /setup skill carry no phase list of their own, and link
  to the checklist;
* the checklist's phase order is the manifest's, and the manifest's is
  setup.sh's;
* wherever a document still names a run of phases, the run is in manifest
  order — a partial sequence (an update's post-merge order) is fine, a wrong
  order is not;
* the skill carries D10.4's three-verb paragraph VERBATIM and never names a
  phase as something to run.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STEPS = ROOT / "deploy" / "single" / "steps.tsv"
CHECKLIST = "deploy/single/CHECKLIST.md"
SKILL = ".claude/skills/setup/SKILL.md"

# The documents that LINK to the checklist and must not restate the phases.
NO_PHASE_LIST = {
    "README.md": "deploy/single/CHECKLIST.md",
    "deploy/single/README.md": "CHECKLIST.md",
    SKILL: "deploy/single/CHECKLIST.md",
}

# Every document that may still NAME phases in sequence: each such run must be
# in manifest order.
SEQUENCE_DOCS = [
    "README.md",
    "deploy/single/README.md",
    "deploy/AIRGAP.md",
    "docs/ARCHITECTURE.md",
    ".claude/rules/deploy-single.md",
    SKILL,
    CHECKLIST,
]

# D10.4, verbatim — "the skill says it in the same words".
THREE_VERBS = (
    "You may change `.env`. You may run `./setup.sh`, `./setup.sh status` and "
    "`./setup.sh report`. Everything else is a finding."
)


def canonical_phases() -> list[str]:
    """The full run, as setup.sh actually performs it.

    Since v2.44.0 that is `check` — the dry GATE, which composes validate and
    preflight — and then the loop over the phases that change something. The
    gate is outside the loop on purpose (it decides whether the loop runs at
    all), so the canonical list is the two halves joined.
    """
    src = (ROOT / "deploy/single/setup.sh").read_text()
    assert re.search(r"^\s*run_phase check; local crc=", src, flags=re.M), (
        "the full run must start with the check gate"
    )
    for m in re.finditer(r"for p in ((?:\w+\s+)+\w+);", src):
        phases = m.group(1).split()
        if phases[0] == "machine":
            return ["check", *phases]
    raise AssertionError("the mutating-phase loop was not found in deploy/single/setup.sh")


def manifest_phases() -> list[str]:
    seen: list[str] = []
    for raw in STEPS.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        p = raw.split("\t", 1)[0]
        if p not in seen:
            seen.append(p)
    return seen


def _read(doc: str) -> str:
    return (ROOT / doc).read_text(encoding="utf-8")


def _full_list(phases: list[str]) -> re.Pattern:
    # any non-letter run may separate phases: "->", "→", "/", " / ", newlines
    return re.compile(r"[^a-z]+".join(phases))


# A separator between two phase names in a SEQUENCE: an arrow, a slash, a dot,
# a comma or a pipe, with optional space and Markdown code/emphasis marks.
_SEP = re.compile(r"^[\s`*]*(?:->|→|/|·|,|\|)[\s`*]*$")


def phase_runs(text: str, phases: list[str]) -> list[list[str]]:
    """Every run of 3+ phase names joined only by sequence separators."""
    hits = [(m.start(), m.end(), m.group(1)) for m in
            re.finditer(r"\b(" + "|".join(phases) + r")\b", text.lower())]
    runs, cur, last_end = [], [], 0
    for start, end, name in hits:
        if cur and _SEP.match(text[last_end:start]):
            cur.append(name)
        else:
            if len(cur) >= 3:
                runs.append(cur)
            cur = [name]
        last_end = end
    if len(cur) >= 3:
        runs.append(cur)
    return runs


def test_the_manifest_is_the_driver():
    assert manifest_phases() == canonical_phases()


def test_the_readmes_and_the_skill_link_to_the_checklist_instead_of_listing():
    full = _full_list(manifest_phases())
    for doc, link in NO_PHASE_LIST.items():
        text = _read(doc)
        assert link in text, f"{doc}: must link to the checklist ({link})"
        assert not full.search(text.lower()), (
            f"{doc}: restates the full phase list — link to {CHECKLIST} instead "
            "(it is generated from deploy/single/steps.tsv)"
        )


def test_the_checklist_phase_order_is_the_manifest():
    headings = re.findall(r"^### `(\w+)`$", _read(CHECKLIST), flags=re.M)
    assert headings == manifest_phases()


def test_every_named_phase_sequence_is_in_manifest_order():
    phases = manifest_phases()
    rank = {p: i for i, p in enumerate(phases)}
    for doc in SEQUENCE_DOCS:
        for run in phase_runs(_read(doc), phases):
            ranks = [rank[p] for p in run]
            assert ranks == sorted(set(ranks)), (
                f"{doc}: names the phases {run} in an order the manifest does not "
                f"run them ({phases})"
            )


def test_phase_runs_detects_a_wrong_order():
    phases = manifest_phases()
    assert phase_runs("`fetch` -> `llm` -> `stack`", phases) == [["fetch", "llm", "stack"]]
    assert phase_runs("check → triage → check → app", phases) == []
    assert phase_runs("app / stack / llm", phases) == [["app", "stack", "llm"]]


def test_the_skill_carries_the_three_verbs_verbatim():
    text = re.sub(r"\s+", " ", _read(SKILL))
    assert THREE_VERBS in text, (
        f"{SKILL}: the D10.4 paragraph must appear verbatim: {THREE_VERBS!r}"
    )


def test_the_skill_never_names_a_phase_to_run():
    text = _read(SKILL)
    named = re.findall(r"(?<![\w/.])\./setup\.sh\s+(" + "|".join(manifest_phases()) + r")\b",
                       text)
    assert not named, (
        f"{SKILL}: names phase(s) {sorted(set(named))} as a command — the "
        "operator-side contract is ./setup.sh, ./setup.sh status, ./setup.sh report"
    )


def test_the_operator_documents_mention_the_exit3_gate():
    for doc in ("deploy/single/README.md", SKILL, CHECKLIST):
        text = _read(doc)
        assert re.search(r"0/1/2/3|\*\*3\*\*|\| 3 \|", text), f"{doc}: no exit-3 mention"
