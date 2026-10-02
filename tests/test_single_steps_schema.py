"""`deploy/single/steps.tsv` is THE process, and the schema is the one list.

The 2026-10-01 design record's D1. `questions.tsv` made a seam a ROW and two
commands read the same rows; `steps.tsv` does the same for a STEP, because the
investigation that day measured what happens when the process lives only in
`main()`'s loop: a phase is one linear bash function, a mid-function
`return 1` abandons every later step in it, and nothing writes that down.
`phase_app` has fourteen steps; its mint-key step returned on failure and
skipped nine `.env` writes and the cockpit build, and `./setup.sh boot` after
it was accepted.

Each rule below is one the record names, and each is a source walk — no
podman, no network, nothing started:

* **every `set_kv` / `set_kv_if_unset` target is in exactly one row's
  `writes`.** A key written outside the manifest is the v2.52.0 "eight blank
  Systems links nobody types" bug, and v2.48.0's missing `CC_LLM_BASE_URL`
  before it;
* **every `reads` and `writes` key has a line in `.env.example`** — the one
  answer file is also the whole map (`tests/test_single_airgap_seams.py` keeps
  the scripts to the same rule);
* **every `probe` names a function that exists and that mutates nothing.** The
  construct list is `tests/test_single_check_is_dry.py`'s, imported rather than
  copied; the functions are the whole installer's — setup.sh and the phase
  files it sources — through the one strict extractor in
  `tests/installer_source.py` (it understands a one-line `f() { ...; }`), so a
  probe is held to the same standard `check` is;
* **`requires` is acyclic and never points forward** — an earlier phase, or an
  earlier row of this one;
* **the phases ARE setup.sh's phases**, from the same reader
  `tests/test_setup_phase_docs.py` pins the prose copies with.

To see the writes rule fail: add a `set_kv_if_unset "$ENV_FILE" CC_SOMETHING`
to the app phase (deploy/single/phases/app.sh) and leave `steps.tsv` alone.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_setup_phase_docs import canonical_phases
from tests.installer_source import installer_functions, single_scripts
from tests.test_single_check_is_dry import FORBIDDEN, _code

ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT / "deploy" / "single"
SETUP = SINGLE / "setup.sh"
STEPS = SINGLE / "steps.tsv"
ENV_EXAMPLE = ROOT / ".env.example"

COLUMNS = ("phase", "step", "kind", "requires", "reads", "writes", "probe", "doc")

# The one key FAMILY the manifest names as a glob: resolve-images.sh writes one
# CC_IMG_<NAME> line per images.txt row, and .env.example documents the family
# in prose rather than one line per image (which is also how
# test_single_airgap_seams.py treats it).
IMG_FAMILY = "CC_IMG_*"


def _rows() -> list[dict[str, str]]:
    out = []
    for raw in STEPS.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\r")
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        assert len(fields) == len(COLUMNS), (
            f"steps.tsv: expected {len(COLUMNS)} tab-separated fields, got "
            f"{len(fields)}: {line!r}"
        )
        row = dict(zip(COLUMNS, fields))
        out.append(row)
    assert out, "steps.tsv declares no rows"
    return out


def _cells(row: dict[str, str], column: str) -> list[str]:
    value = row[column]
    if value == "-":
        return []
    return [c for c in value.split(",") if c]


def _qual(row: dict[str, str]) -> str:
    return f"{row['phase']}/{row['step']}"


# ── shape ───────────────────────────────────────────────────────────────────


def test_every_row_is_well_formed():
    seen: set[str] = set()
    for row in _rows():
        qual = _qual(row)
        assert qual not in seen, f"{qual} appears twice — the ledger keys on it"
        seen.add(qual)
        assert row["kind"] in ("run", "gate", "human"), f"{qual}: kind {row['kind']!r}"
        assert row["probe"] not in ("", "-"), (
            f"{qual}: a step whose effect cannot be read is a step nothing can resume"
        )
        assert row["doc"] not in ("", "-"), f"{qual}: no doc sentence (D8 renders it)"
        assert "*" not in row["reads"], (
            f"{qual}: `reads` is fingerprinted in order, so it may not carry a glob"
        )
        for key in _cells(row, "reads"):
            if key.startswith("@"):
                # A TREE input (v2.57.0): `@<dir>` is the content hash of a
                # repo-relative directory of the release — a PATH, which must
                # exist and stay inside the checkout, never an .env key.
                rel = key[1:]
                assert rel and not rel.startswith("/") and ".." not in rel, (
                    f"{qual}: tree input {key!r} must be @<repo-relative dir>"
                )
                assert (ROOT / rel).is_dir(), (
                    f"{qual}: reads {key!r}, but {rel}/ is not a directory of this release"
                )
                continue
            assert re.fullmatch(r"[A-Z][A-Z0-9_]*", key), f"{qual}: reads {key!r}"


def test_the_phases_are_setup_shs_phases():
    """One definition. `canonical_phases()` reads the driver itself, which is
    the same reader that pins the prose copies in test_setup_phase_docs.py."""
    declared = []
    for row in _rows():
        if row["phase"] not in declared:
            declared.append(row["phase"])
    assert declared == canonical_phases(), (
        f"steps.tsv declares {declared}, setup.sh runs {canonical_phases()}"
    )


def test_rows_are_grouped_by_phase_in_run_order():
    """The driver walks the rows in file order, so a phase's rows may not be
    interleaved with another's."""
    order = [row["phase"] for row in _rows()]
    first_seen: list[str] = []
    for phase in order:
        if phase not in first_seen:
            first_seen.append(phase)
    assert order == sorted(order, key=first_seen.index), (
        "steps.tsv rows must be contiguous per phase, in run order"
    )


