/**
 * Decisions Inbox — Central Command's approval gate as a first-class view.
 *
 * Shapes mirror the backend rows verbatim (central_command/db/repo.py +
 * api/routes.py enrichment). snake_case is kept on purpose: these objects
 * cross the gateway RPC boundary untranslated, so the field names in the
 * UI grep-match the backend.
 */

export interface PolicyFlag {
  policy: string;
  action_index: number;
  capability?: string;
  argument?: string;
  value?: unknown;
  problem: string;
}

/** Contract shape: which record the action mutates + the version it was read at. */
export interface TargetRef {
  id?: string;
  system?: string;
  read_version?: string;
}

export interface ProposalAction {
  capability: string;
  arguments?: Record<string, unknown>;
  target_ref?: string | TargetRef;
  reversibility?: string;
  charter_diff?: string;
  /** skill.doc_add against an existing doc version — absent means nothing to
   *  diff (skill.create, or a skill.doc_add with no prior version). */
  skill_diff?: string;
  /** mcp.sync_source: proposed file bytes (captured at propose time) diffed
   *  against what is currently on disk, one entry per file. */
  mcp_diffs?: { path: string; diff: string }[];
}

export interface ProposalEvidence {
  kind: string;
  claim?: string;
  source_ref?: string | Record<string, unknown>;
  /** Re-derived from the event log for operator evidence — never the agent's copy. */
  source_feedback?: string;
  /** Was the agent's quote found in the recorded source? `undefined`/null means
   *  NOT CHECKED — never "checked and inconclusive". */
  claim_matches_source?: boolean | null;
  /** Where a coaching citation came from (stage 5a): the operator's selection,
   *  or the record the coach read past it into. */
  citation?: 'selected' | 'discovered' | 'unresolved';
}

/**
 * Where a proposal came from when it was not the drafter's own idea. Today's
 * only writer is the consult path (doctrine Decision 2): the specialist drafts,
 * so `agent_id` is the DRAFTER and `consulted_by` is the agent that asked.
 * Null/absent on every other path.
 */
export interface ProposalOrigin {
  consulted_by?: string | null;
  caller_session_id?: string | null;
}

/** Who decided a proposal (server-derived from the `proposal.decided` event and
 *  the provenance approver). `overrides` lists EVERY deciding override with the
 *  server's own `scope_text` — the cockpit renders it and builds none. An
 *  override approval is never `operator`. Pinned by tests/test_overrides_wire.py. */
export interface DecidedBy {
  kind: 'operator' | 'auditor' | 'override';
  overrides: { override_id: string; scope_text: string }[];
}

/** One override a pending proposal is parked behind in its shadow period. `k`
 *  is read fresh by the server. */
export interface ShadowMark {
  override_id: string;
  k: number;
  n: number;
  complete: boolean;
}

/** Row from proposals list (decisions.list). */
export interface ProposalSummary {
  id: string;
  session_id: string;
  agent_id: string;
  intent: string;
  status: string;
  created_at: string;
  decided_at?: string | null;
  origin?: ProposalOrigin | null;
  /** This proposal executed under the retired dry-run mode — nothing was
   *  written. Derived server-side from the `proposal.executed` event's
   *  "[dry-run]" result: history, kept true of the decision forever. */
  simulated?: boolean;
  /** Null/absent while undecided. */
  decided_by?: DecidedBy | null;
  /** Non-empty only on a pending proposal in an override's shadow period. */
  shadow?: ShadowMark[];
  /** Set only on an APPROVED proposal waiting out an outage (RETRY_PENDING);
   *  null for every other status. The server computes it
   *  (`routes.waiting_facts`); the cockpit renders and derives nothing. */
  waiting?: WaitingFacts | null;
  /** The chain link this proposal carries when it is a redraft (the column);
   *  null on a first draft. Server-built (`routes.annotate_redraft_facts`). */
  redraft?: RedraftLink | null;
  /** The ancestors from `redraft.of` back to the root, OLDEST first; empty on
   *  a first draft. The cockpit lists them and builds none. */
  chain?: ChainLink[];
  /** FAILED at the end of a chain that hit the limit (read from the
   *  `proposal.failed` event: the row has no marker). */
  chain_exhausted?: boolean;
  /** `settings.world_moved_redraft_limit`, so "n of <limit>" is never derived
   *  in the browser. */
  redraft_limit?: number;
}

/** Why a redraft exists: the drafter was resumed after the world moved, after
 *  the operator's rejection, or after a failed execution. */
export type RedraftCause = 'world_moved' | 'rejected' | 'failed';

/** The row's `proposal.redraft` column (epic-world-changed-redraft). */
export interface RedraftLink {
  of: string;
  root: string;
  cause: RedraftCause;
  /** World-moved redrafts in the chain up to and including this one. */
  world_moved_count: number;
}

/** What a failed execution recorded when the world had moved. */
export interface WorldMoved {
  target?: string;
  expected?: unknown;
  actual?: unknown;
  what_moved?: string;
}

/** One ancestor in a redraft's chain, read from the row and its
 *  `proposal.decided` / `proposal.failed` events. Pinned by
 *  tests/test_world_moved_wire.py. */
export interface ChainLink {
  id: string;
  status: string;
  verdict: string | null;
  approver: string | null;
  decided_at: string | null;
  failure: { capability: string | null; error: string | null; world_moved: WorldMoved | null } | null;
  chain_exhausted: boolean;
}

