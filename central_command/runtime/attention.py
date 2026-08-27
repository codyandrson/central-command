"""The attention budget — how much unsolicited contact an agent may cost the
operator in a day.

The teaming doctrine's Decision 4 settled the shape: agents may initiate
contact, and what keeps it from becoming noise is an explicit budget, defaulting
LOW, with the dial in the operator's hands. This module is that rule, and NOTHING here
is advice to an agent — a charter sentence asking an agent to be considerate is
guidance without a mechanism, which is the failure mode this codebase has been
bitten by three times. The budget is enforced at the seam that actually opens a
lane (`questions._open_discussion`), so an agent that ignores its charter still
cannot spend more attention than it has.

**Every other agent's budget is 0, which means UNLIMITED.** That is not a
loophole; it is the scope of the decision. The EA is the agent whose whole job
is scheduled, unprompted contact, so it is the agent whose contact needs a
ceiling. An ordinary agent opens a discussion because a task it was GIVEN is
genuinely ambiguous — throttling that would silently degrade work the operator
asked for into a guess, which is the opposite of what the budget is for. If a
second agent ever earns a budget, give it one here; do not make the default
finite.

WHAT COUNTS: `agent.discussion_opened` events whose actor is this agent, since
LOCAL midnight. The day boundary is the operator's day, not UTC's — a budget
that rolls over at 17:00 local would give the EA a second morning report every
evening. Counting the opened-lane event (rather than, say, tasks fired) is what
makes the budget a count of INTERRUPTIONS: a run that reports in plain text
without opening a lane costs nothing, which is exactly the incentive wanted.
"""

from __future__ import annotations

from datetime import datetime

from central_command.runtime.ea_profile import AGENT_ID as EA_AGENT_ID

# The event whose count IS the budget spend. One name, used by the counter and
# by the emitter that pays into it.
CONTACT_EVENT = "agent.discussion_opened"


def budget_for(agent_id: str | None) -> int:
    """This agent's daily lane-opening allowance; 0 means unlimited."""
    if agent_id != EA_AGENT_ID:
        return 0
    from central_command.config import settings

    return max(0, int(settings.ea_contact_budget_per_day))


def local_midnight(now: datetime | None = None) -> datetime:
    """Today's 00:00 in the host's local zone, as an aware datetime."""
    current = now.astimezone() if now is not None else datetime.now().astimezone()
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


async def contacts_today(agent_id: str, now: datetime | None = None) -> int:
    """How many conversations this agent has opened since local midnight."""
    from central_command.db import repo

    return await repo.count_events_since(
        CONTACT_EVENT, f"agent:{agent_id}", local_midnight(now)
    )


async def may_open_lane(
    agent_id: str | None, now: datetime | None = None
) -> tuple[bool, int, int]:
    """(allowed, used, budget) for `agent_id` opening a conversation now.

    An unlimited agent is answered without touching the database — the common
    case must not cost a query per question.
    """
    budget = budget_for(agent_id)
    if budget <= 0:
        return True, 0, 0
    used = await contacts_today(str(agent_id), now)
    return used < budget, used, budget
