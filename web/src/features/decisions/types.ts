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
  /** This execution was a DRY RUN — nothing was written. Derived server-side
   *  from the `proposal.executed` event's "[dry-run]" result, so it stays true
   *  of the decision even after the deployment switches to live. */
  simulated?: boolean;
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
  provenance?: Record<string, unknown> | null;
  result_text?: string | null;
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
  dismissals: WorkItem[];
  operatorItems: OperatorItem[];
}

export type DecisionSelection =
  | { kind: 'proposal'; id: string }
  | { kind: 'dismissal'; id: string }
  | { kind: 'operator_item'; id: string }
  | null;
