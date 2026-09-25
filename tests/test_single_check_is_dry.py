"""`./setup.sh check` executes nothing, and its section table is the docs'.

The 2026-09-23 design record's D5: the operator's loop is *edit `.env` → check →
triage → check → … → `all`*, and it only works if `check` is trustworthy in one
specific way — **it changes nothing**. A check that pulled an image, built one,
started a container, installed a package or generated a secret would make
"nothing changed but `.env`" false, and the operator would stop running it
before the install.

So this is a source walk with two jobs:

* **dryness.** Start at `phase_check`, walk every function it calls (and every
  function THOSE call) within `setup.sh`, and fail on a mutating construct:
  `podman pull` / `build` / `run`, `compose … up`, `npm ci|install`,
  `pip install`, `uv pip install` without `--dry-run`, or a `make-secrets` call.
  The walk is transitive on purpose: the point of composing `validate`,
  `preflight` and `machine --dry-run` is that check runs the SAME code, so the
  guard has to follow it there.
* **the table.** `check --list` is what an operator (or the /setup skill) reads
  to know what a section covers; `deploy/single/README.md` carries the same
  table. Two copies of a list is how a list rots, so the test pins them to each
  other — the same shape `tests/test_setup_phase_docs.py` uses for the phases.

The complement is `tests/test_single_no_tree_writes.py` (nothing is WRITTEN
inside the checkout); together they are D5's and D7's acceptance criteria.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# The bash that can run this repo's shell scripts. On Windows a bare "bash" is
# System32's WSL launcher (its error reads "The RPC call contains a handle
# that differs from the declared handle type") — 26 tests failed that way on
# the 2026-09-25 testbed run, all of them in files that shelled out with the
# bare name. `update._bash()` resolves Git Bash from git's own install.
from central_command.api.update import _bash as _resolve_bash  # noqa: E402
BASH = _resolve_bash() or "bash"


ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT / "deploy" / "single"
SETUP = SINGLE / "setup.sh"
README = SINGLE / "README.md"

# Construct -> why it may never appear anywhere check can reach.
FORBIDDEN = {
    r"\bpodman\s+pull\b": "a pull is an acquisition — that is the fetch phase",
    r"\bpodman\s+build\b": "a build is the fetch phase's, and check cannot prove one anyway",
    r"\bpodman\s+run\b": "starting a container is a side effect",
    r"\bcompose\b[^\n]*\bup\b": "`compose up` deploys — check only renders (`config`)",
    r"\bnpm\s+(?:ci|install)\b": "the npm tree is the fetch phase's",
    r"(?<!uv )\bpip install\b": "check resolves with --dry-run, never installs",
    r"\buv pip install\b(?![^\n]*--dry-run)": "a resolve must carry --dry-run",
    # The INVOCATION, not the name: check's own messages name make-secrets.sh as
    # the command that fills a blank credential, which is the whole point of the
    # WARN it prints instead of generating one.
    r"(?:\$HERE/|\./|bash\s+)make-secrets\.sh": "check never generates a credential — the llm phase does",
}


def _functions() -> dict[str, str]:
    """name -> body, for every function defined in setup.sh."""
    text = SETUP.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    name = None
    body: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", line)
        if m and name is None:
            name = m.group(1)
            body = []
            continue
        if name is not None:
            if line == "}":
                out[name] = "\n".join(body)
                name = None
                continue
            body.append(line)
    return out


def _code(body: str) -> str:
    """Comments and single-quoted literals dropped — the same pragmatism as
    tests/test_single_no_tree_writes.py: a message that MENTIONS `podman pull`
    does not run one."""
    lines = []
    for line in body.splitlines():
        if line.lstrip().startswith("#"):
            continue
        line = re.sub(r"'[^'\n]*'", "''", line)
        line = re.sub(r"(?<![$\w])#.*$", "", line)
        lines.append(line)
    return "\n".join(lines)


def _reachable() -> set[str]:
    funcs = _functions()
    assert "phase_check" in funcs, "setup.sh has no phase_check"
    seen: set[str] = set()
    queue = ["phase_check"]
    while queue:
        fn = queue.pop()
        if fn in seen:
            continue
        seen.add(fn)
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", _code(funcs[fn])):
            if token in funcs and token not in seen:
                queue.append(token)
    return seen


def test_check_reaches_the_phases_it_composes():
    """If this shrinks, check stopped composing and started copying."""
    reach = _reachable()
    for fn in ("validate_answers", "validate_compose_config", "preflight_host",
               "machine_report", "phase_machine", "resolve_python_deps",
               "check_images", "check_indexes", "check_llm", "check_models"):
        assert fn in reach, f"phase_check no longer reaches {fn}"


def test_nothing_check_can_reach_mutates_anything():
    funcs = _functions()
    offenders = []
    for fn in sorted(_reachable()):
        code = _code(funcs[fn])
        for pattern, why in FORBIDDEN.items():
            for m in re.finditer(pattern, code):
                offenders.append(f"{fn}(): {m.group(0)!r} — {why}")
    assert not offenders, (
        "`./setup.sh check` must execute nothing (design record 2026-09-23, D5):\n  "
        + "\n  ".join(offenders)
    )


def _list_rows() -> list[tuple[str, str]]:
    # cwd= + basename, not the full path: on Windows the first `bash` on PATH
    # may be WSL's launcher, which cannot open a Windows path.
    r = subprocess.run([BASH, "setup.sh", "check", "--list"],
                       cwd=SINGLE, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    rows = []
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        section, _, desc = line.partition(" ")
        rows.append((section.strip(), desc.strip()))
    return rows


def test_check_list_needs_no_answer_file():
    """It is documentation: a machine with no `.env` must still be able to
    print it (and printing it must not create a state directory)."""
    rows = _list_rows()
    assert [s for s, _ in rows] == ["answers", "host", "machine", "images",
                                    "indexes", "llm", "compose", "models"], rows


def test_check_list_matches_the_table_in_the_readme():
    readme = README.read_text(encoding="utf-8")
    for section, desc in _list_rows():
        row = f"| `{section}` | {desc} |"
        assert row in readme, (
            f"deploy/single/README.md is missing the `check` section row for "
            f"{section!r}. Expected the line:\n  {row}"
        )
