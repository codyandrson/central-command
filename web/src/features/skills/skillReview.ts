/**
 * The bundled-skill update review (epic-skills-refresh-on-update): wire types
 * for the three `/api/skills/update-review` routes and the pure line logic
 * the update dialog renders from them.
 *
 * Everything here is client-side on purpose (Assumption A8): the server sends
 * the four texts of an item and the `git merge-file --diff3` output; the
 * colour classification is computed with `diff-sequences`, the dependency the
 * chat DiffView already uses, and the merge's conflict regions are parsed out
 * of the `merged` text. Nothing in this file touches the network.
 */
import diff from 'diff-sequences';

export type ReviewStatus =
  | 'unchanged-upstream' | 'unmodified' | 'modified' | 'retired-upstream'
  | 'retired-locally' | 'new' | 'local-only' | 'skill-retired';

export type Verdict = 'take_release' | 'keep_mine' | 'edit';

/** The shape `GET /api/skills/update-review/{target}` answers (story 2.3). */
export interface ReviewItem {
  skill_id: string;
  /** '*' for a whole-skill (skill-retired) row. */
  doc_key: string;
  status: ReviewStatus;
  bundled_path: string;
  base_origin: 'library' | 'installed-tree';
  base: string | null;
  ours: string | null;
  theirs: string | null;
  merged: string | null;
  conflicts: number;
  ours_hash: string | null;
  theirs_hash: string | null;
  needs_verdict: boolean;
  verdict: Verdict | null;
  resolved_content: string | null;
  resolved_at: string | null;
  applied_at: string | null;
  apply_error: string | null;
}

export interface ReviewCounts {
  unmodified: number;
  new: number;
  modified: number;
  unresolved: number;
  retired_upstream: number;
  retired_locally: number;
  skill_retired: number;
}

export interface Review {
  target: string;
  ref: string;
  computed_at: string;
  counts: ReviewCounts;
  items: ReviewItem[];
}

const COUNT_KEYS: Array<keyof ReviewCounts> = [
  'unmodified', 'new', 'modified', 'unresolved', 'retired_upstream', 'retired_locally', 'skill_retired',
];

/** True only for something shaped like a review: a version-check body or an
 *  error page that happened to answer 200 must not be rendered as one. */
export function isReview(value: unknown): value is Review {
  if (!value || typeof value !== 'object') return false;
  const v = value as Partial<Review>;
  return Array.isArray(v.items)
    && !!v.counts
    && typeof v.counts === 'object'
    && COUNT_KEYS.every((k) => typeof (v.counts as unknown as Record<string, unknown>)[k] === 'number');
}

export function itemKey(item: Pick<ReviewItem, 'skill_id' | 'doc_key'>): string {
  return `${item.skill_id}/${item.doc_key}`;
}

/** Rows that wait on the operator and have no verdict yet. */
export function isUnresolved(item: ReviewItem): boolean {
  return item.needs_verdict && item.verdict === null;
}

/** Docs the updater refreshes without asking: untouched here, or retired
 *  upstream with nothing edited here. */
