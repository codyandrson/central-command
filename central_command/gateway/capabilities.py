"""The capability registry — the governed catalog of every way the system can
touch the world, and under what gate.

This is the Governance Library's source of truth, and it is not documentation
that can drift: the Executor dispatches gated writes from HANDLER-keyed names
that `tests/test_governance.py` holds in exact parity with the `gate="human
approval"` entries here. A capability the registry doesn't list cannot execute;
a listed one the Executor can't perform fails the suite.

Reads are ungated by design (DESIGN §3: reading is layered, writing is gated) —
they are listed so the operator can see the whole surface, not because they
pass through the approval gate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Capability:
    name: str          # the wire name a proposal Action carries (version-stripped)
    kind: str          # "write" | "read"
    gate: str          # "human approval" | "ungated read" | "ungated" (in-sandbox writes, D-sandbox)
    risk: str          # what a wrong use costs, in plain words
    holder: str        # which component invokes it
    route: str         # the concrete path to the world
    arguments: list[str]
    description: str


REGISTRY: list[Capability] = [
    # --- gated writes: Executor-only, after operator approval -----------------
    Capability(
        name="jira.set_due_date",
        kind="write",
        gate="human approval",
        risk="external, reversible — a wrong date can be set back",
        holder="Executor",
        route="integrations/jira.py → Jira REST setDueDate (native, D23; n8n façade fallback until CC_JIRA_API_TOKEN is set)",
        arguments=["issue_key", "due_date"],
        description="Set a Jira issue's due date.",
    ),
    Capability(
        name="jira.create_issue",
        kind="write",
        gate="human approval",
        risk="external, reversible — a wrongly created issue can be closed or deleted in the Jira UI",
        holder="Executor",
        route="integrations/jira.py → Jira REST createIssue (native, D23; n8n façade fallback until CC_JIRA_API_TOKEN is set)",
        arguments=["project_key", "summary", "description?", "issue_type? (Task|Bug|Story|Epic|Subtask)", "parent?", "due_date?", "labels?", "custom_fields?"],
        description=(
            "Create a new Jira issue for not-yet-tracked work. Built after the "
            "2026-07-20 charter-v2 incident: the coached charter instructed the "
            "agent to propose this capability before it existed, and four "
            "approved proposals failed at execution."
        ),
    ),
    Capability(
        name="jira.add_comment",
        kind="write",
        gate="human approval",
        risk="external, append-only — visible to others; lib-jira has no delete op",
        holder="Executor",
        route="integrations/jira.py → Jira REST addComment (native, D23; n8n façade fallback until CC_JIRA_API_TOKEN is set)",
        arguments=["issue_key", "body"],
        description="Add a comment to a Jira issue.",
    ),
    Capability(
        name="jira.update_attributes",
        kind="write",
        gate="human approval",
        risk="external, reversible — priority/labels/due date can be restored",
        holder="Executor",
        route="integrations/jira.py → Jira REST updateAttributes (native, D23; n8n façade fallback until CC_JIRA_API_TOKEN is set)",
        arguments=["issue_key", "priority", "due_date?", "labels"],
        description="Update a Jira issue's priority, labels, and optionally due date.",
    ),
    Capability(
        name="jira.set_fields",
        kind="write",
        gate="human approval",
        risk="external, reversible — a custom field's previous value can be restored in the Jira UI",
        holder="Executor",
        route="integrations/jira.py → Jira REST editIssue, fields resolved through /rest/api/3/field (native only — no façade counterpart)",
        arguments=["issue_key", "fields ({field name: value})"],
        description=(
            "Set an issue's CUSTOM fields, addressed by their human name and "
            "validated against the shape the instance declares for each. Built "
            "2026-08-11: the operator added four cost/benefit fields and "
            "jira-expert could neither see nor set them — the read paths asked "
            "Jira for a hard-coded field allowlist, and the only write was "
            "jira.update_attributes' fixed priority/labels/due-date trio. Ids "
            "are per-instance, so a proposal names the field and the Executor "
            "resolves it; an unknown name is refused rather than sent, because "
            "Jira accepts an unknown field key with a 200 and binds nothing."
        ),
    ),
    Capability(
        name="graph.add_episode",
        kind="write",
        gate="human approval",
        risk="internal, append-only — a wrong fact is supersedable, never silently edited",
        holder="Executor",
        route="Graphiti MCP add_episode → group central_command (tenanted; never `main`)",
        arguments=["name", "episode_body", "source_description", "scope", "for_agent"],
        description=(
            "Commit a distilled, human-approved claim to the team knowledge "
            "graph. The Executor stamps trust=human-approved and the approver "
            "into the episode source, so pedigree is queryable forever (D13: "
            "distilled claims only, never pasted email text)."
        ),
    ),
    # Graph curation (the remediation loop, 2026-08-20): gated wrappers around
    # the operator-curation primitives, so a PROBLEM verdict's fix can be
    # PROPOSED by the graph-curator and approved cut by cut. Deterministic
    # Neo4j edits — never re-ingestion; each executed action queues a fresh
    # read-back of the same episode into the Verify tab.
    Capability(
        name="graph.create_node",
        kind="write",
        gate="human approval",
        risk="reversible — a new entity, embedded at write time, with its own provenance episode",
        holder="Executor",
        route="neo4j_writer.create_node (bolt)",
        arguments=["name", "group_id", "summary", "labels", "verification_id"],
        description="Add an entity the extraction dropped (2026-08-20).",
    ),
    Capability(
        name="graph.create_edge",
        kind="write",
        gate="human approval",
        risk="reversible — a new relationship with an explicit validity window, with its own provenance episode",
        holder="Executor",
        route="neo4j_writer.create_edge (bolt)",
        arguments=["source_uuid", "target_uuid", "name", "fact",
                   "valid_at", "invalid_at", "verification_id"],
        description="Add a relationship the extraction dropped (2026-08-20).",
    ),
    Capability(
        name="graph.merge_nodes",
        kind="write",
        gate="human approval",
        risk="irreversible — every edge and mention of the dropped node moves to the kept one",
        holder="Executor",
        route="neo4j_writer.merge_nodes (bolt; identity kept whole in both halves)",
        arguments=["keep_uuid", "drop_uuid", "verification_id"],
        description="Fold a duplicate entity into the one being kept.",
    ),
    Capability(
        name="graph.update_node",
        kind="write",
        gate="human approval",
        risk="reversible — rename/re-summarise/re-type, re-embedded when the text changed",
        holder="Executor",
        route="neo4j_writer.update_node (bolt)",
        arguments=["uuid", "name", "summary", "labels", "verification_id"],
        description="Fix an entity's name, summary, or type labels.",
    ),
    Capability(
        name="graph.delete_node",
        kind="write",
        gate="human approval",
        risk="irreversible — DETACH DELETE removes the node and every edge touching it",
        holder="Executor",
        route="neo4j_writer.delete_node (bolt)",
        arguments=["uuid", "verification_id"],
        description="Remove a hallucinated entity.",
    ),
    Capability(
        name="graph.update_edge",
        kind="write",
        gate="human approval",
        risk="reversible — wording, endpoints (repoint), or validity window; restore clears invalid_at AND expired_at",
        holder="Executor",
        route="neo4j_writer.update_edge (bolt; repoint is copy-then-delete)",
        arguments=["uuid", "name", "fact", "source_uuid", "target_uuid",
                   "valid_at", "invalid_at", "clear_valid", "clear_invalid",
                   "clear_expired", "verification_id"],
        description="Fix a relationship's fact, endpoints, or temporal validity.",
    ),
    Capability(
        name="graph.delete_edge",
        kind="write",
        gate="human approval",
        risk="irreversible — the relationship is removed outright",
        holder="Executor",
        route="neo4j_writer.delete_edge (bolt)",
        arguments=["uuid", "verification_id"],
        description="Remove an invented relationship (a self-loop, a fact never stated).",
    ),
    Capability(
        name="graph.rescope_episode",
        kind="write",
        gate="human approval",
        risk="reversible — an episode and what it produced move to another group; shared entities/edges are split (copied), nothing deleted",
        holder="Executor",
        route="neo4j_writer.rescope_episode (bolt; the episode is the unit of scope)",
        arguments=["episode_uuid", "group_id", "verification_id"],
        description="Move an episode to the group it should have been committed to (2026-09-01).",
    ),
    Capability(
        name="catalog.tag",
        kind="write",
        gate="human approval",
        risk=(
            "internal, reversible — a tag is curation metadata on a catalog "
            "lineage; a wrong one is re-tagged, and nothing is ever deleted"
        ),
        holder="Executor (proposed by the knowledge-steward from its document work)",
        route="db/repo.set_document_tags → catalog_document.tags",
        arguments=["document_id", "tags", "mode? (replace|add|remove)"],
        description=(
            "Curate a catalog document's tags — currency and standing, in the "
            "library's own words: 'current as of <date>', 'superseded', "
            "'draft', 'rescinded'. Decision 7's catalog-metadata class: gated "
            "today, and the auditor-agreement autonomy it names graduates "
            "LATER, on the same evidence ladder the dismissal classes climb."
        ),
    ),
    Capability(
        name="jira.link_issues",
        kind="write",
        gate="human approval",
        risk="external, reversible — a wrong link can be deleted in the Jira UI",
        holder="Executor",
        route="integrations/jira.py → Jira REST linkIssues (native, D23; n8n façade fallback until CC_JIRA_API_TOKEN is set)",
        arguments=["from_key", "to_key", "link_type (Blocks|Relates|Duplicate)"],
        description=(
            "Create a typed dependency link between two issues. Direction: "
            "from_key is the OUTWARD side — for Blocks, from_key BLOCKS to_key. "
            "Links express dependencies, never hierarchy (charter rule)."
        ),
    ),
    Capability(
        name="jira.transition_issue",
        kind="write",
        gate="human approval",
        risk="external, usually reversible — workflows can gate reverse transitions",
        holder="Executor",
        route="integrations/jira.py → Jira REST transitionIssue (native, D23; n8n façade fallback until CC_JIRA_API_TOKEN is set)",
        arguments=["issue_key", "transition (name)"],
        description=(
            "Move an issue through its workflow by transition NAME — resolved "
            "against the issue's actually-available transitions; unavailable "
            "names fail loudly, never guess."
        ),
    ),
    Capability(
        name="jira.create_filter",
        kind="write",
        gate="human approval",
        risk="external, reversible — deletable in the Jira UI (and via REST)",
        holder="Executor",
        route="integrations/jira.py → Jira REST createFilter (native only — no façade counterpart)",
        arguments=["name", "jql", "description?"],
        description="Save a Jira filter (a named, reusable JQL query).",
    ),
    Capability(
        name="jira.create_dashboard",
        kind="write",
        gate="human approval",
        risk="external, reversible — deletable in the Jira UI (and via REST)",
        holder="Executor",
        route="integrations/jira.py → Jira REST createDashboard (native only — no façade counterpart)",
        arguments=["name", "description?", "gadgets?"],
        description=(
            "Create a private Jira dashboard and populate it with gadgets — "
            "each gadget identifier must come from jira.list_gadgets. A "
            "gadget config may bind a filter by 'filterName' (resolved to the "
            "real id at execution time — the composite-proposal case, where "
            "the filter does not exist yet); placeholder filterIds are "
            "refused before anything is created. A gadget's failure does not "
            "undo the dashboard or earlier gadgets; per-gadget outcomes are "
            "reported."
        ),
    ),
    Capability(
        name="jira.create_project",
        kind="write",
        gate="human approval",
        risk=(
            "external, IRREVERSIBLE in effect — deleting a project deletes "
            "every issue inside it; this is the most conservative class in "
            "the Reversibility enum (contract/enums.py), not the reversible "
            "default the other jira.* writes carry"
        ),
        holder="Executor",
        route="integrations/jira.py → Jira REST createProject (native only — no façade counterpart)",
        arguments=["key", "name", "project_type_key?"],
        description=(
            "Create a new Jira project. jira-expert-only (2026-08-13): other "
            "agents were inventing project keys (SUPPORT, CS, PERSONAL) that "
            "failed at execution, so ONLY the agent consulted about where "
            "work belongs may propose this, and only when no existing "
            "project fits. The lead is always the credential's own account. "
            "Deleting a created project takes its issues with it — treat "
            "this as a one-way door, unlike the other jira.* writes above."
        ),
    ),
    Capability(
        name="confluence.create_page",
        kind="write",
        gate="human approval",
        risk="external, reversible — trash_page moves a wrongly created page to the trash, recoverable",
        holder="Executor",
        route="integrations/confluence.py → Confluence REST createPage (v2 on Cloud, v1 content on Server/DC)",
        arguments=["space_id_or_key", "title", "body_storage", "parent_id?"],
        description="Create a new Confluence page. Body is always storage format.",
    ),
    Capability(
        name="confluence.update_page",
        kind="write",
        gate="human approval",
        risk=(
            "external, reversible — versioned, so a previous revision can be "
            "restored in the Confluence UI; the write itself refuses to "
            "execute over a version newer than the one the operator approved"
        ),
        holder="Executor",
        route="integrations/confluence.py → Confluence REST updatePage (version-checked before write)",
        arguments=["page_id", "title", "body_storage", "expected_version"],
        description=(
            "Replace a Confluence page's title/body, bumping its version. "
            "'expected_version' must be the version the proposing agent read "
            "just before drafting — a mismatch at execution time means "
            "someone else changed the page since, and the write is refused "
            "rather than silently overwriting it (the mcp.sync_source "
            "doctrine applied to a version number instead of file bytes)."
        ),
    ),
    Capability(
        name="confluence.move_page",
        kind="write",
        gate="human approval",
        risk="external, reversible — the page can be moved back to its old parent",
        holder="Executor",
        route="integrations/confluence.py → Confluence REST updatePage, repointing the parent/ancestor",
        arguments=["page_id", "new_parent_id"],
        description="Move a Confluence page under a different parent page.",
    ),
    Capability(
        name="confluence.trash_page",
        kind="write",
        gate="human approval",
        risk="external, reversible — trashed pages remain recoverable from Confluence's trash until purged",
        holder="Executor",
        route="integrations/confluence.py → Confluence REST deletePage (moves to trash, not a permanent delete)",
        arguments=["page_id"],
        description="Move a Confluence page to the trash.",
    ),
    Capability(
        name="confluence.upload_attachment",
        kind="write",
        gate="human approval",
        risk="external, reversible — a wrongly uploaded attachment can be deleted from the page in the UI",
        holder="Executor",
        route="integrations/confluence.py → Confluence REST addAttachment (v1 multipart, both flavors)",
        arguments=["page_id", "filename", "content", "encoding?"],
        description=(
            "Upload an attachment to a Confluence page. 'content' rides the "
            "proposal itself (utf8 text, or base64 for binary), captured at "
            "propose time — the Executor writes exactly those bytes and never "
            "re-fetches from any external source (the mcp.sync_source doctrine)."
        ),
    ),
    Capability(
        name="confluence.set_labels",
        kind="write",
        gate="human approval",
        risk="external, reversible — labels can be removed again (integrations/confluence.py's remove_labels)",
        holder="Executor",
        route="integrations/confluence.py → Confluence REST addLabels (v1, add-only on both flavors)",
        arguments=["page_id", "labels"],
        description="Add one or more labels to a Confluence page. Additive — never removes or replaces existing labels.",
    ),
    Capability(
        name="confluence.create_space",
        kind="write",
        gate="human approval",
        risk=(
            "external, IRREVERSIBLE — no delete-space capability is exposed "
            "here; a mistaken space stays until removed by hand in the "
            "Confluence UI (deleting a space deletes every page in it, a "
            "bigger one-way door than this client takes on lightly)"
        ),
        holder="Executor",
        route="integrations/confluence.py → Confluence REST createSpace",
        arguments=["key", "name", "description?"],
        description=(
            "Create a new Confluence space. Granted narrowly (the "
            "jira-project-propose precedent) — propose only when no existing "
            "space fits."
        ),
    ),
    Capability(
        name="charter.update",
        kind="write",
        gate="human approval",
        risk="internal, versioned — a bad charter is one revert away (history is append-only)",
        holder="Executor",
        route="guidance_version table via repo.save_charter_version (D5 coaching loop)",
        arguments=["agent_id", "content", "rationale"],
        description=(
            "Commit an agent-PROPOSED charter edit as a new governed version. "
            "The version row carries the pedigree: proposed by the agent, "
            "approved by the operator — coaching on the record. `agent_id` is "
            "the SUBJECT of the edit, not the drafter (stage 5a): since the "
            "coach drafts edits for other agents, the two differ, and the "
            "drafter is supplied server-side from the proposal row — never as "
            "an argument the agent writes. A deliberate widening of the same "
            "shape as task.create naming an assignee, and the sharper one: it "
            "acts on how a teammate thinks, not on what is on its board."
        ),
    ),
    Capability(
        name="skill.create",
        kind="write",
        gate="human approval",
        risk=(
            "internal, versioned — documents are append-only versions and a "
            "skill can be retired; a wrong skill teaches nobody until an "
            "operator grants it"
        ),
        holder="Executor",
        route="db/repo.py → create_skill + add_skill_doc(doc_key='guidance'), the same pair POST /api/skills uses",
        arguments=["title", "summary", "guidance_content", "describes?"],
        description=(
            "Create a new skill in the team's skills library from an agent's "
            "draft: a title, a one-line summary, and the GUIDANCE document a "
            "granted agent loads in full. The skill id is derived server-side "
            "from the title and an existing id is REFUSED, never silently "
            "retitled — the same rule POST /api/skills enforces for the "
            "operator, because two sources interleaved under one id is how a "
            "library stops being trustworthy. Creating a skill grants it to "
            "nobody: the agent proposes CONTENT, and who holds it stays a "
            "separate operator decision. `added_by` is the DRAFTER, taken "
            "from the proposal row server-side — never an argument."
        ),
    ),
    Capability(
        name="skill.doc_add",
        kind="write",
        gate="human approval",
        risk=(
            "internal, versioned — a new version supersedes the current "
            "document and the prior one stays readable forever; never an "
            "in-place edit"
        ),
        holder="Executor",
        route="db/repo.py → add_skill_doc (new version + rebuilt FTS chunks), the same call POST /api/skills/{id}/docs makes",
        arguments=["skill_id", "doc_key", "title", "content", "source_url?",
                   "captured_at?", "describes?"],
        description=(
            "Add or re-version ONE document on an existing skill. `kind` is "
            "deliberately not an argument: doc_key='guidance' is the guidance "
            "document and every other key is a searchable reference — the "
            "invariant the whole delivery path relies on, enforced here "
            "rather than trusted to a proposal. Refused on an unknown or "
            "RETIRED skill. When the drafting agent worked from a URL the "
            "fetched text was captured at PROPOSE time and is already in "
            "`content`: the Executor writes exactly those bytes and never "
            "re-fetches, so what the operator reviewed is what lands (the "
            "mcp.sync_source doctrine). `added_by` is the DRAFTER, supplied "
            "server-side from the proposal row."
        ),
    ),
    Capability(
        name="task.create",
        kind="write",
        gate="human approval",
        risk=(
            "internal, reversible — a wrongly created task can be cancelled; "
            "but the task RUNS on approval, and its own gated writes park in "
            "the Decisions Inbox like any others"
        ),
        holder="Executor",
        route="api/routes.py → create_and_run_task (THE one task-create path)",
        arguments=["agent_id", "instructions", "title?", "await_result?"],
        description=(
            "Create a first-class Task assigned to a named agent, proposed by "
            "an agent and approved by the operator. The first capability that "
            "acts on Central Command's own work queue rather than an external "
            "system. `agent_id` is the assignee (The operator, 2026-07-25): an agent "
            "may propose work FOR another agent, and the operator's approval "
            "is the delegation — a deliberate widening of D7's star shape, "
            "where agent-to-agent delegation otherwise runs only through the "
            "orchestrator. `await_result` (Decision 5, 2026-08-22): when true, "
            "the drafter's session parks AWAITING_AGENTS on the created task "
            "and is resumed with its distilled outcome once it goes terminal, "
            "instead of being resumed immediately with the bare 'task created' "
            "text — the same wait shape a consultation uses, keyed on the task "
            "id rather than a specialist's session. Ignored on a proposal "
            "drafted during a consultation (that session already ends on this "
            "decision)."
        ),
    ),
    Capability(
        name="litellm.add_model",
        kind="write",
        gate="human approval",
        risk="internal, reversible — a wrong model can be deleted; a wrong mapping re-pointed",
        holder="Executor",
        route="integrations/litellm.py → LiteLLM admin POST /model/new (self-hosted proxy)",
        arguments=["model_name", "model", "model_info?", "extra?"],
        description=(
            "Register a model/alias in the self-hosted LiteLLM proxy. model_name "
            "is the alias callers use (doubles as a Central Command model name); "
            "model is the provider string ('anthropic/claude-…'). model_info "
            "carries custom METADATA (created_by, created_date, …); extra is "
            "litellm_params (rpm, etc.). Provider keys are NEVER in a proposal — "
            "the Executor supplies them (a keyless re-registration once broke the "
            "Claude models, 2026-07-22)."
        ),
    ),
    Capability(
        name="litellm.update_model",
        kind="write",
        gate="human approval",
        risk="internal, reversible — an in-place field edit; the stored provider key is preserved by the proxy's PATCH merge",
        holder="Executor",
        route="integrations/litellm.py → LiteLLM admin PATCH /model/{model_id}/update (self-hosted proxy)",
        arguments=["model_id", "model_info?", "litellm_params?", "model_name?"],
        description=(
            "Edit an EXISTING model in place, addressed by its proxy model_id "
            "(from litellm.list_models). Only the sent fields change; everything "
            "omitted — including the stored, encrypted provider key — is preserved "
            "(true partial merge, verified on LiteLLM 1.84.0). This is the "
            "churn-free, key-safe way to fix metadata (native created_at/created_by), "
            "tune params (rpm/tpm/timeout/cost), or re-point an alias — replacing "
            "the delete+add pattern that dropped the Claude keys (2026-07-22). A "
            "field cannot be nulled via update, only overwritten."
        ),
    ),
    Capability(
        name="litellm.delete_model",
        kind="write",
        gate="human approval",
        risk="internal, reversible but LIVE — deleting an alias Central Command routes to breaks runs until re-added",
        holder="Executor",
        route="integrations/litellm.py → LiteLLM admin POST /model/delete (self-hosted proxy)",
        arguments=["model_id"],
        description=(
            "Remove a model from the LiteLLM proxy by its proxy model_id (from "
            "litellm.list_models, NOT the alias). Paired with add_model in one "
            "proposal, this re-points an alias — the model-tiering knob."
        ),
    ),
    Capability(
        name="litellm.create_key",
        kind="write",
        gate="human approval",
        risk="internal, reversible — a virtual key can be deleted; issuing one changes nothing Central Command routes through yet",
        holder="Executor",
        route="integrations/litellm.py → LiteLLM admin POST /key/generate (self-hosted proxy)",
        arguments=["key_alias", "models?", "metadata?", "tags?", "duration?"],
        description=(
            "Issue a virtual key for spend attribution / model scoping. key_alias "
            "is the human label; models scopes callable aliases; tags drive per-tag "
            "spend. Budgets/rate-limits are NOT set here. The plaintext key is "
            "returned by the proxy ONCE and NEVER retained or logged — only a "
            "masked preview + token_id reach the record. Central Command does not USE keys until "
            "per-agent wiring (Phase 5); a usable token comes from regenerating."
        ),
    ),
    Capability(
        name="litellm.update_key",
        kind="write",
        gate="human approval",
        risk="internal, reversible — edits a key's scope/metadata/alias in place; no secret involved",
        holder="Executor",
        route="integrations/litellm.py → LiteLLM admin POST /key/update (self-hosted proxy)",
        arguments=["key", "models?", "metadata?", "tags?", "key_alias?", "duration?"],
        description=(
            "Edit a virtual key in place, addressed by its token (the hash from "
            "list_keys — Central Command holds no plaintext). Change model scope, metadata, tags, "
            "alias, or duration. Patch semantics; budgets/rate-limits not touched."
        ),
    ),
    Capability(
        name="litellm.create_team",
        kind="write",
        gate="human approval",
        risk="internal, reversible — a team can be deleted; creating one changes nothing Central Command routes through",
        holder="Executor",
        route="integrations/litellm.py → LiteLLM admin POST /team/new (self-hosted proxy)",
        arguments=["team_alias", "models?"],
        description=(
            "Create a team for attribution / model scoping. team_alias is the human "
            "name; models scopes the team's keys. Budgets/rate-limits not set here."
        ),
    ),
    Capability(
        name="litellm.delete_team",
        kind="write",
        gate="human approval",
        risk="internal, reversible but LIVE — the team's keys lose their team scope until re-created",
        holder="Executor",
        route="integrations/litellm.py → LiteLLM admin POST /team/delete (self-hosted proxy)",
        arguments=["team_id"],
        description="Delete a team by its team_id (from litellm.list_teams).",
    ),
    Capability(
        name="litellm.delete_key",
        kind="write",
        gate="human approval",
        risk="internal, reversible but LIVE — deleting a key Central Command routes through breaks that traffic until re-issued",
        holder="Executor",
        route="integrations/litellm.py → LiteLLM admin POST /key/delete (self-hosted proxy)",
        arguments=["key_alias?", "key?"],
        description=(
            "Delete a virtual key by its alias (preferred — Central Command does not hold the "
            "plaintext). Reversible in effect (re-issue), but live traffic on that "
            "key breaks until then."
        ),
    ),
    Capability(
        name="litellm.set_fallbacks",
        kind="write",
        gate="human approval",
        risk="internal, reversible but LIVE — changes router behavior for that model on approval",
        holder="Executor",
        route="integrations/litellm.py → LiteLLM admin POST /fallback (self-hosted proxy)",
        arguments=["model", "fallback_models", "fallback_type?"],
        description=(
            "Set a model's fallback chain: when a call to `model` fails after "
            "retries, the router tries `fallback_models` (aliases) in order. "
            "fallback_type is 'general' (default), 'context_window', or "
            "'content_policy'. The resilience/tiering knob — routing STRATEGY "
            "itself is config-level and not settable here."
        ),
    ),
    Capability(
        name="litellm.delete_fallbacks",
        kind="write",
        gate="human approval",
        risk="internal, reversible but LIVE — that model loses its fallback behavior on approval",
        holder="Executor",
        route="integrations/litellm.py → LiteLLM admin DELETE /fallback/{model} (self-hosted proxy)",
        arguments=["model", "fallback_type?"],
        description=(
            "Remove a model's fallback chain for a given fallback_type. Reversible "
            "(re-set it), but the model runs without that fallback until then."
        ),
    ),
    Capability(
        name="litellm.apply_config_change",
        kind="write",
        gate="human approval",
        risk=(
            "internal, HIGHEST BLAST RADIUS in the registry — a bad config "
            "takes ALL inference down for the whole team; the arc RESTARTS the "
            "proxy (a short gap even when it works), and a failing post-check "
            "AUTO-ROLLS-BACK to the previous file contents, re-renders and "
            "restarts again. Rollback that itself fails is an operator incident"
        ),
        holder="Executor",
        route=(
            "gateway/executor.py → write deploy/pi/litellm/*.yaml + git commit → "
            "re-render configmap/cc-litellm-config (and policy.py --apply for "
            "model-preferences.yaml) → kubectl rollout restart deploy/cc-litellm "
            "→ deploy/pi/litellm/checks.sh"
        ),
        arguments=["files ({path: full new content}, only deploy/pi/litellm/config.yaml "
                   "and deploy/pi/litellm/model-preferences.yaml)"],
        description=(
            "Change the LiteLLM settings that have NO admin API — routing "
            "strategy, num_retries, cooldowns, per-deployment rpm/tpm/order, "
            "callbacks, cache settings — by editing the config FILES and "
            "restarting the proxy under a verified arc: validate (YAML parses, "
            "no secret shapes, required aliases intact) → pre-check the running "
            "proxy (a failing pre-check ABORTS, nothing is written) → snapshot "
            "→ write + commit → re-render + restart + wait Ready → post-check → "
            "auto-rollback on failure. Content is captured at PROPOSE time and "
            "written verbatim (the mcp.sync_source doctrine). model-preferences.yaml "
            "must move in the SAME proposal when routing intent changes, or the "
            "post-check's policy.py --check fails and the change rolls back."
        ),
    ),
    Capability(
        name="calendar.create_event",
        kind="write",
        gate="human approval",
        risk=(
            "external, reversible — a wrongly created event can be deleted, but "
            "any invited attendee has already been notified"
        ),
        holder="Executor",
        route="integrations/calendar_facade.py → n8n cc-calendar-facade webhook, mode create_event (Google OAuth stays in n8n)",
        arguments=["title", "start", "end", "description?", "attendees?", "calendar_id?"],
        description=(
            "Put a new event on the operator's calendar. start/end are RFC3339 "
            "with an explicit offset or Z — the façade compares instants, never "
            "strings. Attendees are email addresses and inviting one sends mail "
            "to a real person, so the proposal must name them explicitly."
        ),
    ),
    Capability(
        name="calendar.update_event",
        kind="write",
        gate="human approval",
        risk=(
            "external, reversible — a PATCH, so omitted fields keep their values "
            "and a wrong change can be changed back; attendees are re-notified"
        ),
        holder="Executor",
        route="integrations/calendar_facade.py → n8n cc-calendar-facade webhook, mode update_event (Google OAuth stays in n8n)",
        arguments=["event_id", "title?", "start?", "end?", "description?", "attendees?", "calendar_id?"],
        description=(
            "Change an existing event, addressed by the event_id from a read. "
            "Partial merge: only the fields supplied change. Moving a meeting is "
            "an update, not a delete-and-recreate — recreating loses the "
            "attendees' responses and the thread of the original invitation."
        ),
    ),
    Capability(
        name="calendar.delete_event",
        kind="write",
        gate="human approval",
        risk=(
            "external, IRREVERSIBLE in effect — cancelling notifies every "
            "attendee and a recreated event does not restore their acceptances "
            "or the original invitation thread; the biggest blast radius on the "
            "calendar surface"
        ),
        holder="Executor",
        route="integrations/calendar_facade.py → n8n cc-calendar-facade webhook, mode delete_event (Google OAuth stays in n8n)",
        arguments=["event_id", "reason", "calendar_id?"],
        description=(
            "Cancel an event, addressed by the event_id from a read. `reason` is "
            "required and rides the proposal for review: cancelling someone's "
            "meeting is the one calendar action whose WHY the operator must see "
            "before approving, and it is recorded with the decision afterwards. "
            "Prefer calendar.update_event whenever the meeting is moving rather "
            "than ending (operator decision 2026-08-06 approved delete into v1)."
        ),
    ),
    # --- lines of effort (2026-08-12) -----------------------------------------
    Capability(
        name="loe.record_checkin",
        kind="write",
        gate="human approval",
        risk=(
            "internal, append-only — a check-in row is never updated or "
            "deleted, so a wrong one is corrected by the NEXT check-in saying "
            "so, not by rewriting the record"
        ),
        holder="Executor",
        route="db/repo.py → record_loe_checkin (append-only loe_checkin table)",
        arguments=["loe_id", "light", "confidence", "summary"],
        description=(
            "Record the outcome of a check-in conversation on a line of "
            "effort: a light (green|yellow|red), a 0-100 confidence, and a "
            "summary of what the operator actually said. The row is stamped "
            "with the LOE's CURRENT semantic version by the Executor — never "
            "by the proposal — so a past score keeps the meaning it was "
            "scored under."
        ),
    ),
    Capability(
        name="loe.create",
        kind="write",
        gate="human approval",
        risk=(
            "internal, reversible — a line of effort that should not exist is "
            "retired through loe.update, which keeps its whole history"
        ),
        holder="Executor",
        route="db/repo.py → create_loe (line_of_effort table)",
        arguments=["loe_id", "name", "agent_id", "semantic", "cadence?",
                   "presentation?"],
        description=(
            "Bring a new line of effort into existence on the operator's "
            "behalf, after interviewing them: the goal's name, the questions "
            "its check-ins ask and the thresholds a light is judged against "
            "(`semantic`), and how often it is checked (`cadence`, default "
            "weekly). The read-before-propose is loe.list — a goal the "
            "operator already has is a recalibration (loe.update), not a "
            "second line saying the same thing, and `loe_id` must not collide "
            "with an existing one. The questions and thresholds are the "
            "operator's own answers from the interview, never invented for "
            "them: this capability writes down a goal they stated, it does "
            "not set one."
        ),
    ),
    Capability(
        name="loe.update",
        kind="write",
        gate="human approval",
        risk=(
            "internal, reversible — a recalibration is another update away, "
            "and retire is a status flip that keeps the whole history"
        ),
        holder="Executor",
        route="db/repo.py → update_loe / retire_loe (line_of_effort table)",
        arguments=["loe_id", "name?", "cadence?", "semantic?", "presentation?",
                   "retire?", "reason?"],
        description=(
            "Recalibrate a line of effort — its name, cadence, questions and "
            "thresholds (`semantic`), or display config (`presentation`) — or "
            "retire it with `retire: true` plus a `reason`. A CHANGED "
            "`semantic` bumps semantic_version, so past check-ins keep the "
            "targets they were scored against: recalibration is honest, "
            "silent target drift is not."
        ),
    ),
    # --- bulk dismissal, agent path (2026-08-17 design, slice 2) --------------
    Capability(
        name="work.bulk_dismiss",
        kind="write",
        gate="human approval",
        risk=(
            "internal, reversible — the covered emails land FOLDED, and a "
            "FOLDED item is reopened from the cockpit (POST /api/work/{id}/"
            "reopen) exactly like any other folded sibling; nothing leaves "
            "Central Command and no mail is deleted anywhere"
        ),
        holder="Executor",
        route=(
            "no external call at all — the approval's own fold commit IS the "
            "effect (gateway._record_execution_success → repo.commit_folds on "
            "the rows propose_bulk_dismiss reserved as FOLD_PENDING); the "
            "handler only re-counts and reports"
        ),
        arguments=["query", "unprocessed", "match_digest", "provider_matches?",
                   "samples?"],
        description=(
            "Dismiss a PINNED set of bulk/promotional emails in one decision. "
            "The set is resolved at PROPOSE time — `query` is the Gmail search "
            "the agent ran, `unprocessed` how many ledger rows it matched, "
            "`match_digest` the fingerprint of exactly those rows — and only "
            "those rows are reserved. New mail matching the same query later "
            "is NOT covered: this is a pinned set, never a standing rule "
            "(design decision 1; standing rules are trust-tier graduation "
            "territory). The Executor performs no external write, because "
            "there is nothing outside to change: approving commits the folds "
            "the propose already reserved, and rejecting or dismissing "
            "releases them back to UNPROCESSED through the same seams every "
            "thread fold uses."
        ),
    ),
    # --- ungated reads: layered context, no approval needed -------------------
    Capability(
        name="loe.list",
        kind="read",
        gate="ungated read",
        risk="none to the world — reads the operator's own goal records",
        holder="agent (loe_list tool)",
        route="db/repo.py → list_loes + list_loe_checkins (read-only)",
        arguments=[],
        description=(
            "Every active line of effort with its current questions and "
            "thresholds, plus its recent check-in history. The "
            "read-before-propose for loe.record_checkin: the questions asked "
            "come from here, never from memory."
        ),
    ),
    Capability(
        name="graph.search_facts",
        kind="read",
        gate="ungated read",
        risk="none to the world — facts are data to the agent, never instructions",
        holder="agent (search_knowledge_graph tool)",
        route="Graphiti MCP search_facts, groups main + central_command",
        arguments=["query"],
        description="Search team knowledge for facts, with temporal validity flags.",
    ),
    Capability(
        name="graph.search_nodes",
        kind="read",
        gate="ungated read",
        risk="none to the world",
        holder="control plane",
        route="Graphiti MCP search_nodes, groups main + central_command",
        arguments=["query"],
        description="Search knowledge-graph entities.",
    ),
    Capability(
        name="graph.get_status",
        kind="read",
        gate="ungated read",
        risk="none to the world",
        holder="control plane",
        route="Graphiti MCP get_status",
        arguments=[],
        description="Health check of the knowledge-graph service.",
    ),
    Capability(
        name="calendar.list_events",
        kind="read",
        gate="ungated read",
        risk="none to the world — a list; changing the calendar is a separate, gated capability",
        holder="agent (read_calendar tool) + control plane (the EA's brief block)",
        route="n8n cc-calendar-facade webhook (Google OAuth stays in n8n)",
        arguments=["time_min", "time_max", "calendar_id?"],
        description=(
            "The operator's calendar over an RFC3339 window, normalised and "
            "conflict-checked in Python before any model sees it. Reading stays "
            "ungated; since 2026-08-06 the same façade also serves the three "
            "calendar WRITES, which are Executor-only and human-gated — an "
            "agent holding this read holds no way to change anything it sees."
        ),
    ),
    Capability(
        name="jira.get_issue",
        kind="read",
        gate="ungated read",
        risk="none to the world",
        holder="agents (jira_get_issue tool) + control plane (verification reads)",
        route="integrations/jira.py → Jira REST getIssue (native, D23; n8n façade fallback until CC_JIRA_API_TOKEN is set)",
        arguments=["issue_key"],
        description=(
            "Read one Jira issue — agents check reality before advising or "
            "proposing; the control plane verifies writes from the outside. "
            "Returns ownership (assignee; null = unassigned), staleness "
            "(updated), epic structure (parent) and typed dependencies (links, "
            "each phrased in its stored direction) — the hygiene axes added "
            "2026-07-25 after jira-expert declared them missing."
        ),
    ),
    Capability(
        name="jira.search_issues",
        kind="read",
        gate="ungated read",
        risk="none to the world",
        holder="agents (jira_search_issues tool) + control plane",
        route="integrations/jira.py → Jira REST searchIssues (native, D23; n8n façade fallback until CC_JIRA_API_TOKEN is set)",
        arguments=["jql", "limit?"],
        description=(
            "Search Jira with a bounded JQL query. Rows carry assignee, "
            "updated, parent and a link_count — deliberately a COUNT, not the "
            "links themselves, so a board-wide sweep stays within budget; open "
            "a specific issue with jira.get_issue for its dependencies."
        ),
    ),
    Capability(
        name="jira.get_transitions",
        kind="read",
        gate="ungated read",
        risk="none to the world",
        holder="agents (jira_get_transitions tool)",
        route="integrations/jira.py → Jira REST getTransitions (native, D23; n8n façade fallback until CC_JIRA_API_TOKEN is set)",
        arguments=["issue_key"],
        description=(
            "The legal next statuses for an issue — read before proposing a "
            "transition, so proposals name real moves."
        ),
    ),
    Capability(
        name="jira.list_fields",
        kind="read",
        gate="ungated read",
        risk="none to the world",
        holder="agents (jira_list_fields tool)",
        route="integrations/jira.py → Jira REST /rest/api/3/field (native only — no façade counterpart)",
        arguments=[],
        description=(
            "The custom fields this instance declares — name, type, and "
            "whether jira.set_fields can write them. The same discovery feeds "
            "jira.get_issue and jira.search_issues, which is why those reads "
            "now carry `custom_fields` keyed by human name: an agent never "
            "needs a customfield_NNNNN id, and must never ask the operator for "
            "one."
        ),
    ),
    Capability(
        name="jira.list_filters",
        kind="read",
        gate="ungated read",
        risk="none to the world",
        holder="agents (jira_list_filters tool)",
        route="integrations/jira.py → Jira REST filter/search (native only — no façade counterpart)",
        arguments=["query?"],
        description=(
            "List saved Jira filters (id, name, jql, description, owner) — "
            "read before proposing jira.create_filter, so proposals never "
            "duplicate an existing one. First page only."
        ),
    ),
    Capability(
        name="jira.list_dashboards",
        kind="read",
        gate="ungated read",
        risk="none to the world",
        holder="agents (jira_list_dashboards tool)",
        route="integrations/jira.py → Jira REST dashboard/search (native only — no façade counterpart)",
        arguments=["query?"],
        description=(
            "List Jira dashboards (id, name, description, view) — read "
            "before proposing jira.create_dashboard. First page only."
        ),
    ),
    Capability(
        name="jira.list_gadgets",
        kind="read",
        gate="ungated read",
        risk="none to the world",
        holder="agents (jira_list_gadgets tool)",
        route="integrations/jira.py → Jira REST dashboard/gadgets (native only — no façade counterpart)",
        arguments=[],
        description=(
            "List every gadget a Jira dashboard can host (title, uri, "
            "module_key). A jira.create_dashboard proposal may only name a "
            "uri/module_key that appears here — never a guess."
        ),
    ),
    Capability(
        name="litellm.list_models",
        kind="read",
        gate="ungated read",
        risk="none to the world — the proxy never returns provider keys (encrypted)",
        holder="agent (litellm_list_models tool)",
        route="integrations/litellm.py → LiteLLM admin GET /model/info (self-hosted proxy)",
        arguments=[],
        description=(
            "List the proxy's models with alias + provider mapping — read before "
            "proposing a change so proposals name real aliases and model_ids."
        ),
    ),
    Capability(
        name="litellm.list_credentials",
        kind="read",
        gate="ungated read",
        risk="none to the world — the proxy never returns credential secret values (encrypted)",
        holder="agent (litellm_list_credentials tool)",
        route="integrations/litellm.py → LiteLLM admin GET /credentials (self-hosted proxy)",
        arguments=[],
        description=(
            "List the proxy's NAMED credentials — a model can reference one "
            "(litellm_credential_name) to authenticate without carrying a key, the "
            "preferred key-safe auth path. Read before proposing a model that "
            "reuses a stored credential."
        ),
    ),
    Capability(
        name="litellm.list_keys",
        kind="read",
        gate="ungated read",
        risk="none to the world — the proxy returns key HASHES + spend, never a usable secret",
        holder="agent (litellm_list_keys tool)",
        route="integrations/litellm.py → LiteLLM admin GET /key/list (self-hosted proxy)",
        arguments=[],
        description=(
            "List the proxy's virtual keys — alias, model scope, spend, id — read "
            "before proposing to create or delete a key. Never returns a plaintext key."
        ),
    ),
    Capability(
        name="litellm.get_routing",
        kind="read",
        gate="ungated read",
        risk="none to the world — read-only routing config",
        holder="agent (litellm_get_routing tool)",
        route="integrations/litellm.py → LiteLLM admin GET /router/settings (self-hosted proxy)",
        arguments=[],
        description=(
            "The routing picture: strategy + retry count (advisory) and configured "
            "fallback chains (actionable). Read before proposing a fallback change."
        ),
    ),
    Capability(
        name="litellm.list_teams",
        kind="read",
        gate="ungated read",
        risk="none to the world",
        holder="agent (litellm_list_teams tool)",
        route="integrations/litellm.py → LiteLLM admin GET /team/list (self-hosted proxy)",
        arguments=[],
        description=(
            "List the proxy's teams — alias, id, model scope. Read before proposing "
            "a team change so proposals name real team_ids."
        ),
    ),
    Capability(
        name="litellm.get_health",
        kind="read",
        gate="ungated read",
        risk="none to the world — reads proxy status, no provider calls",
        holder="agent (litellm_get_health tool)",
        route="integrations/litellm.py → LiteLLM admin GET /health/readiness/details (self-hosted proxy)",
        arguments=[],
        description=(
            "The proxy's own health — status, DB, cache, version, callbacks. Cheap; "
            "the 'is the proxy up' read."
        ),
    ),
    Capability(
        name="litellm.check_model_health",
        kind="read",
        gate="ungated read",
        risk="internal — makes a REAL provider call per model checked (tokens + latency), never a world write",
        holder="agent (litellm_check_model_health tool)",
        route="integrations/litellm.py → LiteLLM admin GET /health (self-hosted proxy)",
        arguments=["model?"],
        description=(
            "Live health of the models — which answer, which fail (with the error). "
            "EXPENSIVE (a real provider call per model); for diagnosing a suspected "
            "outage, ideally one alias at a time."
        ),
    ),
    Capability(
        name="litellm.get_spend",
        kind="read",
        gate="ungated read",
        risk="none to the world",
        holder="agent (litellm_get_spend tool)",
        route="integrations/litellm.py → LiteLLM admin GET /spend/logs (self-hosted proxy)",
        arguments=[],
        description=(
            "Recent spend aggregated per model and key — the 'what is costing "
            "money' read over a rolling request-log window."
        ),
    ),
    Capability(
        name="litellm.read_config",
        kind="read",
        gate="ungated read",
        risk=(
            "none to the world — reads three tracked repo files; config.yaml/"
            "model-preferences.yaml reference env vars never raw secrets, and "
            "the k8s manifest references secret NAMES via secretKeyRef, never "
            "values"
        ),
        holder="agent (litellm_read_config tool)",
        route=(
            "repo files deploy/pi/litellm/{config.yaml,model-preferences.yaml} "
            "and deploy/k3s/30-litellm.yaml (path-allowlisted)"
        ),
        arguments=["path"],
        description=(
            "Current content of a LiteLLM config or deployment file. "
            "config.yaml and model-preferences.yaml are the two files a "
            "litellm.apply_config_change proposal may carry — read-before-"
            "propose for full-file changes. 30-litellm.yaml (the k8s "
            "Deployment manifest) is readable for TROUBLESHOOTING ONLY and is "
            "not proposable through that capability. Any other path is "
            "refused."
        ),
    ),
    Capability(
        name="litellm.read_logs",
        kind="read",
        gate="ungated read",
        risk=(
            "none to the world — logs may carry request metadata but never "
            "virtual-key plaintext (LiteLLM masks keys in its own logs)"
        ),
        holder="agent (litellm_read_logs tool)",
        route=(
            "kubectl logs deploy/cc-litellm via a READ-ONLY scoped kubeconfig "
            "(CC_LITELLM_LOG_KUBECONFIG, SA cc-litellm-logreader) — distinct "
            "from the Executor's write kubeconfig"
        ),
        arguments=["tail_lines?", "previous?"],
        description=(
            "Recent log lines from the LiteLLM proxy pod. `previous=True` reads "
            "the prior container's logs, for crash diagnosis. Degrades honestly "
            "when the read-only kubeconfig is not provisioned."
        ),
    ),
    Capability(
        name="litellm.list_mcp_servers",
        kind="read",
        gate="ungated read",
        risk="none to the world — reads registration rows, no provider calls",
        holder="agent (litellm_list_mcp_servers tool)",
        route="integrations/litellm.py → LiteLLM admin GET /v1/mcp/server (self-hosted proxy)",
        arguments=[],
        description=(
            "The MCP servers registered on the proxy — id, alias, url, transport. "
            "Visibility for diagnosis; every MCP WRITE stays with the MCP "
            "pipeline's owner (mcp-manager, decided 2026-08-06)."
        ),
    ),
    Capability(
        name="email.list_refs",
        kind="read",
        gate="ungated read",
        risk="none to the world — read-only by construction",
        holder="feed poller / backlog sweeper",
        route="n8n façade cc-email-facade → lib-email-provider",
        arguments=["scope_query"],
        description="List message references matching a Gmail query (ids only, no bodies).",
    ),
    Capability(
        name="web.fetch",
        kind="read",
        gate="ungated read",
        risk=(
            "none to the world — a GET, converted to text/markdown; the only "
            "state it touches is the far server's own access log"
        ),
        holder="agent (fetch_url tool), granted only via the web-read pack",
        route="integrations/webfetch.py → httpx GET, optional client-PKI (mTLS) from config",
        arguments=["url"],
        description=(
            "Fetch one URL and return its content as text/markdown — HTML "
            "converted (script/style/nav stripped), JSON/plain text as-is, "
            "binary content degraded to an honest note rather than raw bytes. "
            "Client-PKI identity (CC_FETCH_CLIENT_CERT/_KEY/_CA_BUNDLE) is ONE "
            "outbound identity fixed in config for the whole process — never a "
            "per-call agent argument, so an agent cannot pick which "
            "certificate it authenticates as. Size-capped and explicitly "
            "truncated, never silently."
        ),
    ),
    Capability(
        name="email.get_message",
        kind="read",
        gate="ungated read",
        risk="none to the world — the façade refuses attachments (D13: text only)",
        holder="dispatcher (hydrate at claim)",
        route="n8n façade cc-email-facade → lib-email-provider",
        arguments=["uuid"],
        description="Fetch one email's content, at claim time, inside the retry machinery.",
    ),

    # --- in-sandbox activity (D-sandbox, slice 1): ungated, no exit ----------
    # gate="ungated" (not "ungated read") — these DO mutate state, but only the
    # state of an agent's own ephemeral, gVisor-isolated sandbox pod. Nothing
    # here is a registered gated write (see gated_write_names()): the sandbox
    # has no exit, so none of these can touch anything durable. The one gated
    # seam that changes that, mcp.sync_source, is a later slice — see the
    # policy note below.
    Capability(
        name="sandbox.exec",
        kind="write",
        gate="ungated",
        risk=(
            "none outside the agent's own ephemeral pod — gVisor-isolated, "
            "no network egress (default-deny NetworkPolicy), no Central Command "
            "credentials reachable (the sandbox-runner holds none), deleted "
            "on session close or TTL"
        ),
        holder="agent (sandbox_exec tool), granted only via the sandbox pack",
        route="central_command/sandbox/runner.py → kubectl exec into the session's Job pod",
        arguments=["command", "timeout?"],
        description="Run a shell command inside the agent's own sandbox session.",
    ),
    Capability(
        name="sandbox.write_file",
        kind="write",
        gate="ungated",
        risk="none outside the agent's own ephemeral pod — same containment as sandbox.exec",
        holder="agent (sandbox_write_file tool), granted only via the sandbox pack",
        route="central_command/sandbox/runner.py → kubectl exec (base64 write) into the session's Job pod",
        arguments=["path", "content"],
        description=(
            "Write a file into the sandbox's /workspace. Refused if `path` "
            "tries to escape /workspace (any '..', or absolute outside it)."
        ),
    ),
    Capability(
        name="sandbox.read_file",
        kind="read",
        gate="ungated read",
        risk="none — a read of the agent's own ephemeral pod",
        holder="agent (sandbox_read_file tool), granted only via the sandbox pack",
        route="central_command/sandbox/runner.py → kubectl exec (base64 read) from the session's Job pod",
        arguments=["path"],
        description="Read a file back from the sandbox's /workspace. Capped at 200,000 bytes, explicitly truncated.",
    ),
    Capability(
        name="sandbox.copy_in",
        kind="write",
        gate="ungated",
        risk=(
            "none outside the agent's own ephemeral pod, AND the source is "
            "restricted by operator grant — `agent_sandbox_source` decides "
            "what may be copied, never the agent's own choice of path"
        ),
        holder="agent (sandbox_copy_in tool), granted only via the sandbox pack",
        route=(
            "runtime/tools.py checks agent_sandbox_source (DB) BEFORE calling "
            "central_command/sandbox/runner.py → kubectl cp into the session's Job pod"
        ),
        arguments=["source_path"],
        description=(
            "Copy a repo-root-relative path into the sandbox workspace — "
            "refused unless the calling agent holds an active "
            "`agent_sandbox_source` grant naming that path or a parent "
            "directory of it. The runner itself never checks this (it has no "
            "DB access at all); the check happens in the runtime tool."
        ),
    ),
    Capability(
        name="sandbox.reset",
        kind="write",
        gate="ungated",
        risk="none outside the agent's own ephemeral pod — deletes it, nothing more",
        holder="agent (sandbox_reset tool), granted only via the sandbox pack",
        route="central_command/sandbox/runner.py → kubectl delete job (cascade=foreground)",
        arguments=[],
        description="Destroy the agent's current sandbox session; the next sandbox call creates a fresh one.",
    ),

    # --- mcp.sync_source (D-sandbox slice 2): the one gated exit -------------
    Capability(
        name="mcp.sync_source",
        kind="write",
        gate="human approval",
        risk=(
            "writes into this repo's servers/<id>/ tree and commits (and "
            "pushes) to master — reversible via git, but it is the seam "
            "everything after it (build/deploy/register) will trust"
        ),
        holder="Executor",
        route=(
            "gateway/executor.py: git-writes files under settings."
            "mcp_servers_root (default <repo root>/servers), `git add` + "
            "`git commit` + `git push origin master`; a push failure does "
            "not fail the execution (the commit is durable locally)"
        ),
        arguments=["server_id", "files", "summary", "diff"],
        description=(
            "Sync reviewed files from an agent sandbox into servers/<id>/ in "
            "the main repo — the ONLY exit from the sandbox pack (D-sandbox "
            "slice 2)."
        ),
    ),

    # --- mcp.build_image / mcp.server_deploy (D-sandbox slice 3) -------------
    Capability(
        name="mcp.build_image",
        kind="write",
        gate="human approval",
        risk=(
            "builds a container image (nothing running anywhere yet) from "
            "already-reviewed source only; the build host runs a shell "
            "pipeline the Executor constructs from a validated server_id — "
            "reversible in effect (the image can simply not be deployed)"
        ),
        holder="Executor",
        route=(
            "gateway/executor.py: tar the reviewed servers/<id>/ tree over "
            "ssh to settings.mcp_build_host, `podman build` from stdin, "
            "import into that host's containerd (-n k8s.io), verify presence "
            "by an exact-match grep of `ctr images ls -q`, record image_ref + "
            "digest"
        ),
        arguments=["server_id"],
        description=(
            "Build a container image for an mcp_server whose source has "
            "already passed mcp.sync_source review (status "
            "synced/built/deployed/registered) and whose servers/<id>/ tree "
            "carries its own Dockerfile — refused otherwise. Single-arch, "
            "pinned to one build host (design §9.3), tagged with an "
            "immutable per-proposal suffix, never :latest."
        ),
    ),
    Capability(
        name="mcp.server_deploy",
        kind="write",
        gate="human approval",
        risk=(
            "a namespace-scoped k8s object (Deployment+Service in cc-mcp), "
            "never cluster-scoped — applied via a SCOPED kubeconfig, "
            "resource-limited and gVisor-isolated by construction"
        ),
        holder="Executor",
        route=(
            "gateway/executor.py: `kubectl --kubeconfig "
            "settings.mcp_deploy_kubeconfig -n cc-mcp apply -f -` with a "
            "rendered Deployment+Service, then `rollout status`"
        ),
        arguments=["server_id"],
        description=(
            "Deploy an mcp_server whose image has already been built "
            "(mcp.build_image approved) to namespace cc-mcp: one replica, "
            "gVisor runtimeClass, REQUIRED nodeAffinity to the build host "
            "(the image exists there only), imagePullPolicy IfNotPresent, "
            "automountServiceAccountToken false, and MANDATORY resource "
            "limits — the handler refuses to apply ANY manifest (including "
            "its own) with no resources.limits set (design §8). CONVENTION: "
            "every agent-built MCP server listens on port 8000 — the "
            "container port and the Service's target port are both fixed to "
            "it, never agent-chosen. LiteLLM registration (mcp tool grants) "
            "is a later slice; this capability only makes the pod exist."
        ),
    ),

    # --- litellm.register_mcp_server (D-sandbox slice 4) ---------------------
    Capability(
        name="litellm.register_mcp_server",
        kind="write",
        gate="human approval",
        risk=(
            "makes a deployed server's tools reachable through the LiteLLM "
            "MCP gateway to any agent later granted access — no credentials "
            "or secrets ride the proposal; reversible via a later "
            "deregister"
        ),
        holder="Executor",
        route=(
            "central_command/integrations/litellm.py: POST /v1/mcp/server with "
            "the proxy admin key (litellm-manager-style — the Executor "
            "alone holds it); idempotent — an already-registered server "
            "(row carries a litellm_alias the proxy still lists) is reported "
            "rather than re-created"
        ),
        arguments=["server_id", "transport"],
        description=(
            "Register an mcp_server whose deploy has already been approved "
            "(mcp.server_deploy, status=deployed) with LiteLLM's MCP "
            "gateway at http://<server_id>.<namespace>.svc.cluster.local:8000/mcp. "
            "`transport` is 'http' (default, streamable-HTTP) or 'sse' — no "
            "other value is accepted. Also adds the new server to the shared "
            "virtual key's object_permission.mcp_servers (merge, idempotent, "
            "non-fatal-but-loud) — without that the key gets an EMPTY tools "
            "list from the proxy, a silent failure (live finding 2026-08-05)."
        ),
    ),

    # --- mcp.server_remove (operator decision 2026-08-03) ---------------------
    Capability(
        name="mcp.server_remove",
        kind="write",
        gate="human approval",
        risk=(
            "tears down the running Deployment+Service in cc-mcp and "
            "deregisters from LiteLLM's MCP gateway — one intent, one "
            "approval (operator decision 2026-08-03); archive-in-place: "
            "servers/<id>/ source and built image tags are never touched, "
            "so a later mcp.server_deploy can bring the same server back"
        ),
        holder="Executor",
        route=(
            "gateway/executor.py: `kubectl --kubeconfig "
            "settings.mcp_deploy_kubeconfig -n cc-mcp delete "
            "deployment/<id> service/<id> --ignore-not-found`, then "
            "central_command/integrations/litellm.py's deregister_mcp_server "
            "(idempotent — skipped cleanly if the row never registered or "
            "the proxy no longer lists it)"
        ),
        arguments=["server_id"],
        description=(
            "Remove an mcp_server: delete its k8s objects (idempotent,"
            "ignore-not-found), deregister it from LiteLLM if registered, "
            "and flip the row to status='retired'. Refuses a missing row or "
            "one already retired. Does NOT touch servers/<id>/ source or "
            "image tags — a deregister-only capability is a later slice, not "
            "this one."
        ),
    ),

    # --- mcp.tool_call (D-sandbox slice 5) ------------------------------------
    Capability(
        name="mcp.tool_call",
        kind="write",
        gate="human approval",
        risk=(
            "whatever the invoked TOOL does — unknown in general, which is "
            "exactly why a tool defaults to this gate; a per-agent operator "
            "override (agent_mcp_gate_override) is the only way a tool skips "
            "it, and that row is written by the operator alone, never a "
            "propose path"
        ),
        holder="Executor",
        route=(
            "central_command/integrations/litellm.py → LiteLLM POST "
            "/mcp-rest/tools/call (admin key; Executor-only, post-approval)"
        ),
        arguments=["server_id", "tool_name", "arguments", "rationale"],
        description=(
            "ONE static capability covering every gated MCP-served tool "
            "invocation, across every registered server — the registry does "
            "NOT grow a new entry per discovered tool (design §7's "
            "'gated tools never lazily loaded' rule: a gated tool is reachable "
            "only through this one propose wrapper, never a direct callable). "
            "Which specific (server_id, tool_name) is gated for a given agent "
            "is decided at toolset-build time from `mcp_tool.default_gate` "
            "plus that agent's unrevoked `agent_mcp_gate_override` row, if any "
            "— an ungated tool is refused here (propose_mcp_tool_call tells "
            "the model to call it directly instead)."
        ),
    ),
]


@dataclass(frozen=True)
class Policy:
    name: str
    status: str        # "enforced" | "planned"
    enforced_at: str   # where in the code the check lives (or would)
    guard: str         # the test that locks it in ("" if planned)
    description: str


POLICIES: list[Policy] = [
    Policy(
        name="Orchestration delegates through the gate, never around it",
        status="enforced",
        enforced_at=(
            "api/orchestration.py driver: assignments ride THE one task-create "
            "path with actor recorded; the orchestrate pack holds no gated "
            "capabilities; a child's proposals gate exactly as if the operator "
            "had tasked it directly — only its ungated text answers flow back "
            "without review (approval attaches to the action class, not the "
            "delegation)"
        ),
        guard=("tests/test_orchestration.py::"
               "test_gated_child_parks_proposal_and_resumes_after_approval"),
        description=(
            "The orchestrator runs projects as a durable assign→receive→decide "
            "loop. Delegating is a trigger: a sub-agent answering a question "
            "or reviewing something returns ungated (nothing external "
            "changed); any world change it proposes parks in the Decisions "
            "Inbox first, and the loop resumes only after the operator's "
            "decision."
        ),
    ),
    Policy(
        name="Orchestration is star-shaped, plan-gated, and stall-escalating",
        status="enforced",
        enforced_at=(
            "driver batch validation: depth-1 (an agent-created task is "
            "refused orchestration; the orchestrator is never an assignment "
            "target), assign_work refused before an operator-APPROVED plan "
            "(D7-P), consecutive no-progress rounds park an operator question "
            "instead of another round (The operator 2026-07-24: stall detection, not "
            "budgets; caps are runaway backstops only)"
        ),
        guard="tests/test_orchestration.py::test_stall_escalates_to_operator_instead_of_looping",
        description=(
            "Only the orchestrator holds the orchestrate pack; children "
            "answer, never re-delegate. Every project's plan is reviewed "
            "before work is assigned, and a stuck loop escalates to the "
            "operator with its progress ledger rather than silently retrying."
        ),
    ),
    Policy(
        name="Heartbeat schedules work, never performs or approves it",
        status="enforced",
        enforced_at=(
            "heartbeat/actions.py closed registry (only operator levers: "
            "enroll, drain windows, task creation, report reads) + import ban "
            "on the gateway's approve/execute machinery"
        ),
        guard="tests/test_heartbeat.py::test_heartbeat_never_imports_the_gate",
        description=(
            "Approval attaches to what the work does, never to the trigger: "
            "a scheduled firing can enroll mail, open a bounded drain window, "
            "create an assigned task, or snapshot a report — and can neither "
            "execute nor approve a write. Proposals produced downstream gate "
            "exactly as if the operator had started the same work by hand."
        ),
    ),
    Policy(
        name="Calendar reads are ungated; calendar writes are Executor-only and human-gated",
        status="enforced",
        enforced_at=(
            "integrations/calendar_facade.py's write helpers "
            "(create_event/update_event/delete_event) are called from "
            "gateway/executor.py and nowhere else — a source walk over the whole "
            "package fails on any other mention, and runtime/ cannot import the "
            "gateway at all; agents hold only propose_calendar_change"
        ),
        guard="tests/test_ea_calendar.py::test_only_the_executor_calls_the_calendar_write_helpers",
        description=(
            "REVISED 2026-08-06 (EA widening slice A, delete included by "
            "operator override of the design doc's no-delete-in-v1). The Google "
            "credential held inside n8n is FULL-SCOPE by operator decision, so "
            "nothing about the credential ever stopped a write; what used to "
            "stop it was the absence of a write path. Now the path exists and "
            "the boundary is CALLER-side: reading the calendar is ungated, "
            "changing it happens only in the Executor, only after the operator "
            "approved a proposal that named the event and (for a delete) the "
            "reason."
        ),
    ),
    Policy(
        name="Web fetch is read-only; client PKI identity lives in config, never in agent arguments",
        status="enforced",
        enforced_at=(
            "integrations/webfetch.py exposes only fetch() (no write/submit "
            "helper exists to call) + the cert/key/CA-bundle come from "
            "central_command.config.settings, not from the tool's argument shape "
            "(fetch_url takes only url)"
        ),
        guard="tests/test_webfetch.py::test_mtls_identity_comes_from_config_not_the_call",
        description=(
            "The web-fetch tool can only GET and render text — there is no "
            "helper it could call to post or authenticate as something else. "
            "The outbound client-PKI identity (CC_FETCH_CLIENT_CERT/_KEY/"
            "_CA_BUNDLE) is ONE identity for the whole process, set by the "
            "operator in config; an agent supplies a URL and nothing about "
            "who it fetches as."
        ),
    ),
    Policy(
        name="Human approval gate",
        status="enforced",
        enforced_at="gateway/executor split; runtime/ never imports the gateway tier",
        guard="tests/test_governance.py::test_runtime_never_imports_the_gateway_tier",
        description=(
            "Nothing changes the world without an operator decision. Agents "
            "hold only propose_* and read tools; the credentialed Executor "
            "runs only after gateway.approve_and_execute."
        ),
    ),
    Policy(
        name="Decision precedes execution on the log",
        status="enforced",
        enforced_at="gateway.approve_and_execute emits proposal.decided before executor.execute",
        guard="tests/test_eventlog.py::test_decision_is_logged_before_execution",
        description=(
            "The append-only log's write order is its read order forever, so "
            "the record that authorises an execution is written before it."
        ),
    ),
    Policy(
        name="Operator-evidence re-derivation",
        status="enforced",
        enforced_at=(
            "contract.claim_supported on BOTH paths — gateway stamps "
            "source_ref=event:<id> on a redraft; runtime checks a coached "
            "charter's quotes AND its cited event against the curated signals"
        ),
        guard="tests/test_reject_redraft.py::test_hallucinated_operator_claim_is_flagged",
        description=(
            "Trusting the operator's words is not trusting the agent's account "
            "of them: quoted feedback is mechanically re-checked against the "
            "recorded event, and the UI re-pulls the feedback for side-by-side "
            "review. A paraphrase or fabrication flags amber. On the coach path "
            "the agent supplies the pointer as well as the quote, so citing a "
            "signal the session was never fed flags the same way (that path went "
            "unchecked until 2026-07-25 — claim_matches_source read None, meaning "
            "never checked)."
        ),
    ),
    Policy(
        name="Every proposal announces itself",
        status="enforced",
        enforced_at="runtime/proposals.park_proposal is the only path that persists a proposal",
        guard="tests/test_proposal_created.py::test_only_the_park_path_creates_proposals",
        description=(
            "proposal.created used to be emitted at the call sites, so the coach "
            "and operator-task paths added later forgot it and their proposals "
            "reached the inbox with no creation event — a charter edit appeared "
            "in the audit trail only at decision time. One seam now persists and "
            "announces, and a source walk fails the suite if a new path bypasses "
            "it. Legibility, not authorisation: kind=proposal is a COMPLETE index "
            "of the review queue."
        ),
    ),
    Policy(
        name="Fold no-drop",
        status="enforced",
        enforced_at="ledger FOLD_PENDING; gateway commit_folds on approve / release_folds on reject or failure",
        guard="tests/test_threading.py::test_rejecting_the_covering_proposal_returns_siblings_to_the_queue",
        description=(
            "A fold is a claim of coverage, not an outcome. Folded siblings go "
            "terminal only when the covering proposal is approved; rejection or "
            "execution failure returns them to the queue. Folding can delay an "
            "email but never drop one."
        ),
    ),
    Policy(
        name="Dismissal review",
        status="enforced",
        enforced_at="dispatcher parks no-proposal runs DISMISS_PENDING; valves count unconfirmed dismissals",
        guard="tests/test_dismissal.py::test_unconfirmed_dismissals_throttle_the_drain",
        description=(
            "No action is a claim, not an outcome: the operator confirms even "
            "the decision to do nothing, and a drain cannot outrun review by "
            "dismissing everything."
        ),
    ),
    Policy(
        name="Approval-coupled throttle",
        status="enforced",
        enforced_at="dispatcher.check_valves (CC_DISPATCH_APPROVAL_LIMIT)",
        guard="tests/test_dismissal.py::test_unconfirmed_dismissals_throttle_the_drain",
        description=(
            "Agents never outrun the operator's review: dispatch stalls when "
            "pending proposals + unconfirmed dismissals reach the limit, and "
            "resumes as decisions clear."
        ),
    ),
    Policy(
        name="Execution-failure honesty",
        status="enforced",
        enforced_at="executor.ExecutionFailed carries completed actions; gateway marks FAILED terminal",
        guard="tests/test_execution_failure.py::test_partial_execution_records_what_completed",
        description=(
            "There are no cross-system transactions, so a failed execution "
            "records exactly which actions completed. FAILED is terminal — "
            "never re-approvable — and folds it claimed are released."
        ),
    ),
    Policy(
        name="Graph write pedigree",
        status="enforced",
        enforced_at="executor stamps trust=human-approved | approver=… into episode source",
        guard="tests/test_graph.py::test_executor_stamps_trust_and_approver_into_the_episode",
        description=(
            "Every fact that enters the knowledge graph carries who approved it "
            "and at what trust level, queryable forever."
        ),
    ),
    Policy(
        name="No past due dates",
        status="enforced",
        enforced_at="gateway/policy.check_actions — flagged amber on the proposal, recorded on proposal.decided",
        guard="tests/test_policy.py::test_past_due_date_flags_the_proposal_for_review",
        description=(
            "Deterministic guard against the live 2024-08-10 incident: any "
            "due_date in the past (or malformed) flags for the reviewer "
            "regardless of model behaviour. Advisory, never blocking — but an "
            "approval over a standing flag is recorded as exactly that."
        ),
    ),
    Policy(
        name="Known capabilities only",
        status="enforced",
        enforced_at="gateway/policy.check_actions — any action whose capability is not a registered gated write flags amber at review time",
        guard="tests/test_policy.py::test_unknown_capability_flags_the_proposal",
        description=(
            "Deterministic guard from the charter-v2 incident: the operator "
            "approved proposals whose capability did not exist, and they could "
            "only fail at execution. Now the impossibility is visible at review "
            "time. Advisory like every policy flag — but the Executor's registry "
            "parity remains the hard stop."
        ),
    ),
    Policy(
        name="Charter edits reference real capabilities",
        status="enforced",
        enforced_at="gateway/policy.check_charter_content — capability names quoted in a proposed or saved charter are checked against the registry",
        guard="tests/test_policy.py::test_charter_referencing_unknown_capability_is_flagged",
        description=(
            "The coaching loop can commit guidance — and guidance that promises "
            "a capability the registry doesn't have programs the agent to "
            "propose impossible actions (the charter-v2 incident, live). "
            "Charter diffs and operator saves now flag unknown capability "
            "references for the reviewer."
        ),
    ),
    Policy(
        name="Uniform agent management",
        status="enforced",
        enforced_at=(
            "the agent table IS the roster (D24); repo.hire_agent creates row + "
            "charter v1 + grants in ONE transaction, so an agent cannot exist "
            "unmanaged; taskability, the coach allowlist, and conversational "
            "lanes all derive from the rows"
        ),
        guard="tests/test_governance.py::test_every_roster_agent_is_uniformly_managed",
        description=(
            "Every agent joins the team through the same standard processes — "
            "roster registration, governed charter (M11), capability grants "
            "(D25), coaching (D5) — and any capability an agent does NOT get "
            "(direct tasking, consultation) carries a recorded reason on the "
            "roster. Hire/retire is a direct operator action with an event-log "
            "record; retirement is a status flip, never a deletion. Born from "
            "the auditor gap: the one agent closing items autonomously briefly "
            "had no feedback path."
        ),
    ),
    Policy(
        name="Capability packs hold only propose/read tools",
        status="enforced",
        enforced_at=(
            "runtime/packs.py — every pack tool lives in runtime/tools.py, "
            "whose only side-effect path is the CallDeferred propose seam; the "
            "runtime→gateway import ban keeps the Executor out of reach"
        ),
        guard="tests/test_packs.py::test_pack_tools_are_only_propose_and_read",
        description=(
            "D25 makes the agent tool surface data (grants), and data must not "
            "be able to smuggle a write: a pack can only bundle tools from the "
            "runtime tier, where proposing is the sole mutation seam and the "
            "Executor performs approved writes after the gate."
        ),
    ),
    Policy(
        name="Charter capability lists are generated from grants",
        status="enforced",
        enforced_at=(
            "runtime/agent.build_charter appends packs.charter_section(grants) "
            "to every pack-built agent's charter; pack capability names and "
            "argument shapes are parity-tested against this registry"
        ),
        guard="tests/test_packs.py::test_pack_capabilities_match_the_registry",
        description=(
            "The 2026-07-22 challenged-dismissal incident: a hand-written "
            "charter capability list drifted from the Executor's real surface, "
            "so the agent refused work it could do. Generated-from-grants "
            "lists cannot drift — the registry parity test breaks the build "
            "before a wrong list reaches an agent."
        ),
    ),
    Policy(
        name="Proposals within granted capabilities",
        status="enforced",
        enforced_at=(
            "gateway/policy.check_grants — review-time amber when a proposal "
            "carries a capability outside the proposing agent's pack grants"
        ),
        guard="tests/test_policy.py::test_capability_outside_grants_is_flagged",
        description=(
            "Grants are the operator's per-agent capability decisions (D25); "
            "a proposal outside them means a stale run or drift, and the "
            "reviewer should see that. Advisory like every flag — the "
            "registry parity remains the hard stop."
        ),
    ),
    Policy(
        name="Auditor graduates per action-class, shadow-first, revocable",
        status="enforced",
        enforced_at=(
            "gateway/auditor.py — CC_AUDITOR_ENABLED default false; shadow mode "
            "records verdicts without touching any gate; each class carries its "
            "own name, its own agreement report and its own mode knob "
            "(dismissal.confirm / CC_AUDITOR_MODE for email, "
            "dismissal.confirm.document / CC_AUDITOR_DOCUMENT_MODE for "
            "documents, work.bulk_dismiss for bulk), and no class ever "
            "auto-confirms an item the operator reopened"
        ),
        guard="tests/test_auditor.py::test_shadow_mode_records_but_never_confirms",
        description=(
            "The auditor is risk reduction, never the sole guard: it re-derives "
            "the agent's no-action claim from the raw email, its verdict is "
            "logged before any state change, only a CONCUR in active mode can "
            "close an item, and flipping the config restores the human gate."
        ),
    ),
    Policy(
        name="In-sandbox activity is ungated; anything leaving the sandbox is gated",
        status="enforced",
        enforced_at=(
            "central_command/sandbox/runner.py is a SEPARATE, credential-free "
            "service (no DB/LiteLLM/Jira access, only a namespace-scoped "
            "kubeconfig) driving a gVisor-isolated, default-deny-network Job "
            "per sandbox session; the five sandbox.* capabilities carry "
            "gate='ungated'/'ungated read', never 'human approval', and have "
            "no Executor handler at all — the sandbox pack's tools are the "
            "only way an agent reaches them"
        ),
        guard="tests/test_sandbox.py::test_sandbox_capabilities_have_no_executor_handler",
        description=(
            "The sandbox is a black box the agent owns completely: nothing it "
            "produces is trusted, read, or acted on by anyone else, because "
            "nothing in it is durable or reachable outside its own ephemeral "
            "pod. That is what makes the mechanism safe to leave ungated — it "
            "authorizes nothing. The one seam that changes that, "
            "mcp.sync_source (a later slice), is where review begins."
        ),
    ),
    Policy(
        name="The sandbox's only exit is mcp.sync_source",
        status="enforced",
        enforced_at=(
            "runtime/tools.py:propose_mcp_sync_source captures file content "
            "at PROPOSE time (reads the sandbox, embeds {path, content} in "
            "the proposal's Action.arguments) and the review surface is a "
            "unified diff against the repo's current servers/<id>/ tree; "
            "gateway/executor.py's mcp.sync_source handler writes EXACTLY "
            "those captured bytes and never re-reads the sandbox at execute "
            "time — an agent that edits its sandbox after proposing changes "
            "nothing about what gets written"
        ),
        guard="tests/test_mcp_sync.py::test_executor_writes_exactly_the_captured_bytes",
        description=(
            "Content is captured at propose time, reviewed as a diff, and "
            "written verbatim by the Executor — never re-read from the "
            "sandbox at execute time. Re-reading at execute time would make "
            "review theater: the diff the operator approved would not "
            "necessarily be the diff that lands."
        ),
    ),
    Policy(
        name="Build and deploy operate only on reviewed, synced source",
        status="enforced",
        enforced_at=(
            "gateway/executor.py's mcp.build_image and mcp.server_deploy "
            "handlers each check the mcp_server row's status before doing "
            "anything (synced-or-later to build; built-or-later plus a "
            "recorded image_ref/digest to deploy) — the state machine "
            "synced -> built -> deployed cannot be skipped from either end"
        ),
        guard="tests/test_mcp_pipeline.py::test_build_refuses_when_not_yet_synced",
        description=(
            "Nothing after mcp.sync_source re-opens the trust boundary it "
            "crossed: build_image and server_deploy are 'normal' gated-write "
            "plumbing over source the operator already reviewed once. The "
            "deploy convention is one container listening on 8000, "
            "resource-limited, gVisor-isolated, reachable only from LiteLLM "
            "(network-policy-enforced, deploy/k3s/61-mcp.yaml — LiteLLM "
            "registration itself is a later slice)."
        ),
    ),
    Policy(
        name="Registration is the LiteLLM-side coupling",
        status="enforced",
        enforced_at=(
            "gateway/executor.py's litellm.register_mcp_server handler "
            "requires a deployed row and dispatches through "
            "integrations/litellm.py, which holds the proxy admin key — "
            "same trust split as every other litellm.* write"
        ),
        guard="tests/test_mcp_register.py::test_register_refuses_when_not_yet_deployed",
        description=(
            "Registration is the LiteLLM-side coupling: only a DEPLOYED "
            "server can be registered, the Executor alone holds the proxy "
            "admin key, and the recorded litellm id is what deregistration "
            "will use. Removal auto-couples deregistration (operator "
            "decision 2026-08-03), a deregister-only lever stays separate."
        ),
    ),
    Policy(
        name="A LiteLLM config change is verified, or it is rolled back",
        status="enforced",
        enforced_at=(
            "gateway/executor.py's litellm.apply_config_change handler: file "
            "content is captured at PROPOSE time and written verbatim; only "
            "deploy/pi/litellm/config.yaml and model-preferences.yaml may be "
            "written; secret shapes in content are refused (these files "
            "reference env vars — LITELLM_SALT_KEY and every key live in the "
            "Secret, never here); a failing PRE-check aborts before anything "
            "is written; the rollback trigger is the POST-check's exit code, "
            "never the Executor's or an agent's judgment"
        ),
        guard="tests/test_litellm_config_change.py::test_post_check_failure_restores_the_files_and_fails_the_proposal",
        description=(
            "The highest-blast-radius capability in the registry carries its "
            "own undo: a config that does not pass deploy/pi/litellm/checks.sh "
            "after the restart is reverted to the snapshotted bytes, "
            "re-rendered, restarted and re-checked, and the proposal FAILS with "
            "both check outputs so the agent redrafts from real evidence. A "
            "rollback that is itself red stops everything and becomes a loud "
            "operator item plus litellm.config_rollback_failed — a proxy that "
            "will not come back under its previous known-good config is an "
            "incident, not an agent loop."
        ),
    ),
    Policy(
        name="MCP tool calls are governed per (agent, tool), on one static capability",
        status="enforced",
        enforced_at=(
            "runtime/packs.py builds each mcp: grant's toolset from "
            "repo.effective_mcp_gates(agent_id) — a gated tool becomes "
            "reachable ONLY through propose_mcp_tool_call (never a direct "
            "callable, never lazily loaded); an ungated tool attaches as a "
            "real MCP toolset, filtered to just that agent's ungated names"
        ),
        guard="tests/test_mcp_grants.py::test_toolset_split_by_effective_gate",
        description=(
            "Every MCP-served tool call is governed per (agent, tool): "
            "default gate from mcp_tool, per-agent operator override from "
            "agent_mcp_gate_override (operator-written only — no propose "
            "path creates that row). Gated tools ride ONE static capability, "
            "mcp.tool_call, across every registered server; ungated tools "
            "attach as direct MCP callables at toolset build."
        ),
    ),
]


def registry_as_dict() -> dict:
    return {
        "capabilities": [asdict(c) for c in REGISTRY],
        "policies": [asdict(p) for p in POLICIES],
    }


def gated_write_names() -> set[str]:
    """The exact set the Executor must (and may only) dispatch."""
    return {c.name for c in REGISTRY if c.kind == "write" and c.gate == "human approval"}
