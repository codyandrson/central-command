import { describe, expect, it } from 'vitest';
import { classifyEvent, isErrorKind, normalizeKind } from './tiers';

describe('classifyEvent', () => {
  it('interrupts on everything that is blocked on the operator', () => {
    for (const kind of [
      'cc.proposal.created',
      'cc.agent.asked',
      'cc.agent.discussion_opened',
      'cc.orchestration.question',
      'cc.orchestration.parked',
      'cc.orchestration.stalled',
      'cc.orchestration.capability_gap',
      'cc.gap.declared',
      'cc.dispatch.stalled',
    ]) {
      expect(classifyEvent(kind), kind).toBe('interrupt');
    }
  });

  it('interrupts on anything that failed, whoever emitted it', () => {
    expect(classifyEvent('cc.proposal.failed')).toBe('interrupt');
    expect(classifyEvent('cc.session.resume_failed')).toBe('interrupt');
    expect(classifyEvent('cc.heartbeat.error')).toBe('interrupt');
    // A subsystem that does not exist yet still interrupts when it breaks —
    // that is the point of the suffix rule over an exhaustive list.
    expect(classifyEvent('cc.something.brand.new.failed')).toBe('interrupt');
  });

  it('badges the stall/review/step family', () => {
    expect(classifyEvent('cc.discussion.stalled')).toBe('badge');
    expect(classifyEvent('cc.session.step')).toBe('badge');
    expect(classifyEvent('cc.task.review')).toBe('badge');
  });

  it('DEFAULTS AN UNKNOWN KIND TO digest, never to a toast', () => {
    // The whole safety property of this module: a chatty event kind nobody has
    // written yet must not start interrupting the operator the day it lands.
    expect(classifyEvent('cc.telemetry.token_tick')).toBe('digest');
    expect(classifyEvent('cc.feed.poll')).toBe('digest');
    expect(classifyEvent('completely.made.up')).toBe('digest');
    expect(classifyEvent('')).toBe('digest');
  });

  it('treats the cc. prefix as noise', () => {
    expect(classifyEvent('proposal.created')).toBe('interrupt');
    expect(classifyEvent('cc.proposal.created')).toBe('interrupt');
    expect(normalizeKind('cc.agent.asked')).toBe('agent.asked');
    expect(normalizeKind('agent.asked')).toBe('agent.asked');
  });

  it('a decided proposal is not an interrupt — the operator decided it', () => {
    expect(classifyEvent('cc.proposal.decided')).toBe('digest');
  });
});

describe('isErrorKind', () => {
  it('identifies the interrupts that have no projection row to diff against', () => {
    expect(isErrorKind('cc.proposal.failed')).toBe(true);
    expect(isErrorKind('cc.session.resume_failed')).toBe(true);
    expect(isErrorKind('cc.heartbeat.error')).toBe(true);
    expect(isErrorKind('cc.proposal.created')).toBe(false);
  });
});
