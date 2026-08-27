"""Tier 3 — the agent runtime (Pydantic AI + DBOS + Claude).

Arrives at M2. This tier can ONLY propose and read: it imports `contract` and the
read-only integrations, but never `gateway` or `executor`. The trust boundary is
enforced by this import rule — agents cannot reach the credentialed executor.
"""
