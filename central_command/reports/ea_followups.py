"""The EA's own open follow-ups — a read over its PRIVATE graph partition.

Slice B of EA widening (docs/superpowers/research/2026-08-06-ea-widening-design.md).
The EA distils commitments/open questions into scope='private' graph episodes
(landing in `graphiti.private_group(AGENT_ID)`, i.e. `central_command_ea` — D11-r1
slice 1) via the gated `graph.add_episode` proposal it already holds. This is
the READ side.

A SIBLING of `ea_calendar.day()`, deliberately, for the same reason: an
outside read over HTTP that can time out must degrade the EA's contact, not
take the whole snapshot with it. Do NOT put this in `ea_report` (pure
Postgres — see its docstring). Like `day()`, this function does not catch its
own failures; the caller (`heartbeat/actions.py`) degrades it exactly like
the calendar block.
"""

from __future__ import annotations

from central_command.integrations import graphiti
from central_command.runtime.ea_profile import AGENT_ID

_QUERY = "open commitment or open question awaiting the operator"


async def open_loops(max_facts: int = 8) -> dict:
    """Compact list of the EA's own open follow-ups. Facts only, no judgment —
    the agent narrates, never invents (same doctrine as the calendar block)."""
    facts = await graphiti.search_private_facts(AGENT_ID, _QUERY, max_facts=max_facts)
    return {
        "available": True,
        "count": len(facts),
        "facts": [
            {"fact": f.get("fact", ""), "valid_at": f.get("valid_at"),
             "invalid_at": f.get("invalid_at")}
            for f in facts
        ],
    }
