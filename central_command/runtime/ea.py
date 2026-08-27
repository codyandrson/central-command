"""The executive assistant's roster entry point and model seam.

There is deliberately no `run_*` function here. The EA has no path of its own:
the heartbeat's `ea.contact` action drives it through `create_and_run_task` —
THE task path, the same one the Task Board and the "+" dialog use — so its
runs, its questions and its proposals land on exactly the machinery every other
tasked agent uses. A private runner would be a second, differently-governed way
to start the same work.

What lives here is what cannot live anywhere else: the hire (so a fresh
database reproduces the agent without touching schema.sql's founding-roster
guards) and the demo-mode model resolution.
"""

from __future__ import annotations

from central_command.runtime.ea_profile import AGENT_ID, CHARTER, DEVIATIONS, NAME, ROLE

__all__ = ["AGENT_ID", "ensure_registered"]


async def ensure_registered() -> None:
    """Idempotently put the EA on the roster as a full member."""
    from central_command.runtime.hiring import ensure_hired
    from central_command.runtime.templates import EA_TEMPLATE

    await ensure_hired(
        agent_id=AGENT_ID,
        name=NAME,
        role=ROLE,
        charter=CHARTER,
        template=EA_TEMPLATE,
        # It distils the record FOR the operator; a consult returning "what
        # everyone did" would flood the asker. Reason recorded in DEVIATIONS.
        consultable=True,  # operator decision 2026-08-05: whole roster, auditor excepted
        deviations=DEVIATIONS,
    )


# NO local `_resolve_model` here, deliberately — a deviation from the
# steward/auditor/consult convention, for the reason that convention exists.
# Those agents have their OWN run paths, so a local resolver is the one place
# their model is chosen. The EA has three callers and owns none of them (the
# heartbeat via `create_and_run_task`, the operator via `converse`, the answer
# via `resume_with_answer`), and they all land on `models.resolve_model`. A
# local resolver here would cover one of the three and silently leave the other
# two running the EA on the inbox-triage double, so the demo stand-in is
# dispatched at that seam instead — see `runtime/models.resolve_model`.
