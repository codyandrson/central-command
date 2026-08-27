"""Deterministic reports composed from the control plane's own record.

A report here does ZERO judgment: it reads existing `repo` functions and hands
back numbers. Narration is an agent's job, over a data block a report produced
— which is what makes "the LLM narrates, never improvises the query" mechanical
rather than a charter promise.

TIER: this package sits beside `runtime/` in the trust graph and must stay
runtime-importable — it may NEVER import `gateway`/`executor`. An agent-facing
tool imports from here (`runtime/tools.read_team_activity`), so a gateway import
would smuggle the write tier into reach of the runtime through the side door.
`tests/test_ea_report.py` walks this package's imports for exactly that.
"""