def test_requires_is_backward_only_and_acyclic():
    rows = _rows()
    position = {_qual(row): i for i, row in enumerate(rows)}
    for i, row in enumerate(rows):
        for req in _cells(row, "requires"):
            assert req in position, (
                f"{_qual(row)} requires {req!r}, which is not a row in steps.tsv"
            )
            assert position[req] < i, (
                f"{_qual(row)} requires {req!r}, which comes LATER in the manifest — "
                "requires is backward-only, which is also what makes it acyclic"
            )


# ── the two .env rules ──────────────────────────────────────────────────────


def _declared_keys() -> set[str]:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", text, flags=re.M))


def test_every_reads_and_writes_key_is_declared_in_env_example():
    declared = _declared_keys()
    missing = []
    for row in _rows():
        for column in ("reads", "writes"):
            for cell in _cells(row, column):
                if cell == IMG_FAMILY or cell.startswith("CC_IMG_"):
                    continue
                # Lower-case entries are state-dir or build artifacts
                # (installed.manifest, .venv, uvicorn.pid, web/dist), not
                # answers — they have no .env line by definition.
                if not re.fullmatch(r"[A-Z][A-Z0-9_]*", cell):
                    continue
                if cell not in declared:
                    missing.append(f"{_qual(row)} {column}: {cell}")
    assert not missing, (
        "the one answer file is also the whole map — add a line (commented is "
        "fine) to .env.example:\n  " + "\n  ".join(missing)
    )


# set_kv / set_kv_if_unset with a LITERAL key. The two other shapes are
# deliberate and not the manifest's business: `cmd_configure` writes the keys
# questions.tsv asked for (a different schema, with its own guard test), and
# resolve-images.sh writes `"$var"` — the CC_IMG_* family, asserted separately
# below because a glob is what the manifest can honestly declare.
SET_KV = re.compile(
    r"""(?:^|[;&|(]|&&|\|\|)\s*(?:cc_)?set_kv(?:_if_unset)?\s+"[^"]+"\s+([A-Z][A-Z0-9_]*)""",
    re.M,
)


def _set_kv_targets() -> set[str]:
    out: set[str] = set()
    # deploy/single/*.sh AND deploy/single/phases/*.sh: the derived-key writes
    # (set_kv_if_unset in phase_app) moved into phases/app.sh in v2.58.0.
    for script in single_scripts():
        text = script.read_text(encoding="utf-8")
        # Comments mention these calls by name (and so does this profile's
        # prose); only code counts.
        body = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        out |= set(SET_KV.findall(body))
    return out


