/**
 * The Skills page's "not refreshed" state (epic-skills-refresh-on-update
 * story 8; updater-contract.md §Status and visibility): the wire type of
 * `GET /api/skills/update-review` (RPC `skills.update_review_latest`) and the
 * pure wording the banner renders from it. No network in this file.
 *
 * The read is deliberately LIGHT — no texts, no hashes — and the server decides
 * which rows are `pending_apply` (not applied, and either an `apply_error` or a
 * decision the finished update never carried out) and which `moved` (the error
 * says a text changed since the review, so a plain retry fails forever).
 */

export interface LatestItem {
  skill_id: string;
  doc_key: string;
  status: string;
  verdict: string | null;
  resolved_at: string | null;
  applied_at: string | null;
  apply_error: string | null;
  pending_apply: boolean;
  moved: boolean;
}

export interface LatestReview {
  target: string;
  ref: string;
  computed_at: string;
  installed: string;
  finished: boolean;
  counts: { not_refreshed: number } & Record<string, number>;
  items: LatestItem[];
}

export function isLatestReview(v: unknown): v is LatestReview {
  if (!v || typeof v !== 'object') return false;
  const r = v as Partial<LatestReview>;
  return typeof r.target === 'string' && Array.isArray(r.items);
}

/** The rows the banner is about. */
export function notRefreshed(review: LatestReview | null): LatestItem[] {
  return review ? review.items.filter((i) => i.pending_apply) : [];
}

export function bannerText(n: number, target: string): string {
  return `${n} bundled ${n === 1 ? 'doc' : 'docs'} not refreshed for v${target} — retry`;
}

/** What one row says. A whole-skill row has doc_key '*'. */
export function rowLabel(i: LatestItem): string {
  return i.doc_key === '*' ? i.skill_id : `${i.skill_id}/${i.doc_key}`;
}

export function rowReason(i: LatestItem): string {
  return i.apply_error ?? 'decided in the review, but the update did not apply it';
}

export const MOVED_HINT =
  'A plain retry cannot fix this: the library or the release text changed since the review. '
  + 'Open the Update dialog (Settings › Updates) to recompute the review, decide this doc again, and apply.';
