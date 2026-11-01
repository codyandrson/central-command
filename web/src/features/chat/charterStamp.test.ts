import { describe, expect, it } from 'vitest';
import { charterFrameTouches, charterStamp } from './charterStamp';

describe('charterFrameTouches', () => {
  const frame = (event: string, ref_id: string) => ({ event, payload: { kind: event.slice(3), ref_id } });

  it('names a charter edit of the agent behind this session, either kind', () => {
    expect(charterFrameTouches(frame('cc.charter.updated', 'jira-expert'), 'agent:jira-expert:sess_1')).toBe(true);
    expect(charterFrameTouches(frame('cc.charter.reverted', 'jira-expert'), 'agent:jira-expert:main')).toBe(true);
  });

  it('leaves another agent, another event and no session alone', () => {
    expect(charterFrameTouches(frame('cc.charter.updated', 'ea'), 'agent:jira-expert:sess_1')).toBe(false);
    expect(charterFrameTouches(frame('cc.session.step', 'jira-expert'), 'agent:jira-expert:sess_1')).toBe(false);
    expect(charterFrameTouches(frame('cc.charter.updated', 'jira-expert'), '')).toBe(false);
  });
});

describe('charterStamp', () => {
  it('says nothing about the charter of no session', () => {
    expect(charterStamp(null)).toBeNull();
  });
});
