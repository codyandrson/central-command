import type { ChatComposerState } from '@/types';

/**
 * Whether a transcript has ENDED, and which ending to name.
 *
 * Pure and separate on purpose. The first version of this rule lived inline in
 * ChatPanel and derived VISIBILITY from `status ∈ {DONE, FAILED, CANCELLED}`
 * while deriving only the TEXT from the server's composer code — under a
 * comment claiming the two "cannot drift apart". They drifted three ways:
 *
 *   - CANCELLED: `why_not_sendable` has no branch for it, so the composer
 *     reads "wait for the turn to finish" under a divider saying the
 *     conversation is closed.
 *   - FAILED: a crashed run was dressed as a normal ending.
 *   - DONE + closed_reason 'operator': deliberately still WRITABLE (writing
 *     reopens the conversation), so "Conversation closed" sat above a live
 *     message box.
 *
 * So both answers now come from one place, and only the two states the close
 * seam actually produces (`converse.close_session`) count as an ending.
 * FAILED and CANCELLED are not closed conversations; ReadOnlySessionNotice
 * already states what they are.
 */
export type SessionEnding = 'concluded' | 'closed';

export function sessionEnding(composer?: ChatComposerState | null): SessionEnding | null {
  const session = composer?.session;
  if (!session || session.mode !== 'conversation') return null;
  // The agent ended its own clarification lane. The server's own refusal code
  // is the authority — it is what also disables the composer.
  if (composer?.code === 'discussion_concluded') return 'concluded';
  // Operator-initiated closes through `converse.close_session`. 'dismissed'
  // (a proposal dismissed from the Decisions Inbox) closes exactly the same
  // way 'operator' does and stays writable for the same reason — writing
  // reopens the conversation — so it gets the same divider. Listing the
  // reasons explicitly, rather than "anything DONE", keeps FAILED/CANCELLED
  // out: those are not closed conversations, and ReadOnlySessionNotice
  // already says what they are.
  if (
    (session.status || '').toUpperCase() === 'DONE' &&
    (session.closedReason === 'operator' || session.closedReason === 'dismissed')
  ) {
    return 'closed';
  }
  return null;
}

export const ENDING_LABEL: Record<SessionEnding, string> = {
  concluded: 'Discussion concluded — task resumed',
  closed: 'Conversation closed',
};
