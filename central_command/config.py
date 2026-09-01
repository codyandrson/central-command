"""Environment configuration. All vars are prefixed CC_ (see .env.example)."""

import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Pydantic's env_file populates Settings FIELDS only — it never exports to
# os.environ. The per-agent lookups (CC_LLM_PROXY_KEY_<AGENT_ID>,
# CC_MODEL_<AGENT_ID>) must read os.environ because their names are open-ended,
# so mirror .env into the process env too. load_dotenv never overrides vars
# already set in the real environment, so systemd/CLI overrides still win.
# (Found live 2026-07-22: the cc-smart canary in .env was silently ignored.)
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="CC_", extra="ignore"
    )

    # Database — the framework spine.
    # 5442 = Central Command's own PG (docker-compose.yml). 5432 is n8n's nat-postgres.
    database_url: str = "postgresql://central_command:central_command@localhost:5442/central_command"

    # Provider:model string for Pydantic AI. Confirm the exact current id at M0.
    default_model: str = "anthropic:claude-sonnet-5"

    # Output-token ceiling for every live agent run. MUST be set explicitly:
    # unset, the OpenAI path sends no max_tokens (unbounded generation on
    # llama.cpp), and the Anthropic-era client defaulted to 4096 — and
    # 4096 is not enough for the one action that re-emits a whole document —
    # `charter.update` carries the FULL revised charter as a single JSON
    # argument. litellm-manager's charter alone is ~3.4k tokens, and with JSON
    # escaping plus intent/rationale/evidence it overran 4096 mid-string: the
    # tool call arrived unterminated, could not be parsed, and the coach run
    # died after four identical retries (2026-07-28, the first live coach test).
    #
    # Sized to THE DEPLOYMENT, not to the job (The operator, 2026-07-28): the serving
    # model is qwen3.6-27b on the workstation, where llama.cpp reports
    # n_ctx = 262144 per slot and sets no output cap of its own (n_predict
    # defaults to unlimited, bounded only by remaining context). So this is the
    # workstation's own ceiling — Central Command imposes nothing tighter, and a
    # bigger document later needs no code change. llama.cpp clamps generation
    # to whatever context the prompt leaves free.
    #
    # Note for whoever tiers an agent onto a hosted model via CC_MODEL_<AGENT>:
    # a request whose max_tokens exceeds THAT model's cap fails rather than
    # clamping (opus/sonnet cap at 128000, haiku at 64000 — see the note in
    # deploy/pi/litellm/config.yaml). Set CC_MAX_OUTPUT_TOKENS for that case.
    #
    # THE FALLBACK IS PART OF THE DEPLOYMENT (found 2026-07-29, the first real
    # workstation outage): cc-default falls back to claude-haiku-4-5, and a
    # 262144 max_tokens request is REJECTED by haiku (64000 cap) — so with the
    # workstation off, every agent run failed even though the fallback route
    # itself was healthy. "Sized to the deployment" therefore means sized to
    # the MINIMUM across primary + fallback: the Pi's .env sets
    # CC_MAX_OUTPUT_TOKENS=64000. This default stays at the workstation
    # ceiling for single-model dev setups; the deployment override is the
    # mechanism this note always prescribed.
    max_output_tokens: int = 262144

    # THE LLM provider agents talk to (live mode). Presence is enablement:
    # there is no on/off flag and no fallback — you configure one endpoint and
    # one key, resolve_model() always pins base_url explicitly (so an ambient
    # ANTHROPIC_BASE_URL cannot redirect traffic), and a provider that is not
    # available fails loudly. Both empty here on purpose: missing config raises
    # in live mode instead of half-configuring a client. .env.example ships the
    # LiteLLM defaults the operator runs.
    #   llm_base_url -> the endpoint's root (e.g. http://localhost:4000);
    #                   runtime/models.py appends /v1 and the OpenAI client adds
    #                   /chat/completions (OpenAI format since 2026-08-19 — the
    #                   backing model is an `openai/...` LiteLLM deployment, so
    #                   the old Anthropic /v1/messages path was a needless
    #                   double translation). The default_model name is sent
    #                   as the model id, so it doubles as the LiteLLM alias.
    #   llm_api_key  -> the key for whatever that endpoint is: a LiteLLM VIRTUAL
    #                   key scoped to Central Command (never the master key), or an
    #                   Anthropic key when pointed at Anthropic directly.
    # Demo mode is unaffected (never hits a provider).
    llm_base_url: str = ""
    llm_api_key: str = ""
    # Per-agent keys (Phase 5, attribution): OPTIONAL env vars named
    # CC_LLM_PROXY_KEY_<AGENT_ID> (agent_id upper-cased, '-'→'_'), e.g.
    # CC_LLM_PROXY_KEY_INBOX_TRIAGE. When set, that agent authenticates against
    # the SAME llm_base_url with its own LiteLLM virtual key, so spend
    # attributes per agent. The operator provisions these (create the key via
    # the manager or UI, capture the plaintext once, paste it here) — Central Command never
    # stores plaintext keys itself. Unset for an agent = the shared llm_api_key.
    # Read dynamically (per-agent env names can't be static fields) —
    # see agent_proxy_key() below.

    # Anthropic key held by the EXECUTOR — not a routing credential. It is the
    # raw key injected into approved LiteLLM `anthropic/...` model registrations
    # (gateway/executor.py::_provider_key) so a re-registered model still
    # authenticates. Agent traffic uses llm_api_key above, never this.
    anthropic_api_key: str = ""

    # The LiteLLM deployment Central Command ADMINISTERS (M/D23 litellm-manager).
    # A different fact from where agent traffic goes: the manager needs a
    # LiteLLM address and master key whether or not llm_base_url points at it.
    #   llm_proxy_base_url -> proxy root for /model/new, /key/generate, …
    #   llm_proxy_admin_key -> see below
    llm_proxy_base_url: str = "http://localhost:4000"
    # Browser-reachable LiteLLM UI URL (tailnet) — display-only, for the
    # cockpit's "View in LiteLLM" link. Distinct from llm_proxy_base_url,
    # which is loopback by rule (the failover invariant). Unset = no link.
    llm_proxy_ui_url: str = ""
    # LiteLLM MASTER key — the admin-plane credential the Executor holds to
    # perform the LiteLLM-manager agent's approved model changes (add/delete)
    # and the manager's reads. Powerful (proxy-wide); lives only here + in .env.
    # Unset = the manager's reads/writes fail loudly (integrations.litellm
    # configured() is False), never half-work.
    llm_proxy_admin_key: str = ""

    # The embedder the curation writer (integrations/neo4j_writer) uses — the
    # LiteLLM ALIAS, not the upstream model name, and the vector dimension the
    # Neo4j indexes were created with. Must equal the graphiti config's
    # embedder block: they index the same vectors, and a mismatched dimension
    # is silently-broken similarity, not an error (verified 2026-08-30: there
    # is NO Neo4j vector index — graphiti scores per-row with
    # vector.similarity.cosine(), so nothing schema-side enforces the width).
    # The dimension is discovered at setup and is effectively permanent —
    # changing the embedder later is a re-embed-the-whole-graph migration
    # (skills/graphiti/references/operations.md, "Changing the embedding
    # model"). Defaults = the homelab instance.
    embed_alias: str = "cc-embedding"
    embed_dim: int = 1024

    # How charters address the human team lead — the first charter-template
    # placeholder (<<operator_name>>, rendered ONCE at hire). The default
    # keeps every pre-onboarding hire reading exactly as before; the setup
    # interview writes the real name.
    operator_name: str = "the operator"

    # Autodiscovery reads LiteLLM's OWN stored credentials straight out of ITS
    # Postgres (2026-08-20 redesign) — the operator's only enrollment step is
    # adding a credential in the LiteLLM UI, nothing to configure here beyond
    # how to reach that database. 5443 = the litellm-db loopback hostPort
    # (deploy/k3s/30-litellm.yaml), same pattern as cc-postgres:5442.
    litellm_db_url: str = ""
    # The salt key LiteLLM encrypts credential_values with (its
    # LITELLM_SALT_KEY) — needed to decrypt what litellm_db_url reads. Unset =
    # integrations.litellm_credstore fails loudly, never half-decrypts.
    litellm_salt_key: str = ""

    # MCP endpoints (existing homelab services).
    graphiti_mcp_url: str = "http://localhost:8000/mcp"

    # Knowledge graph tenanting: reads span the preserved homelab graph ("main")
    # plus Central Command's own group; writes land ONLY in Central Command's group, so
    # approved episodes never pollute the inherited graph.
    graph_read_groups: str = "main,central_command"
    graph_write_group: str = "central_command"

    # Session-close reflection (self-directed learning §2). Everything it
    # produces is human-gated: episodes are ordinary proposals, the coaching
    # self-nomination is a signal the coach may cite. `max_episodes` is the
    # per-session bound N — provisional, to be revised from observed shadow
    # volume rather than from taste.
    reflection_enabled: bool = True
    reflection_max_episodes: int = 3
    # Scope limit (2026-08-05): a transcript below this many characters skips
    # reflection entirely — trivial sessions were the bulk of the review load.
    reflection_min_transcript_chars: int = 2000

    # n8n tool façade — the Jira provider, holding credentials inside n8n (M3).
    n8n_jira_url: str = "http://localhost:5678/webhook/cc-jira-facade"
    jira_facade_token: str = ""

    # Native Jira client (D23) — set email + api token to cut over from the
    # n8n façade; unset them to fall back. Base URL is the Cloud site.
    # No instance-specific default (2026-08-21, sibling of rehearsal finding
    # F11): a gift install must never point at the operator's Jira. Unset + jira packs
    # granted = reads fail loudly with a URL-shaped error, which is the honest
    # state. The operator's instance sets this in .env.
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""

    # n8n email façade (M12) — wraps lib-email-provider; Gmail creds stay in n8n.
    email_facade_url: str = "http://localhost:5678/webhook/cc-email-facade"
    email_facade_token: str = ""

    # n8n calendar façade — wraps the operator's Google Calendar; the OAuth
    # credential stays in n8n. The credential is FULL-SCOPE by operator
    # decision: read-only is enforced by the façade workflow's mode gate and by
    # integrations/calendar_facade.py exposing only a list. Empty token =
    # not configured; the EA's brief simply carries no calendar.
    calendar_facade_url: str = "http://localhost:5678/webhook/cc-calendar-facade"
    calendar_facade_token: str = ""

    # Web fetch (D-web-read): an ungated read, granted only via the `web-read`
    # pack. The cert/key/CA-bundle settings configure ONE outbound identity for
    # every fetch this process makes — never a per-call agent argument (see the
    # policy note in gateway/capabilities.py). PEM paths, not PKCS#12; must be
    # readable by the process running the app.
    fetch_client_cert: str = ""
    fetch_client_key: str = ""
    fetch_ca_bundle: str = ""
    # Comma-separated host suffixes the client cert may be PRESENTED to
    # (e.g. "corp.example.com,tools.internal"). Empty = present it to every
    # host — fine on the homelab, but a corporate PKI identity should be
    # scoped so an agent-named external URL can't be handed the cert.
    fetch_cert_hosts: str = ""
    fetch_timeout: float = 30.0
    fetch_max_bytes: int = 2_000_000

    # Pluggable outbound trust for the FIXED, operator-configured integration
    # clients — Jira, Graphiti's MCP endpoint, the LiteLLM admin/embed calls
    # (work-transition compatibility design, decision 3; see
    # integrations/http.py). Distinct from the fetch_* settings above, which
    # scope an agent-named URL's client identity by host; these apply
    # uniformly because the target of each of those calls is one fixed,
    # operator-configured endpoint. Empty (default) = unchanged homelab
    # behavior — public CA verification, no client cert.
    ca_bundle: str = ""       # path to a custom trust bundle (httpx `verify=`)
    client_cert: str = ""     # path to a client cert, or a combined cert+key PEM
    client_key: str = ""      # path to the client key, if not combined with client_cert above

    # Jira auth mode (work-transition compatibility design, decision 2):
    # "basic" (default, Cloud — email + API token) or "bearer" (Data Center —
    # a PAT, email ignored). Endpoint-shape differences between Cloud and DC
    # are NOT addressed here on purpose — this is an auth-only seam,
    # verified against the real DC instance on-site.
    jira_auth_mode: str = "basic"

    # Git-hosting read integration (work-transition compatibility design, the
    # "Git hosting" catalog-implications bullet): Central Command is a CLIENT of
    # existing forges (GitHub, self-hosted GitLab), never a host. Multiple
    # NAMED instances are first-class — "name=kind:base_url" per instance,
    # comma-separated, e.g.
    # "github=github:https://api.github.com,work-gl=gitlab:https://gitlab.example.com".
    # Empty = the `forge-read` pack's tools answer "no forge instances
    # configured" rather than erroring. Parsed defensively by
    # integrations/forge.py — a malformed entry fails loud, naming itself.
    forge_instances: str = ""
    # Per-instance PAT: CC_FORGE_TOKEN_<NAME> (name upper-cased, '-'→'_'), the
    # same convention as CC_LLM_PROXY_KEY_<AGENT_ID> above — read dynamically
    # from os.environ by forge.py, not a Settings field (the instance set is
    # open-ended). Missing token for a configured instance = requests go
    # UNAUTHENTICATED (works for public GitHub, subject to rate limits) rather
    # than refusing.

    # Native Confluence client, Phase 1 read-only (D-confluence). Same giftable
    # convention as jira_base_url above: no instance-specific default, so a
    # gift install never points at the operator's Confluence — unset + a confluence
    # pack granted means reads fail loudly with a URL-shaped error. The operator's
    # instance sets these in .env.
    confluence_base_url: str = ""
    confluence_email: str = ""
    confluence_api_token: str = ""
    # Auth mode, same seam as jira_auth_mode: "basic" (default, Cloud — email
    # + API token) or "bearer" (Server/DC — a PAT, email ignored).
    confluence_auth_mode: str = "basic"
    # API flavor: "cloud" (default, REST API v2 + v1 search) or "server" (DC
    # 9.x, everything via REST API v1). The server shapes are coded to
    # Atlassian's published DC 9.x docs and are VERIFY-ON-SITE, not proven
    # against a live instance — same posture as jira_auth_mode's DC comment.
    confluence_api_flavor: str = "cloud"
    # Names the active macro/profile an operator is using (e.g. a documented
    # space layout or template set) — informational only, read by agents to
    # know which convention applies; nothing here parses or enforces it.
    confluence_profile: str = ""

    # Brave Search (the `web-search` pack). Empty = search is UNAVAILABLE and
    # `search_web` says so honestly rather than raising — an agent without it
    # asks the operator for URLs and reads them with `fetch_url`.
    brave_api_key: str = ""

    # The operator's own timezone — the EA's calendar day is a LOCAL day
    # (00:00-24:00 here), never a UTC one, because "today's schedule" is a fact
    # about where the operator is standing.
    ea_timezone: str = "America/Denver"

    # Live email feed (M12). PULL: Central Command polls the façade and enrolls new
    # mail into the Work Ledger; the dispatcher's valves then govern the pace.
    #   feed_enabled       -> autostart the poll loop with the API (default OFF)
    #   feed_poll_seconds  -> how often to list for new mail
    #   feed_query         -> the Gmail search scoping WHAT is ingested — keep
    #                         it narrow; this one knob decides ingest volume
    feed_enabled: bool = False
    feed_poll_seconds: float = 60.0
    feed_query: str = "in:inbox newer_than:1d"

    # Backlog sweep (M13): windowed walk backwards through mailbox history,
    # enrolling refs only (content fetched at claim time). The cutoff is a
    # DATE, not a duration — a fixed, auditable line per deployment: mail older
    # than this is never processed. Empty = the sweep refuses to start.
    # Window = days per list query (bisected if over the provider's 2,500-ref cap).
    backlog_query: str = "in:inbox"
    backlog_window_days: int = 30
    backlog_cutoff_date: str = ""  # ISO date/datetime, e.g. "2026-04-20"

    # API.
    api_host: str = "0.0.0.0"
    api_port: int = 8080

    # Run modes.
    #   demo_mode=True  -> drive agents with a deterministic model (no API key needed)
    #   executor_mode   -> "live" performs real writes; "dry_run" logs them instead
    demo_mode: bool = False
    executor_mode: str = "live"

    # Backpressure dispatcher (Phase 2, M7). PULL not push — these are the valves.
    #   dispatch_enabled          -> autostart the drain loop with the API
    #   dispatch_concurrency      -> max work items in flight at once (D15: start 1)
    #   dispatch_approval_limit   -> pause dispatch while this many proposals are
    #                                AWAITING_HUMAN, so agents can't outrun review
    #   dispatch_token_budget     -> per-process token ceiling (0 = unlimited)
    #   dispatch_max_attempts     -> attempts before an item is parked FAILED
    #   dispatch_stale_claim_seconds -> recover items a crashed process left CLAIMED
    dispatch_enabled: bool = False
    dispatch_concurrency: int = 1
    dispatch_approval_limit: int = 3
    dispatch_token_budget: int = 0
    dispatch_poll_seconds: float = 2.0
    dispatch_max_attempts: int = 3
    dispatch_stale_claim_seconds: int = 900

    # Semantic relatedness freeze (2026-08-19) — the fuzzy layer of the
    # relatedness lock. The claim query hard-locks exact keys (thread, subject,
    # sender); this gate catches what no key can — same topic, different
    # everything — by comparing the claimed item's embedding against every item
    # still in flight or owing an unexecuted proposal, and releasing it back
    # with a backoff on a match. Fail-open: an unreachable embedder never
    # blocks dispatch. Skipped in demo mode (no proxy to embed with).
    #   semantic_freeze_enabled   -> the gate itself
    #   semantic_freeze_threshold -> cosine similarity at or above which the
    #                                item is frozen; lower = more aggressive
    semantic_freeze_enabled: bool = True
    semantic_freeze_threshold: float = 0.70

    # Heartbeat (Phase 4, D27) — the scheduler for recurring team work.
    # Approval attaches to what the work DOES, never to the trigger: the
    # heartbeat only invokes levers the operator already has (enroll mail,
    # open the drain, create tasks, snapshot reports) and can neither perform
    # nor approve a write. Same opt-in rule as the dispatcher and feed:
    #   heartbeat_enabled      -> autostart the tick loop with the API
    #   heartbeat_tick_seconds -> how often due schedules are checked
    heartbeat_enabled: bool = False
    heartbeat_tick_seconds: float = 15.0

    # Orchestration (D7 phase 2). Safety BACKSTOPS, not pacing — the operator's call
    # (2026-07-24): how long a project takes doesn't matter; what matters is
    # catching stuck. The control is the progress ledger + stall escalation;
    # these caps only bound runaway token burn, and hitting any of them forces
    # an operator question instead of another round.
    #   orch_stall_limit  -> consecutive no-progress/looping rounds before the
    #                        driver escalates to the operator
    #   orch_max_rounds   -> hard ceiling on orchestration rounds per project
    #   orch_fanout_limit -> hard ceiling on child assignments per project
    orch_stall_limit: int = 2
    orch_max_rounds: int = 50
    orch_fanout_limit: int = 25

    # Auditor (Phase 4, D8/D12) — independent re-verification of agent claims.
    #   auditor_enabled -> off by default; the human gate is the baseline
    #   auditor_mode    -> "shadow" records verdicts without changing any gate;
    #                      "active" graduates exactly one low-risk action class:
    #                      auto-confirming CONCURRED dismissals. Revocable by
    #                      flipping back — graduation is config, never code.
    #   auditor_document_mode -> the DOCUMENT dismissal class's own knob
    #                      (sources-catalog slice 6). `auditor_mode` stays the
    #                      EMAIL class's knob and nothing else: a per-class
    #                      mode is exactly what makes turning documents on NOT
    #                      an ungraduated graduation — email can be active
    #                      while documents accrue their own shadow evidence,
    #                      and each class is revoked on its own.
    auditor_enabled: bool = False
    auditor_mode: str = "shadow"
    auditor_document_mode: str = "shadow"

    # Deterministic stale-page annotation (sources-catalog slice 7,
    # Decision 9's own action class) — the same graduation ladder, its own
    # knob:
    #   'off'  (default) -> staleness reaches pages ONLY through gated repair
    #                       proposals. The claims ledger and the freshness
    #                       sweep still run; nothing touches Confluence.
    #   'auto'           -> the operator's graduation flip: the freshness
    #                       sweep writes the bounded stale panel DIRECTLY
    #                       through the credentialed Confluence client,
    #                       stamped actor 'system:wiki-freshness' and emitting
    #                       wiki.page.annotated. Deterministic template over
    #                       claim rows — no model anywhere in that path, which
    #                       is what makes it graduatable at all.
    wiki_annotation_mode: str = "off"

    # Graph verification auditor (2026-08-19 spec) — closes the loop after
    # graph.add_episode: the approval gated the EPISODE, extraction ran after
    # it, unreviewed. Same ladder, its own flags:
    #   graph_auditor_enabled -> off by default; the sweep still runs its
    #                            MECHANICAL checks once its schedule is
    #                            enabled, but the judgment agent only runs
    #                            (and verdicts only accrue) when this is on
    #   graph_auditor_mode    -> "shadow" parks every audited episode for the
    #                            operator with the verdict attached; "active"
    #                            auto-closes ONLY aligned + mechanically-clean
    #                            + addition-only rows. Deltas that INVALIDATE
    #                            existing facts always park — that class
    #                            (graph.verify_invalidation) graduates
    #                            separately, with its own flag, later.
    graph_auditor_enabled: bool = False
    graph_auditor_mode: str = "shadow"

    # Outage equivalence (2026-07-31): how many times a session parked
    # AWAITING_RESUME may be re-driven before the system admits the outage
    # outlived its patience. Exhaustion is a PROMOTION, not a loop — the unit
    # converts to today's terminal behaviour plus an operator item, so nothing
    # retries forever silently.
    resume_retry_limit: int = 5

    # Outage equivalence slice 3: how many times an APPROVED proposal whose
    # execution hit a transient dependency failure may be replayed before the
    # same promotion applies (terminal FAILED + an operator item). The decision
    # never re-opens — what is bounded is the system's patience with the
    # dependency, not the operator's approval.
    execution_retry_limit: int = 5

    # Outage equivalence slice 4: how many times a TASK whose run died on a
    # transient dependency failure may be re-run before the same promotion
    # applies (terminal FAILED + an operator item + `task.run_exhausted`).
    #
    # Note what has NO limit: a transiently-released WORK ITEM. A task is a
    # standing instruction the operator can re-issue; a queued email is a thing
    # that happened once, so it waits out the outage however long it lasts and
    # only its backoff grows (`work_item.not_before`).
    task_retry_limit: int = 5

    # LOOP BREAKER, not a budget. How many model requests one run may make
    # before pydantic-ai raises `UsageLimitExceeded`. Tokens are free here
    # (local models) — what is not free is a run that ping-pongs the same tool
    # call forever, holding a session RUNNING and nobody watching. Applied at
    # the one seam (`runtime/run.py:_run_live`), so every run path inherits it.
    # `UsageLimitExceeded` classifies SEMANTIC: a looping run re-loops on retry,
    # so exhaustion promotes to the operator rather than spinning.
    run_request_limit: int = 50

    # CONTEXT WINDOW of the model agents actually talk to, in input tokens —
    # the fallback when the proxy cannot tell us. LiteLLM only knows
    # `max_input_tokens` for models in its cost map, and the local deployments
    # (`cc-default` → qwen3.6-27b) are not in it, so `/model/info` answers
    # None today; declaring `model_info.max_input_tokens` on those deployments
    # is a litellm-manager proposal, not a code change. 200000 is deliberately
    # conservative: under-estimating the window makes us measure pressure too
    # early, over-estimating makes us miss it entirely.
    context_window: int = 200000

    # Fraction of the window at which a session is called "under context
    # pressure" (slice 1 emits an event and changes nothing). 0.6, not Claude
    # Code's 0.8-0.9: these sessions pause for days and the window can shrink
    # under them when the `cc-default` alias is re-pointed.
    context_pressure_threshold: float = 0.6

    # Tier-3: how long a clarification discussion may sit quiet before the
    # sweep flags it (S1, at the operator) or nudges the agent (S2).
    discussion_stall_hours: int = 24

    # The ATTENTION BUDGET (teaming doctrine, Decision 4): how many times a day
    # the EA may open a conversation with the operator unprompted. The dial is
    # The operator's and the default errs LOW — the notification-budget evidence puts
    # total unsolicited agent contact at ~3-5/day before fatigue, and three
    # scheduled kinds (morning report, check-in, digest) cost one each.
    # 0 = unlimited, and every other agent's budget IS 0 — this is a per-agent
    # rule (runtime/attention.py), not a system-wide throttle.
    ea_contact_budget_per_day: int = 3

    # Sandbox slice 1 (D-sandbox): the runner is a SEPARATE, credential-free
    # service (central_command/sandbox/runner.py) that drives ephemeral gVisor
    # Jobs on the chromebox by shelling out to kubectl — this process talks
    # to it over plain HTTP, localhost-only, same trust as api_port today.
    #   sandbox_runner_url            -> ONE address for the whole process
    #   sandbox_session_ttl_seconds   -> mirrors CC_SANDBOX_SESSION_TTL_SECONDS
    #                                     read by the runner itself (kept here
    #                                     too so tools can describe it honestly)
    #   sandbox_exec_timeout_seconds  -> default per-exec bound; a tool call
    #                                     may ask for less, never more
    sandbox_runner_url: str = "http://127.0.0.1:8090"
    sandbox_runner_token: str = ""  # shared bearer token; runner enforces when set
    sandbox_session_ttl_seconds: int = 3600
    sandbox_exec_timeout_seconds: int = 120

    # Sandbox slice 2 (mcp.sync_source): the one gated exit from a sandbox into
    # servers/<id>/ in this repo. Cap is on TOTAL captured bytes across every
    # file in one proposal — the content is embedded in the proposal at
    # propose time, so an uncapped read would bloat the append-only event log
    # forever. mcp_servers_root empty means "<repo root>/servers", resolved at
    # use (both runtime, for the diff, and the Executor, for the write).
    mcp_sync_max_bytes: int = 524288
    mcp_servers_root: str = ""

    # Sandbox slice 3 (mcp.build_image / mcp.server_deploy): the two gated
    # steps that turn reviewed source into a running (namespace-scoped,
    # resource-limited, gVisor-isolated) pod. Both refuse loudly, not
    # silently, when unconfigured — an unset build host or kubeconfig must
    # never look like a build/deploy that quietly did nothing.
    #   mcp_build_host        -> ssh target the image is built ON (single-arch
    #                            per design §9.3 — the chromebox only, since
    #                            that's where gVisor/the image will run).
    #                            Empty = refuse to build.
    #   mcp_deploy_kubeconfig -> path to the SCOPED kubeconfig
    #                            (make-mcp-kubeconfig.sh writes one) the
    #                            Executor applies manifests with. Empty =
    #                            refuse to deploy.
    mcp_build_host: str = ""
    mcp_deploy_kubeconfig: str = ""

    # litellm.apply_config_change (2026-08-06): the SCOPED kubeconfig the
    # Executor re-renders the cc-litellm ConfigMap and restarts the proxy
    # with (deploy/k3s/make-litellm-kubeconfig.sh writes one). Empty or
    # missing = refuse BEFORE anything is written — a config change that
    # cannot be rendered and verified must never leave a half-applied repo.
    litellm_kubeconfig: str = ""

    # litellm-manager troubleshooting (2026-08-06): a SEPARATE, read-only
    # kubeconfig (deploy/k3s/make-litellm-kubeconfig.sh also writes this one)
    # for runtime/tools.py's litellm_read_logs — pods get/list + pods/log get
    # ONLY, no configmaps/deployments/secrets. Kept distinct from
    # litellm_kubeconfig (the Executor's write identity) because runtime/
    # tools may hold READ credentials only.
    litellm_log_kubeconfig: str = ""

    # cc-crawler (2026-08-07): the browser-rendering crawl service, a
    # chromebox-pinned pod exposed by ServiceLB, so this stays LOOPBACK like
    # every other CC_* endpoint. Rung 2 for docs ingestion — rung 1 is
    # webfetch. Not reachable is a normal state (chromebox down / not
    # deployed); integrations/crawler.py degrades instead of raising.
    crawler_url: str = "http://127.0.0.1:8091"

    # Cockpit Graph panel (2026-08-09): read-only bolt reads against the
    # Graphiti graph, via deploy/k3s/cc-graph-bolt.service (a standing
    # port-forward of svc/neo4j 7687 to 127.0.0.1 ONLY — same loopback rule
    # as every other CC_* endpoint; the ClusterIP-only Service is unchanged).
    # `integrations/neo4j_reader.py` opens every session read-only; there is
    # no raw-Cypher endpoint. `runtime/` still holds zero Neo4j references —
    # agents keep reaching the graph only through Graphiti's MCP.
    neo4j_url: str = "bolt://127.0.0.1:7687"
    neo4j_password: str = ""
    # Browser-reachable Neo4j Browser URL (tailnet) — display-only, for the
    # Systems view's "Open →" link. Same pattern as llm_proxy_ui_url: distinct
    # from neo4j_url (loopback bolt, by the failover invariant). Unset = no
    # link.
    neo4j_browser_url: str = ""

    # Browser-reachable n8n UI URL (tailnet) — display-only, for the Systems
    # view. Same pattern as llm_proxy_ui_url. Unset = no link.
    n8n_ui_url: str = ""

    # Browser-reachable VictoriaLogs UI URL (tailnet) — display-only, for the
    # Systems view. Same pattern as llm_proxy_ui_url. Unset = no link.
    vlogs_ui_url: str = ""

    # Browser-reachable Swagger (/docs) URLs for the sandbox runner and the
    # crawler — display-only, for the Systems view, labelled "Swagger" there.
    # Both services are loopback/cluster-internal; a value here implies the
    # operator has put a tailnet listener in front (e.g. tailscale serve).
    # Unset = no link.
    sandbox_docs_url: str = ""
    crawler_docs_url: str = ""

    # Browser-reachable database UI (e.g. Adminer, deploy/k3s/85-adminer.yaml)
    # — display-only, for the Systems view. Unset = no link.
    db_ui_url: str = ""

    def agent_proxy_key(self, agent_id: str | None) -> str:
        """The per-agent LiteLLM virtual key for `agent_id`, from
        CC_LLM_PROXY_KEY_<AGENT_ID> (upper-cased, '-'→'_'), or "" if unset.
        Read from the environment dynamically since the agent set is open."""
        if not agent_id:
            return ""
        return os.environ.get(f"CC_LLM_PROXY_KEY_{agent_id.upper().replace('-', '_')}", "")

    def agent_model(self, agent_id: str | None) -> str:
        """Per-agent MODEL override for `agent_id`, from CC_MODEL_<AGENT_ID>
        (same provider:model shape as CC_DEFAULT_MODEL), or "" if unset. Lets
        one agent canary a different LiteLLM alias (e.g. the cc-smart adaptive
        router) while everyone else stays on default_model. Read dynamically
        for the same reason as agent_proxy_key."""
        if not agent_id:
            return ""
        return os.environ.get(f"CC_MODEL_{agent_id.upper().replace('-', '_')}", "")


settings = Settings()
