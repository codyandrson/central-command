"""Tier 2 — the gateway and executor (the trust boundary).

Arrives at M3. The gateway runs the policy check and routes a proposal to the
Decisions Inbox; on approval the executor performs the write (holding the
credentials path), stamps provenance, and guarantees execute-once.
"""
