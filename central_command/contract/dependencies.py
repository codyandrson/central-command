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

Story 2 of the epic created the module with the READ-TOOL map; story 3 adds the
closed vocabulary (`DEPENDENCIES`), the capability map (`CAPABILITY_DEPENDENCY`,
every row of the Executor's registry), the probe-name rule and the parity
tests. The probe FUNCTIONS stay in `heartbeat/probes.py` (config, httpx and the
standard library only); this module only says which names have one.

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

from .failures import DependencyDown

LLM = "llm"
EMBEDDER = "embedder"
GRAPH = "graph"
LITELLM = "litellm"
N8N = "n8n"
CALENDAR = "calendar"
JIRA = "jira"
CONFLUENCE = "confluence"
FORGE = "forge"
SANDBOX = "sandbox"
LOCAL = "local"

# THE list of dependency names — every tuple in this module, every recorded park
# and every `dependency_probe` row uses one of these and nothing else. `local`
# is Postgres: it is never probed and never gated (nothing waits for it).
DEPENDENCIES = (LLM, EMBEDDER, GRAPH, LITELLM, N8N, CALENDAR, JIRA, CONFLUENCE,
                FORGE, SANDBOX, LOCAL)

# What a probe exists for: every name but `local`. `heartbeat/probes.py` keys
# its probe table by exactly these (a parity test pins it).
PROBED = tuple(d for d in DEPENDENCIES if d != LOCAL)

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


# The attribute a tool stamps on a re-raised original so a consult's asker can
# park on the specialist's real tuple (`runtime/tools.py`); named here so the
# Executor-side helper below reads the same one.
DEPENDENCIES_ATTR = "_cc_read_dependencies"


def stamped_dependencies(exc: BaseException | None) -> tuple[str, ...]:
    """The dependencies an exception CARRIES: a `DependencyDown`'s own tuple, or
    the stamp a tool left on a re-raised original (walking the cause chain).
    Empty when the error names none — the caller then falls back."""
    seen: set[int] = set()
    cur = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, DependencyDown) and cur.dependencies:
            return cur.dependencies
        stamp = getattr(cur, DEPENDENCIES_ATTR, None)
        if stamp:
            return tuple(stamp)
        cur = cur.__cause__
    return ()


# --- the write capabilities (every row of gateway/capabilities.REGISTRY) --------
#
# Keyed by the FULL capability name, so a new registry row with no entry fails
# the parity test instead of silently meaning "attempt anyway". The tuple is
# what a retry of that capability waits on: the retry sweep probes every name
# in it (never `local`) and skips the unit, spending no attempt, while any is
# down.
#
# - graph writes embed (a node's name is embedded before it is written) and an
#   add_episode is the ingest path: `graph` AND `embedder`;
# - `graph.get_status` only talks bolt;
# - `mail.*` and `email.*` ride the n8n façade;
# - `autodiscovery.skip` reads the proxy's catalog;
# - `mcp.*` runs in the sandbox pod;
# - `catalog`, `charter`, `skill`, `loe`, `work` write Postgres only (`local`);
# - `web.fetch` is the open web, not a dependency (`()`).


def _each(deps: tuple[str, ...], names: str) -> dict[str, tuple[str, ...]]:
    return {n: deps for n in names.split()}


_GRAPH_AND_EMBEDDER_NAMES = (
    "graph.add_episode graph.create_node graph.create_edge graph.merge_nodes "
    "graph.update_node graph.delete_node graph.update_edge graph.delete_edge "
    "graph.rescope_episode graph.delete_episode graph.search_facts "
    "graph.search_nodes"
)

