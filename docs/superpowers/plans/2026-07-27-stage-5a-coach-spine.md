# Stage 5a — Coach Spine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hire `coach` as a real rostered agent whose governed charter IS its system prompt, and fix the false provenance stamp so a coach-drafted charter version names the coach as drafter and the target as subject.

**Architecture:** The ephemeral per-target `Agent(name=f"{agent_id}-coach")` in `runtime/run.py` is replaced by one rostered `coach` agent, assembled from capability-pack grants through the standard builder path. Two new packs carry its surface: `record-read` (ungated reads over Central Command's own record) and `charter-propose` (the existing `charter.update` gated write). Because the coach now drafts for *someone else*, the proposal row's `agent_id` becomes the **drafter** and the target lives only in the action arguments — which is what makes the executor's provenance fix meaningful.

**Tech Stack:** Python 3.12, FastAPI, Pydantic AI 2.18, asyncpg/Postgres, pytest (asyncio), React + TypeScript (Vite) for the cockpit.

## Global Constraints

- **The trust boundary is the import graph.** `runtime/` must never import `gateway/` or `executor`. Enforced by `tests/test_governance.py::test_runtime_never_imports_the_gateway_tier`.
- **`claim_matches_source: None` means NOT CHECKED** — never "checked and inconclusive." A citation that resolves to no event flags `False`.
- **Capability lists are GENERATED from grants**, never hand-written into charter text (D25).
- **Roster membership is a non-empty `role` column**, set only by schema seeds and `repo.hire_agent` — never by `upsert_agent`.
- **A fresh database must reproduce the roster.** In `schema.sql`, the founding `INSERT` must NOT seed `role`; the follow-up `UPDATE` is guarded on `role = ''`.
- **Existing charter-version rows are never rewritten.** A row saying `agent:inbox-triage` is still true of the session that produced it.
- **Every proposal goes through `runtime/proposals.park_proposal()`.** A `repo.save_proposal(...)` anywhere else fails `tests/test_proposal_created.py`.
- **Assert on rows your test created, never on global counts** — the dev DB is shared.
- **Tests that call live-model code must pin `demo_mode=True`** (`monkeypatch.setattr(settings, "demo_mode", True)`) or the offline suite calls Claude for real and spends money.
- **Git:** work on `master`; `git push origin master` in the same session as the commit.

---

## File Structure

**Created:**
- `central_command/runtime/coach_profile.py` — the coach's built-in ("v0") charter.
- `tests/test_coach_provenance.py` — the drafter/target provenance guard (Task 1).
- `tests/test_coach_agent.py` — the coach is rostered, and its governed charter is its prompt (Tasks 4–5).

**Modified:**
- `central_command/gateway/executor.py` — `execute()` gains `proposer`; all handlers take it; `_charter_update` stamps drafter and target separately.
- `central_command/gateway/gateway.py:141` — passes `proposer=prop_row["agent_id"]`.
- `central_command/gateway/capabilities.py` — `charter.update` description records the drafter/target widening.
- `central_command/runtime/packs.py` — two new packs.
- `central_command/runtime/tools.py` — five ungated record-read tools.
- `central_command/runtime/roster.py` — `coach` in `SEED`.
- `central_command/runtime/agent.py` — `builtin_charter` branch for `coach`.
- `central_command/db/schema.sql` — `coach` roster seed + grants seed.
- `central_command/runtime/run.py` — `_coach_prompt` rewritten third-person; `run_coach` rebuilt on the standard builder; `_verified_evidence` async with selected/discovered/unresolved.
- `tests/test_coach_evidence.py` — updated for the async, record-aware checker.
- `web/src/features/decisions/types.ts` + `DecisionsView.tsx` — render the citation marker.
- `docs/STATUS.md`, `CLAUDE.md` — record the change.

---

### Task 1: Provenance — the drafter is server-side truth

The bug: `_charter_update` reads `args["agent_id"]` (the **target**) and stamps it as the proposer. Once a coach drafts for another agent, the version row claims the target proposed its own edit.

**Files:**
- Modify: `central_command/gateway/executor.py:42-260` (handler signatures), `:82-101` (`_charter_update`), `:289-317` (`_execute_action`, `execute`)
- Modify: `central_command/gateway/gateway.py:141-143`
- Test: `tests/test_coach_provenance.py` (create)

**Interfaces:**
- Consumes: `repo.save_charter_version(agent_id, content, rationale, created_by)`, `repo.current_charter(agent_id)`, `gateway.approve_and_execute(session_id, proposal_id, approver, model)`.
- Produces: `executor.execute(actions, approver, source_refs, proposer=None) -> ExecutionOutcome`. Every handler in `HANDLERS` now has the signature `(args: dict, approver: str, proposer: str | None) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_coach_provenance.py`:

```python
"""A coach-drafted charter version must name the COACH as drafter and the
TARGET as subject.

`executor._charter_update` used to stamp `created_by` from `args["agent_id"]` —
the target — because `execute()` never received the drafting agent. That was
harmless while every charter edit was self-proposed, and became a false
provenance record the moment a coach could draft for someone else (stage 5a).

The drafter is taken from the proposal ROW, never from action args: an actor an
agent can write into its own arguments is an agent-authored claim about
provenance (the same rule stage 4 applied to `task.create`'s approver).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway


def _pg_available() -> bool:
    async def probe() -> bool:
        try:
            conn = await repo._conn()
            await conn.close()
            return True
        except Exception:  # noqa: BLE001
            return False

    try:
        return asyncio.run(probe())
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(), reason=f"no Postgres at {settings.database_url}"
)


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    # charter.update writes only the (test) DB — live mode exercises the real
    # handler instead of dry-run's no-op.
    monkeypatch.setattr(settings, "executor_mode", "live")


async def _seed_charter_proposal(drafter: str, target: str, extra_args: dict | None = None) -> tuple[str, str]:
    """A proposal DRAFTED BY `drafter` that edits `target`'s charter."""
    sid = "sess_prov_" + uuid.uuid4().hex[:8]
    pid = "prop_prov_" + uuid.uuid4().hex[:8]
    await repo.upsert_agent(drafter, drafter, "demo", "c")
    await repo.upsert_agent(target, target, "demo", "c")
    await repo.save_charter_version(target, "CHARTER before coaching.", "seed",
                                    created_by="operator")
    await repo.save_paused_session(sid, drafter, {})
    await repo.save_proposal(
        proposal_id=pid, session_id=sid, agent_id=drafter,
        intent=f"Charter edit for {target}",
        actions=[{
            "capability": "charter.update",
            "arguments": {
                "agent_id": target,
                "content": "CHARTER before coaching.\n\nCOACHED RULE: check first.",
                "rationale": "Encodes recurring operator feedback.",
                **(extra_args or {}),
            },
            "target_ref": {"system": "central_command", "id": target,
                           "read_version": "unknown"},
            "reversibility": "reversible",
        }],
        evidence=[], expected_effect=f"{target} charter gains a rule",
        idempotency_key=f"{pid}:0", status="AWAITING_HUMAN",
    )
    return sid, pid


async def test_a_coach_drafted_version_names_the_drafter_and_the_target():
    suffix = uuid.uuid4().hex[:6]
    drafter, target = f"prov-drafter-{suffix}", f"prov-target-{suffix}"
    sid, pid = await _seed_charter_proposal(drafter, target)

    await gateway.approve_and_execute(sid, pid, approver="human:lee")

    row = await repo.current_charter(target)
    assert "COACHED RULE" in row["content"], "the edit did not commit"
    assert f"agent:{drafter}" in row["created_by"], (
        f"created_by must name the DRAFTER; got {row['created_by']!r}"
    )
    assert f"for {target}" in row["created_by"], (
        f"created_by must name the TARGET as subject; got {row['created_by']!r}"
    )


async def test_the_charter_updated_event_separates_proposer_from_target():
    suffix = uuid.uuid4().hex[:6]
    drafter, target = f"prov-ev-drafter-{suffix}", f"prov-ev-target-{suffix}"
    sid, pid = await _seed_charter_proposal(drafter, target)

    await gateway.approve_and_execute(sid, pid, approver="human:lee")

    evs = [e for e in await repo.list_events_of_kinds(["charter.updated"])
           if e["ref_id"] == target]
    assert evs, "no charter.updated event for the target"
    payload = evs[-1]["payload"]
    assert payload["proposed_by"] == f"agent:{drafter}"
    assert payload["target"] == target


async def test_a_proposer_claimed_in_action_args_never_overrides_the_row():
    """Provenance comes from the proposal row. An agent that writes
    `proposer` into its own arguments changes nothing."""
    suffix = uuid.uuid4().hex[:6]
    drafter, target = f"prov-spoof-{suffix}", f"prov-victim-{suffix}"
    sid, pid = await _seed_charter_proposal(
        drafter, target, extra_args={"proposer": "human:lee"},
    )

    await gateway.approve_and_execute(sid, pid, approver="human:lee")

    row = await repo.current_charter(target)
    assert f"agent:{drafter}" in row["created_by"]
    assert "human:lee" in row["created_by"]  # only as the APPROVER
    assert "agent:human:lee" not in row["created_by"]


async def test_a_self_proposed_edit_still_reads_as_before():
    """The existing pedigree format is unchanged when drafter == target, so
    old rows and new self-edits stay comparable."""
    agent = "prov-self-" + uuid.uuid4().hex[:6]
    sid, pid = await _seed_charter_proposal(agent, agent)

    await gateway.approve_and_execute(sid, pid, approver="human:lee")

    row = await repo.current_charter(agent)
    assert row["created_by"] == f"agent:{agent} (approved human:lee)"
```

- [ ] **Step 2: Run the test to verify it FAILS against the current executor**

Run: `.venv/bin/python -m pytest tests/test_coach_provenance.py -v`

Expected: `test_a_coach_drafted_version_names_the_drafter_and_the_target` FAILS with `created_by must name the DRAFTER; got 'agent:prov-target-xxxxxx (approved human:lee)'`, and `test_the_charter_updated_event_separates_proposer_from_target` FAILS with `KeyError: 'target'`. `test_a_self_proposed_edit_still_reads_as_before` PASSES already.

**This step is not optional.** A guard not proven to fail on the broken version is decoration. Record the failure output before continuing.

- [ ] **Step 3: Thread `proposer` through the executor**

In `central_command/gateway/executor.py`, add the third parameter to **every** handler. The signatures become `(args: dict, approver: str, proposer: str | None) -> str` for all nineteen: `_jira_set_due_date`, `_jira_create_issue`, `_jira_add_comment`, `_jira_update_attributes`, `_graph_add_episode`, `_charter_update`, `_task_create`, `_jira_link_issues`, `_jira_transition_issue`, `_litellm_add_model`, `_litellm_update_model`, `_litellm_delete_model`, `_litellm_create_key`, `_litellm_update_key`, `_litellm_delete_key`, `_litellm_create_team`, `_litellm_delete_team`, `_litellm_set_fallbacks`, `_litellm_delete_fallbacks`.

The parameter is uniform on purpose: an implicit special case for one handler is how the next reader misses that provenance is available at all. Only `_charter_update` reads it today.

Replace `_charter_update` (currently lines 82-101) with:

```python
async def _charter_update(args: dict, approver: str, proposer: str | None) -> str:
    # The coaching loop's write (D5): an agent-PROPOSED charter edit becomes a
    # new governed version only HERE, after approval — the version row carries
    # the pedigree (proposed by the agent, approved by the human), and history
    # stays append-only so a bad coached charter is one revert away.
    #
    # DRAFTER vs TARGET (stage 5a): since the coach drafts edits for OTHER
    # agents, these are two different agents and the record must say so. The
    # drafter comes from the proposal ROW (server-side truth, passed in by the
    # gateway) — never from `args`, because an actor an agent can write into
    # its own arguments is an agent-authored claim about provenance.
    from central_command import events
    from central_command.db import repo

    target = args["agent_id"]
    drafter = proposer or target        # a self-proposed edit reads as it always did
    pedigree = (
        f"agent:{drafter} (approved {approver})" if drafter == target
        else f"agent:{drafter} for {target} (approved {approver})"
    )
    row = await repo.save_charter_version(
        target, args["content"], args.get("rationale"), created_by=pedigree,
    )
    await events.emit(
        "charter.updated",
        ref_id=target,
        payload={"version": row["version"], "rationale": args.get("rationale"),
                 "proposed_by": f"agent:{drafter}", "target": target},
        actor=approver,
    )
    return f"charter for {target} updated to v{row['version']}"
```

Replace `_execute_action` and `execute` (currently lines 289-317) with:

```python
async def _execute_action(action: Action, approver: str, proposer: str | None) -> str:
    """Route one Action to its façade call. Returns a human-readable result."""
    capability = action.capability.split("@", 1)[0]  # strip @version
    if settings.executor_mode == "dry_run":
        return f"[dry-run] would execute {action.capability} with {action.arguments}"
    handler = HANDLERS.get(capability)
    if handler is None:
        raise ExecutorError(f"unknown capability: {action.capability}")
    return await handler(action.arguments, approver, proposer)


async def execute(
    actions: list[Action], approver: str, source_refs: list[str],
    proposer: str | None = None,
) -> ExecutionOutcome:
    """Execute a proposal's actions in order. Provenance is stamped from the
    approver and the evidence source refs. A failing action raises
    ExecutionFailed carrying every result that completed before it.

    `proposer` is the agent that DRAFTED the proposal, supplied by the gateway
    from the proposal row. It is not an argument any agent can write: a
    capability that records who asked for it must take that from the control
    plane, never from the request (stage 5a — a coach drafts for others, so
    drafter and target are no longer the same agent).
    """
    results: list[str] = []
    for a in actions:
        try:
            results.append(await _execute_action(a, approver, proposer))
        except Exception as e:  # noqa: BLE001 — every failure becomes the honest record
            raise ExecutionFailed(a.capability, str(e), results) from e
    provenance = Provenance(
        approver=approver,
        source_refs=source_refs,
        executed_at=datetime.now(timezone.utc),
    )
    return ExecutionOutcome(result_text="; ".join(results), provenance=provenance)
```

- [ ] **Step 4: Pass the drafter from the gateway**

In `central_command/gateway/gateway.py`, replace the `executor.execute(...)` call at line 141:

```python
        outcome = await executor.execute(
            proposal.actions, approver=approver, source_refs=source_refs,
            proposer=prop_row["agent_id"],
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_coach_provenance.py tests/test_coach_loop.py tests/test_governance.py -v`
Expected: PASS (all four new tests, plus the existing coach-loop and governance suites unchanged).

- [ ] **Step 6: Commit**

```bash
git add tests/test_coach_provenance.py central_command/gateway/executor.py central_command/gateway/gateway.py
git commit -m "Charter provenance: the drafter comes from the proposal row, not the args

executor.execute() never received the drafting agent, so _charter_update
stamped created_by from args['agent_id'] — the TARGET. Harmless while every
charter edit was self-proposed; a false record the moment a coach drafts for
someone else. The drafter is now passed server-side from proposal.agent_id and
stamped separately from the target, in the version row and the charter.updated
event. Existing rows are not rewritten: they are true of the sessions that
produced them."
```

---

### Task 2: The `charter-propose` pack

Adds **no new gated write** — `charter.update` is already the 7th gated write in the registry. What changes is who may hold it and for whom.

**Files:**
- Modify: `central_command/runtime/packs.py` (add to `PACKS`)
- Modify: `central_command/gateway/capabilities.py:122-134` (`charter.update` description)
- Test: `tests/test_packs.py` (add one test; the existing parity test covers the rest automatically)

**Interfaces:**
- Consumes: `packs.Pack`, `packs.GatedCapability`, the existing `propose_jira_update` tool.
- Produces: `packs.PACKS["charter-propose"]`, grantable by name.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_packs.py`:

```python
def test_charter_propose_pack_carries_the_existing_gated_write():
    """Stage 5a adds a PACK, not a capability: charter.update is already the
    registry's 7th gated write. A new pack here would be a governance change
    hiding as a plumbing change."""
    pack = packs.PACKS["charter-propose"]
    assert [c.name for c in pack.capabilities] == ["charter.update"]
    assert "charter.update" in gated_write_names()
    # The drafter is NOT an argument the agent writes (see executor.execute).
    assert "proposer" not in pack.capabilities[0].arguments
    assert "drafter" not in pack.capabilities[0].arguments
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_packs.py::test_charter_propose_pack_carries_the_existing_gated_write -v`
Expected: FAIL with `KeyError: 'charter-propose'`

- [ ] **Step 3: Add the pack**

In `central_command/runtime/packs.py`, add to the `PACKS` dict (after `"task-propose"`):

```python
    "charter-propose": Pack(
        name="charter-propose",
        description=(
            "Propose a governed charter edit for a teammate (or yourself), "
            "committed as a new version after the operator approves it."
        ),
        tool_names=("propose_jira_update",),
        guidance=(
            "COACHING: a charter is an agent's standing doctrine — every later "
            "run inherits it, so an edit outlives the session that proposed "
            "it. Change as LITTLE as possible: add or sharpen the rules the "
            "operator's feedback demands and keep every working section "
            "verbatim. `content` is the FULL revised charter text, not a diff "
            "and not a fragment; the operator reviews it as a diff before it "
            "takes effect. Never write a capability list into charter text — "
            "capability lists are GENERATED from the agent's grants. Cite "
            "every signal you acted on: kind='operator', "
            "source_ref='event:<id>', locator='event log', claim=<the "
            "feedback, QUOTED VERBATIM>. Both the quote and the event id are "
            "re-checked mechanically against the record, so never paraphrase "
            "and never cite an event you have not read."
        ),
        capabilities=(
            GatedCapability(
                name="charter.update",
                arguments=("{'agent_id': '<the agent whose charter changes>', "
                           "'content': '<the FULL revised charter text>', "
                           "'rationale': '<1-2 sentences on what changed and why>'}"),
                notes=("target_ref = {'system': 'central_command', 'id': '<agent_id>', "
                       "'read_version': 'v<current version>'}, reversibility = "
                       "'reversible'. `agent_id` is the SUBJECT of the edit — the "
                       "agent being coached, which may be another agent or "
                       "yourself. You never state who drafted it: the control "
                       "plane records that from the proposal itself."),
            ),
        ),
    ),
```

- [ ] **Step 4: Record the widening in the registry**

In `central_command/gateway/capabilities.py`, replace the `charter.update` `description` (lines 129-134) with:

```python
        arguments=["agent_id", "content", "rationale"],
        description=(
            "Commit an agent-PROPOSED charter edit as a new governed version. "
            "The version row carries the pedigree: proposed by the agent, "
            "approved by the operator — coaching on the record. `agent_id` is "
            "the SUBJECT of the edit, not the drafter (stage 5a): since the "
            "coach drafts edits for other agents, the two differ, and the "
            "drafter is supplied server-side from the proposal row — never as "
            "an argument the agent writes. A deliberate widening of the same "
            "shape as task.create naming an assignee, and the sharper one: it "
            "acts on how a teammate thinks, not on what is on its board."
        ),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_packs.py tests/test_governance.py -v`
Expected: PASS — including `test_pack_capabilities_match_the_registry` (charter.update is a registered gated write and all three required arguments appear in the hint) and `test_pack_tools_are_only_propose_and_read`.

- [ ] **Step 6: Commit**

```bash
git add central_command/runtime/packs.py central_command/gateway/capabilities.py tests/test_packs.py
git commit -m "charter-propose pack: an existing gated write, newly grantable

No new capability — charter.update has been the 7th gated write since D5. The
pack makes it grantable to the coach, and the registry entry now records the
widening: agent_id is the SUBJECT, and the drafter is server-side truth."
```

---

### Task 3: The `record-read` pack

The coach's reads over Central Command's own record. Decision 2: full read access, not scoped to a designated target — a coach that can only read the agent it was pointed at cannot answer "is this a pattern across the team?".

**Files:**
- Modify: `central_command/runtime/tools.py` (append five tools)
- Modify: `central_command/runtime/packs.py` (add to `PACKS`)
- Test: `tests/test_packs.py` (add one test)

**Interfaces:**
- Consumes: `repo.list_agents`, `repo.list_grants`, `repo.current_charter`, `repo.list_charter_versions`, `repo.list_proposals`, `repo.get_event`, `repo.list_events_of_kinds`, `run.gather_coaching_signals`, `tools._clip`.
- Produces: `tools.read_team_roster`, `tools.read_agent_charter`, `tools.read_charter_history`, `tools.read_agent_record`, `tools.read_record_event`; `packs.PACKS["record-read"]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_packs.py`:

```python
def test_record_read_pack_is_read_only():
    """The coach's reads over Central Command's own record — ungated, and holding
    no gated capability at all. A read pack that could propose would put a
    write behind a name that promises otherwise."""
    pack = packs.PACKS["record-read"]
    assert pack.capabilities == ()
    assert not any(t.startswith("propose_") for t in pack.tool_names)
    for tool_name in pack.tool_names:
        assert getattr(tools_mod, tool_name).__module__ == "central_command.runtime.tools"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_packs.py::test_record_read_pack_is_read_only -v`
Expected: FAIL with `KeyError: 'record-read'`

- [ ] **Step 3: Add the read tools**

Append to `central_command/runtime/tools.py` (after `search_knowledge_graph` and the `_clip` helper):

```python
# --- The record-read pack (stage 5a) -----------------------------------------
# Reads over Central Command's OWN record — the team's roster, charters, proposals
# and decisions. Ungated like every read, and best-effort like every read: a
# failing lookup degrades the run's context, never wedges it.
#
# These live in the runtime tier and reach the DB through `repo` only. They must
# never import the gateway (the trust boundary): everything here is history the
# control plane already wrote down.


async def read_team_roster(ctx: RunContext) -> str:
    """List the team: every agent's id, role, lifecycle status and capability
    grants. Use it to learn who exists before reading or citing anyone's record.
    """
    from central_command.db import repo

    try:
        rows = await repo.list_agents(include_retired=True)
    except Exception as e:  # noqa: BLE001
        return f"roster unavailable ({type(e).__name__})"
    lines = []
    for r in rows:
        try:
            grants = [g["pack"] for g in await repo.list_grants(r["id"])
                      if g["revoked_at"] is None]
        except Exception:  # noqa: BLE001
            grants = []
        lines.append(
            f"- {r['id']} ({r.get('status') or 'ACTIVE'}): {r.get('role') or ''}"
            f" | grants: {', '.join(grants) or 'none'}"
        )
    return "\n".join(lines) or "no agents on the roster"


async def read_agent_charter(ctx: RunContext, agent_id: str) -> str:
    """Read an agent's CURRENT governed charter, in full, with its version
    number. Read the charter you are about to edit — an edit written without it
    cannot 'keep every working section verbatim'."""
    from central_command.db import repo

    try:
        row = await repo.current_charter(agent_id)
    except Exception as e:  # noqa: BLE001
        return f"charter unavailable ({type(e).__name__})"
    if row is None:
        from central_command.runtime.agent import builtin_charter

        try:
            return f"{agent_id} charter (built-in v0):\n{builtin_charter(agent_id)}"
        except ValueError:
            return f"{agent_id} has no charter on record"
    return f"{agent_id} charter (v{row['version']}):\n{row['content']}"


async def read_charter_history(ctx: RunContext, agent_id: str) -> str:
    """An agent's charter versions: who drafted each, when, and why. Use it to
    see whether a rule you are about to add was already tried — or already
    reverted."""
    from central_command.db import repo

    try:
        rows = await repo.list_charter_versions(agent_id)
    except Exception as e:  # noqa: BLE001
        return f"charter history unavailable ({type(e).__name__})"
    if not rows:
        return f"{agent_id} has no governed charter versions (built-in v0 only)"
    lines = [
        f"- v{r['version']} ({r['status']}) by {r.get('created_by') or 'unknown'}"
        f" at {r.get('created_at')}: {r.get('rationale') or 'no rationale recorded'}"
        for r in rows
    ]
    return "\n".join(lines)


async def read_agent_record(ctx: RunContext, agent_id: str, limit: int = 20) -> str:
    """An agent's recent proposals and how the operator decided them —
    including rejection feedback verbatim, with the event id you must cite to
    quote it. This is the corpus a coaching claim is checked against."""
    from central_command.db import repo

    try:
        props = {p["id"]: p for p in await repo.list_proposals()
                 if p["agent_id"] == agent_id}
        decided = await repo.list_events_of_kinds(["proposal.decided"])
    except Exception as e:  # noqa: BLE001
        return f"record unavailable ({type(e).__name__})"
    lines = []
    for e in reversed(decided):
        if e["ref_id"] not in props:
            continue
        payload = e.get("payload") or {}
        note = f" — “{payload['feedback']}”" if payload.get("feedback") else ""
        lines.append(
            f"- (event:{e['id']}) {payload.get('verdict', '?')}: "
            f"“{props[e['ref_id']]['intent']}”{note}"
        )
        if len(lines) >= limit:
            break
    if not lines:
        return f"no decided proposals on record for {agent_id}"
    return _clip("\n".join(lines), 8000)


async def read_record_event(ctx: RunContext, event_id: int) -> str:
    """Read one event from the append-only log by id — the exact recorded
    words behind a signal. Read an event before citing it: a citation is
    re-checked against the record, and one that names an event that does not
    exist is flagged as unverified."""
    from central_command.db import repo

    try:
        row = await repo.get_event(int(event_id))
    except Exception as e:  # noqa: BLE001
        return f"event unavailable ({type(e).__name__})"
    if row is None:
        return (f"event:{event_id} does not exist in the record — do not cite it")
    return _clip(
        f"event:{row['id']} kind={row['kind']} ref={row['ref_id']} "
        f"actor={row['actor']} at={row['created_at']}\npayload: {row['payload']}",
        4000,
    )
```

- [ ] **Step 4: Add the pack**

In `central_command/runtime/packs.py`, add to `PACKS` (before `"charter-propose"`):

```python
    "record-read": Pack(
        name="record-read",
        description=(
            "Read Central Command's own record: the roster, any agent's charter "
            "and version history, its decided proposals and the operator's "
            "feedback, and any event by id."
        ),
        tool_names=("read_team_roster", "read_agent_charter", "read_charter_history",
                    "read_agent_record", "read_record_event"),
        guidance=(
            "READING THE RECORD: you have full read access to the team's "
            "record, and a selection you are given is context, not a fence — "
            "you may go and read more when it changes your judgment. What you "
            "may NOT do is cite something you have not read: every citation is "
            "re-checked against the record, and a citation the operator did "
            "not select is marked `discovered` for them at review time. A "
            "discovered citation is verified as REAL, never as representative "
            "— if you reach past what the operator chose, say plainly in your "
            "rationale why it matters."
        ),
    ),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_packs.py tests/test_governance.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add central_command/runtime/tools.py central_command/runtime/packs.py tests/test_packs.py
git commit -m "record-read pack: the first read capability over our own record

Five ungated reads — roster, charter, charter history, decided proposals with
the operator's feedback and its event id, and any event by id. Full access, not
scoped to one target (Decision 2): a coach that can only read the agent it was
pointed at cannot see a pattern across the team."
```

---

### Task 4: Hire the coach

Both a hire on the live database and a `schema.sql` seed. The live DB gets it by seed-on-boot; a fresh database must reproduce the roster or it boots a team missing a member.

**Files:**
- Create: `central_command/runtime/coach_profile.py`
- Modify: `central_command/runtime/roster.py:41-99` (`SEED`)
- Modify: `central_command/runtime/agent.py:98-121` (`builtin_charter`)
- Modify: `central_command/db/schema.sql` (roster seed + grants seed)
- Test: `tests/test_coach_agent.py` (create)

**Interfaces:**
- Consumes: `roster.AgentDef`, `packs.PACKS["record-read"]`, `packs.PACKS["charter-propose"]`, `packs.PACKS["ask-operator"]`.
- Produces: `coach_profile.CHARTER`; roster id `"coach"`; grants `record-read`, `charter-propose`, `ask-operator`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_coach_agent.py`:

```python
"""The coach is a real rostered agent (stage 5a, Decision 1).

Before this, coaching ran on `Agent(name=f"{agent_id}-coach")` — ephemeral,
unrostered, with a two-sentence hardcoded prompt: no roster row, no governed
charter, and no way to coach it. That was an undocumented deviation from the
uniform-management rule sitting in the tree. Hiring it converts the deviation
into a governed team member, and is what makes "work with the coach" (5c) and
"thumbs-down goes somewhere" (5d) name something concrete.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from central_command.config import settings
from central_command.db import repo


def _pg_available() -> bool:
    async def probe() -> bool:
        try:
            conn = await repo._conn()
            await conn.close()
            return True
        except Exception:  # noqa: BLE001
            return False

    try:
        return asyncio.run(probe())
    except Exception:  # noqa: BLE001
        return False


# --- no database needed -------------------------------------------------------


def test_the_coach_is_in_the_founding_seed_with_a_builtin_charter():
    from central_command.runtime.agent import builtin_charter
    from central_command.runtime.roster import SEED

    coach = next((a for a in SEED if a.id == "coach"), None)
    assert coach is not None, "coach missing from the founding SEED"
    assert coach.role.strip()
    assert coach.deviations.strip(), (
        "coach is not consultable — uniform management requires a recorded reason"
    )
    assert builtin_charter("coach").strip()


def test_the_coach_charter_writes_no_capability_list():
    """Capability lists are GENERATED from grants (D25). A hand-written one in
    charter text is exactly the drift the 2026-07-22 incident cost us."""
    from central_command.runtime.coach_profile import CHARTER

    assert "capability = '" not in CHARTER
    assert "charter.update" not in CHARTER


def test_the_coach_is_seeded_in_schema_sql():
    """A fresh database must reproduce the roster — and the founding INSERT
    must not seed `role`, or the guarded UPDATE below it silently no-ops (the
    2026-07-26 clean-database failure)."""
    from pathlib import Path

    schema = (Path(__file__).parents[1] / "central_command" / "db" / "schema.sql").read_text()
    assert "('coach'," in schema
    assert "where id = 'coach' and role = ''" in schema
    for pack in ("record-read", "charter-propose"):
        assert f"('coach', '{pack}'" in schema or f"('coach',    '{pack}'" in schema, (
            f"coach is not granted {pack} in schema.sql"
        )


# --- database -----------------------------------------------------------------

pg = pytest.mark.skipif(
    not _pg_available(), reason=f"no Postgres at {settings.database_url}"
)


@pg
async def test_the_live_roster_holds_the_coach_with_its_grants():
    from central_command.runtime.packs import granted_packs
    from central_command.runtime.roster import roster_ids

    assert "coach" in await roster_ids()
    assert set(await granted_packs("coach")) >= {"record-read", "charter-propose"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_coach_agent.py -v`
Expected: FAIL — `coach missing from the founding SEED`, `ModuleNotFoundError: central_command.runtime.coach_profile`, and the schema assertion.

- [ ] **Step 3: Write the coach's built-in charter**

Create `central_command/runtime/coach_profile.py`:

```python
"""The coach's built-in ("v0") charter — the agent that drafts governed charter
edits for the rest of the team.

Coaching used to run on an ephemeral `Agent(name=f"{agent_id}-coach")` built per
request: no roster row, no governed charter, nothing to coach. Since stage 5a
the coach is a rostered agent whose CURRENT governed charter is its system
prompt, so coaching the coach actually changes how it runs.

No capability list here — that section is GENERATED from the coach's grants at
run start (D25).
"""

CHARTER = (
    "You are the team's coach. Your job is to turn the operator's feedback "
    "about a teammate into a minimal, well-argued edit to that teammate's "
    "governing charter — and nothing else.\n\n"
    "WHAT A CHARTER IS: an agent's standing doctrine. Every future run of that "
    "agent inherits it, so a rule you add outlives the session that added it, "
    "and a sentence written loosely becomes a habit. Change as LITTLE as "
    "possible: add or sharpen exactly the rules the feedback demands, and keep "
    "every working section verbatim. You propose the FULL revised charter "
    "text; the operator reviews it as a diff.\n\n"
    "WHO YOU EDIT: the agent named in the request — not yourself, unless the "
    "request names you. You never state who drafted the edit; the control "
    "plane records that from the proposal itself.\n\n"
    "THE SIGNALS: the operator curates what feeds a session and their "
    "selection is the foreground — start there, and treat it as what they "
    "actually want addressed. You also have full read access to the team's "
    "record and may read further when it changes your judgment: a pattern "
    "across several rejections is worth more than any one of them. Reaching "
    "past the selection is allowed; doing it silently is not — say in your "
    "rationale what you went and found, and why it matters.\n\n"
    "CITATIONS: cite every signal you acted on as evidence — kind='operator', "
    "source_ref='event:<id>', locator='event log', claim=<the feedback, QUOTED "
    "VERBATIM>. Both the quote and the event id are re-checked mechanically "
    "against the record, and citations the operator did not select are marked "
    "`discovered` for them at review time. Never paraphrase inside a quote, "
    "and never cite an event you have not read — an event id that resolves to "
    "nothing is flagged as unverified and lands on the record that way.\n\n"
    "WHEN NOT TO PROPOSE: feedback about one bad call is not automatically a "
    "durable rule. If what you are given is a one-off, or too vague to encode "
    "without inventing the operator's intent, say so in plain text and propose "
    "nothing — a charter that accumulates a rule per incident becomes a "
    "checklist nobody can follow. Coaching only on failures drifts an agent "
    "toward timidity: when the record shows an agent was right to act, that is "
    "worth encoding too.\n\n"
    "TRUST: the operator is the human team lead and their word is trusted "
    "ground truth; it needs no corroboration. Everything else — including your "
    "own account of what the operator said — is a claim that gets checked."
)
```

- [ ] **Step 4: Add the coach to the roster and the built-in charter lookup**

In `central_command/runtime/roster.py`, append to the `SEED` tuple (after the `litellm-manager` entry, before the closing `)`):

```python
    AgentDef(
        id="coach",
        name="Coach",
        role="draft governed charter edits from the operator's coaching signals",
        taskable=True,
        consultable=False,
        deviations=(
            "not consultable: coaching is operator-directed work on a "
            "teammate's governing document, not a knowledge source other "
            "agents query mid-run — an agent asking to have another agent's "
            "charter edited is a request to the operator, not a consult"
        ),
        conversational=True,  # 5c: the operator works with it iteratively
    ),
```

In `central_command/runtime/agent.py`, add to `builtin_charter` (before the final `raise`):

```python
    if agent_id == "coach":
        from central_command.runtime.coach_profile import CHARTER as c

        return c
```

- [ ] **Step 5: Seed the coach in schema.sql**

In `central_command/db/schema.sql`, add `coach` to the founding roster `INSERT` — note it seeds **no `role`**, so the guarded `UPDATE` below still fires on a fresh database:

```sql
insert into agent (id, name, model, charter) values
    ('inbox-triage',    'Inbox Triage',    'inbox-triage',    'triage email into gated Jira + knowledge-graph proposals'),
    ('jira-expert',     'Jira Expert',     'jira-expert',     'Jira hygiene: advisory consultation + operator-tasked changes'),
    ('auditor',         'Auditor',         'auditor',         'independently re-derive no-action claims before they close'),
    ('orchestrator',    'Orchestrator',    'orchestrator',    'orchestrate multi-agent projects; route unassigned tasks (D7)'),
    ('litellm-manager', 'LiteLLM Manager', 'litellm-manager', 'manage the self-hosted LiteLLM proxy: models + provider mappings (spend read-only)'),
    ('coach',           'Coach',           'coach',           'draft governed charter edits from the operator''s coaching signals')
on conflict (id) do nothing;
```

And add its guarded role/flags migration immediately after the `litellm-manager` one:

```sql
update agent set role = 'draft governed charter edits from the operator''s coaching signals',
    taskable = true, consultable = false, conversational = true,
    deviations = 'not consultable: coaching is operator-directed work on a teammate''s governing document, not a knowledge source other agents query mid-run — an agent asking to have another agent''s charter edited is a request to the operator, not a consult'
    where id = 'coach' and role = '';
```

Append a new grants seed at the end of the file:

```sql
-- Stage 5a (2026-07-27, the operator): the coach is a rostered agent, hired. Coaching
-- previously ran on an ephemeral per-target Agent(...) with a hardcoded prompt
-- — no roster row, no governed charter, nothing to coach. Its surface is the
-- whole job and no more: read the team's record, propose a charter edit, ask
-- the operator when a request is ambiguous. No Jira, no graph, no LiteLLM —
-- a grant an agent cannot use is a lie on its profile.
insert into agent_grant (agent_id, pack, granted_by) values
    ('coach', 'record-read',     'seed:stage5a'),
    ('coach', 'charter-propose', 'seed:stage5a'),
    ('coach', 'ask-operator',    'seed:stage5a')
on conflict (agent_id, pack) do nothing;
```

- [ ] **Step 6: Apply the schema to the running database**

Run: `.venv/bin/python -c "
import asyncio, pathlib
from central_command.db import repo
async def main():
    sql = pathlib.Path('central_command/db/schema.sql').read_text()
    conn = await repo._conn()
    try:
        await conn.execute(sql)
    finally:
        await conn.close()
    print('schema applied')
asyncio.run(main())
"`

Expected: `schema applied` (the schema is idempotent — `create table if not exists`, `on conflict do nothing`, guarded updates).

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_coach_agent.py tests/test_governance.py -v`
Expected: PASS — including `test_every_roster_agent_is_uniformly_managed`, `test_the_founding_seed_is_uniformly_managed` and `test_the_seed_matches_the_schema_seeds`.

- [ ] **Step 8: Commit**

```bash
git add central_command/runtime/coach_profile.py central_command/runtime/roster.py central_command/runtime/agent.py central_command/db/schema.sql tests/test_coach_agent.py
git commit -m "Hire the coach: a rostered agent, not an ephemeral one

Coaching ran on Agent(name=f'{agent_id}-coach') — built per request, no roster
row, no governed charter, no way to coach it. An undocumented deviation from
the uniform-management rule, sitting in the tree. The coach is now a founding
roster member with a built-in v0 charter and three grants (record-read,
charter-propose, ask-operator), seeded in schema.sql so a fresh database
reproduces the team."
```

---

### Task 5: `run_coach` rebuilt — the governed charter IS the prompt

The test that makes Decision 5 real. If coaching the coach cannot change how it runs, Approach 2 was built by accident.

**Files:**
- Modify: `central_command/runtime/run.py:245-278` (`_coach_prompt`), `:281-357` (`run_coach`)
- Test: `tests/test_coach_agent.py` (extend)

**Interfaces:**
- Consumes: `agent.build_hired_agent`, `agent.load_charter`, `agent.builtin_charter`, `packs.granted_packs`, `skills.loadout`, `proposals.park_proposal`.
- Produces: `run_coach(agent_id, model=None, signal_ids=None, note=None, note_event_id=None, signals=None) -> dict` — same keys as before plus `"target"`. The parked proposal's `agent_id` is now `"coach"`.

**Note on the builder:** do **not** use `build_agent_for("coach")` here. That is the resume-path factory and passes no charter — `build_hired_agent(charter="")` falls through `build_charter`'s `content or CHARTER` to the inbox-triage charter. Harmless on resume (the persisted history carries the original prompt) and wrong for a fresh run. Load the charter explicitly, exactly as `run_task` does.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_coach_agent.py`:

```python
@pg
async def test_the_coachs_governed_charter_is_its_system_prompt():
    """Decision 5, made executable. If a coached charter cannot change how the
    coach runs, hiring it bought nothing but a row in a table."""
    from central_command.runtime.run import build_coach_agent

    marker = "COACH-MARKER-" + uuid.uuid4().hex[:8]
    await repo.save_charter_version(
        "coach", f"You are the team's coach.\n\n{marker}", "prompt test",
        created_by="operator",
    )
    agent = build_coach_agent(await _coach_loadout())

    prompt = "\n".join(agent._system_prompts)
    assert marker in prompt, "the governed charter is not the coach's prompt"
    # ...and the generated capability section rode along, proving the grants
    # were assembled rather than a bare string being passed through.
    assert "GRANTED CAPABILITIES" in prompt
    assert "charter.update" in prompt
    # ...and no other agent's charter leaked in via build_charter's fallback.
    assert "You are an inbox-triage agent" not in prompt


async def _coach_loadout():
    from central_command.runtime.run import coach_loadout

    return await coach_loadout()


@pg
async def test_a_coach_run_parks_the_proposal_under_the_coach():
    """The proposal row identifies the agent that GENERATED it. The target is
    a property of the action, not of the proposal — which is what makes the
    executor's drafter/target split mean anything."""
    from central_command.runtime.run import run_coach
    from central_command.runtime.spike_model import make_coach_model

    target = "coach-target-" + uuid.uuid4().hex[:6]
    await repo.upsert_agent(target, target, "demo", "c")
    await repo.save_charter_version(target, "CHARTER for a coaching test.",
                                    "seed", created_by="operator")
    out = await run_coach(
        target, model=make_coach_model(), note="Check for an existing link first.",
        note_event_id=(await _note_event(target))["id"],
    )

    assert out["deferred"] is True
    row = await repo.load_proposal(out["proposal_id"])
    assert row["agent_id"] == "coach", "the drafter owns the proposal row"
    assert row["actions"][0]["arguments"]["agent_id"] == target, (
        "the target belongs in the action arguments"
    )
    assert out["target"] == target


async def _note_event(target: str) -> dict:
    from central_command import events

    return await events.emit(
        "agent.coach_requested", ref_id=target,
        payload={"signal_ids": [], "note": "Check for an existing link first."},
        actor="operator",
    )
```

Add the demo-mode pin at the top of the database section of `tests/test_coach_agent.py`:

```python
@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_coach_agent.py -v -k "system_prompt or parks"`
Expected: FAIL with `ImportError: cannot import name 'build_coach_agent' from 'central_command.runtime.run'`

- [ ] **Step 3: Rewrite the coach prompt as third-person**

In `central_command/runtime/run.py`, replace the tail of `_coach_prompt` (lines 245-278 — the string beginning `"YOUR CURRENT CHARTER (v{version})..."`) so the prompt addresses the coach about a *teammate*. The whole function becomes:

```python
def _coach_prompt(agent_id: str, charter: str, version: int, signals: list[dict]) -> str:
    lines = [_signal_line(i, s) for i, s in enumerate(signals)]
    return (
        f"COACHING SESSION for the agent '{agent_id}'.\n\n"
        f"{agent_id}'s CURRENT CHARTER (v{version}):\n---\n{charter}\n---\n\n"
        "THE OPERATOR'S CURATED FEEDBACK (their selection — the foreground):\n"
        + "\n".join(lines) + "\n\n"
        "If this feedback implies a durable rule the charter is missing (or one "
        "it states badly), call propose_jira_update with ONE action:\n"
        f"  capability = 'charter.update'\n"
        f"  arguments  = {{'agent_id': '{agent_id}', 'content': <the FULL revised "
        "charter text>, 'rationale': <1-2 sentences on what changed and why>}\n"
        f"  target_ref = {{'system': 'central_command', 'id': '{agent_id}', "
        f"'read_version': 'v{version}'}}, reversibility = 'reversible'.\n"
        "Change as LITTLE as possible: add or sharpen the rules the feedback "
        "demands, keep every working section verbatim. Cite each signal you "
        "acted on, quoting it exactly and naming its event id — the quote and "
        "the id are both re-checked against the record. You may read further "
        "into the record than this selection; anything you cite from outside it "
        "is marked `discovered` for the operator. If the feedback gives you "
        "nothing durable to encode, reply in plain text saying why."
    )
```

- [ ] **Step 4: Rebuild `run_coach` on the standard builder**

In `central_command/runtime/run.py`, replace `run_coach` (lines 281-357) with:

```python
COACH_ID = "coach"


async def coach_loadout() -> dict:
    """Everything a coach run needs from the control plane: the coach's CURRENT
    governed charter (falling back to its built-in v0), its pack grants and its
    skills. Loaded in one place so the run path and the tests build the same
    agent."""
    from central_command.runtime.agent import builtin_charter, load_charter
    from central_command.runtime import skills as skills_mod
    from central_command.runtime.packs import granted_packs

    charter = await load_charter(COACH_ID) or builtin_charter(COACH_ID)
    packs = await granted_packs(COACH_ID)
    caps, catalog = await skills_mod.loadout(COACH_ID)
    return {"charter": charter, "packs": packs, "capabilities": caps,
            "skills_catalog": catalog}


def build_coach_agent(loadout: dict, model=None):
    """The coach, assembled from its grants with its GOVERNED CHARTER as its
    system prompt (Decision 5).

    Not `build_agent_for`: that is the resume-path factory and passes no
    charter, so `build_charter("")` would fall through to the inbox-triage
    charter. A fresh run must load the real one — this is the difference
    between coaching the coach working and being a document nobody reads.
    """
    from central_command.runtime.agent import build_hired_agent

    return build_hired_agent(
        COACH_ID, model=model, charter=loadout["charter"], packs=loadout["packs"],
        capabilities=loadout["capabilities"], skills_catalog=loadout["skills_catalog"],
    )


async def run_coach(
    agent_id: str,
    model=None,
    signal_ids: list[int] | None = None,
    note: str | None = None,
    note_event_id: int | None = None,
    signals: list[dict] | None = None,
) -> dict:
    """D5: the COACH proposes a charter edit for `agent_id` from OPERATOR-CURATED
    feedback. The operator reviews the candidate signals first and selects which
    ones feed the session (`signal_ids` — event ids), and/or supplies their own
    feedback (`note`, already on the log as `note_event_id` so the coached
    charter can cite it). The edit is a PROPOSAL like any other — Decisions
    Inbox, approval, executor commit with pedigree, revert available.

    Since stage 5a the drafter is the rostered `coach`, not an ephemeral agent
    wearing the target's name: the session and the proposal row belong to the
    coach (the agent that generated them), and `agent_id` — the target — lives
    in the action arguments, where the Executor reads it.
    """
    # Callers may supply precomputed candidates (the auditor's signals come
    # from the gateway-tier agreement report, which this module must not
    # import); the operator's selection filter applies either way.
    if signals is None:
        signals = await gather_coaching_signals(agent_id)
    if signal_ids is not None:
        wanted = set(signal_ids)
        signals = [s for s in signals if s["event_id"] in wanted]
    if note and note.strip():
        signals.append({"kind": "note", "event_id": note_event_id or 0,
                        "feedback": note.strip()})
    if not signals:
        return {"deferred": False, "output": "no coaching signals selected",
                "signals": 0, "target": agent_id}

    current = await repo.current_charter(agent_id)
    if current is None:
        from central_command.runtime.agent import builtin_charter

        content, version = builtin_charter(agent_id), 0
    else:
        content, version = current["content"], current["version"]

    coach = build_coach_agent(await coach_loadout(), model=model)
    session_id = "sess_" + uuid.uuid4().hex[:12]
    result = await _run_live(
        coach,
        _coach_prompt(agent_id, content, version, signals),
        model=None,
        deps=TriageDeps(agent_id=COACH_ID, session_id=session_id),
        agent_id=COACH_ID,
        session_id=session_id,
    )
    tokens = _total_tokens(result)

    call = extract_deferred(result)
    if call is None:
        await repo.mark_session_done(session_id)
        return {"deferred": False, "output": str(result.output), "target": agent_id,
                "signals": len(signals), "tokens": tokens, "session_id": session_id}

    parked = await park_proposal(
        session_id=session_id, agent_id=COACH_ID, result=result, call=call,
        evidence=await _verified_evidence(proposal_from_call(call).evidence, signals),
        context={"coached": True, "signals": len(signals), "target": agent_id},
    )
    return {"deferred": True, "session_id": session_id, "target": agent_id,
            "proposal_id": parked["proposal_id"], "intent": parked["proposal"].intent,
            "signals": len(signals), "tokens": tokens}
```

Remove the now-unused `from pydantic_ai import Agent, DeferredToolRequests` and `from central_command.runtime.tools import declare_gap, propose_jira_update` imports that lived inside the old `run_coach` body, and the `from central_command.runtime.models import resolve_model as _resolve` import if nothing else in the function uses it.

Note `_verified_evidence` is awaited here — it becomes async in Task 6. Until then it is sync; **make Task 6 the very next task**, or temporarily call it without `await`. If executing tasks strictly in order, write `evidence=_verified_evidence(...)` here and add the `await` in Task 6 Step 4.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_coach_agent.py tests/test_coach_loop.py tests/test_proposal_created.py -v`
Expected: PASS.

`tests/test_coach_loop.py` may need one adjustment: assertions that read the coached session's `agent_id` now find `"coach"`. If a test asserts the parked proposal belongs to the target, update it to assert `row["agent_id"] == "coach"` and `row["actions"][0]["arguments"]["agent_id"] == <target>` — that is the intended change, not a regression. Do not weaken an assertion to make it pass; state in the commit which assertion changed and why.

- [ ] **Step 6: Commit**

```bash
git add central_command/runtime/run.py tests/test_coach_agent.py tests/test_coach_loop.py
git commit -m "run_coach runs the rostered coach, with its charter as its prompt

The bespoke Agent(...) construction is gone: the coach is assembled from its
grants through build_hired_agent with its CURRENT governed charter as the
system prompt, so coaching the coach changes how it runs. The session and the
proposal row now belong to the coach — the agent that generated them — and the
target lives in the action arguments, which is what gives the executor's
drafter/target split something to separate."
```

---

### Task 6: Citations — selected, discovered, unresolved

Once the coach reads freely it can cite something real the operator did not tick. Today's check calls that a fabrication.

**Files:**
- Modify: `central_command/runtime/run.py:219-251` (`_verified_evidence`)
- Modify: `tests/test_coach_evidence.py` (the pure suite gains a stubbed `get_event`)

**Interfaces:**
- Consumes: `repo.get_event(event_id) -> dict | None`, `contract.claim_supported`.
- Produces: `async _verified_evidence(evidence, signals) -> list[dict]`. Each operator entry gains `citation` ∈ `{"selected", "discovered", "unresolved"}` alongside `claim_matches_source`.

- [ ] **Step 1: Write the failing tests**

Rewrite `tests/test_coach_evidence.py`'s body to await the checker and stub the record. Keep the module docstring, `SIGNALS` and `_ev` as they are; add at the top of the test section:

```python
import pytest

from central_command.db import repo

# The record the coach may have read past its selection into. Stubbed so this
# suite stays pure — no Postgres, no model, always in the offline run.
RECORD = {
    7788: {"id": 7788, "kind": "proposal.decided", "ref_id": "prop_x",
           "actor": "operator", "created_at": None,
           "payload": {"verdict": "rejected",
                       "feedback": "stop proposing dates you cannot source"}},
}


@pytest.fixture(autouse=True)
def stub_record(monkeypatch):
    async def _get_event(event_id: int):
        return RECORD.get(int(event_id))

    monkeypatch.setattr(repo, "get_event", _get_event)
```

Then make every existing test `async def` and `await` the checker. The changed and new cases:

```python
async def test_a_selected_citation_is_marked_selected():
    out = await _verified_evidence([_ev(SIGNALS[0]["feedback"], "event:2453")], SIGNALS)
    assert out[0]["claim_matches_source"] is True
    assert out[0]["citation"] == "selected"
    assert "event:2453" in out[0]["locator"]


async def test_a_real_event_outside_the_selection_verifies_and_is_marked_discovered():
    """Decision 4: the coach has full read access, so citing something the
    operator did not tick is legitimate — and still checked, against the
    record. Verified as REAL, never as representative: the marker is what
    makes the reach visible at approval time."""
    out = await _verified_evidence(
        [_ev("stop proposing dates you cannot source", "event:7788")], SIGNALS
    )
    assert out[0]["claim_matches_source"] is True
    assert out[0]["citation"] == "discovered"
    assert "discovered" in out[0]["locator"]


async def test_a_paraphrase_of_a_discovered_event_still_flags():
    out = await _verified_evidence(
        [_ev("the operator dislikes unsourced dates", "event:7788")], SIGNALS
    )
    assert out[0]["claim_matches_source"] is False
    assert out[0]["citation"] == "discovered"


async def test_a_citation_that_resolves_to_no_event_is_flagged_false():
    """The hallucination case: an event id that exists nowhere. A check RAN
    and the pointer resolved to nothing, so this is False — never None, which
    means NOT CHECKED and would render as unremarkable in the Inbox."""
    out = await _verified_evidence([_ev("Some feedback entirely", "event:1317")], SIGNALS)
    assert out[0]["claim_matches_source"] is False
    assert out[0]["citation"] == "unresolved"
    assert "does not exist" in out[0]["locator"]
    assert "event:1317" in out[0]["locator"]


async def test_an_unparseable_pointer_is_flagged_never_guessed():
    for ref in ("the event log", "", "event:abc"):
        out = await _verified_evidence([_ev("There is already a jira task", ref)], SIGNALS)
        assert out[0]["claim_matches_source"] is False, ref
        assert out[0]["citation"] == "unresolved", ref
        assert "UNVERIFIABLE" in out[0]["locator"], ref


async def test_non_operator_evidence_is_left_alone():
    out = await _verified_evidence(
        [_ev("TASKS-17 relates to TASKS-4", "TASKS-17", kind="jira_state")], SIGNALS
    )
    assert "claim_matches_source" not in out[0]
    assert "citation" not in out[0]
    assert out[0]["locator"] == "event log"  # untouched


async def test_none_is_never_written_by_this_checker():
    """`claim_matches_source: None` means NOT CHECKED — it is what the Inbox
    reads as 'nothing was verified'. Every operator entry this function
    touches must come out True or False."""
    out = await _verified_evidence(
        [_ev(SIGNALS[0]["feedback"], "event:2453"),
         _ev("invented", "event:99999"),
         _ev("stop proposing dates you cannot source", "event:7788")],
        SIGNALS,
    )
    assert [e["claim_matches_source"] for e in out] == [True, False, True]
    assert all(e["claim_matches_source"] is not None for e in out)
```

Keep `test_quote_survives_punctuation_and_case_drift_but_not_paraphrase`, `test_the_operators_own_note_is_checkable_like_any_other_signal`, `test_every_operator_entry_is_checked_not_just_the_first`, `test_an_empty_claim_never_passes` and `test_both_tiers_share_one_checker` — make the first four `async` and `await` the call; the last needs no change. Delete `test_citing_a_signal_the_session_was_never_fed_is_flagged`: its premise (any unfed pointer is a fabrication) is exactly what Decision 4 retires, and it is replaced by the three cases above.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_coach_evidence.py -v`
Expected: FAIL — `TypeError: object list can't be used in 'await' expression`, and `KeyError: 'citation'`.

- [ ] **Step 3: Widen the checker to the record**

In `central_command/runtime/run.py`, replace `_verified_evidence` (lines 219-251) with:

```python
def _event_source_text(row: dict) -> str:
    """The searchable text of a recorded event. `claim_supported` normalizes
    away punctuation, so the serialized payload works as the containment
    corpus — and using the WHOLE payload means a quote is checked against what
    the record actually holds, not against the one field we thought to read
    (the 2026-07-25 blank-pane bug, where only `feedback` was consulted and
    `note` signals verified against nothing)."""
    import json

    return json.dumps(row.get("payload") or {}, ensure_ascii=False, default=str)


async def _verified_evidence(evidence, signals: list[dict]) -> list[dict]:
    """The operator's words are trusted; the agent's ACCOUNT of them is not.

    The redraft path has the gateway re-check quotes against the one recorded
    rejection feedback. A coach run has neither of those givens: the agent
    supplies BOTH the quote and the pointer, across many signals — so both are
    checked here.

    Since stage 5a the coach reads the record freely (Decision 2), so the
    corpus is wider than the operator's selection and a citation carries a
    marker saying which it came from:

      selected   — in what the operator ticked; verified against it.
      discovered — a real event the coach went and found; verified against the
                   record. Verified as REAL, not as representative: a genuine
                   rejection from months ago the operator considers settled
                   still passes, and the marker is what makes that visible at
                   approval time.
      unresolved — the pointer names no event, or an event that does not exist.
                   FALSE, never None: a check ran and failed. `None` means NOT
                   CHECKED and renders as unremarkable in the Inbox, which is
                   how an unverified quote reached standing doctrine once
                   already (2026-07-25).

    The stakes are why this exists: a coached charter becomes standing doctrine
    that every later run inherits, so a drifted quote outlives the proposal
    that carried it.
    """
    by_id = {s["event_id"]: s.get("feedback") or "" for s in signals}
    out = []
    for e in evidence:
        d = e.model_dump()
        if d.get("kind") == "operator":
            cited = _cited_event_id(d.get("source_ref"))
            if cited is None:
                d["claim_matches_source"] = False
                d["citation"] = "unresolved"
                d["locator"] = (
                    f"UNVERIFIABLE — {d.get('source_ref')!r} does not name an event"
                )
            elif cited in by_id:
                d["claim_matches_source"] = claim_supported(
                    d.get("claim") or "", by_id[cited]
                )
                d["citation"] = "selected"
                d["locator"] = f"coaching signal event:{cited} (selected by the operator)"
            else:
                try:
                    row = await repo.get_event(cited)
                except Exception:  # noqa: BLE001 — a lookup we cannot do is not a pass
                    row = None
                if row is None:
                    d["claim_matches_source"] = False
                    d["citation"] = "unresolved"
                    d["locator"] = (
                        f"UNVERIFIABLE — event:{cited} does not exist in the record"
                    )
                else:
                    d["claim_matches_source"] = claim_supported(
                        d.get("claim") or "", _event_source_text(row)
                    )
                    d["citation"] = "discovered"
                    d["locator"] = (
                        f"record event:{cited} (discovered by the coach — not "
                        "part of the operator's selection)"
                    )
        out.append(d)
    return out
```

- [ ] **Step 4: Await it at the one call site**

In `run_coach`, ensure the `park_proposal` call reads:

```python
        evidence=await _verified_evidence(proposal_from_call(call).evidence, signals),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_coach_evidence.py tests/test_coach_agent.py tests/test_coach_loop.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add central_command/runtime/run.py tests/test_coach_evidence.py
git commit -m "Citations verify against the record, marked selected or discovered

Decision 4: the coach reads freely, so a citation the operator did not tick is
legitimate — and still checked, now against the cited event in the record
rather than a string built from the selection. Each citation carries where it
came from, shown at approval time. A pointer that resolves to no event is
False, not None: a check ran and failed, and None means NOT CHECKED."
```

---

### Task 7: The Inbox shows where a citation came from

**Files:**
- Modify: `web/src/features/decisions/types.ts:34-40`
- Modify: `web/src/features/decisions/DecisionsView.tsx:417-429`

**Interfaces:**
- Consumes: the proposal-detail route, which passes evidence dicts through whole — `claim_matches_source` and `citation` already reach the client (`routes.py:1319-1330` only *adds* `source_feedback`).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Extend the evidence type**

In `web/src/features/decisions/types.ts`, replace the `ProposalEvidence` interface:

```ts
export interface ProposalEvidence {
  kind: string;
  claim?: string;
  source_ref?: string | Record<string, unknown>;
  /** Re-derived from the event log for operator evidence — never the agent's copy. */
  source_feedback?: string;
  /** Was the agent's quote found in the recorded source? `undefined`/null means
   *  NOT CHECKED — never "checked and inconclusive". */
  claim_matches_source?: boolean | null;
  /** Where a coaching citation came from (stage 5a): the operator's selection,
   *  or the record the coach read past it into. */
  citation?: 'selected' | 'discovered' | 'unresolved';
}
```

- [ ] **Step 2: Render the marker and the verification result**

In `web/src/features/decisions/DecisionsView.tsx`, replace the evidence block at lines 417-429:

```tsx
        {(detail.evidence ?? []).map((e, i) => (
          <div key={i} className="mb-2 rounded-lg border border-border/40 p-2.5">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <span className="cockpit-badge">{e.kind}</span>
              {e.source_ref != null && <span className="truncate font-mono text-[0.667rem] text-muted-foreground">{refLabel(e.source_ref)}</span>}
              {e.citation === 'discovered' && (
                <span className="cockpit-badge" title="The coach found this in the record; you did not select it. Verified as real, not as representative.">discovered</span>
              )}
              {e.citation === 'selected' && (
                <span className="cockpit-badge" title="From the signals you selected for this session.">selected</span>
              )}
              {e.claim_matches_source === false && (
                <span className="cockpit-badge text-destructive" title="The quote was not found in the recorded source, or the cited event does not exist.">unverified quote</span>
              )}
            </div>
            {e.claim && <p className="text-[0.8rem] leading-relaxed text-foreground/85">{e.claim}</p>}
            {e.source_feedback && (
              <p className="mt-1 border-l-2 border-primary/40 pl-2 text-[0.733rem] italic text-foreground/70">
                recorded feedback: “{e.source_feedback}”
              </p>
            )}
          </div>
```

- [ ] **Step 3: Build the cockpit to verify it compiles**

Run: `cd web && npm run build`
Expected: build succeeds with no TypeScript errors.

- [ ] **Step 4: Commit**

```bash
git add web/src/features/decisions/types.ts web/src/features/decisions/DecisionsView.tsx
git commit -m "Inbox: show whether a coaching citation was selected or discovered

Decision 4 makes reaching past the operator's selection legitimate, so the
review surface has to show when it happened — and a failed quote check, which
the Inbox never rendered at all."
```

---

### Task 8: Record the change

**Files:**
- Modify: `docs/STATUS.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the gotchas that will bite the next session**

In `CLAUDE.md`, add to the "Conventions & gotchas" list:

```markdown
- **A proposal's `agent_id` is the DRAFTER, never the subject.** Since stage 5a
  the coach drafts charter edits for other agents, so the two differ: the
  proposal row (and its session, its Inbox attribution, its throttle count)
  belongs to the agent that generated it, and the target lives in the action
  arguments where the Executor reads it. `executor.execute()` takes `proposer`
  from the gateway for exactly this reason — an actor an agent can write into
  its own args is an agent-authored claim about provenance. Existing
  charter-version rows were NOT rewritten: they are true of the sessions that
  produced them.
- **A fresh run must load the charter; `build_agent_for` does not.** It is the
  RESUME factory and passes no charter, so `build_hired_agent(charter="")`
  falls through `build_charter`'s `content or CHARTER` to the inbox-triage
  charter. Harmless on resume (the persisted history carries the original
  prompt) and wrong anywhere else — `run_task` and `run_coach` load the real
  one. If you add a third fresh-run path, load the charter.
```

- [ ] **Step 2: Update STATUS.md**

Add a stage-5a entry to `docs/STATUS.md` recording: the coach is hired (roster + built-in v0 charter + three grants, seeded in `schema.sql`); `run_coach` runs it through `build_hired_agent` with its governed charter as its system prompt; the proposal row belongs to the drafter; the executor stamps drafter and target separately and existing rows are untouched; citations carry `selected`/`discovered`/`unresolved` and an unresolved pointer is `False`. Note what is **not** built: 5b, 5c, 5d, and the team-wide page (Decision 3, deliberately not built).

- [ ] **Step 3: Run the full offline suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Report the actual counts; do not claim a clean run without the output in front of you.

- [ ] **Step 4: Confirm no secrets are riding along**

Run: `git check-ignore -v .env && git diff --cached | grep -nE 'sk-ant-|ATATT|gh[pous]_|BEGIN [A-Z ]*PRIVATE KEY' || echo "no secret shapes in the diff"`
Expected: `.env` is ignored; `no secret shapes in the diff`.

- [ ] **Step 5: Commit and push**

```bash
git add docs/STATUS.md CLAUDE.md
git commit -m "Stage 5a on the record: the coach is a team member"
git push origin master
git status -sb   # must read '## master...origin/master' with no 'ahead'
```

---

## Live validation (The operator — not an agent step)

Claude sets up state and verifies via API/tests; the operator performs the clicks.

1. Open the Agents page → **litellm-manager** → Coach.
2. Select its 4 signals (charter v0) and run the session.
3. In the Decisions Inbox, review the `charter.update` proposal as a diff. Check the evidence block: each citation should read `selected`, and none should read `unverified quote`.
4. Approve.
5. Confirm the new version row names the **coach** as drafter and **litellm-manager** as subject:

```bash
curl -s localhost:8080/api/agents/litellm-manager/charter/versions | python -m json.tool | head -30
```

Expect the newest version's `created_by` to read `agent:coach for litellm-manager (approved human:lee)`.

**Note before you look:** litellm-manager's *existing* charter versions were self-proposed and read `agent:litellm-manager (approved …)`. That is not a bug and was deliberately not backfilled — the before/after comparison spans the change in who drafts.

---

## Self-review notes

**Spec coverage.** Decision 1 → Task 4. Decision 2 → Task 3 (`record-read`, unscoped) + the charter's foregrounding language. Decision 3 (no page) → nothing built, correct. Decision 4 → Task 6 + Task 7. Decision 5 → Task 5, with the charter-is-prompt test. Provenance fix → Task 1. Citation change → Task 6. `run_coach` rebuilt → Task 5. UI in 5a → Task 7. Spec test list: (1) provenance, proven failing → Task 1 Step 2; (2) charter-is-prompt → Task 5; proposer-in-args → Task 1; discovered verifies / paraphrase flags → Task 6; `None` still means not checked → Task 6; pack/registry parity → Tasks 2–3; fresh DB reproduces the roster → Task 4.

**One spec item deliberately not implemented:** "coaching a RETIRED agent 409s with the real reason (404 for an unknown id)." That behavior already exists at `routes.py:662-673` and is unchanged by 5a. Adding a test for it is cheap and worth doing, but it guards existing code rather than new work — fold it into Task 4 if the reviewer wants it covered.

**Known ordering constraint:** Task 5 awaits `_verified_evidence`, which becomes async in Task 6. Execute 5 and 6 back to back, or follow the note in Task 5 Step 4.
