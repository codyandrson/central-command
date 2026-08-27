"""The idempotent-hire seam — how a purpose-built agent joins the roster.

Extracted from `runtime/steward.py` when the EA became the second agent that
has to hire itself (2026-07-30). It is not a convenience wrapper: the race-loss
branch below is the subtle part, and hand-copying it into every new profile is
exactly how one copy ends up missing it.

Roster membership IS a non-empty `role` column, set only by schema.sql's
founding seeds and `repo.hire_agent`. An `upsert_agent` registration (the
`jira_expert`/`auditor` precedent) leaves that column empty: the agent exists,
holds no charter version and no grants, and is NOT on the roster. Those two
stay as they are — they are founders with built-in charters. Anything hired
after the founding five comes through here, so a fresh database reproduces it
without touching the founding-roster UPDATEs and their `where … and role = ''`
guards (which shipped a broken roster once).
"""

from __future__ import annotations


async def ensure_hired(
    *,
    agent_id: str,
    name: str,
    role: str,
    charter: str,
    template,
    consultable: bool = False,
    deviations: str = "",
    hired_by: str = "system",
) -> None:
    """Idempotently put `agent_id` on the roster as a full member.

    `template` is a `templates.HireTemplate` — it supplies the pack grants and
    the taskable/conversational flags, so the loadout lives in one place with
    the operator-facing templates rather than being spelled out at each call
    site. `consultable` is passed explicitly because no template carries it:
    consultability is a per-agent judgment with a recorded `deviations` reason.
    """
    from central_command.db import repo
    from central_command.runtime.templates import render_charter

    if await repo.get_agent(agent_id) is not None:
        return
    # One-time template render (2026-08-21 design): placeholders filled and
    # [[if-pack]] sections resolved against the grants this hire lands with,
    # so the recorded charter version is plain text from the first moment.
    charter = render_charter(charter, packs=template.default_packs)
    try:
        await repo.hire_agent(
            agent_id,
            name,
            role,
            charter,
            list(template.default_packs),
            template=template.id,
            taskable=template.taskable,
            consultable=consultable,
            conversational=template.conversational,
            deviations=deviations,
            hired_by=hired_by,
        )
    except ValueError:
        # Lost a race with another process doing the same idempotent hire.
        # `hire_agent` raises ValueError on the unique violation, and an agent
        # that now exists is exactly the outcome asked for. Re-raise only if it
        # genuinely is not there — that ValueError meant something else (an
        # empty charter, no grants).
        if await repo.get_agent(agent_id) is None:
            raise