CAPABILITY_DEPENDENCY: dict[str, tuple[str, ...]] = {
    **_each(_GRAPH_AND_EMBEDDER, _GRAPH_AND_EMBEDDER_NAMES),
    **_each(_GRAPH, "graph.get_status"),
    **_each((JIRA,),
            "jira.set_due_date jira.create_issue jira.add_comment "
            "jira.update_attributes jira.set_fields jira.link_issues "
            "jira.delete_link jira.assign_issue jira.delete_issue "
            "jira.transition_issue jira.create_filter jira.create_dashboard "
            "jira.create_project jira.get_issue jira.search_issues "
            "jira.find_users jira.get_transitions jira.list_fields "
            "jira.list_filters jira.list_dashboards jira.list_gadgets"),
    **_each((CONFLUENCE,),
            "confluence.create_page confluence.update_page confluence.move_page "
            "confluence.trash_page confluence.upload_attachment "
            "confluence.set_labels confluence.create_space"),
    **_each((CALENDAR,),
            "calendar.create_event calendar.update_event calendar.delete_event "
            "calendar.list_events"),
    **_each((N8N,),
            "mail.create_rule mail.report_spam mail.unsubscribe mail.send "
            "mail.move email.list_refs email.get_message"),
    **_each((LITELLM,),
            "litellm.add_model litellm.update_model litellm.delete_model "
            "litellm.create_key litellm.update_key litellm.create_team "
            "litellm.delete_team litellm.delete_key litellm.set_fallbacks "
            "litellm.delete_fallbacks litellm.apply_config_change "
            "autodiscovery.skip litellm.list_models litellm.list_credentials "
            "litellm.list_keys litellm.get_routing litellm.list_teams "
            "litellm.get_health litellm.check_model_health litellm.get_spend "
            "litellm.list_mcp_servers litellm.register_mcp_server"),
    # A file in this checkout, and the cluster's pod logs: same as the tools.
    **_each((LOCAL,), "litellm.read_config"),
    **_each((), "litellm.read_logs web.fetch"),
    **_each((SANDBOX,),
            "sandbox.exec sandbox.write_file sandbox.read_file sandbox.copy_in "
            "sandbox.reset mcp.sync_source mcp.build_image mcp.server_deploy "
            "mcp.server_remove mcp.tool_call"),
    **_each((LOCAL,),
            "catalog.tag charter.update skill.create skill.doc_add "
            "loe.record_checkin loe.create loe.update loe.list work.bulk_dismiss"),
}


def dependencies_for_capability(capability: str | None) -> tuple[str, ...]:
    """What a retry of `capability` waits on. Tolerates the `@version` suffix.

    A name with no entry (or None) is `local`: nothing to probe, nothing to wait
    for. That is the only fail-open left, and the parity test makes sure no
    registry row relies on it."""
    if not capability:
        return (LOCAL,)
    return CAPABILITY_DEPENDENCY.get(str(capability).split("@", 1)[0], (LOCAL,))


def recorded_dependencies(dependencies) -> tuple[str, ...]:
    """What a park record says it waited on. A record with none (or an empty
    list) reads as `llm`, the dependency every park waited on before this
    vocabulary — THE one place that default lives in Python (the SQL twin is
    `repo._deps_sql`)."""
    return tuple(dependencies or ()) or (LLM,)


def waited_on(dependencies) -> tuple[str, ...]:
    """`recorded_dependencies` as the operator reads it: `local` is Postgres,
    never a wait, so it is dropped (and an all-`local` record reads `llm`)."""
    return tuple(d for d in recorded_dependencies(dependencies) if d != LOCAL) or (LLM,)


def failed_dependencies(exc: BaseException | None,
                        default: tuple[str, ...] = (LLM,)) -> tuple[str, ...]:
    """What a park stamps for the failure it is recording: the typed error's own
    tuple, else `default` — the model, since a run that died transiently died
    on its model turn. THE one place that stamping default lives."""
    return stamped_dependencies(exc) or default


def gate_names(dependencies, *, model_turn: bool = False) -> tuple[str, ...]:
    """The names a unit is gated on: every recorded dependency that has a probe
    (never `local`, never an unknown), plus `llm` when its re-drive is a model
    turn. Order kept, duplicates dropped. Rows that recorded none read as
    `llm` — the dependency every park was waiting on before this vocabulary."""
    names = [d for d in recorded_dependencies(dependencies) if d in PROBED]
    if model_turn and LLM not in names:
        names.append(LLM)
    return tuple(dict.fromkeys(names))
