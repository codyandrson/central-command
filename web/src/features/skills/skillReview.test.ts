import { describe, expect, it } from 'vitest';
import {
  countMarkers, isConflictMarker, mergedRows, sideBySideRows, summaryLine,
  type Review, type ReviewItem,
} from './skillReview';

const ITEM = {
  base: 'a\nb\nc\nd\n', ours: 'a\nb\nc\nd\n', theirs: 'a\nb\nc\nd\n', merged: 'a\nb\nc\nd\n',
};

function kinds(rows: ReturnType<typeof mergedRows>) {
  return rows.map((r) => (r.kind === 'conflict' ? 'conflict' : `${r.kind}:${r.text}`));
}

describe('mergedRows', () => {
  it('marks what the release added, what it removed and what only the library changed', () => {
    const rows = mergedRows({
      base: 'a\nb\nc\nd\n',
      theirs: 'a\nb\nc\nd\nrelease tail\n',
      ours: 'a\nb\nmine c\nd\n',
      // merged = the library's edit of c plus the release's tail, b removed by the release
      merged: 'a\nmine c\nd\nrelease tail\n',
    });
    // b was dropped by the merge but kept by the release: it is the library's removal.
    expect(kinds(rows)).toEqual([
      'same:a', 'mine-del:b', 'mine-del:c', 'mine:mine c', 'same:d', 'add:release tail',
    ]);
  });

  it('marks a line the release dropped as removed', () => {
    const rows = mergedRows({
      base: 'a\nb\nc\n', ours: 'a\nb\nc\n', theirs: 'a\nc\n', merged: 'a\nc\n',
    });
    expect(kinds(rows)).toEqual(['same:a', 'del:b', 'same:c']);
  });

  it('puts a conflict region where its base block stood, with its three blocks', () => {
    const rows = mergedRows({
      base: 'one\nten\neleven\n', ours: '', theirs: '',
      merged: 'one\n<<<<<<< mine (library)\nmine ten\n||||||| base (v0)\nten\n=======\nrelease ten\n>>>>>>> release (v1)\neleven\n',
    });
    expect(kinds(rows)).toEqual(['same:one', 'conflict', 'same:eleven']);
    const c = rows[1];
    expect(c.kind === 'conflict' && c.region).toEqual({
      mine: ['mine ten'], base: ['ten'], release: ['release ten'],
    });
  });

  it('places a conflict whose base block is empty', () => {
    const rows = mergedRows({
      base: 'one\ntwo\n', ours: '', theirs: '',
      merged: 'one\n<<<<<<< mine (library)\nx\n||||||| base (v0)\n=======\ny\n>>>>>>> release (v1)\ntwo\n',
    });
    expect(kinds(rows)).toEqual(['same:one', 'conflict', 'same:two']);
  });

  it('shows a malformed merge as plain lines instead of guessing at regions', () => {
    const rows = mergedRows({ ...ITEM, base: 'a\n', merged: 'a\n<<<<<<< mine (library)\nstray\n' });
    expect(rows.every((r) => r.kind !== 'conflict')).toBe(true);
    expect(rows.some((r) => r.kind !== 'conflict' && r.text.startsWith('<<<<<<<'))).toBe(true);
  });

  it('copes with a missing base (everything merged is new)', () => {
    const rows = mergedRows({ base: null, ours: 'x\n', theirs: 'x\n', merged: 'x\n' });
    expect(kinds(rows)).toEqual(['add:x']);
  });
});

describe('sideBySideRows', () => {
  it('pairs agreeing lines and runs of differing ones', () => {
    expect(sideBySideRows('a\nmine\nz\n', 'a\nrelease one\nrelease two\nz\n')).toEqual([
      { left: 'a', right: 'a', same: true },
      { left: 'mine', right: 'release one', same: false },
      { left: null, right: 'release two', same: false },
      { left: 'z', right: 'z', same: true },
    ]);
  });
});

describe('conflict markers', () => {
  it('counts only lines as git merge-file writes them', () => {
    const text = [
      '<<<<<<< mine (library)', '||||||| base (v0)', '=======', '>>>>>>> release (v1)',
      'Title', '=====', '==========', '<<<<<<<', '>>>>>>>>', 'a ======= b',
    ].join('\n');
    expect(countMarkers(text)).toBe(4);
    expect(isConflictMarker('=======')).toBe(true);
    expect(isConflictMarker('========')).toBe(false);
    expect(countMarkers(text.replace(/\n/g, '\r\n'))).toBe(4);
  });
});

describe('summaryLine', () => {
  const review = (counts: Partial<Review['counts']>, items: Partial<ReviewItem>[] = []): Review => ({
    target: '1', ref: 'v1', computed_at: 'now',
    counts: {
      unmodified: 0, new: 0, modified: 0, unresolved: 0, retired_upstream: 0, retired_locally: 0,
      skill_retired: 0, ...counts,
    },
    items: items as ReviewItem[],
  });
  const unmod = { status: 'unmodified' as const };

  it('omits a zero term', () => {
    expect(summaryLine(review({ unmodified: 3 }, [unmod, unmod, unmod]))).toBe(
      '3 docs follow the release unchanged',
    );
    expect(summaryLine(review({ new: 2, unresolved: 1 }))).toBe('2 new · 1 needs your decision');
    expect(summaryLine(review({ unmodified: 1, new: 1, unresolved: 2 }, [unmod]))).toBe(
      '1 doc follows the release unchanged · 1 new · 2 need your decision',
    );
    expect(summaryLine(review({}))).toBeNull();
  });

  it('counts an upstream retirement with nothing edited here as following the release', () => {
    const quiet = { status: 'retired-upstream' as const, needs_verdict: false };
    const asked = { status: 'retired-upstream' as const, needs_verdict: true };
    expect(summaryLine(review({ retired_upstream: 2 }, [quiet, asked]))).toBe(
      '1 doc follows the release unchanged',
    );
  });
});
