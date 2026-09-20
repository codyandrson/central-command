"""Who asked for this write is the control plane's claim, never the agent's.

A proposal's `agent_id` is the DRAFTER, and it lives on the proposal ROW. It is
not the SUBJECT: since the coach drafts charter edits for other agents, the two
are routinely different, and the target lives in the action arguments where the
Executor reads it. That split is why `executor.execute()` takes `proposer` as a
parameter at all — `executor.execute`'s own docstring says it: "It is not an
argument any agent can write: a capability that records who asked for it must
take that from the control plane, never from the request."

What this guards is the drift back. Every handler already receives `args`, and
`args` is the one thing an agent fully controls; a handler that reached for
`args["proposer"]` instead of the parameter beside it would compile, pass its
own tests, and quietly turn the provenance record into an agent-authored claim
— on the coaching loop, where a charter edit's pedigree is the entire audit
trail. Nothing loud would happen. The record would simply start saying whatever
the drafting model wrote.

Three checks, smallest blast radius first:

1. a behavioural round-trip — `execute()` hands the handler the gateway's
   `proposer`, even when the arguments carry a different, louder answer;
2. a source walk over `gateway/executor.py` — no handler reads a provenance
   identity out of `args`;
3. a source walk over the call sites — every `executor.execute(...)` passes
   `proposer=` from the proposal row, and nothing derives it from the actions.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from central_command.config import settings
from central_command.contract.models import Action, Reversibility
from central_command.gateway import executor

SRC = pathlib.Path(__file__).resolve().parent.parent / "central_command"
EXECUTOR = SRC / "gateway" / "executor.py"

# Keys an agent could write into its own arguments that would READ as
# provenance. `agent_id` is deliberately NOT here: it is the SUBJECT of several
# capabilities (charter.update's target, task.create's assignee), which is the
# whole distinction DL-009 draws.
PROVENANCE_KEYS = {
    "proposer", "drafter", "proposed_by", "actor", "approver", "added_by",
    "created_by", "updated_by", "on_behalf_of", "as_agent", "requested_by",
}

# The only names an `agent:{...}` provenance string in the Executor may be
# built from. `drafter` is `proposer or target` — a self-proposed edit reads
# as it always did — and that binding is pinned by its own test below.
PROVENANCE_NAMES = {"proposer", "drafter"}


# --------------------------------------------------------------------------
# 1. Behavioural: the parameter wins over the arguments.
# --------------------------------------------------------------------------

async def test_the_handler_is_handed_the_gateways_proposer_not_the_agents(monkeypatch):
    seen: dict = {}

    async def spy(args: dict, approver: str, proposer: str | None) -> str:
        seen.update(args=args, approver=approver, proposer=proposer)
        return "ok"

    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setitem(executor.HANDLERS, "test.provenance_spy", spy)

    lying_args = {
        # Everything a drafting model could write to claim it was somebody else.
        "proposer": "orchestrator", "actor": "human:operator",
        "proposed_by": "agent:coach", "approver": "human:operator",
    }
    outcome = await executor.execute(
        [Action(
            capability="test.provenance_spy",
            arguments=dict(lying_args),
            target_ref={"system": "test", "id": "x"},
            reversibility=Reversibility.reversible,
        )],
        approver="human:operator",
        source_refs=["item:1"],
        proposer="inbox-triage",       # what the gateway read off the proposal row
        proposal_id="prop_1",
    )

    assert seen["proposer"] == "inbox-triage", (
        "the Executor handed the handler a proposer taken from the action "
        "arguments — provenance must come from the proposal ROW the gateway "
        "read, never from anything an agent can write into its own args"
    )
    assert seen["approver"] == "human:operator"
    # The lie is still IN the args — it is not scrubbed, it is simply not read.
    assert seen["args"]["proposer"] == "orchestrator"
    assert outcome.provenance.approver == "human:operator"


# --------------------------------------------------------------------------
# 2. Source walk: no handler reads an identity out of args.
# --------------------------------------------------------------------------

def _executor_tree() -> ast.AST:
    return ast.parse(EXECUTOR.read_text(encoding="utf-8"))


def _args_keys_read():
    """(lineno, key) for every `args["k"]` / `args.get("k")` in the Executor."""
    for node in ast.walk(_executor_tree()):
        if (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name) and node.value.id == "args"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            yield node.lineno, node.slice.value
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "args"
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            yield node.lineno, node.args[0].value


def test_the_args_walk_sees_the_arguments_it_is_guarding():
    keys = {k for _line, k in _args_keys_read()}
    assert "agent_id" in keys, (
        "the args walk no longer sees `args[\"agent_id\"]` — charter.update's "
        "TARGET, the very argument this guard has to tell apart from "
        "provenance. A walk that sees nothing forbids nothing"
    )
    assert len(keys) > 40


def test_no_executor_handler_reads_provenance_out_of_the_arguments():
    offenders = sorted({
        f"executor.py:{line} args[{key!r}]"
        for line, key in _args_keys_read() if key in PROVENANCE_KEYS
    })
    assert offenders == [], (
        "an Executor handler read an identity out of the action arguments. "
        "`args` is the one thing the drafting agent controls end to end, so an "
        "actor taken from it is an agent-authored claim about provenance, not a "
        "record. Use the `proposer` parameter — the gateway takes it from the "
        "proposal ROW for exactly this reason — or `approver` for the human "
        f"side: {offenders}"
    )


def test_every_agent_provenance_string_is_built_from_the_gateways_proposer():
    """`f"agent:{x}"` is how the Executor writes WHO into the record. Pin the
    names `x` may be, so the next one has to be argued for."""
    offenders = []
    for node in ast.walk(_executor_tree()):
        if not isinstance(node, ast.JoinedStr):
            continue
        parts = node.values
        for i, part in enumerate(parts):
            if not (isinstance(part, ast.Constant) and isinstance(part.value, str)
                    and part.value.endswith("agent:")):
                continue
            nxt = parts[i + 1] if i + 1 < len(parts) else None
            name = (nxt.value.id
                    if isinstance(nxt, ast.FormattedValue) and isinstance(nxt.value, ast.Name)
                    else ast.unparse(nxt) if nxt is not None else "<nothing>")
            if name not in PROVENANCE_NAMES:
                offenders.append(f"executor.py:{node.lineno} f\"agent:{{{name}}}\"")
    assert sorted(offenders) == [], (
        "the Executor stamped an `agent:` provenance string from something "
        f"other than {sorted(PROVENANCE_NAMES)}. The drafter comes from the "
        "proposal row via the `proposer` parameter; anything else is the "
        f"agent's own account of who asked: {sorted(offenders)}"
    )


def test_the_drafter_fallback_is_still_proposer_or_target():
    """`_charter_update` is the one handler with a second provenance name, and
    the fallback is load-bearing: `drafter = proposer or target` reads a
    self-proposed edit the way it always did, while a coach-drafted one names
    both. `drafter = args[...]` would pass the walk above and restore the whole
    bug, so the binding itself is pinned."""
    fn = next(
        n for n in ast.walk(_executor_tree())
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_charter_update"
    )
    bindings = {
        t.id: ast.unparse(n.value)
        for n in ast.walk(fn) if isinstance(n, ast.Assign)
        for t in n.targets if isinstance(t, ast.Name)
    }
    assert bindings.get("drafter") == "proposer or target", (
        "_charter_update's `drafter` is no longer `proposer or target` (got "
        f"{bindings.get('drafter')!r}). The coach drafts charter edits for OTHER "
        "agents, so the pedigree must name the drafter from the proposal row "
        "and the target from the args — never one standing in for the other"
    )
    assert bindings.get("target") == "args['agent_id']", (
        "the charter edit's TARGET should still come from the arguments — that "
        "half is the agent's to state and the operator's to approve"
    )


# --------------------------------------------------------------------------
# 3. Source walk: every call site passes the row's drafter.
# --------------------------------------------------------------------------

def _execute_call_sites():
    """(rel, lineno, proposer_expression|None) for every `executor.execute(...)`
    in the package."""
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "execute"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "executor"):
                continue
            kw = next((k for k in node.keywords if k.arg == "proposer"), None)
            yield rel, node.lineno, (ast.unparse(kw.value) if kw else None)


def test_the_call_site_walk_sees_the_gateway():
    sites = list(_execute_call_sites())
    assert [s for s in sites if s[0] == "gateway/gateway.py"], (
        "no `executor.execute(...)` call found in gateway/gateway.py — the walk "
        "has lost the only caller it exists to check"
    )


@pytest.mark.parametrize(
    "rel,line,proposer",
    list(_execute_call_sites()),
    ids=[f"{r}:{l}" for r, l, _p in _execute_call_sites()],
)
def test_every_execute_call_names_a_proposer_from_the_proposal_row(rel, line, proposer):
    assert proposer is not None, (
        f"{rel}:{line} calls executor.execute() without `proposer=`. The "
        "Executor then stamps `proposed_by: None` on capabilities whose whole "
        "point is recording who asked — read it off the proposal row "
        "(`prop_row[\"agent_id\"]`), which is the drafter"
    )
    assert "agent_id" in proposer, (
        f"{rel}:{line} passes proposer={proposer!r}, which does not read the "
        "proposal row's `agent_id`. The drafter is a column, not a derivation: "
        "anything computed from the actions is the agent's own claim"
    )
    for forbidden in ("args", "arguments", "action"):
        assert forbidden not in proposer, (
            f"{rel}:{line} derives proposer={proposer!r} from the action "
            "arguments — that is precisely the agent-authored provenance "
            "DL-009 forbids"
        )
