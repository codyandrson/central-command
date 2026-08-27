/**
 * Agents page types — the shapes `agents.roster` and `agents.detail` return.
 *
 * These mirror the spine's own rows deliberately: the roster IS the `agent`
 * table (D24), grants ARE the `agent_grant` rows including revoked ones (D25 —
 * revocation is a timestamp, never a delete), and the charter IS its version
 * history. Nothing here is a cockpit-side derivation the record can't back.
 */

export interface AgentRow {
  id: string;
  name: string;
  model: string | null;
  thinking: string | null;
  role: string;
  status: 'ACTIVE' | 'RETIRED' | string;
  taskable: boolean;
  consultable: boolean;
  conversational: boolean;
  deviations: string | null;
  template: string | null;
  hired_by: string | null;
  retired_reason: string | null;
  retired_at: string | null;
  created_at: string | null;
  charter_version: number;
  packs: string[];
  proposals: { total: number; decided: number; [status: string]: number };
  /** Sessions that have not landed (anything but DONE/FAILED, open
   *  conversations included). >0 sorts the agent to the top of the roster. */
  active_sessions: number;
  /** null — never 0 — when nothing has been decided yet. */
  approval_rate: number | null;
}

export interface CharterVersion {
  version: number;
  content: string;
  status: string;
  rationale: string | null;
  created_by: string;
  created_at: string | null;
  /** The coaching proposal that produced this version — null for
   *  operator-typed saves and every version predating this field. */
  proposal_id?: string | null;
}

export interface Grant {
  agent_id: string;
  pack: string;
  granted_by: string;
  granted_at: string | null;
  revoked_at: string | null;
  revoked_reason: string | null;
}

/**
 * A skill grant as `repo.list_agent_skills` returns it: the
 * `agent_skill_grant` row joined to its skill, plus two computed columns.
 *
 * `has_guidance` is the load-bearing one. `runtime/skills.py`'s
 * `capabilities_for` offers the agent NOTHING for a granted skill that has no
 * current guidance document — it logs a warning and skips — so a healthy-looking
 * grant can deliver nothing at all. The flag is that same condition computed in
 * SQL, and `tests/test_agent_skills_panel.py` holds the two together against the
 * live runtime. Treat it as "is this skill actually delivered", not as metadata.
 *
 * `status` is the SKILL's status, not the grant's: retiring a skill never
 * touches its grants, so a RETIRED skill here means held-on-the-record and
 * not-delivered — a second, independent reason the agent receives nothing.
 */
export interface SkillGrant {
  agent_id: string;
  skill_id: string;
  granted_by: string;
  granted_at: string | null;
  revoked_at: string | null;
  revoked_reason: string | null;
  title: string;
  summary: string;
  status: 'ACTIVE' | 'RETIRED' | string;
  has_guidance: boolean;
  reference_count: number;
}

/** A library catalog row as the grant picker needs it. The detail payload
 *  carries ACTIVE skills only here — `grant_skill_route` 409s on a retired
 *  skill, so one in the picker would be a click that can only fail. */
export interface SkillCatalogEntry {
  id: string;
  title: string;
  summary: string;
  status: string;
  reference_count: number;
  holder_count: number;
}

export interface PackInfo {
  name: string;
  description: string;
  tools: string[];
  capabilities: string[];
}

export interface ProposalRow {
  id: string;
  session_id: string;
  agent_id: string;
  intent: string;
  status: string;
  created_at: string | null;
  decided_at: string | null;
}

export interface SessionRow {
  id: string;
  agent_id: string;
  status: string;
  mode: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface CoachingSignal {
  kind: string;
  event_id: number;
  intent?: string | null;
  feedback: string;
  /** Who wrote the quotable text: the operator (rejections, notes, reopens) or
   *  the agent itself (task outcomes, declared gaps, deferred contacts).
   *  Absent on older payloads — treat a missing value as 'operator'. */
  author?: 'operator' | 'agent';
  /** ISO-8601 timestamp of the underlying event, when the source row has one. */
  at?: string;
}

export interface AgentDetail {
  agent: AgentRow;
  charter: { current: CharterVersion; versions: CharterVersion[] };
  grants: Grant[];
  packs: PackInfo[];
  /** Skill grants including revoked ones AND grants on retired skills, so the
   *  panel can render history and both not-delivered states. */
  skills: SkillGrant[];
  skill_catalog: SkillCatalogEntry[];
  proposals: ProposalRow[];
  sessions: SessionRow[];
  signals: CoachingSignal[];
}