def test_every_env_key_the_profile_writes_is_in_exactly_one_rows_writes():
    written: dict[str, list[str]] = {}
    for row in _rows():
        for cell in _cells(row, "writes"):
            written.setdefault(cell, []).append(_qual(row))

    duplicated = {k: v for k, v in written.items() if len(v) > 1}
    assert not duplicated, (
        "a key with two writers is a key whose value nobody owns: "
        f"{duplicated}"
    )

    missing = sorted(k for k in _set_kv_targets() if k not in written)
    assert not missing, (
        "these keys are written by deploy/single/*.sh (or phases/*.sh) and declared by no row "
        "in steps.tsv — which is the v2.52.0 'eight blank Systems links nobody "
        f"types' shape of defect: {missing}"
    )

    assert IMG_FAMILY in written, (
        "resolve-images.sh writes CC_IMG_<NAME> per images.txt row; the manifest "
        "must declare the family on the fetch/resolve-images row"
    )


# ── the probes ──────────────────────────────────────────────────────────────
# The installer's functions, from tests/installer_source.py: setup.sh AND the
# phase files it sources (v2.58.0 — a phase's own probes live in
# deploy/single/phases/<phase>.sh). Its STRICT extractor was this file's: a
# one-line `f() { ...; }` closes on its own line, so the helpers the probes call
# (have_image, api_up, demo_decided — one-liners) stay in the map, and a
# probe's dryness covers what it calls.


def _reachable_from(funcs: dict[str, str], roots: list[str]) -> set[str]:
    seen: set[str] = set()
    queue = list(roots)
    while queue:
        fn = queue.pop()
        if fn in seen or fn not in funcs:
            continue
        seen.add(fn)
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", _code(funcs[fn])):
            if token in funcs and token not in seen:
                queue.append(token)
    return seen


def test_every_probe_exists_in_the_installer():
    funcs = installer_functions()
    missing = sorted({row["probe"] for row in _rows() if row["probe"] not in funcs})
    assert not missing, f"steps.tsv names probes the installer (setup.sh + phases/*.sh) does not define: {missing}"


def test_no_probe_can_mutate_anything():
    """A probe is the one thing the driver TRUSTS: it is run after a phase to
    decide whether the step's effect is there, and again on a later run to
    decide whether the phase can be skipped. A probe that pulled, built,
    started or installed something would make every run of `./setup.sh` a
    deployment."""
    funcs = installer_functions()
    probes = sorted({row["probe"] for row in _rows()})
    offenders = []
    for probe in probes:
        for fn in sorted(_reachable_from(funcs, [probe])):
            code = _code(funcs[fn])
            for pattern, why in FORBIDDEN.items():
                for m in re.finditer(pattern, code):
                    offenders.append(f"{probe} -> {fn}(): {m.group(0)!r} — {why}")
    assert not offenders, (
        "a probe (or something it calls) mutates state:\n  " + "\n  ".join(offenders)
    )


def test_a_flag_gated_step_probes_zero_when_the_flag_is_off():
    """"Not applicable is DONE" (D1), held to the row's own words.

    A row whose `doc` says "when CC_ENABLE_X=1" is declaring itself
    CONDITIONAL, and its probe must consult that flag — otherwise a deployment
    without speech, n8n, the crawler or the sandbox blocks forever on rows it
    will never perform. (A CC_ENABLE_* merely in `reads` is not the same
    claim: `stack/up-stack` reads three of them to pick compose PROFILES and
    comes up either way.)
    """
    funcs = installer_functions()
    bad = []
    for row in _rows():
        flags = set(re.findall(r"when (CC_ENABLE_[A-Z0-9_]+)=", row["doc"]))
        if not flags:
            continue
        reach = _reachable_from(funcs, [row["probe"]])
        body = "\n".join(_code(funcs[fn]) for fn in reach if fn in funcs)
        for flag in sorted(flags):
            assert flag in _cells(row, "reads"), (
                f"{_qual(row)}: its doc is conditional on {flag}, so the flag is "
                "an input and belongs in `reads` (it is fingerprinted)"
            )
            if flag not in body:
                bad.append(f"{_qual(row)}: probe {row['probe']} never consults {flag}")
    assert not bad, (
        "a step a flag turns off must probe 0 — a component this install does "
        "not have is not an unfinished step:\n  " + "\n  ".join(bad)
    )
