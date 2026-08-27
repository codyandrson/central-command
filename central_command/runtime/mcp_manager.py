"""The MCP manager's roster entry point.

Modelled on `runtime/ea.py`: no dedicated run path lives here, because this
agent has none — it is tasked directly (`run_task`) or talked to
(`converse`), both of which already run any hired agent on the generic
pack-built agent (D24). What lives here is what cannot live anywhere else:
the hire, so a fresh database reproduces the agent without touching
schema.sql's founding-roster guards, and the default sandbox copy-in grant a
server-authoring agent needs to actually reach `servers/`.
"""

from __future__ import annotations

from central_command.runtime.mcp_manager_profile import AGENT_ID, CHARTER, DEVIATIONS, NAME, ROLE

__all__ = ["AGENT_ID", "ensure_registered"]

# The default copy-in grant (design doc 2026-08-03 §6): an MCP-authoring agent
# needs the `servers/` tree in its sandbox to see prior art and the pipeline's
# own conventions. Granted at hire time so the agent can work from its first
# task, same as its pack grants.
_DEFAULT_SANDBOX_SOURCE = "servers"


async def ensure_registered() -> None:
    """Idempotently put the MCP manager on the roster as a full member, with
    its default sandbox source grant."""
    from central_command.db import repo
    from central_command.runtime.hiring import ensure_hired
    from central_command.runtime.templates import MCP_MANAGER_TEMPLATE

    await ensure_hired(
        agent_id=AGENT_ID,
        name=NAME,
        role=ROLE,
        charter=CHARTER,
        template=MCP_MANAGER_TEMPLATE,
        # Building and shipping servers, not answering questions about the
        # rest of the team's work. Reason recorded in DEVIATIONS.
        consultable=True,  # operator decision 2026-08-05: whole roster, auditor excepted
        deviations=DEVIATIONS,
    )
    # `grant_sandbox_source` is an upsert (on-conflict re-grants), so calling
    # it on every idempotent registration is safe — it just re-affirms the
    # same row instead of growing a second check.
    await repo.grant_sandbox_source(AGENT_ID, _DEFAULT_SANDBOX_SOURCE, granted_by="system")
