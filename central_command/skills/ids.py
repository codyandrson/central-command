"""The one place a skill id is validated.

A skill id is not just a primary key: `runtime.skills._tool_name` derives the
model-visible search tool name from it by collapsing everything outside
`[a-z0-9_]` to `_`. So `cc-test-lite` and `cc_test_lite` — two perfectly legal
distinct rows — produce the SAME tool name, and pydantic-ai raises `UserError`
at `agent.run()` (not at build) when one agent holds both:

    UserError: FunctionToolset 'cc_test_lite' defines a tool whose name
    conflicts with existing tool from FunctionToolset 'cc-test-lite'

Every run of that agent then dies before the model is called. Ids that are pure
punctuation (`---`) collapse to nothing at all.

The pattern below does not by itself make collisions impossible — `a-b` and
`a_b` both satisfy it — so this is the FIRST of two defences. The second is the
uniqueness check in `runtime.skills.capabilities_for`, which skips the loser
with a warning rather than letting library data brick an agent.
"""

from __future__ import annotations

import re

SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

SKILL_ID_HELP = (
    "a skill id must be lowercase and start with a letter or digit, then "
    "letters, digits, '-' or '_' (it becomes part of a tool name the model "
    "sees)"
)


def validate_skill_id(skill_id: str) -> str:
    """Return `skill_id` unchanged, or raise `ValueError` naming the rule."""
    if not SKILL_ID_RE.match(skill_id or ""):
        raise ValueError(f"invalid skill id {skill_id!r} — {SKILL_ID_HELP}")
    return skill_id
