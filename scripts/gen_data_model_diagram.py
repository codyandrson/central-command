"""Generate docs/diagrams/data-model.md from central_command/db/schema.sql.

The page is Mermaid inside markdown (rendered by GitHub): one erDiagram per
area, then a table of every table with its area and purpose.

    python scripts/gen_data_model_diagram.py           # write the file
    python scripts/gen_data_model_diagram.py --check   # exit 1 if it is stale

Python 3.12, standard library only. Output is deterministic.

What is read from the SQL: `create table` (columns, types, inline `references`,
table-level `primary key` / `unique` / `foreign key`) and `alter table` with
`add column` / `add constraint` clauses. Only DECLARED foreign keys are drawn;
a text column that merely holds another table's id is not a relationship here.

What cannot be read from SQL is kept in the three dicts below. The script fails
loudly when schema.sql and the dicts disagree, which is what keeps the page
honest when the schema changes.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "central_command" / "db" / "schema.sql"
OUTPUT = ROOT / "docs" / "diagrams" / "data-model.md"

# ---------------------------------------------------------------------------
# Hand-kept metadata: area order and description, table -> area, table -> purpose.
# ---------------------------------------------------------------------------

AREAS: dict[str, str] = {
    "Agents, charters and grants": (
        "The roster (an agent row is a roster entry), the versioned charters each agent "
        "runs under, and the capability packs granted to it."
    ),
    "Sessions and tasks": (
        "Agent runs with their durable transcript, the tasks that start them, the asks an "
        "agent puts to the operator, and the derived full-text recall index."
    ),
    "Proposals and approval gates": (
        "Every world-changing action is a proposal awaiting a decision; gate overrides are "
        "the operator's standing relaxation of that gate for one agent and one capability."
    ),
    "Work ledger and mail": (
        "The durable per-item ledger that mail and documents enter through, the standing "
        "mail rules applied before any model call, and resumable feed cursors."
    ),
    "Events, audit and outage state": (
        "The append-only event log and the persisted result of the dependency probes that "
        "drive outage recovery."
    ),
    "Schedules and settings": (
        "Recurring team work held as data, and the small key/value store for operator "
        "settings."
    ),
    "MCP servers and sandbox": (
        "MCP servers the system knows about, their discoverable tools, per-agent gate "
        "overrides on those tools, and the sources an agent may copy into its sandbox."
    ),
    "Skills library": (
        "Curated subject knowledge held as governed, versioned data, granted per agent, "
        "plus the bookkeeping that reviews bundled releases against it."
    ),
    "Goals (lines of effort)": (
        "The operator's goal-tracking substrate: lines of effort, their append-only "
        "check-ins, and the archived versions of each line's scoring semantics; the "
        "calendar plan ledger of the work blocks and placeholders the assistant created "
        "or adopted; and the stored day plans with the outcomes the evening review "
        "recorded."
    ),
    "Knowledge graph and notes": (
        "The audit and ingest queue behind approved knowledge-graph episodes, and the "
        "small always-in-context note store."
    ),
    "Source catalog": (
        "Sources, the documents found in them with their versions and locations, and the "
        "evidence ledger behind wiki claims."
    ),
}

TABLE_AREA: dict[str, str] = {
    "agent": "Agents, charters and grants",
    "agent_grant": "Agents, charters and grants",
    "guidance_version": "Agents, charters and grants",
    "session": "Sessions and tasks",
    "task": "Sessions and tasks",
    "operator_item": "Sessions and tasks",
    "session_recall": "Sessions and tasks",
    "session_recall_mark": "Sessions and tasks",
    "proposal": "Proposals and approval gates",
    "agent_gate_override": "Proposals and approval gates",
    "work_item": "Work ledger and mail",
    "mail_rule": "Work ledger and mail",
    "feed_state": "Work ledger and mail",
    "audit_event": "Events, audit and outage state",
    "dependency_probe": "Events, audit and outage state",
    "heartbeat_schedule": "Schedules and settings",
    "app_setting": "Schedules and settings",
    "mcp_server": "MCP servers and sandbox",
    "mcp_tool": "MCP servers and sandbox",
    "agent_mcp_gate_override": "MCP servers and sandbox",
    "agent_sandbox_source": "MCP servers and sandbox",
    "skill": "Skills library",
    "skill_doc": "Skills library",
    "skill_chunk": "Skills library",
    "skill_bundled_text": "Skills library",
    "skill_doc_review": "Skills library",
    "agent_skill_grant": "Skills library",
    "line_of_effort": "Goals (lines of effort)",
    "loe_checkin": "Goals (lines of effort)",
    "loe_semantic": "Goals (lines of effort)",
    "calendar_plan_event": "Goals (lines of effort)",
    "day_plan": "Goals (lines of effort)",
    "day_plan_outcome": "Goals (lines of effort)",
    "graph_verification": "Knowledge graph and notes",
    "graph_ingest_job": "Knowledge graph and notes",
    "agent_note": "Knowledge graph and notes",
    "source": "Source catalog",
    "catalog_document": "Source catalog",
    "catalog_version": "Source catalog",
    "catalog_location": "Source catalog",
    "wiki_claim": "Source catalog",
}

TABLE_PURPOSE: dict[str, str] = {
    "agent": "The roster entry for an agent: lane flags, lifecycle status, model and thinking settings.",
    "agent_grant": "Which capability packs each agent holds; revocation is a timestamp, never a delete.",
    "guidance_version": "Versioned charters: exactly one CURRENT version per agent, history immutable.",
    "session": "One agent run with its serialized run state, status and lineage (a conversation or a one-shot).",
    "task": "An operator- or agent-created unit of work for the team, resolved when its session does.",
    "operator_item": "An agent's ask of the operator (plan review, question, stall, continue) answered in the Decisions Inbox.",
    "session_recall": "Derived full-text index of message parts from an agent's own past sessions; rebuildable.",
    "session_recall_mark": "Per-session indexing watermark for the recall index.",
    "proposal": "A drafted world change (actions and evidence) awaiting an approve, reject or dismiss decision.",
    "agent_gate_override": "The operator's standing relaxation of one capability's gate for one agent, scoped and revocable.",
    "work_item": "The Work Ledger: one durable row per email or document, the source of truth for what still needs doing.",
    "mail_rule": "Standing inbox rules that auto-dismiss matching mail before any model call, applied in position order.",
    "feed_state": "Durable cursors for resumable sweeps; correctness never depends on it.",
    "audit_event": "The append-only event log: one ordered record serving audit, observability and the live stream.",
    "dependency_probe": "Persisted up/down result of each dependency probe, written only when the state changes.",
    "heartbeat_schedule": "Recurring team work as data: a time rule plus an action from the closed registry, born disabled.",
    "app_setting": "Small key/value store for operator-facing settings and once-per-database seed markers.",
    "mcp_server": "One row per MCP server the system knows about, with its build and registration state.",
    "mcp_tool": "A registered server's discovered tools, each carrying its default gate.",
    "agent_mcp_gate_override": "The operator's per-agent widening or narrowing of an MCP tool's default gate.",
    "agent_sandbox_source": "Which repository paths an agent may copy into its ephemeral sandbox; operator-granted.",
    "skill": "A skill in the Skills Library: curated subject knowledge granted per agent and loaded on demand.",
    "skill_doc": "One version of a skill document (guidance or reference); a correction is a new version.",
    "skill_chunk": "Search chunks of the current version of a reference document.",
    "skill_bundled_text": "Content-addressed record of the bundled document texts the library has received.",
    "skill_doc_review": "Review of one target release against the library: per-document merge status and the operator's verdict.",
    "agent_skill_grant": "Which skills each agent holds; revocation is a timestamp kept on the record.",
    "line_of_effort": "One of the operator's goals, with its versioned scoring semantics and display settings.",
    "loe_checkin": "Append-only check-in recording a light, confidence and summary under a stamped semantic version.",
    "loe_semantic": "Archived superseded semantic versions of a line of effort.",
    "calendar_plan_event": "Executor-written ledger of the calendar events the assistant created or adopted (work block or placeholder), retired by timestamp.",
    "day_plan": "Executor-written record of the day's approved plan (ranked issues, reasons, planning numbers re-read from the tracker), superseded by timestamp.",
    "day_plan_outcome": "Executor-written evening-review outcome per date and issue (done, actual hours, note, planned or not), superseded by timestamp.",
    "graph_verification": "Worklist auditing each approved graph episode after extraction, with its verdict and closing state.",
    "graph_ingest_job": "Durable ingest queue: one job per approved episode or episode removal, run in order per group.",
    "agent_note": "Gated, never-edited standing notes (private to an agent or about the operator), retired by timestamp.",
    "source": "A governed catalog source (for example a filesystem root) with its config, cursor and enable switch.",
    "catalog_document": "Stable lineage identity of a document across locations and versions; rescinded by tag, never deleted.",
    "catalog_version": "One observed version of a document lineage, deduplicated by content hash.",
    "catalog_location": "Every place a document lineage is found; marked missing, never deleted.",
    "wiki_claim": "Evidence ledger: each proposal citation becomes a claim row carrying the evidence version recorded at write time.",
}

# Columns that carry state, in priority order; at most MAX_STATE_COLUMNS are shown
# per table in addition to its primary-key and foreign-key columns.
STATE_COLUMNS = (
    "status", "state", "kind", "mode", "scope", "enabled", "verdict", "gate",
    "light", "revoked_at", "retired_at", "rescinded_at", "terminal_at",
    "closed_at", "decided_at", "last_fired_at",
)
MAX_STATE_COLUMNS = 5

PLACEHOLDER_PURPOSE = "TODO: purpose not found"


# ---------------------------------------------------------------------------
# SQL parsing
# ---------------------------------------------------------------------------


@dataclass
class Column:
    name: str
    type: str
    not_null: bool = False


@dataclass(frozen=True)
class ForeignKey:
    columns: tuple[str, ...]
    target: str


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)
    primary_key: tuple[str, ...] = ()
    uniques: list[tuple[str, ...]] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


def strip_comments(sql: str) -> str:
    """Remove `--` and `/* */` comments, leaving string literals intact."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i : j + 1])
            i = j + 1
        elif sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                i += 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def split_top_level(text: str, sep: str) -> list[str]:
    """Split on `sep` outside quotes and parentheses."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            buf.append(text[i : j + 1])
            i = j + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def matching_paren(text: str, start: int) -> int:
    """Index of the `)` closing the `(` at `start`, quote-aware."""
    depth = 0
    i, n = start, len(text)
    while i < n:
        c = text[i]
        if c == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced parentheses in schema.sql")


def ident(raw: str) -> str:
    return raw.strip().strip('"').lower()


def ident_list(raw: str) -> tuple[str, ...]:
    return tuple(ident(p) for p in raw.split(",") if p.strip())


TYPE_STOP = re.compile(
    r"\b(not\s+null|null|default|primary\s+key|references|unique|check|generated|constraint)\b"
)
REFERENCES = re.compile(r"\breferences\s+(\"?\w+\"?)", re.IGNORECASE)


def mermaid_type(raw: str) -> str:
    t = raw.strip().lower()
    t = re.sub(r"\([^)]*\)", "", t)
    t = t.replace("timestamp with time zone", "timestamptz")
    t = t.replace("timestamp without time zone", "timestamp")
    t = t.replace("double precision", "double")
    t = t.replace("character varying", "varchar")
    t = t.replace("[]", "_array")
    return re.sub(r"\s+", "_", t.strip()) or "unknown"


def add_foreign_key(table: Table, fk: ForeignKey) -> None:
    """Record a foreign key once (a column may be declared again by `alter table`)."""
    if fk not in table.foreign_keys:
        table.foreign_keys.append(fk)


def parse_column(item: str, table: Table) -> None:
    """Apply one column definition (create or `add column`) to the table."""
    m = re.match(r'("?\w+"?)\s*(.*)$', item, re.DOTALL)
    if not m:
        raise ValueError(f"cannot parse column definition: {item!r}")
    name, rest = ident(m.group(1)), re.sub(r"\s+", " ", m.group(2)).strip()
    low = rest.lower()
    stop = TYPE_STOP.search(low)
    col_type = rest[: stop.start()] if stop else rest
    is_pk = re.search(r"\bprimary key\b", low) is not None
    col = Column(name, mermaid_type(col_type), not_null=is_pk or "not null" in low)
    if table.column(name) is None:
        table.columns.append(col)
    if is_pk:
        table.primary_key = (name,)
    if re.search(r"\bunique\b", low) and not re.search(r"\bunique\s*\(", low):
        table.uniques.append((name,))
    ref = REFERENCES.search(rest)
    if ref:
        add_foreign_key(table, ForeignKey((name,), ident(ref.group(1))))


def apply_table_constraint(item: str, table: Table) -> bool:
    """Apply a table-level constraint; return False when `item` is not one."""
    body = re.sub(r"^constraint\s+\"?\w+\"?\s+", "", item, flags=re.IGNORECASE)
    low = body.lower()
    if low.startswith("primary key"):
        table.primary_key = ident_list(body[body.index("(") + 1 : body.rindex(")")])
        return True
    if low.startswith("unique"):
        table.uniques.append(ident_list(body[body.index("(") + 1 : body.index(")")]))
        return True
    if low.startswith("foreign key"):
        cols = ident_list(body[body.index("(") + 1 : body.index(")")])
        ref = REFERENCES.search(body)
        if ref is None:
            raise ValueError(f"foreign key without references: {item!r}")
        add_foreign_key(table, ForeignKey(cols, ident(ref.group(1))))
        return True
    return low.startswith(("check", "exclude"))


def parse_schema(sql: str) -> dict[str, Table]:
    tables: dict[str, Table] = {}
    statements = split_top_level(strip_comments(sql), ";")

    for stmt in statements:
        flat = re.sub(r"\s+", " ", stmt)
        m = re.match(r'create table (?:if not exists )?("?\w+"?)\s*\(', flat, re.IGNORECASE)
        if not m:
            continue
        start = stmt.index("(")
        body = stmt[start + 1 : matching_paren(stmt, start)]
        table = Table(ident(m.group(1)))
        for item in split_top_level(body, ","):
            if not apply_table_constraint(item, table):
                parse_column(item, table)
        tables[table.name] = table

    for stmt in statements:
        flat = re.sub(r"\s+", " ", stmt)
        m = re.match(r'alter table (?:if exists )?(?:only )?("?\w+"?) (.*)$', flat, re.IGNORECASE)
        if not m:
            continue
        name = ident(m.group(1))
        if name not in tables:
            raise SystemExit(f"schema.sql: `alter table {name}` precedes or lacks its create table")
        for clause in split_top_level(m.group(2), ","):
            cm = re.match(r"add column (?:if not exists )?(.*)$", clause, re.IGNORECASE)
            if cm:
                parse_column(cm.group(1), tables[name])
                continue
            km = re.match(r"add (constraint .*|primary key .*|unique .*|foreign key .*)$", clause, re.IGNORECASE)
            if km:
                apply_table_constraint(km.group(1), tables[name])
    return tables


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate(tables: dict[str, Table]) -> None:
    problems: list[str] = []
    schema_names = set(tables)
    for label, mapping in (("TABLE_AREA", TABLE_AREA), ("TABLE_PURPOSE", TABLE_PURPOSE)):
        for name in sorted(schema_names - set(mapping)):
            problems.append(f"table `{name}` is in schema.sql but missing from {label}")
        for name in sorted(set(mapping) - schema_names):
            problems.append(f"{label} names `{name}`, which is not a table in schema.sql")
    for name, area in sorted(TABLE_AREA.items()):
        if area not in AREAS:
            problems.append(f"TABLE_AREA puts `{name}` in unknown area {area!r} (not in AREAS)")
    for area in AREAS:
        if area not in TABLE_AREA.values():
            problems.append(f"area {area!r} has no tables")
    for table in tables.values():
        for fk in table.foreign_keys:
            if fk.target not in tables:
                problems.append(f"`{table.name}` references unknown table `{fk.target}`")
            for col in fk.columns:
                if table.column(col) is None:
                    problems.append(f"`{table.name}` foreign key names unknown column `{col}`")
    if problems:
        raise SystemExit(
            "gen_data_model_diagram: the area/purpose dicts and schema.sql disagree:\n  - "
            + "\n  - ".join(problems)
            + "\nUpdate AREAS / TABLE_AREA / TABLE_PURPOSE at the top of "
            "scripts/gen_data_model_diagram.py."
        )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def relationship(child: Table, fk: ForeignKey) -> str:
    cols = set(fk.columns)
    not_null = all((c := child.column(n)) is not None and c.not_null for n in fk.columns)
    one_to_one = cols == set(child.primary_key) or any(cols == set(u) for u in child.uniques)
    left = "||" if not_null else "|o"
    right = "o|" if one_to_one else "o{"
    return f'    {fk.target} {left}--{right} {child.name} : "{", ".join(fk.columns)}"'


def entity_block(table: Table) -> list[str]:
    fk_cols = {c for fk in table.foreign_keys for c in fk.columns}
    pk_cols = set(table.primary_key)
    state = [n for n in STATE_COLUMNS if table.column(n) is not None and n not in pk_cols | fk_cols]
    shown = pk_cols | fk_cols | set(state[:MAX_STATE_COLUMNS])
    lines = [f"    {table.name} {{"]
    for col in table.columns:
        if col.name not in shown:
            continue
        keys = [k for k, on in (("PK", col.name in pk_cols), ("FK", col.name in fk_cols)) if on]
        lines.append(f"        {col.type} {col.name}" + (f" {', '.join(keys)}" if keys else ""))
    lines.append("    }")
    return lines


def area_diagram(area: str, tables: dict[str, Table]) -> str:
    members = sorted(n for n, a in TABLE_AREA.items() if a == area)
    member_set = set(members)
    lines = ["erDiagram"]
    for name in members:
        lines += entity_block(tables[name])
    rels: set[str] = set()
    for table in tables.values():
        for fk in table.foreign_keys:
            if table.name in member_set or fk.target in member_set:
                rels.add(relationship(table, fk))
    lines += sorted(rels)
    return "\n".join(lines)


def md_cell(text: str) -> str:
    return text.replace("|", "\\|")


def render(tables: dict[str, Table]) -> str:
    out = [
        "# Data model",
        "",
        (
            "This page is generated from `central_command/db/schema.sql` by "
            "`scripts/gen_data_model_diagram.py`. Do not edit it by hand: change the schema "
            "(or the area and purpose lists at the top of the script) and run "
            "`python scripts/gen_data_model_diagram.py` to regenerate it."
        ),
        "",
        (
            "Each diagram covers one area and shows a table's primary-key, foreign-key and "
            "main state columns only. Only foreign keys declared in the schema are drawn; a "
            "table from another area appears as a bare entity. A text column that holds "
            "another table's id without a declared foreign key is not drawn."
        ),
        "",
    ]
    for area, description in AREAS.items():
        out += [f"## {area}", "", description, "", "```mermaid", area_diagram(area, tables), "```", ""]
    out += ["## All tables", "", "| Table | Area | Purpose |", "| --- | --- | --- |"]
    area_rank = {a: i for i, a in enumerate(AREAS)}
    for name in sorted(tables, key=lambda n: (area_rank[TABLE_AREA[n]], n)):
        purpose = TABLE_PURPOSE.get(name, PLACEHOLDER_PURPOSE)
        out.append(f"| `{name}` | {md_cell(TABLE_AREA[name])} | {md_cell(purpose)} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the file on disk differs from what would be generated",
    )
    args = parser.parse_args(argv)

    tables = parse_schema(SCHEMA.read_text(encoding="utf-8"))
    validate(tables)
    generated = render(tables)
    rel = OUTPUT.relative_to(ROOT)

    if args.check:
        current = OUTPUT.read_bytes().decode("utf-8") if OUTPUT.exists() else None
        if current != generated:
            reason = "is missing" if current is None else "is out of date"
            print(
                f"{rel} {reason}: it differs from what scripts/gen_data_model_diagram.py "
                "generates from schema.sql. Run `python scripts/gen_data_model_diagram.py` "
                "and commit the result.",
                file=sys.stderr,
            )
            return 1
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(generated.encode("utf-8"))
    print(f"wrote {rel} ({len(tables)} tables, {len(AREAS)} areas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
