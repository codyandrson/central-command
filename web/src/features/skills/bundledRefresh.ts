/**
 * The Skills page's flagged bundled docs (DL-232): the wire type of
 * `GET /api/skills/release-flags` (RPC `skills.release_flags`) as a zod schema,
 * and the pure wording the banner renders from it. No network in this file.
 *
 * A flag is a bundled doc the operator edited that a release then changed or
 * removed: the release comparison kept the library's text, and the operator
 * decides here — take the release's text, keep theirs, or edit.
 */

import { z } from 'zod';

export type Decision = 'take_release' | 'keep_mine' | 'edit';

const releaseFlagSchema = z.object({
  skill_id: z.string(),
  doc_key: z.string(),
  release: z.string(),
  change: z.enum(['changed', 'removed']),
  bundled_path: z.string(),
  flagged_at: z.string(),
  /** The library's text now; null when the doc is retired here. */
  ours: z.string().nullable(),
  ours_hash: z.string().nullable(),
  /** What the release ships; null when it removed the doc. */
  theirs: z.string().nullable(),
});

export type ReleaseFlag = z.infer<typeof releaseFlagSchema>;

/** The `skills.release_flags` answer: `{ items: ReleaseFlag[] }`. */
export const flagListSchema = z.object({ items: z.array(releaseFlagSchema) });

export function bannerText(n: number): string {
  return `${n} bundled ${n === 1 ? 'doc you edited was' : 'docs you edited were'} changed by a release — your text was kept`;
}

export function rowLabel(f: Pick<ReleaseFlag, 'skill_id' | 'doc_key'>): string {
  return `${f.skill_id}/${f.doc_key}`;
}

/** What happened to the doc, in one phrase. */
export function changeWord(f: ReleaseFlag): string {
  if (f.change === 'removed') return `v${f.release} no longer ships it`;
  if (f.ours === null) return `retired here; v${f.release} changed it`;
  return `changed here and in v${f.release}`;
}

/** The label of the take-the-release button for this flag. */
export function takeLabel(f: ReleaseFlag): string {
  if (f.change === 'removed') return 'Retire it (as the release did)';
  if (f.ours === null) return 'Reactivate with the release text';
  return 'Take the release text';
}

/** Whether an edit is offered: there is a text to start from. */
export function canEdit(f: ReleaseFlag): boolean {
  return f.ours !== null || f.theirs !== null;
}