export function followCount(review: Review): number {
  return review.items.filter(
    (i) => i.status === 'unmodified' || (i.status === 'retired-upstream' && !i.needs_verdict),
  ).length;
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** "N docs follow the release unchanged · M new · K need your decision",
 *  a zero term omitted; null when there is nothing to say. */
export function summaryLine(review: Review): string | null {
  const parts: string[] = [];
  const follow = followCount(review);
  if (follow > 0) parts.push(`${follow} ${plural(follow, 'doc follows', 'docs follow')} the release unchanged`);
  if (review.counts.new > 0) parts.push(`${review.counts.new} new`);
  if (review.counts.unresolved > 0) {
    parts.push(`${review.counts.unresolved} ${plural(review.counts.unresolved, 'needs', 'need')} your decision`);
  }
  return parts.length ? parts.join(' · ') : null;
}

// --- conflict markers --------------------------------------------------------

/** Lines as the diff sees them: a trailing newline does not make an empty line. */
export function splitLines(text: string): string[] {
  if (text === '') return [];
  const lines = text.split(/\r?\n/);
  if (lines[lines.length - 1] === '') lines.pop();
  return lines;
}

const OPEN = /^<{7} \S/;
const BASE = /^\|{7} \S/;
const CLOSE = /^>{7} \S/;
const DIVIDER = '=======';

/** A line exactly as `git merge-file` writes a conflict marker: a run of seven
 *  and a label, or the bare divider. The server refuses an edit holding one
 *  (story 2.3); this is the same rule, so Save can be disabled before the post. */
export function isConflictMarker(line: string): boolean {
  return line === DIVIDER || OPEN.test(line) || BASE.test(line) || CLOSE.test(line);
}

export function countMarkers(text: string): number {
  return splitLines(text).filter(isConflictMarker).length;
}

// --- diff --------------------------------------------------------------------

function commonMasks(a: string[], b: string[]): [boolean[], boolean[]] {
  const inA = new Array<boolean>(a.length).fill(false);
  const inB = new Array<boolean>(b.length).fill(false);
  diff(
    a.length, b.length,
    (i, j) => a[i] === b[j],
    (n, ai, bi) => {
      for (let k = 0; k < n; k++) { inA[ai + k] = true; inB[bi + k] = true; }
    },
  );
  return [inA, inB];
}

export interface ConflictRegion {
  mine: string[];
  base: string[];
  release: string[];
}

export type MergedRow =
  | { kind: 'same' | 'add' | 'del' | 'mine' | 'mine-del'; text: string }
  | { kind: 'conflict'; region: ConflictRegion };

/** The gutter mark of a row; colour is never the only signal. */
export const GUTTER: Record<MergedRow['kind'], string> = {
  same: ' ', add: '+', del: '-', mine: '~', 'mine-del': '~', conflict: '!',
};

interface FlatLine { text: string; region: number | null }

/** Split diff3 output into plain lines and conflict regions. Returns null for
 *  a malformed structure (an unterminated or out-of-order marker): the caller
 *  then shows the merged text as plain lines, markers included, rather than
 *  guess at regions. */
function parseRegions(merged: string): { flat: FlatLine[]; regions: ConflictRegion[] } | null {
  const flat: FlatLine[] = [];
  const regions: ConflictRegion[] = [];
  let cur: ConflictRegion | null = null;
  let part: keyof ConflictRegion | null = null;
  for (const line of splitLines(merged)) {
    if (cur === null) {
      if (OPEN.test(line)) { cur = { mine: [], base: [], release: [] }; part = 'mine'; }
      else if (BASE.test(line) || CLOSE.test(line) || line === DIVIDER) return null;
      else flat.push({ text: line, region: null });
    } else if (BASE.test(line) && part === 'mine') part = 'base';
    else if (line === DIVIDER && (part === 'base' || part === 'mine')) part = 'release';
    else if (CLOSE.test(line) && part === 'release') {
      const r = regions.length;
      regions.push(cur);
      // The region stands where its base block stood: a sentinel no real line
      // equals anchors it (a conflict with an empty base block has no lines),
      // then the base block itself, which pairs with the base text.
      flat.push({ text: `\u0000conflict ${r}\u0000`, region: r });
      for (const b of cur.base) flat.push({ text: b, region: r });
      cur = null; part = null;
    } else if (OPEN.test(line) || BASE.test(line) || CLOSE.test(line)) return null;
    else cur[part as keyof ConflictRegion].push(line);
  }
  return cur === null ? { flat, regions } : null;
}

/**
 * The default view: the merged text, line by line.
 *
 * Rows come from diffing the item's base against the merged text with each
 * conflict region folded back to its base block, then classifying what the
 * diff found: a line only the merge has is the release's (`add`, green +) when
 * the release added it and the library's (`mine`, blue ~) otherwise; a line
 * only the base has is the release's removal (`del`, red -) when the release
 * dropped it and the library's (`mine-del`, blue ~) otherwise. Each conflict
 * region is one `conflict` row at its place, carrying its three blocks.
 */
export function mergedRows(item: Pick<ReviewItem, 'base' | 'ours' | 'theirs' | 'merged'>): MergedRow[] {
  const base = splitLines(item.base ?? '');
  const theirs = splitLines(item.theirs ?? '');
  const parsed = parseRegions(item.merged ?? '');
  const flat = parsed ? parsed.flat : splitLines(item.merged ?? '').map((text) => ({ text, region: null }));
  const regions = parsed ? parsed.regions : [];

  const [baseInTheirs, theirsInBase] = commonMasks(base, theirs);
  const releaseRemoved = new Set(base.filter((_, i) => !baseInTheirs[i]));
  const releaseAdded = new Set(theirs.filter((_, i) => !theirsInBase[i]));

  const [inBase, inFlat] = commonMasks(base, flat.map((f) => f.text));
  const rows: MergedRow[] = [];
  const emitted = new Set<number>();
  let i = 0;
  let j = 0;
  while (i < base.length || j < flat.length) {
    if (i < base.length && !inBase[i]) {
      rows.push({ kind: releaseRemoved.has(base[i]) ? 'del' : 'mine-del', text: base[i] });
      i++;
    } else if (j < flat.length && !inFlat[j]) {
      const f = flat[j];
      if (f.region !== null) {
        if (!emitted.has(f.region)) { emitted.add(f.region); rows.push({ kind: 'conflict', region: regions[f.region] }); }
      } else rows.push({ kind: releaseAdded.has(f.text) ? 'add' : 'mine', text: f.text });
      j++;
    } else if (i < base.length && j < flat.length) {
      const f = flat[j];
      if (f.region !== null) {
        if (!emitted.has(f.region)) { emitted.add(f.region); rows.push({ kind: 'conflict', region: regions[f.region] }); }
      } else rows.push({ kind: 'same', text: f.text });
      i++; j++;
    } else break; // masks pair equally many lines, so only reached on a bug
  }
  return rows;
}

export interface SideBySideRow { left: string | null; right: string | null; same: boolean }

/** "mine | release": the library's text against the release's, paired line for
 *  line where they agree and run against run where they differ. */
export function sideBySideRows(ours: string | null, theirs: string | null): SideBySideRow[] {
  const a = splitLines(ours ?? '');
  const b = splitLines(theirs ?? '');
  const [inA, inB] = commonMasks(a, b);
  const rows: SideBySideRow[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && inA[i] && inB[j]) {
      rows.push({ left: a[i], right: b[j], same: true });
      i++; j++;
      continue;
    }
    const lefts: string[] = [];
    const rights: string[] = [];
    while (i < a.length && !inA[i]) lefts.push(a[i++]);
    while (j < b.length && !inB[j]) rights.push(b[j++]);
    if (!lefts.length && !rights.length) break;
    for (let k = 0; k < Math.max(lefts.length, rights.length); k++) {
      rows.push({ left: lefts[k] ?? null, right: rights[k] ?? null, same: false });
    }
  }
  return rows;
}
