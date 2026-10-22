"""docs/ARCHITECTURE.md is tied to the tree: a claim the tree contradicts fails here.

History: the map was stamped "verified at v2.38.2" and its own rule ("when you
move or rename a seam, fix the row in the same change") was kept by nobody — 29
releases later its capability count, event count, module lists, route table and
credential claims had all drifted, and a fresh reader could not tell which rows
to trust (2026-10 documentation review, claims-architecture AR-001/005). A rule
that nothing enforces is a hope, so this file enforces the part of it that is
cheap to recompute from the tree:

* every package and top-level module under `central_command/` is named, and so
  is every module file inside a package, in a table row of its own package (or
  qualified as `pkg/mod.py`);
* every path the document names — backticked or linked — exists, and every
  `file.py::name` it cites resolves to a real def or class;
* the counts that are cheap to recompute match: capabilities in the registry,
  heartbeat action kinds, distinct event types (within a tolerance), the cockpit
  `cc-*` routes, the web feature directories, the k3s numbered manifests;
* the state sets in section 3 equal the ones the schema and the enums declare.

What it deliberately does NOT check: prose (what a module is FOR), the
credential table's wording, the decision-id index. Those are reviewed, not
recomputed. Rows that describe something deleted say so in the line
("deleted in vX.Y.Z") and are exempt from the existence check — history is a
record, not a claim about the tree.
"""

from __future__ import annotations

import ast
import fnmatch
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "ARCHITECTURE.md"
PKG = ROOT / "central_command"

# Directories never walked: huge, generated, or not ours.
_PRUNE = {".git", "node_modules", ".venv", "__pycache__", "server-dist", "dist",
          ".pytest_cache", "_bmad-output", "worktrees", "vendor-unused"}

# Event types are counted from `emit("kind", ...)` call sites by AST. A new
# event type should not fail every other session's suite, so the documented
# figure may trail the tree by this fraction; a rename-sized drift still fails.
EVENT_TOLERANCE = 0.05

_EXT = (".py", ".ts", ".tsx", ".sh", ".sql", ".tsv", ".yaml", ".yml", ".md", ".toml",
        ".service", ".path", ".timer", ".txt", ".json")


def doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


# --- the tree, walked once ----------------------------------------------------

_TREE_CACHE: dict[Path, list[str]] = {}


def tree_paths(root: Path = ROOT) -> list[str]:
    """Every file and directory path under root (posix, root-relative)."""
    if root in _TREE_CACHE:
        return _TREE_CACHE[root]
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root).as_posix()
        # docs/vendor holds ~47k files and is never named by file here.
        pruned = [d for d in dirnames if d in _PRUNE or (rel == "docs" and d == "vendor")]
        dirnames[:] = [d for d in dirnames if d not in pruned]
        # A pruned directory still exists; only its contents are not walked.
        out.extend(f"{rel}/{d}/" if rel != "." else f"{d}/" for d in pruned)
        if rel != ".":
            out.append(rel + "/")
        out.extend(f"{rel}/{f}" if rel != "." else f for f in filenames)
    _TREE_CACHE[root] = out
    return out


# --- what the document names --------------------------------------------------


def _is_path_token(tok: str) -> bool:
    if not tok or tok.startswith(("/", "-", ".", "~", "$")):
        return False
    if re.search(r"[<>{}\s=|,;()]", tok):
        return False
    return "/" in tok or tok.endswith(_EXT)


def documented_paths(text: str) -> list[tuple[int, str]]:
    """(line number, token) for each backticked path and each relative link
    target, skipping lines that record a deletion."""
    found: list[tuple[int, str]] = []
    for n, line in enumerate(text.splitlines(), 1):
        if re.search(r"\bdeleted in v\d", line):
            continue
        toks = re.findall(r"`([^`\n]+)`", line)
        toks += [t for t in re.findall(r"\]\(([^)\s]+)\)", line)
                 if not re.match(r"[a-z]+:", t)]
        for tok in toks:
            tok = tok.split("#", 1)[0]
            if _is_path_token(tok.split("::", 1)[0]):
                found.append((n, tok))
    return found


def resolve(tok: str, paths: list[str]) -> bool:
    """Does the token name something in the tree?

    Tried as: a path from the repo root, from `central_command/`, from `docs/`
    (link targets are relative to the document); then, for a name with a
    directory part or a bare file name, as a suffix of some tracked path.
    A `*` is a glob.
    """
    path = tok.split("::", 1)[0].rstrip("/")
    is_dir = tok.rstrip().endswith("/")
    pset = set(paths)
    for base in ("", "central_command/", "docs/"):
        cand = base + path
        norm = os.path.normpath(cand).replace(os.sep, "/")
        if "*" in norm:
            if any(fnmatch.fnmatch(p.rstrip("/"), norm) for p in paths):
                return True
        elif norm in pset or norm + "/" in pset:
            return True
    needle = os.path.normpath(path).replace(os.sep, "/").lstrip("./")
    if "*" in needle:
        return any(fnmatch.fnmatch(p.rstrip("/"), "*" + needle) for p in paths)
    if is_dir:
        return any(p.endswith("/" + needle + "/") for p in paths)
    return any(p == needle or p.endswith("/" + needle) for p in paths)


