"""Which dependencies a live read tool needs — the outage doctrine's read map.

PRD FR-40 (epic-outage-doctrine): a DOWN dependency parks work in place, so the
system has to know, for every read an agent can make, WHICH dependencies it
waits on. A read maps to a TUPLE of names (a graph search embeds its query, so
it needs `graph` AND `embedder`; a web fetch needs none), a unit waits on every
one of them, and the tuple is recorded on the park so the retry sweep gates on
exactly those probes.

This lives in `contract/` for the same reason `ARG_SPECS` does: the runtime
reads it to decide what a tool raises, and the API tier reads it to decide
what a parked read waits on, and `runtime/` may never import `gateway/`.
The contract tier imports nothing from the other tiers.

Story 2 of the epic creates the module with the READ-TOOL map; story 3 adds the
closed vocabulary (`DEPENDENCIES`), the write-capability map, the probes and
the parity tests.

Three kinds of entry, all keys of `TOOL_DEPENDENCY`:

- a tuple of probed dependencies: the read PARKS on a DOWN failure under a run
  that has a landing tail (`TriageDeps.park_reads`) and otherwise re-raises;
- a tuple holding `local`: Postgres-only work. Nothing probes it and nothing
  waits for it, so it NEVER parks — a Postgres failure propagates as it always
  has (`is_parkable` is False);
- the empty tuple: not a dependency at all. `fetch_url` and `search_web` read
  the open web, where a 503 is that site's answer and the tool's text stays.
"""

from __future__ import annotations

LOCAL = "local"

_GRAPH_AND_EMBEDDER = ("graph", "embedder")  # embeds its query, then reads Neo4j
_GRAPH = ("graph",)                          # lists or fetches by id: bolt only

TOOL_DEPENDENCY: dict[str, tuple[str, ...]] = {
    # --- the knowledge graph (integrations/graphiti.py; reads over bolt) ------
    "search_knowledge_graph": _GRAPH_AND_EMBEDDER,
    "search_knowledge_graph_entities": _GRAPH_AND_EMBEDDER,
    "search_graph_group": _GRAPH_AND_EMBEDDER,
    "list_graph_groups": _GRAPH,
    "list_graph_group_episodes": _GRAPH,
    # --- Jira (native client; n8n only for the Cloud-only reads) --------------
    "jira_get_issue": ("jira",),
    "jira_search_issues": ("jira",),
    "jira_get_transitions": ("jira",),
    "jira_list_projects": ("jira",),
    "jira_list_fields": ("jira",),
    "jira_find_users": ("jira",),
    "jira_list_filters": ("jira",),
    "jira_list_dashboards": ("jira",),
    "jira_list_gadgets": ("jira",),
    # --- Confluence -----------------------------------------------------------
    "confluence_list_spaces": ("confluence",),
    "confluence_get_page": ("confluence",),
    "confluence_search": ("confluence",),
    "confluence_list_children": ("confluence",),
    "confluence_list_attachments": ("confluence",),
    "confluence_list_labels": ("confluence",),
    "confluence_page_versions": ("confluence",),
    # --- the code forge (GitHub / GitLab) -------------------------------------
    "forge_list_instances": ("forge",),
    "forge_search_repos": ("forge",),
    "forge_read_file": ("forge",),
    "forge_list_issues": ("forge",),
    "forge_get_issue": ("forge",),
    "forge_list_merge_requests": ("forge",),
    "forge_get_merge_request": ("forge",),
    "forge_list_commits": ("forge",),
    # --- the calendar ---------------------------------------------------------
    "read_calendar": ("calendar",),
    # --- the mailbox, through the n8n façade ----------------------------------
    "mail_search": ("n8n",),
    "mail_read": ("n8n",),
    "mail_list_folders": ("n8n",),
    # --- the LiteLLM proxy -----------------------------------------------------
    "litellm_list_models": ("litellm",),
    "litellm_list_credentials": ("litellm",),
    "litellm_list_keys": ("litellm",),
    "litellm_list_teams": ("litellm",),
    "litellm_get_routing": ("litellm",),
    "litellm_get_health": ("litellm",),
    "litellm_check_model_health": ("litellm",),
    "litellm_get_spend": ("litellm",),
    "litellm_list_mcp_servers": ("litellm",),
    "litellm_provider_catalog": ("litellm",),
    "litellm_probe_model": ("litellm",),
    # A file in this checkout, not the proxy.
    "litellm_read_config": (LOCAL,),
    # The proxy pod's logs through a read-only kubeconfig: the cluster API is
    # the dependency, and nothing probes it. Not a dependency here (empty), so
    # its own failure text stays, as it does for a web page.
    "litellm_read_logs": (),
    # --- the agent's own sandbox pod ------------------------------------------
    "sandbox_exec": ("sandbox",),
    "sandbox_write_file": ("sandbox",),
    "sandbox_read_file": ("sandbox",),
    "sandbox_copy_in": ("sandbox",),
    "sandbox_reset": ("sandbox",),
    # --- Postgres-only reads: never probed, never gated, never parked ---------
    "read_team_roster": (LOCAL,),
    "read_agent_charter": (LOCAL,),
    "read_charter_history": (LOCAL,),
    "read_agent_record": (LOCAL,),
    "read_record_event": (LOCAL,),
    "read_team_activity": (LOCAL,),
    "catalog_list_documents": (LOCAL,),
    "catalog_get_document": (LOCAL,),
    "loe_list": (LOCAL,),
    # --- the open web: not a dependency ---------------------------------------
    "fetch_url": (),
    "search_web": (),
    # --- a consultation is a read in the ASKER's run --------------------------
    # The default is the model; the tuple a park actually records is the one
    # the specialist's sub-run failed on (a re-raised DependencyDown's own
    # tuple, or `llm` for a model failure) — see `tools.consult_agent`.
    "consult_agent": ("llm",),
}

# Tools in a pack that are not reads at all (they pause for an operator, start
# work or drive a project), so they have no entry above. The parity test walks
# every pack tool name and requires it to be a key of TOOL_DEPENDENCY, a
# `propose_*` tool, or one of these.
NOT_READS = frozenset({
    "ask_operator", "assign_work", "conclude_discussion", "create_task",
    "record_progress", "submit_plan",
})


def is_parkable(dependencies: tuple[str, ...] | None) -> bool:
    """May a DOWN failure on a read with these dependencies PARK a run?

    Not for an empty tuple (not a dependency) and not for `local` (nothing
    probes Postgres and nothing waits for it)."""
    return bool(dependencies) and LOCAL not in dependencies


def parkable_reads() -> frozenset[str]:
    """The reads that can park a run — the keys a parked read can be resumed
    by name from (`runtime.tools.run_read_by_name`)."""
    return frozenset(n for n, d in TOOL_DEPENDENCY.items() if is_parkable(d))
