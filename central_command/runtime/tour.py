"""The EA-hosted team tour — the prompt material behind the `onboarding_tour`
contact kind.

Spec: `docs/superpowers/specs/2026-08-23-ea-hosted-team-tour-design.md`.

NO new runtime machinery, deliberately. The tour is an `ea.contact` kind whose
brief tells the EA to broker introductions using tools it already holds:
`task-propose` for the intro tasks, `ask-operator` for the lane, `graph-propose`
for the closing summary. Each introduction is a real turn by the real agent,
which is the whole point — the EA never describes a colleague.

What lives here is two prompt shapes and the ONE build-time check the spec
demands (its "guidance-needs-a-mechanism" clause): whether each toured agent
can actually PROPOSE an episode, so the intro brief promises a proposal only
where the grants back it and states the honest fallback where they do not.
Both facts — episode capability and taskability — are read from the live
grants and the live roster at compose time, never hardcoded: the only fixed
list here is the arc ORDER the spec ratified.
"""

from __future__ import annotations

# Decision 5: core five, in this order. The EA is the host and introduces
# itself first, so it is not in the arc.
TOUR_ARC: tuple[str, ...] = (
    "inbox-triage", "jira-expert", "knowledge-steward", "coach",
)

# The gated capability that makes "propose to record what you learned" a
# promise the agent can keep. Named once; the audit test reads it from here.
EPISODE_CAPABILITY = "graph.add_episode"


_RECORD_BY_PROPOSAL = (
    "END BY RECORDING WHAT YOU LEARNED. You hold the episode-propose "
    "capability, so propose a `graph.add_episode` action carrying what the "
    "operator told you about how they want you to work — distilled, one to "
    "three sentences, in your own words, with the conversation as the source. "
    "The operator approves it in the Decisions Inbox like any other proposal. "
    "Propose nothing if they told you nothing durable; an empty tour is a "
    "finding, not a failure."
)

_RECORD_BY_SUMMARY = (
    "END BY SUMMARISING WHAT YOU LEARNED — ALOUD, not as a proposal. You do "
    "NOT hold an episode-propose capability, so say plainly what you heard, "
    "state that you cannot record it yourself, and name knowledge-steward as "
    "the teammate who can carry it into the team's knowledge if the operator "
    "wants it kept. Do not imply anything has been recorded."
)

INTRO_TASK_TEMPLATE = (
    "INTRODUCE YOURSELF TO YOUR NEW OPERATOR. This is their first working "
    "contact with you, so it is also your first acceptance test — be exactly "
    "who your charter says you are.\n\n"
    "1. Say who you are and what you are FOR, from your own charter. Two or "
    "three sentences, in your own voice, no feature list.\n"
    "2. Say what you can actually DO, from your GENERATED capabilities "
    "section — name the gated ones as things you PROPOSE, never as things you "
    "perform, and say what stays behind the operator's approval. If you carry "
    "skills, name them and what they are for.\n"
    "3. Ask 2 or 3 CALIBRATION questions, derived from your own charter — the "
    "judgment calls your charter leaves to you, where this operator's "
    "preference would change what you do. Ask about their standards, not "
    "about facts you could look up. Vague questions waste the one chance you "
    "get; if your charter gives you nothing worth asking, say so — that is a "
    "finding about your charter and the operator should hear it.\n"
    "4. {recording}\n\n"
    "{delivery}"
)

_DELIVER_BY_LANE = (
    "DELIVERY: end your run by calling `ask_operator(needs_discussion=True)` "
    "with your introduction and your questions AS the question — that opens "
    "the conversation lane where the operator answers you. Do the recording "
    "step above only after they have answered; concluding the discussion "
    "comes first, the proposal second. If they answer from the Decisions "
    "Inbox instead of this lane, the lane closes at that moment and your "
    "resumed turn must not end in plain text — end it with "
    "`ask_operator(needs_discussion=True)` again for whatever still needs "
    "their reply — a turn ended in plain text completes your run with the "
    "conversation unfinished."
)

_DELIVER_IN_CHAT = (
    "DELIVERY: you are already in a conversation with the operator — answer "
    "here. Introduce yourself and ask your questions in this turn, then wait "
    "for their reply; do the recording step above only after they have "
    "answered."
)


async def can_propose_episode(agent_id: str) -> bool:
    """Does this agent's LIVE grant set cover `graph.add_episode`?

    The spec's build-time check, executed at compose time rather than trusted
    from a table: a pack revoked yesterday must change what the intro promises
    today.
    """
    from central_command.runtime.packs import granted_capability_names, granted_packs

    return EPISODE_CAPABILITY in granted_capability_names(await granted_packs(agent_id))


async def intro_task_instructions(agent_id: str) -> str:
    """The self-contained brief for one agent's introduction.

    Both slots are read from live state: what the agent can RECORD from its
    grants, and how it DELIVERS from its taskability — an agent reached in an
    existing chat lane must not be told to open one.
    """
    from central_command.runtime.roster import roster

    taskable = any(a.id == agent_id and a.taskable for a in await roster())
    return INTRO_TASK_TEMPLATE.format(
        recording=(
            _RECORD_BY_PROPOSAL if await can_propose_episode(agent_id)
            else _RECORD_BY_SUMMARY
        ),
        delivery=_DELIVER_BY_LANE if taskable else _DELIVER_IN_CHAT,
    )