def _function_exists(tok: str, paths: list[str]) -> bool:
    """`tests/test_x.py::test_name` — the function (or class) is defined in that file."""
    file_part, _, fn = tok.partition("::")
    fn = fn.split("::")[-1]
    for base in ("", "central_command/"):
        for p in paths:
            if p == base + file_part or p.endswith("/" + file_part):
                src = (ROOT / p).read_text(encoding="utf-8", errors="replace")
                return re.search(rf"\b(?:def|class) {re.escape(fn)}\b", src) is not None
    return False


def missing_paths(text: str, paths: list[str]) -> list[str]:
    bad = []
    for n, tok in documented_paths(text):
        if not resolve(tok, paths):
            bad.append(f"line {n}: `{tok}` does not exist")
        elif "::" in tok and not _function_exists(tok, paths):
            bad.append(f"line {n}: `{tok}` — no such def or class")
    return bad


# --- package and module coverage ----------------------------------------------


def packages() -> list[str]:
    return sorted(p.name for p in PKG.iterdir()
                  if p.is_dir() and (p / "__init__.py").exists())


def top_modules() -> list[str]:
    return sorted(p.name for p in PKG.glob("*.py") if p.name != "__init__.py")


def package_modules(pkg: str) -> list[str]:
    return sorted(p.name for p in (PKG / pkg).glob("*.py") if p.name != "__init__.py")


def package_rows(text: str, pkg: str) -> str:
    """Every table row whose first cell is `pkg/`, joined."""
    rows = [ln for ln in text.splitlines() if re.match(rf"\|\s*`{re.escape(pkg)}/`\s*\|", ln)]
    return "\n".join(rows)


def unnamed_modules(text: str) -> list[str]:
    out = []
    for pkg in packages():
        if f"`{pkg}/`" not in text:
            out.append(f"package {pkg}/ is not named")
            continue
        rows = package_rows(text, pkg)
        for mod in package_modules(pkg):
            if mod not in rows and f"{pkg}/{mod}" not in text:
                out.append(f"{pkg}/{mod} is in no `{pkg}/` row of the package map")
    for mod in top_modules():
        if f"`{mod}`" not in text:
            out.append(f"top-level module {mod} is not named")
    return out


# --- recomputed counts --------------------------------------------------------


def event_types() -> set[str]:
    kinds: set[str] = set()
    for p in PKG.rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            a0 = node.args[0]
            if name in ("emit", "_emit") and isinstance(a0, ast.Constant) \
                    and isinstance(a0.value, str):
                kinds.add(a0.value)
    return kinds


def heartbeat_kinds() -> list[str]:
    src = (PKG / "heartbeat" / "actions.py").read_text(encoding="utf-8")
    return re.findall(r'^\s+kind="([\w.]+)",', src, flags=re.M)


def _comment_states(sql: str, anchor: str, stop: str) -> list[str]:
    """States listed as `A | B | C` in the schema comment after `anchor`."""
    start = sql.index(anchor)
    block = sql[start:sql.index(stop, start)]
    text = " ".join(re.sub(r"^\s*--\s?", "", ln) for ln in block.splitlines())
    return re.findall(r"(?:^|\|)\s*([A-Z][A-Z_]+)\b", text.split("Terminal:")[0])


def schema_state_sets() -> dict[str, list[str]]:
    sql = (PKG / "db" / "schema.sql").read_text(encoding="utf-8")
    return {
        "Session": _comment_states(sql, "RUNNING | AWAITING_HUMAN", "status      text not null"),
        "Task": _comment_states(sql, "-- NEW | ASSIGNED", "session_id   text"),
        "Work item": _comment_states(sql, "-- UNPROCESSED | CLAIMED", "-- FOLD_PENDING = covered"),
        "Guidance": ["CURRENT", "SUPERSEDED"],
    }


def proposal_states() -> list[str]:
    from central_command.contract.enums import ProposalStatus
    # PROPOSED is the in-memory default before a proposal is parked and APPROVED
    # is declared but never assigned (the document says so, in section 3);
    # neither is ever a persisted state.
    return [s.value for s in ProposalStatus if s.value not in ("PROPOSED", "APPROVED")]


def documented_state_sets(text: str) -> dict[str, list[str]]:
    """The `| Entity | States |` table: first cell is the entity, second a
    comma-separated state list."""
    sets: dict[str, list[str]] = {}
    for ln in text.splitlines():
        m = re.match(r"\|\s*([A-Za-z ]+?)\s*\|\s*([A-Z_]+(?:,\s*[A-Z_]+)+)\s*\|\s*$", ln)
        if m:
            sets[m.group(1)] = [s.strip() for s in m.group(2).split(",")]
    return sets