/** What a RETRY_PENDING proposal waits on (epic-outage-doctrine story 8). */
export interface WaitingFacts {
  dependencies: string[];
  /** Earliest `down_since` among the dependencies recorded down; null when
   *  none is (the probes say it is up and the backoff has not elapsed). */
  down_since: string | null;
  attempts: number;
  /** When the next retry is due (`retry_state.not_before`). */
  next_retry: string | null;
  /** A process restart cut the execution short: only the operator's retry or a
   *  dismiss moves it. */
  interrupted: boolean;
  completed_actions: number;
  /** Failing against a healthy probe — reported by the loud rule. */
  failing_despite_probe: boolean;
  last_error?: string | null;
}

export interface JiraIssueSnapshot {
  summary?: string | null;
  status?: string | null;
  issue_type?: string | null;
  priority?: string | null;
  assignee?: string | null;
  due_date?: string | null;
  labels?: string[] | null;
  updated?: string | null;
  url?: string | null;
  description?: string | null;
  error?: string;
}

/** Full row from proposals.get — list fields plus reviewer enrichment. */
export interface ProposalDetail extends ProposalSummary {
  actions: ProposalAction[];
  evidence: ProposalEvidence[];
  expected_effect?: string;
  policy_flags?: PolicyFlag[];
  /** The agent's OWN confidence in this proposal (uncertainty triage). Absent
   *  means the agent stated none — never render that as 'low'. */
  confidence?: Confidence | null;
  source_emails?: WorkItem[];
  folds?: WorkItem[];
  /** Live snapshot of every Jira issue the actions touch, keyed by issue key
   *  — read at review time, so it is the SOURCE beside the agent's
   *  jira_state claims. `error` means the read failed; the key still shows. */
  jira_issues?: Record<string, JiraIssueSnapshot>;
  /** The auditor's verdict on this proposal (bulk dismissals). Present means
   *  it was audited; a challenge is why the proposal is still awaiting you. */
  audit?: AuditRecord | null;
}

/** An agent's self-assessed confidence, recorded on the proposal. Advisory —
 *  every proposal still passes the human gate. */
export interface Confidence {
  level: 'high' | 'medium' | 'low';
  rationale?: string;
}

export interface AuditRecord {
  action_class?: string;
  verdict: 'concur' | 'challenge';
  rationale?: string;
  mode?: string;
  auto_confirmed?: boolean;
  operator_hold?: boolean;
}

/** Work-ledger row; dismissals arrive with payload included. */
export interface WorkItem {
  id: string;
  message_id?: string;
  thread_id?: string | null;
  feed?: string;
  /** The transport it arrived on (fixture | api | gmail). */
  source?: string;
  /** WHAT it is: 'email' | 'document'. Absent means email (the default the
   *  column carries), so the source pane must not say "document" on a
   *  backend that has not sent this yet. */
  kind?: string;
  subject?: string | null;
  state?: string;
  session_id?: string | null;
  attempts?: number;
  last_error?: string | null;
  enrolled_at?: string;
  /** When it went terminal (PROCESSED/FOLDED/FAILED) — the history view's
   *  sort/display key for confirmed dismissals. */
  terminal_at?: string | null;
  payload?: {
    from?: string;
    text?: string;
    dismissal_rationale?: string;
    operator_note?: string;
    audit?: AuditRecord;
    [key: string]: unknown;
  } | null;
}

/**
 * D7 phase 2: an orchestrator ask OF the operator — a plan review, a
 * question, or a control-plane stall escalation. Answering resumes the
 * parked orchestration loop; the answer is trusted operator input.
 */
export interface OperatorItem {
  id: string;
  /** 'continue': the run spent its request window (the loop breaker) and asks
   *  for another. Verdict-only — 'approved' grants a fresh window, 'declined'
   *  ends the run. */
  kind: 'plan_review' | 'question' | 'stall' | 'continue';
  session_id: string;
  agent_id: string;
  task_id?: string | null;
  /** The blocked task's title, resolved server-side in the same payload — an
   *  id alone does not tell the operator what work is stalled. */
  task_title?: string | null;
  body: string;
  status: 'OPEN' | 'ANSWERED';
  verdict?: string | null;
  answer?: string | null;
  created_at?: string | null;
  answered_at?: string | null;
  /** Tier 3: the agent judged this needed real back-and-forth and opened a
   *  clarification conversation. Answer it THERE, not here. */
  discussion_session_id?: string | null;
  /** Whether that lane's session is still writable — `null` when there is no
   *  lane at all. A lane can close (concluded, or its session otherwise went
   *  terminal) while the item stays OPEN, so the pane must check this rather
   *  than inferring "open" from `discussion_session_id` alone. */
  lane_open?: boolean | null;
  /** The ledger item (email/document) the asking session was processing —
   *  present when the ask came off the ingest path, so the reviewer can read
   *  the email the question is about, like a dismissal card. */
  source_email?: WorkItem | null;
}

export interface DecisionsData {
  proposals: ProposalSummary[];
  /** Approved proposals waiting on an outage. NOT part of the "pending" count:
   *  the decision was made; what waits is the world. */
  waiting?: ProposalSummary[];
  dismissals: WorkItem[];
  operatorItems: OperatorItem[];
}

export type DecisionSelection =
  | { kind: 'proposal'; id: string }
  | { kind: 'dismissal'; id: string }
  | { kind: 'operator_item'; id: string }
  | null;
