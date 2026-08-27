/** Tests for mergeRecoveredTail. */
import { describe, it, expect } from 'vitest';
import { mergeRecoveredTail } from './mergeRecoveredTail';
import type { ChatMsg } from '@/features/chat/types';

function makeMsg(role: string, text: string, ts?: number): ChatMsg {
  return {
    role: role as ChatMsg['role'],
    html: `<p>${text}</p>`,
    rawText: text,
    timestamp: new Date(ts ?? Date.now()),
  };
}

describe('mergeRecoveredTail', () => {
  it('returns recovered when existing is empty', () => {
    const recovered = [makeMsg('user', 'Hello')];
    expect(mergeRecoveredTail([], recovered)).toEqual(recovered);
  });

  it('returns existing when recovered is empty', () => {
    const existing = [makeMsg('user', 'Hello')];
    expect(mergeRecoveredTail(existing, [])).toEqual(existing);
  });

  it('appends new messages when recovered starts where existing ends', () => {
    const ts = 1700000000000;
    const existing = [
      makeMsg('user', 'Hello', ts),
      makeMsg('assistant', 'Hi', ts + 1000),
    ];
    const recovered = [
      makeMsg('user', 'Hello', ts),
      makeMsg('assistant', 'Hi', ts + 1000),
      makeMsg('user', 'New question', ts + 2000),
    ];
    const result = mergeRecoveredTail(existing, recovered);
    expect(result).toHaveLength(3);
    expect(result[2].rawText).toBe('New question');
  });

  it('does not duplicate overlapping messages', () => {
    const ts = 1700000000000;
    const existing = [makeMsg('user', 'Hello', ts), makeMsg('assistant', 'Hi', ts + 1000)];
    const recovered = [makeMsg('user', 'Hello', ts), makeMsg('assistant', 'Hi', ts + 1000), makeMsg('user', 'Follow up', ts + 2000)];
    const result = mergeRecoveredTail(existing, recovered);
    // Should have 3 messages, not 4 or 5
    expect(result).toHaveLength(3);
  });

  it('uses anchor path when no suffix-prefix overlap', () => {
    const ts = 1700000000000;
    const existing = [
      makeMsg('user', 'Message A', ts),
      makeMsg('assistant', 'Reply A', ts + 1000),
      makeMsg('user', 'Message B unique content here', ts + 2000),
      makeMsg('assistant', 'Old reply B', ts + 3000),
    ];
    const recovered = [
      makeMsg('user', 'Message B unique content here', ts + 2000),
      makeMsg('assistant', 'New reply B (corrected)', ts + 3000),
      makeMsg('user', 'Message C', ts + 4000),
    ];
    const result = mergeRecoveredTail(existing, recovered);
    // Should preserve A messages, replace from B onwards
    expect(result.some(m => m.rawText === 'Message A')).toBe(true);
    expect(result.some(m => m.rawText === 'Reply A')).toBe(true);
    expect(result.some(m => m.rawText === 'New reply B (corrected)')).toBe(true);
    expect(result.some(m => m.rawText === 'Message C')).toBe(true);
    // Old reply should be replaced, not retained
    expect(result.some(m => m.rawText === 'Old reply B')).toBe(false);
  });

  it('falls back to recovered when no overlap or anchor found', () => {
    const existing = [
      makeMsg('user', 'Old message 1', 1000000),
      makeMsg('assistant', 'Old reply 1', 1000001),
    ];
    const recovered = [
      makeMsg('user', 'Completely different', 2000000),
      makeMsg('assistant', 'New reply', 2000001),
    ];
    const result = mergeRecoveredTail(existing, recovered);
    expect(result).toEqual(recovered);
  });

  it('handles single message overlap', () => {
    const ts = 1700000000000;
    const existing = [makeMsg('user', 'Only msg', ts)];
    const recovered = [makeMsg('user', 'Only msg', ts), makeMsg('assistant', 'Reply', ts + 1000)];
    const result = mergeRecoveredTail(existing, recovered);
    expect(result).toHaveLength(2);
  });

  /**
   * The operator's own words are the one thing the client is authoritative
   * about. Central Command persists a turn's transcript when the turn ENDS, so the
   * mid-turn recovery read returns a history that stops before the message
   * being answered — and the merge used to "correct" the transcript by
   * deleting it, then restore it seconds later when the turn landed.
   */
  describe('never drops what the operator just typed', () => {
    const ts = 1700000000000;

    it('keeps the in-flight message on the anchor path', () => {
      const existing = [
        makeMsg('user', 'first question', ts),
        makeMsg('assistant', 'first answer', ts + 1000),
        makeMsg('user', 'the message being answered right now', ts + 60_000),
      ];
      // What the server knows mid-turn: everything except the new message.
      const recovered = [
        makeMsg('user', 'first question', ts),
        makeMsg('assistant', 'first answer', ts + 1000),
      ];

      const result = mergeRecoveredTail(existing, recovered);

      expect(result.map((m) => m.rawText)).toContain('the message being answered right now');
      expect(result[result.length - 1].rawText).toBe('the message being answered right now');
    });

    it('keeps it even when nothing anchors and recovered would win outright', () => {
      const existing = [
        makeMsg('assistant', 'something entirely unrelated', ts),
        makeMsg('user', 'my in-flight message', ts + 60_000),
      ];
      const recovered = [
        makeMsg('user', 'a different conversation', 2_000_000),
        makeMsg('assistant', 'a different reply', 2_000_001),
      ];

      const result = mergeRecoveredTail(existing, recovered);

      expect(result.map((m) => m.rawText)).toContain('my in-flight message');
    });

    it('does not duplicate it once the server has caught up', () => {
      const existing = [
        makeMsg('user', 'my message', ts),
      ];
      // The turn finished; the transcript now contains it, plus the reply.
      const recovered = [
        makeMsg('user', 'my message', ts),
        makeMsg('assistant', 'the reply', ts + 2000),
      ];

      const result = mergeRecoveredTail(existing, recovered);

      expect(result.filter((m) => m.rawText === 'my message')).toHaveLength(1);
      expect(result).toHaveLength(2);
    });

    it('leaves an assistant-terminated transcript alone', () => {
      // Regression guard for the rule's scope: only a trailing run of USER
      // turns is protected, so ordinary tail correction still works.
      const existing = [
        makeMsg('user', 'q', ts),
        makeMsg('assistant', 'stale partial answer', ts + 1000),
      ];
      const recovered = [
        makeMsg('user', 'q', ts),
        makeMsg('assistant', 'the real answer', ts + 1000),
      ];

      const result = mergeRecoveredTail(existing, recovered);

      expect(result.map((m) => m.rawText)).toEqual(['q', 'the real answer']);
    });
  });
});
