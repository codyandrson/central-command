import { describe, expect, it } from 'vitest';
import { sideBySideRows, splitLines } from './skillReview';

describe('sideBySideRows', () => {
  it('pairs agreeing lines and runs of differing ones', () => {
    expect(sideBySideRows('a\nmine\nz\n', 'a\nrelease one\nrelease two\nz\n')).toEqual([
      { left: 'a', right: 'a', same: true },
      { left: 'mine', right: 'release one', same: false },
      { left: null, right: 'release two', same: false },
      { left: 'z', right: 'z', same: true },
    ]);
  });

  it('shows one side alone when the other has no text', () => {
    expect(sideBySideRows(null, 'a\n')).toEqual([{ left: null, right: 'a', same: false }]);
  });
});

describe('splitLines', () => {
  it('does not turn a trailing newline into an empty line', () => {
    expect(splitLines('a\r\nb\n')).toEqual(['a', 'b']);
    expect(splitLines('')).toEqual([]);
  });
});
