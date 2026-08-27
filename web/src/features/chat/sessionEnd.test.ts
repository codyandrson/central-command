import { describe, expect, it } from 'vitest';
import type { ChatComposerState } from '@/types';
import { ENDING_LABEL, sessionEnding } from './sessionEnd';

function composer(over: Partial<ChatComposerState> & {
  session?: Partial<NonNullable<ChatComposerState['session']>> | null;
} = {}): ChatComposerState {
  const { session, ...rest } = over;
  return {
    enabled: true,
    session: session === null ? null : {
      id: 'sess_x', mode: 'conversation', status: 'AWAITING_OPERATOR',
      agentId: 'jira-expert', closedReason: null, ...session,
    },
    ...rest,
  } as ChatComposerState;
}

describe('sessionEnding', () => {
  it('names a concluded discussion from the server refusal code', () => {
    const ending = sessionEnding(composer({
      enabled: false, code: 'discussion_concluded',
      session: { status: 'DONE', closedReason: 'concluded' },
    }));
    expect(ending).toBe('concluded');
    expect(ENDING_LABEL[ending!]).toBe('Discussion concluded — task resumed');
  });

  it('names an operator-closed conversation', () => {
    expect(sessionEnding(composer({
      session: { status: 'DONE', closedReason: 'operator' },
    }))).toBe('closed');
  });

  it('does NOT claim a live conversation has ended', () => {
    expect(sessionEnding(composer())).toBeNull();
  });

  it('does not fire on FAILED — a crash is not a closed conversation', () => {
    // The composer says "FAILED — start a new conversation"; a divider reading
    // "Conversation closed" would dress an error as a normal ending.
    expect(sessionEnding(composer({
      enabled: false, code: 'failed', session: { status: 'FAILED' },
    }))).toBeNull();
  });

  it('does not fire on CANCELLED, which the composer calls turn_in_progress', () => {
    // why_not_sendable has no CANCELLED branch, so it falls through to
    // "wait for the turn to finish" — the exact sentence a "closed" divider
    // would contradict.
    expect(sessionEnding(composer({
      enabled: false, code: 'turn_in_progress', session: { status: 'CANCELLED' },
    }))).toBeNull();
  });

  it('does not fire on a DONE session with no recorded reason', () => {
    // Pre-existing rows closed before closed_reason existed. Saying nothing is
    // better than asserting an ending we cannot source.
    expect(sessionEnding(composer({
      session: { status: 'DONE', closedReason: null },
    }))).toBeNull();
  });

  it('ignores runs — they are not conversations', () => {
    expect(sessionEnding(composer({
      enabled: false, code: 'not_a_conversation',
      session: { mode: 'oneshot', status: 'DONE', closedReason: 'operator' },
    }))).toBeNull();
  });

  it('is null with no session at all', () => {
    expect(sessionEnding(composer({ session: null }))).toBeNull();
    expect(sessionEnding(null)).toBeNull();
    expect(sessionEnding(undefined)).toBeNull();
  });
});