async def arc() -> list[dict]:
    """The arc, filtered to agents actually on the roster, each annotated with
    how the EA can reach it.

    `taskable` is the load-bearing annotation: `task.create` refuses a
    non-taskable agent AT EXECUTION, so an intro task proposed for one would be
    approved by the operator and then fail. Those introductions happen in the
    cockpit's chat instead — every active roster agent is chattable
    (`roster.conversational_agent_ids`), so nothing in the arc is unreachable.
    """
    from central_command.runtime.roster import roster

    by_id = {a.id: a for a in await roster()}
    out = []
    for agent_id in TOUR_ARC:
        a = by_id.get(agent_id)
        if a is None:  # retired or never hired in this deployment
            continue
        out.append({
            "agent_id": a.id,
            "role": a.role,
            "taskable": a.taskable,
            "instructions": await intro_task_instructions(a.id),
        })
    return out


_SUMMARY_EPISODE = (
    "CLOSE THE TOUR. Propose one `graph.add_episode` recording that the tour "
    "happened and what it settled about how this operator wants the team to "
    "work — the standing preferences, not the pleasantries, and not anything a "
    "teammate already proposed for itself."
)

_SUMMARY_NO_EPISODE = (
    "CLOSE THE TOUR by telling the operator what it settled, in plain text. "
    "You cannot record it yourself — you hold no episode-propose capability — "
    "so name knowledge-steward as the teammate who can."
)


async def brief() -> str:
    """The EA's tour-host brief — the `onboarding_tour` framing, composed
    against the live roster and the live grants."""
    entries = await arc()
    lines = [
        "This is the ONBOARDING TOUR. A new operator has just stood this "
        "system up and has met nobody on it. You are the HOST: you open the "
        "conversation, you introduce yourself, and then you broker an "
        "introduction to each teammate in turn. You never describe a "
        "colleague — each of them speaks for itself.",
        "",
        "OPEN by calling `ask_operator(needs_discussion=True)`. Everything "
        "below happens in that one conversation lane, across turns, at the "
        "operator's pace. Never run ahead: one introduction at a time, and "
        "wait for them before moving on.",
        "",
        "THE LANE HAS TWO DOORS, and only one of them keeps it open. The "
        "operator can reply IN this lane — that keeps it open, turn after "
        "turn. Or they can answer from the Decisions Inbox item this call "
        "opened, which CLOSES the lane at that moment and resumes you with "
        "just their answer. Either way, a turn you end in PLAIN TEXT "
        "COMPLETES THE RUN — and with it the whole tour, however many "
        "teammates are still unmet — because there is no lane left for the "
        "tour to continue in; your words land in a task-result panel the "
        "operator may never read. So the rule is mechanical, not "
        "optional: any turn that still expects a reply from the operator — "
        "including the calibration turn in step 1, and every turn after an "
        "Inbox answer resumes you — must end by calling "
        "`ask_operator(needs_discussion=True)` again with the next step, "
        "never with plain text. That call is what reopens, or keeps open, "
        "the lane the rest of the tour depends on.",
        "",
        "1. YOUR OWN INTRODUCTION, first. Say who you are and what you are "
        "for, from your charter; say what you can propose and what stays "
        "behind their approval, from your GENERATED capabilities section; "
        "then ask your own 2 or 3 calibration questions — how often they want "
        "to hear from you, when they do not, and what is worth interrupting "
        "them for. Those answers set your contact doctrine, so ask them "
        "properly. When their answers come back, acknowledge them in ONE "
        "sentence and continue straight to step 2 IN THE SAME TURN, closing "
        "that turn with `ask_operator(needs_discussion=True)` per the rule "
        "above, never plain text — the tour does not end at calibration. "
        "This is exactly where it died on day one: the host closed in plain "
        "text after the calibration exchange and the tour ended with nine "
        "teammates unmet.",
        "",
        "2. THE ARC. Then, in this order, broker an introduction to each of "
        "these teammates. The `instructions` text under each is the intro "
        "brief — use it VERBATIM as the task's instructions; it has already "
        "been checked against that agent's real capabilities.",
        "",
    ]
    for i, e in enumerate(entries, start=1):
        lines.append(f"   {i}. {e['agent_id']} — {e['role']}")
        if e["taskable"]:
            lines.append(
                "      Reachable by TASK: propose a `task.create` naming "
                f"`{e['agent_id']}` with the instructions below. The operator "
                "approves it in the Decisions Inbox and it runs at that "
                "moment — that approval IS part of the tour, so tell them "
                "what they are about to see."
            )
        else:
            lines.append(
                "      NOT TASKABLE — a `task.create` for this agent would be "
                "approved and then fail at execution, so do not propose one. "
                "Tell the operator to open a chat with it in the cockpit and "
                "paste the brief below as their opening message. Say plainly "
                "why the route differs; it is a real limit of the system, not "
                "a detail to smooth over."
            )
        lines.append("      instructions:")
        lines.append("      ```")
        lines.extend("      " + ln for ln in e["instructions"].splitlines())
        lines.append("      ```")
        lines.append("")

    lines += [
        "3. THE REST OF THE TEAM. Then name everyone else on your GENERATED "
        "YOUR TEAM section — one line each, what they are for — and OFFER "
        "introductions: now, on request, or at first contact ('you will meet "
        "mcp-manager the first time you need an MCP server'). Do not run "
        "them unasked; the arc above is deliberately short so each "
        "introduction gets real attention.",
        "",
        "4. " + (_SUMMARY_EPISODE if await can_propose_episode("ea")
                 else _SUMMARY_NO_EPISODE),
        "",
        "The tour is RESUMABLE by construction: which intro tasks exist is "
        "the whole of its state. If this lane picks up part-way, read what "
        "has already happened and carry on from there rather than starting "
        "over.",
    ]
    return "\n".join(lines)
