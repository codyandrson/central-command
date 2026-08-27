import { describe, expect, it } from 'vitest';
import { normalizeCronJob } from './useCrons';

describe('normalizeCronJob', () => {
  it('preserves explicit root routing fields from the gateway', () => {
    const job = normalizeCronJob({
      id: 'cron-1',
      sessionTarget: 'main',
      sessionKey: 'agent:reviewer:main',
      payload: {
        kind: 'systemEvent',
        text: 'Reminder',
      },
      schedule: {
        kind: 'every',
        everyMs: 300000,
      },
      enabled: true,
    });

    expect(job.sessionTarget).toBe('main');
    expect(job.sessionKey).toBe('agent:reviewer:main');
    expect(job.payloadKind).toBe('systemEvent');
    expect(job.message).toBe('Reminder');
  });

  it('passes through the Central Command heartbeat action kind and params', () => {
    const job = normalizeCronJob({
      id: 'drain-window',
      actionKind: 'dispatch.window',
      actionParams: { minutes: 10 },
      payload: { kind: 'systemEvent', text: '[heartbeat action: dispatch.window]' },
      schedule: { kind: 'cron', expr: '0 * * * *' },
      enabled: false,
    });

    expect(job.actionKind).toBe('dispatch.window');
    expect(job.actionParams).toEqual({ minutes: 10 });
  });

  it('leaves legacy jobs without a session key unassigned', () => {
    const job = normalizeCronJob({
      id: 'cron-2',
      payload: {
        kind: 'agentTurn',
        message: 'Summarize',
      },
      schedule: {
        kind: 'cron',
        expr: '0 9 * * *',
      },
      enabled: true,
    });

    expect(job.sessionTarget).toBeUndefined();
    expect(job.sessionKey).toBeUndefined();
  });
});
