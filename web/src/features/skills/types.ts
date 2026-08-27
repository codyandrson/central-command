/**
 * Skills page types — the shapes `skills.list`, `skills.detail`,
 * `skills.history` and `gaps.list` return.
 *
 * These mirror the spine's own rows: a `SkillRow` is a `skill` row plus the
 * reference-doc count the catalog query computes, and a `SkillDoc` is a
 * `skill_doc` row. Document history is versions of the same `doc_key` — a new
 * version never overwrites the old one, so every prior version stays readable
 * exactly like a charter version.
 *
 * A `Gap` is NOT a table row: it is the payload of a `gap.declared` event,
 * flattened by `repo.recent_gaps` with the event's `ref_id` as `session_id`
 * and its timestamp as `at`.
 */

/** A `skill` row as the database holds it, and nothing more. This is exactly
 *  what `skills.detail` returns under `skill` — `repo.get_skill` is a plain
 *  `select *`, so it carries none of the counts the catalog query computes. */
export interface SkillBase {
  id: string;
  title: string;
  summary: string;
  status: 'ACTIVE' | 'RETIRED' | string;
  created_at: string;
  retired_reason?: string | null;
  retired_at?: string | null;
}

/**
 * A catalog row: the skill plus the two counts `repo.list_skills` computes for
 * it. These are correlated subselects, not client-side derivations.
 *
 * `holder_count` mirrors `get_skill_route`'s holder set predicate for predicate
 * — roster agents (non-empty `role`) with a non-revoked grant on an ACTIVE
 * skill — so this number and the length of `SkillDetail.agents` are the same
 * number computed twice, and `test_gateway_detail_and_list_agree` holds them to
 * it.
 */
export interface SkillRow extends SkillBase {
  reference_count: number;
  holder_count: number;
}

export interface SkillDoc {
  id: string;
  skill_id: string;
  doc_key: string;
  kind: 'guidance' | 'reference' | string;
  title: string;
  content: string;
  version: number;
  is_current: boolean;
  source_url: string | null;
  describes: string | null;
  captured_at: string | null;
  added_by: string;
  created_at: string;
  /** Set on the version that WAS current when the operator ended the doc_key.
   *  A doc with `retired_at` and `is_current: false` in the detail payload is
   *  a retired key kept as a click-target for its history. */
  retired_at: string | null;
  retired_reason: string | null;
}

export interface SkillDetail {
  /** `SkillBase`, NOT `SkillRow`: this payload has no `reference_count` and no
   *  `holder_count`. The view derives both from `docs` and `agents`, which it
   *  already has in full. */
  skill: SkillBase;
  docs: SkillDoc[];
  /** Agent ids holding an ACTIVE grant on this skill. */
  agents: string[];
}

export interface Gap {
  kind: 'missing_knowledge' | 'stale_knowledge' | 'missing_tool' | 'missing_agent' | string;
  subject: string;
  need: string;
  doc_id: string | null;
  doc_says: string | null;
  observed: string | null;
  agent_id: string | null;
  session_id: string | null;
  at: string;
}

/** What `skills.import` reports back: the skill it wrote into, the guidance
 *  doc_key, and the reference doc_keys it added a version of. */
export interface ImportResult {
  skill_id: string;
  guidance: string;
  references: string[];
}

/** What `POST /api/skills/import-site` reports back — which rung of the
 *  ladder it climbed (llms-full.txt, sitemap+trafilatura, or the browser
 *  renderer) and what landed. */
export interface SiteImportResult {
  imported: { doc_key: string; title: string; source_url: string }[];
  skipped: { url: string; reason: string }[];
  truncated: boolean;
  rung: 'llms-full' | 'sitemap' | 'crawler' | string;
  notes: string[];
}
