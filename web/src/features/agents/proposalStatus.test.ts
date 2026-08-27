import { describe, expect, it } from 'vitest';

import { proposalStatusLabel } from './proposalStatus';

describe('proposalStatusLabel', () => {
  it('says a parked execution is APPROVED, with the outage named', () => {
    // The doctrine: the operator's decision stands, the world-change queues.
    // A raw RETRY_PENDING reads as "still waiting on me" — the opposite.
    const label = proposalStatusLabel('RETRY_PENDING');
    expect(label).toContain('approved');
    expect(label).toContain('outage');
    expect(label).not.toContain('RETRY_PENDING');
  });

  it('passes every other status through verbatim', () => {
    for (const s of ['AWAITING_HUMAN', 'EXECUTED', 'FAILED', 'REJECTED', 'EXECUTING']) {
      expect(proposalStatusLabel(s)).toBe(s);
    }
  });
});
