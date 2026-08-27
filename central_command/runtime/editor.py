"""The editor's roster entry point.

Modelled on `runtime/mcp_manager.py`: no dedicated run path lives here,
because this agent has none — it is reached only via consult (a drafter
consulting it with a draft) or a direct task/conversation, both of which
already run any hired agent on the generic pack-built agent (D24). What lives
here is what cannot live anywhere else: the hire, so a fresh database
reproduces the agent without touching schema.sql's founding-roster guards.
"""

from __future__ import annotations

from central_command.runtime.editor_profile import AGENT_ID, CHARTER, DEVIATIONS, NAME, ROLE

__all__ = ["AGENT_ID", "ensure_registered"]


async def ensure_registered() -> None:
    """Idempotently put the editor on the roster as a full member."""
    from central_command.runtime.hiring import ensure_hired
    from central_command.runtime.templates import EDITOR_TEMPLATE

    await ensure_hired(
        agent_id=AGENT_ID,
        name=NAME,
        role=ROLE,
        charter=CHARTER,
        template=EDITOR_TEMPLATE,
        consultable=True,  # operator decision 2026-08-05: whole roster, auditor excepted
        deviations=DEVIATIONS,
    )
