/**
 * The line diff the Skills page shows for a flagged bundled doc (DL-232): the
 * library's text against what the release ships, side by side. Computed
 * client-side with `diff-sequences`, the dependency the chat DiffView already
 * uses. Nothing in this file touches the network.
 */
import diff from 'diff-sequences';

/** Lines as the diff sees them: a trailing newline does not make an empty line. */
export function splitLines(text: string): string[] {
  if (text === '') return [];
  const lines = text.split(/\r?\n/);
  if (lines[lines.length - 1] === '') lines.pop();
  return lines;
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
