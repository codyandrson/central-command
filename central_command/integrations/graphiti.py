"""Client for the Graphiti knowledge graph, via its MCP server (streamable HTTP).

The Pi's own `cc-graphiti` (127.0.0.1:8000/mcp) fronts the Neo4j graph — one
service of `deploy/pi/docker-compose.yml`, not the desktop's retired
`nat-graphiti`.
Per DESIGN §3, **reading is layered, writing is gated**: read operations are
ungated and safe for the runtime tier (agents pull context freely); write
operations are called by the Executor *after* approval — never handed to a
thinker agent. The graph never ingests raw email text, only distilled claims
(D13: store refs, not bodies).

Group tenanting: reads span the preserved homelab graph (`main`) plus
Central Command's own group; writes land in Central Command's shared group by default,
or in an agent's own PRIVATE partition (`central_command_<agent_id>`, see
`private_group`) when the approved proposal's scope says so (D11-r1: agents
have a private partition; every write is still gated — private only changes
WHERE the episode lands, never whether it needed approval). A read given an
`agent_id` additionally spans that agent's own private partition, so an agent
can recall its own past private episodes but not another agent's.
"""

from __future__ import annotations

import json

import httpx

from central_command.config import settings
from central_command.integrations import http as http_client

# Operations that mutate the graph — only the Executor may call these, post-approval.
WRITE_OPS = {"add_episode"}
READ_OPS = {"search_facts", "search_nodes", "get_status"}

_PROTOCOL = "2025-03-26"
_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


class GraphitiError(Exception):
    pass


def parse_sse_message(text: str) -> dict:
    """Extract the JSON-RPC message from a streamable-HTTP SSE response body.
    The server answers each POST with a short SSE stream whose final `data:`
    line carries the response."""
    message = None
    for line in text.splitlines():
        if line.startswith("data: "):
            message = line[len("data: "):]
    if message is None:
        raise GraphitiError(f"no SSE data in response: {text[:200]!r}")
    return json.loads(message)


async def _call_tool(name: str, arguments: dict, timeout: float = 30.0) -> dict:
    """One MCP tools/call round-trip (initialize → initialized → call).

    A fresh session per call keeps this stateless and reconnect-proof; the
    handshake is two tiny local HTTP posts, which is noise next to the graph
    query itself.
    """
    url = settings.graphiti_mcp_url
    async with httpx.AsyncClient(timeout=timeout, **http_client.client_kwargs()) as client:
        init = await client.post(
            url,
            headers=_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "central_command", "version": "0.1"},
                },
            },
        )
        init.raise_for_status()
        session = init.headers.get("mcp-session-id")
        if not session:
            raise GraphitiError("MCP server returned no session id")
        headers = {**_HEADERS, "mcp-session-id": session}

        await client.post(
            url, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
        )

        resp = await client.post(
            url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        resp.raise_for_status()

    message = parse_sse_message(resp.text)
    if "error" in message:
        raise GraphitiError(str(message["error"]))
    result = message.get("result") or {}
    content = (result.get("content") or [{}])[0].get("text", "")
    if result.get("isError"):
        raise GraphitiError(content or "graphiti tool call failed")
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return {"message": content}


async def steward_map() -> dict[str, str]:
    """`{steward_group: agent_id}` for every ACTIVE roster agent that stewards
    a domain group. Domain stewardship (2026-08-22): a steward's group lives
    INSIDE the shared read set (every agent reads it) but is that agent's
    primary write target — this map is what both `_read_groups` (read-side
    widening) and the read-tool attribution stamp (`runtime/tools.py`) key off.
    ponytail: fresh fetch per call, no cache — fine at current roster size,
    revisit if this path gets hot."""
    from central_command.db import repo as db_repo

    agents = await db_repo.list_agents(include_retired=False)
    return {
        a["steward_group"]: a["id"]
        for a in agents
        if (a.get("steward_group") or "").strip()
    }


async def _read_groups() -> list[str]:
    groups = [g.strip() for g in settings.graph_read_groups.split(",") if g.strip()]
    for group_id in (await steward_map()).keys():
        if group_id not in groups:
            groups.append(group_id)
    return groups


def private_group(agent_id: str) -> str:
    """An agent's own graph partition — never read by another agent.

    Underscore separator, NOT a colon: graphiti_core rejects any group_id
    outside ``[a-zA-Z0-9_-]`` — and add_episode is queued, so a bad group id
    is a silent drop that still acks (found 2026-08-15: 25 approved episodes
    lost). ``tests/test_graph_scope.py`` pins the charset."""
    return f"{settings.graph_write_group}_{agent_id}"


async def groups_for(agent_id: str | None) -> list[str]:
    groups = await _read_groups()
    return groups + [private_group(agent_id)] if agent_id else groups


# --- reads (ungated; safe for the runtime tier) --------------------------------


async def search_facts(query: str, max_facts: int = 8, agent_id: str | None = None) -> list[dict]:
    """Relevant facts (entity relationships, with temporal validity)."""
    out = await _call_tool(
        "search_memory_facts",
        {"query": query, "max_facts": max_facts, "group_ids": await groups_for(agent_id)},
    )
    return out.get("facts", [])


async def search_nodes(query: str, max_nodes: int = 8, agent_id: str | None = None) -> list[dict]:
    """Relevant entities (nodes with summaries)."""
    out = await _call_tool(
        "search_nodes",
        {"query": query, "max_nodes": max_nodes, "group_ids": await groups_for(agent_id)},
    )
    return out.get("nodes", [])


async def search_private_facts(agent_id: str, query: str, max_facts: int = 8) -> list[dict]:
    """Facts from ONLY `agent_id`'s private partition — never the shared groups
    `search_facts` also spans. For reads that want exclusively an agent's own
    distilled record (e.g. the EA's follow-up tracking, slice B) where shared-
    group results would just be noise."""
    out = await _call_tool(
        "search_memory_facts",
        {"query": query, "max_facts": max_facts, "group_ids": [private_group(agent_id)]},
    )
    return out.get("facts", [])


async def get_episodes(
    last_n: int = 50, agent_id: str | None = None, private_only: bool = False
) -> list[dict]:
    """Most recent episodes in our groups — the shared memory, listed, plus the
    caller's own private partition when `agent_id` is given (D11-r1: agents
    have a private partition; a caller with no agent context sees shared only,
    e.g. the cockpit's team-wide memory panel). `private_only=True` (with an
    `agent_id`) narrows the read to exactly that agent's private group — the
    per-agent chat panel's default view."""
    group_ids = [private_group(agent_id)] if private_only and agent_id else await groups_for(agent_id)
    out = await _call_tool(
        "get_episodes",
        {"group_ids": group_ids, "max_episodes": last_n},
    )
    return out.get("episodes", [])


async def get_status() -> dict:
    return await _call_tool("get_status", {})


# --- writes (Executor-only, post-approval) -------------------------------------


async def add_episode(
    name: str, episode_body: str, source_description: str, group_id: str | None = None
) -> str:
    """Commit one distilled, approved episode to a graph group — Central Command's
    shared group by default, or `group_id` (e.g. `private_group(agent_id)`)
    when the approved proposal scoped it private.
    Graphiti queues ingestion (entity/fact extraction) server-side; the return
    is its acknowledgement, not the finished graph state."""
    out = await _call_tool(
        "add_memory",
        {
            "name": name,
            "episode_body": episode_body,
            "source": "text",
            "source_description": source_description,
            "group_id": group_id or settings.graph_write_group,
        },
        timeout=60.0,
    )
    return out.get("message", "episode submitted")
