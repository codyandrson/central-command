"""Every agent-building path either wires MCP grants live, or is a recorded,
deliberate exception — and a new path fails loudly until classified.

The gap this locks shut (2026-08-04): `runtime/converse.py`'s `_build` built
chat agents via `build_hired_agent`/`build_litellm_manager` but never called
`packs.mcp_toolsets_for`/`mcp_charter_section`, so an `mcp:<server_id>` grant
was live on `run_task`/`build_agent_for` (resume) but inert in chat — same
agent, same grant, different behavior depending on which surface asked.

Modeled on `tests/test_proposal_created.py`'s source walk: requiring the
literal helper name in the file, rather than counting today's call sites,
fails the suite the moment a new assembler forgets it — in the same commit
that adds it.
"""

from __future__ import annotations

import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "central_command" / "runtime"

HELPER = "mcp_toolsets_for"
# The exempt check looks for an actual CALL, not the docstring cross-references
# jira_expert.py/litellm_manager.py carry pointing back at their caller.
HELPER_CALL = "mcp_toolsets_for("

# Assemblers: compute mcp_toolsets/mcp_section from grants and thread them
# into a builder. Source must reference the helper.
WIRED = {"agent.py", "run.py", "converse.py", "steward.py"}

# Deliberately NOT wired, with the reason pinned so an accidental
# half-wiring (or an accidental full-wiring that then silently regresses)
# is loud either way.
EXEMPT = {
    "consult.py": (
        "advisory sub-runs deliberately exclude MCP — NON_ADVISORY_TOOLS bars "
        "propose_mcp_tool_call and per-caller gates have no coherent stand-in "
        "(packs.py:647-651)"
    ),
    "reflection.py": (
        "the distiller is tool-free by design; _propose_episode deliberately "
        'narrows packs to ("graph-propose",)'
    ),
    "orchestrator.py": (
        "holds no propose_*/write path by its own module docstring; routing "
        "mode is tool-free"
    ),
    "jira_expert.py": (
        "leaf builder — accepts extra_toolsets/mcp_section as parameters "
        "computed by its caller (run.py/agent.py), never calls the helper "
        "itself"
    ),
    "litellm_manager.py": (
        "leaf builder — accepts extra_toolsets/mcp_section as parameters "
        "computed by its caller (run.py/agent.py/converse.py), never calls "
        "the helper itself"
    ),
    "confluence_expert.py": (
        "leaf builder — accepts extra_toolsets/mcp_section as parameters "
        "computed by its caller (run.py/agent.py), never calls the helper "
        "itself"
    ),
    "wiki_agent.py": (
        "leaf builder — accepts extra_toolsets/mcp_section as parameters "
        "computed by its caller (run.py/agent.py), never calls the helper "
        "itself"
    ),
    "coach_profile.py": (
        "docstring-only mention of a historical `Agent(...)` shape (coaching "
        "no longer builds an ephemeral agent this way) — no agent "
        "construction in this file at all"
    ),
}

# A file "builds agents with toolsets" if it constructs a pydantic-ai Agent(
# directly, or calls one of the builder entry points.
BUILDER_MARKERS = (
    "Agent(",
    "build_hired_agent",
    "build_agent(",
    "build_jira_expert",
    "build_litellm_manager",
)


def _builds_agents(path: pathlib.Path) -> bool:
    src = path.read_text(encoding="utf-8")
    return any(marker in src for marker in BUILDER_MARKERS)


@pytest.mark.parametrize("name", sorted(WIRED))
def test_wired_files_reference_the_helper(name):
    src = (SRC / name).read_text(encoding="utf-8")
    assert HELPER in src, f"{name} is WIRED but no longer calls {HELPER}"


@pytest.mark.parametrize("name", sorted(EXEMPT))
def test_exempt_files_do_not_reference_the_helper(name):
    src = (SRC / name).read_text(encoding="utf-8")
    assert HELPER_CALL not in src, (
        f"{name} is EXEMPT ({EXEMPT[name]!r}) but now calls {HELPER} — "
        "either it's actually wired (move it to WIRED) or the wiring is "
        "accidental (remove it)"
    )


def test_every_agent_building_file_is_classified():
    classified = WIRED | set(EXEMPT)
    offenders = sorted(
        p.name for p in SRC.glob("*.py")
        if p.name not in classified and _builds_agents(p)
    )
    assert offenders == [], (
        "these files build agents (construct Agent( or call a builder entry "
        "point) but are in neither WIRED nor EXEMPT — classify them: "
        f"{offenders}"
    )