# --- the tests ----------------------------------------------------------------


def test_every_package_and_module_is_named():
    problems = unnamed_modules(doc_text())
    assert not problems, (
        "docs/ARCHITECTURE.md and central_command/ disagree — fix the row in the "
        "same change that moved the module:\n  " + "\n  ".join(problems)
    )


def test_every_documented_path_exists():
    problems = missing_paths(doc_text(), tree_paths())
    assert not problems, (
        "docs/ARCHITECTURE.md names paths the tree does not have (renamed or "
        "deleted? fix the row, or mark a history row 'deleted in vX.Y.Z'):\n  "
        + "\n  ".join(problems)
    )


def test_the_capability_count_matches_the_registry():
    from central_command.gateway.capabilities import REGISTRY
    m = re.search(r"(\d+) capabilities", doc_text())
    assert m, "the document must state the registry size as 'N capabilities'"
    assert int(m.group(1)) == len(REGISTRY), (
        f"the document says {m.group(1)} capabilities; the registry holds {len(REGISTRY)}"
    )


def test_the_heartbeat_action_kinds_match_the_registry():
    text = doc_text()
    kinds = heartbeat_kinds()
    assert kinds, "no heartbeat action kinds found in heartbeat/actions.py"
    named = set(re.findall(r"`([a-z_]+\.[a-z_]+)`", text))
    missing = [k for k in kinds if k not in named]
    assert not missing, f"heartbeat action kinds the document never names: {missing}"
    m = re.search(r"(\d+) action kinds", text)
    assert m and int(m.group(1)) == len(kinds), (
        f"the document must say '{len(kinds)} action kinds' (found {m.group(1) if m else None})"
    )


def test_the_event_type_count_is_close_to_the_tree():
    m = re.search(r"(\d+) distinct event types", doc_text())
    assert m, "the document must state the event-type count as 'N distinct event types'"
    documented, actual = int(m.group(1)), len(event_types())
    assert documented <= actual * (1 + EVENT_TOLERANCE) and \
        documented >= actual * (1 - EVENT_TOLERANCE), (
        f"the document says {documented} distinct event types; the tree emits {actual} "
        f"(tolerance {EVENT_TOLERANCE:.0%}) — recount and fix the row"
    )


def test_the_cockpit_is_covered():
    text = doc_text()
    routes = sorted(p.stem for p in (ROOT / "web/server/routes").glob("cc-*.ts")
                    if not p.name.endswith(".test.ts"))
    missing = [r for r in routes if f"`{r}`" not in text]
    assert not missing, f"cockpit routes the document never names: {missing}"
    features = sorted(p.name for p in (ROOT / "web/src/features").iterdir() if p.is_dir())
    section = text.split("## 5.", 1)[1].split("\n## 6.", 1)[0]
    missing = [f for f in features if f"`{f}`" not in section]
    assert not missing, f"web/src/features directories section 5 never names: {missing}"


def test_the_k3s_manifests_are_named():
    text = doc_text()
    stems = sorted(p.stem for p in (ROOT / "deploy/k3s").glob("[0-9][0-9]-*.yaml"))
    missing = [s for s in stems if s not in text]
    assert not missing, f"deploy/k3s numbered manifests the document never names: {missing}"


def test_the_state_sets_match_the_schema_and_enums():
    documented = documented_state_sets(doc_text())
    expected = {**schema_state_sets(), "Proposal": proposal_states()}
    for entity, states in expected.items():
        assert entity in documented, f"the state-set table has no {entity} row"
        assert documented[entity] == states, (
            f"{entity} states: the document lists {documented[entity]}, "
            f"the tree declares {states}"
        )


def test_the_freshness_rule_is_stated_and_names_this_guard():
    text = re.sub(r"\s+", " ", doc_text())
    assert "fix the row in the same change" in text
    assert "tests/test_architecture_doc.py" in text


# --- the guard's own sensitivity ----------------------------------------------


def test_a_renamed_module_is_caught():
    """The failure this file exists for, on synthetic input: a document that
    names a module the tree no longer has, and a tree with a module the
    document never names."""
    paths = ["central_command/", "central_command/api/", "central_command/api/routes.py"]
    assert missing_paths("see `api/routes.py`", paths) == []
    assert missing_paths("see `api/roots.py`", paths) == ["line 1: `api/roots.py` does not exist"]
    assert missing_paths("see `roots.py`", paths) == ["line 1: `roots.py` does not exist"]
    assert missing_paths("gone: `api/old.py` (deleted in v2.1.0)", paths) == []
    assert missing_paths("see [x](../central_command/api/routes.py)", paths) == []
    assert resolve("api/*.py", paths)
    assert not resolve("api/*.ts", paths)


def test_state_table_parser_reads_the_document_shape():
    sample = "| Session | RUNNING, DONE, FAILED |\n| Not | a state set |\n"
    assert documented_state_sets(sample) == {"Session": ["RUNNING", "DONE", "FAILED"]}
