/**
 * The mechanical findings on a verification row, as the Verify card words them
 * (kept apart from the view so the wording is testable without rendering).
 */
import type { VerificationRow } from './useGraphVerifications';

/** One mechanical finding: the plain-worded line, and (when there is one) the
 *  full detail behind it — shown clipped, complete on hover, like the
 *  truncated problem note in the history list. */
export interface MechanicalFlag { text: string; detail?: string }

/** How much of an error text the card shows before clipping; the whole text
 *  stays in the element's title. */
export const DETAIL_CLIP = 280;

export function clip(text: string, max: number = DETAIL_CLIP): string {
  return text.length > max ? `${text.slice(0, max).trimEnd()}…` : text;
}

/** The mechanical-check messages, plainly worded — the spec's list, verbatim,
 *  plus the ingest failure (extraction FAILED, and is not retried). */
export function mechanicalMessages(m: VerificationRow['mechanical']): MechanicalFlag[] {
  const out: MechanicalFlag[] = [];
  const error = typeof m.error === 'string' && m.error.trim() ? m.error.trim() : undefined;
  if (m.ingest_failed) {
    out.push({
      text: 'extraction FAILED and was not retried',
      detail: error ?? 'no error text was recorded',
    });
  }
  if (m.missing) {
    out.push({
      text: m.resubmitted ? 'episode never landed, even after a re-submission' : 'episode never landed',
      detail: error,
    });
  }
  if (m.empty_delta) out.push({ text: 'extraction produced nothing' });
  if (m.unembedded && m.unembedded.length > 0) {
    out.push({ text: `${m.unembedded.length} entities unembedded (invisible to semantic recall)` });
  }
  if (m.no_approved_text) out.push({ text: 'no approved text on record' });
  if (m.episode_deleted) out.push({ text: 'episode was deleted before it was audited' });
  return out;
}
