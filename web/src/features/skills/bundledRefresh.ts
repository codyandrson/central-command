/**
 * The Skills page's flagged bundled docs (DL-232): the wire type of
 * `GET /api/skills/release-flags` (RPC `skills.release_flags`) and the pure
 * wording the banner renders from it. No network in this file.
 *
 * A flag is a bundled doc the operator edited that a release then changed or
 * removed: the release comparison kept the library's text, and the operator
 * decides here — take the release's text, keep theirs, or edit.
 */

export type Decision = 'take_release' | 'keep_mine' | 'edit';

export interface ReleaseFlag {
  skill_id: string;
  doc_key: string;
  release: string;
  change: 'changed' | 'removed';
  bundled_path: string;
  flagged_at: string;
  /** The library's text now; null when the doc is retired here. */
  ours: string | null;
  ours_hash: string | null;
  /** What the release ships; null when it removed the doc. */
  theirs: string | null;
}

export function isFlagList(v: unknown): v is { items: ReleaseFlag[] } {
  return !!v && typeof v === 'object' && Array.isArray((v as { items?: unknown }).items);
}

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
