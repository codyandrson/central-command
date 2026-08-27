"""The LiteLLM-manager agent (Phase 2 of the LiteLLM integration).

The operator's lever over the self-hosted proxy, on the same gated spine as
every other agent: it READS the proxy freely (models, spend) and PROPOSES model
changes (add/delete) that flow through the Decisions Inbox → approval → Executor.
It never touches the proxy directly — the Executor holds the admin key.

Two build modes:
- direct task (`advisory=False`): the operator tasked it; it holds
  propose_litellm_change and its proposals go through the gate.
- advisory (`advisory=True`): reads + text only, no propose tool. This mode
  reaches NOTHING: the manager is not consultable, and since consultation
  generalized (2026-07-29) the advisory build every consult uses is the generic
  `runtime/consult.py:build_advisory_agent`. Left in place rather than deleted
  in the same change that generalized the mechanism — if it earns a use it
  should be that generic one, not a per-agent copy.
"""

from __future__ import annotations

from pydantic_ai import Agent, DeferredToolRequests

from central_command.runtime.deps import TriageDeps
from central_command.runtime.litellm_manager_profile import CHARTER
from central_command.runtime.models import resolve_model
from central_command.runtime.templates import render_v0
from central_command.runtime.tools import current_time, declare_gap

AGENT_ID = "litellm-manager"


async def build_litellm_manager(
    model=None, charter: str | None = None, advisory: bool = False, packs=None,
    capabilities=None, skills_catalog: str = "",
    extra_toolsets=(), mcp_section: str = "", team_section: str = "",
) -> Agent:
    """`extra_toolsets`/`mcp_section` (D-sandbox slice 5): computed by the
    caller from `packs_mod.mcp_toolsets_for`/`mcp_charter_section` — see
    runtime/agent.py:build_agent_for. Dropped in advisory mode, along with
    every other write-capable grant (below)."""
    from central_command.runtime import packs as packs_mod
    from central_command.runtime.agent import build_charter

    if packs is None:
        packs = packs_mod.DEFAULT_PACKS[AGENT_ID]
    if advisory:
        # Advisory mode holds READ packs only — no propose tool, and no
        # mcp: grant either (a dynamic pack name, not in PACKS at all).
        packs = [p for p in packs
                 if not p.startswith("mcp:") and not packs_mod.PACKS[p].capabilities]
        extra_toolsets, mcp_section = (), ""
    return Agent(
        await resolve_model(model, agent_id=AGENT_ID),
        name=AGENT_ID,
        system_prompt=build_charter(
            charter or render_v0(CHARTER), packs, mcp_section, team_section
        ) + (
            "\n\n" + skills_catalog if skills_catalog else ""
        ),
        deps_type=TriageDeps,  # unused, but resume paths pass one — keep the shape
        output_type=str if advisory else [str, DeferredToolRequests],
        # declare_gap is ungated (returns text, never CallDeferred) — fits
        # both the advisory (str-only) and direct-task output shapes.
        tools=[declare_gap, current_time],
        toolsets=[packs_mod.toolset_for(packs), *extra_toolsets],
        capabilities=list(capabilities or []),
    )


async def ensure_registered() -> None:
    from central_command.db import repo

    await repo.upsert_agent(
        AGENT_ID, "LiteLLM Manager", "",
        "manage the self-hosted LiteLLM proxy: models + provider mappings (spend read-only)",
    )
