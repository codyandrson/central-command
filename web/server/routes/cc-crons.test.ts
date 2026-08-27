/**
 * cc-crons adapter — engine-state passthrough and structured authoring of
 * built-in heartbeat actions (the D27 follow-ups).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';

const SCHEDULES = {
  schedules: [
    {
      id: 'mail-poll', name: 'Mail poll', schedule_kind: 'every',
      schedule: { every_seconds: 300 }, action_kind: 'feed.poll',
      action_params: {}, enabled: true, created_by: 'seed:heartbeat',
      last_fired_at: null, next_due: null, last_status: null, last_error: null,
    },
    {
      id: 'nightly-task', name: 'Nightly sweep', schedule_kind: 'cron',
      schedule: { expr: '0 2 * * *' }, action_kind: 'task.create',
      action_params: { agent_id: 'jira-expert', instructions: 'sweep' },
      enabled: false, created_by: 'seed:heartbeat',
      last_fired_at: null, next_due: null, last_status: null, last_error: null,
    },
  ],
  actions: {
    'feed.poll': { description: 'Poll mail once.', params: {}, required: [] },
    'dispatch.window': {
      description: 'Bounded drain window.',
      params: { minutes: 'window length in minutes (default 10)' }, required: [],
    },
  },
};

const ENGINE = { running: false, reason: 'not started', tick_seconds: 15, next_due: {} };

describe('cc-crons adapter', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.resetModules();
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function respond(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
      status, headers: { 'content-type': 'application/json' },
    });
  }

  async function buildApp() {
    vi.doMock('../lib/config.js', () => ({
      config: { gatewayUrl: 'http://cc.test' },
    }));
    vi.doMock('../middleware/rate-limit.js', () => ({
      rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
    }));
    const mod = await import('./cc-crons.js');
    const app = new Hono();
    app.route('/', mod.default);
    return app;
  }

  function routeFetch(handlers: Record<string, (init?: RequestInit) => Response>) {
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname + (init?.method && init.method !== 'GET' ? ` ${init.method}` : '');
      const handler = handlers[path];
      if (!handler) throw new Error(`unexpected fetch: ${init?.method ?? 'GET'} ${url}`);
      return handler(init);
    });
  }

  it('lists jobs with engine state and the action registry', async () => {
    const app = await buildApp();
    routeFetch({
      '/api/heartbeat/schedules': () => respond(SCHEDULES),
      '/api/heartbeat': () => respond(ENGINE),
    });

    const res = await app.request('/api/crons');
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.result.engine).toEqual({ running: false, reason: 'not started', tickSeconds: 15 });
    expect(Object.keys(body.result.actions)).toContain('dispatch.window');
    const mailPoll = body.result.jobs.find((j: { id: string }) => j.id === 'mail-poll');
    expect(mailPoll.actionKind).toBe('feed.poll');
  });

  it('starts and stops the engine via the passthrough', async () => {
    const app = await buildApp();
    routeFetch({
      '/api/heartbeat/start POST': () => respond({ running: true, reason: 'running' }),
    });

    const res = await app.request('/api/crons/engine', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ running: true }),
    });
    expect(res.status).toBe(200);
    expect((await res.json()).result).toEqual({ running: true, reason: 'running' });
  });

  it('creates a built-in action schedule with cleaned params', async () => {
    const app = await buildApp();
    let created: Record<string, unknown> | null = null;
    routeFetch({
      '/api/heartbeat/schedules POST': (init) => {
        created = JSON.parse(String(init?.body));
        return respond({ ...SCHEDULES.schedules[0], id: 'evening-drain', action_kind: 'dispatch.window', action_params: { minutes: 15 } });
      },
    });

    const res = await app.request('/api/crons', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        job: {
          name: 'Evening drain',
          schedule: { kind: 'cron', expr: '0 18 * * *' },
          payload: { kind: 'heartbeatAction', actionKind: 'dispatch.window', params: { minutes: '15', ignored: '' } },
        },
      }),
    });
    expect(res.status).toBe(200);
    expect(created).toMatchObject({
      action_kind: 'dispatch.window',
      action_params: { minutes: 15 },
    });
    expect((created as unknown as { action_params: Record<string, unknown> }).action_params).not.toHaveProperty('ignored');
  });

  it('refuses authoring task.create as a raw heartbeat action', async () => {
    const app = await buildApp();
    const res = await app.request('/api/crons', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        job: {
          schedule: { kind: 'cron', expr: '0 2 * * *' },
          payload: { kind: 'heartbeatAction', actionKind: 'task.create', params: {} },
        },
      }),
    });
    expect(res.status).toBe(422);
  });

  it('patches a built-in action structurally but never morphs an agent task', async () => {
    const app = await buildApp();
    let patched: Record<string, unknown> | null = null;
    routeFetch({
      '/api/heartbeat/schedules': () => respond(SCHEDULES),
      '/api/heartbeat/schedules/mail-poll PATCH': (init) => {
        patched = JSON.parse(String(init?.body));
        return respond({ ...SCHEDULES.schedules[0], action_kind: 'dispatch.window', action_params: { minutes: 5 } });
      },
    });

    const ok = await app.request('/api/crons/mail-poll', {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        patch: { payload: { kind: 'heartbeatAction', actionKind: 'dispatch.window', params: { minutes: '5' } } },
      }),
    });
    expect(ok.status).toBe(200);
    expect(patched).toMatchObject({ action_kind: 'dispatch.window', action_params: { minutes: 5 } });

    const refused = await app.request('/api/crons/nightly-task', {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        patch: { payload: { kind: 'heartbeatAction', actionKind: 'feed.poll', params: {} } },
      }),
    });
    expect(refused.status).toBe(422);
  });

  it('creates a scheduled agent task carrying a model/thinking override', async () => {
    const app = await buildApp();
    let created: Record<string, unknown> | null = null;
    routeFetch({
      '/api/heartbeat/schedules POST': (init) => {
        created = JSON.parse(String(init?.body));
        return respond({ ...SCHEDULES.schedules[1], id: 'model-task' });
      },
    });

    const res = await app.request('/api/crons', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        job: {
          agentId: 'jira-expert',
          schedule: { kind: 'cron', expr: '0 2 * * *' },
          payload: { kind: 'agentTurn', message: 'sweep', model: 'anthropic/claude-sonnet-5', thinking: 'medium' },
        },
      }),
    });
    expect(res.status).toBe(200);
    expect(created).toMatchObject({
      action_kind: 'task.create',
      action_params: { agent_id: 'jira-expert', instructions: 'sweep', model: 'anthropic/claude-sonnet-5', thinking: 'medium' },
    });
  });

  it('mapSchedule round-trips model/thinking onto payload for a scheduled agent task', async () => {
    const app = await buildApp();
    routeFetch({
      '/api/heartbeat/schedules': () => respond({
        ...SCHEDULES,
        schedules: [{
          ...SCHEDULES.schedules[1],
          action_params: { agent_id: 'jira-expert', instructions: 'sweep', model: 'anthropic/claude-sonnet-5', thinking: 'medium' },
        }],
      }),
      '/api/heartbeat': () => respond(ENGINE),
    });

    const res = await app.request('/api/crons');
    const body = await res.json();
    expect(body.result.jobs[0].payload).toMatchObject({
      model: 'anthropic/claude-sonnet-5',
      thinking: 'medium',
    });
  });

  it('clears a task.create model/thinking override on edit when the payload sends blanks', async () => {
    const app = await buildApp();
    let patched: Record<string, unknown> | null = null;
    routeFetch({
      '/api/heartbeat/schedules': () => respond({
        ...SCHEDULES,
        schedules: [{
          ...SCHEDULES.schedules[1],
          action_params: { agent_id: 'jira-expert', instructions: 'sweep', model: 'anthropic/claude-sonnet-5', thinking: 'medium' },
        }],
      }),
      '/api/heartbeat/schedules/nightly-task PATCH': (init) => {
        patched = JSON.parse(String(init?.body));
        return respond(SCHEDULES.schedules[1]);
      },
    });

    const res = await app.request('/api/crons/nightly-task', {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        patch: { payload: { kind: 'agentTurn', message: 'sweep', model: '', thinking: '' } },
      }),
    });
    expect(res.status).toBe(200);
    // JSON.stringify drops keys whose value is `undefined`, so a cleared
    // override reaches the API as an absent key, not an explicit null.
    expect((patched as unknown as { action_params: Record<string, unknown> }).action_params).toEqual({
      agent_id: 'jira-expert', instructions: 'sweep',
    });
  });

  it('still refuses freeform payload edits on built-in actions', async () => {
    const app = await buildApp();
    routeFetch({ '/api/heartbeat/schedules': () => respond(SCHEDULES) });

    const res = await app.request('/api/crons/mail-poll', {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ patch: { payload: { kind: 'agentTurn', message: 'do something else' } } }),
    });
    expect(res.status).toBe(422);
  });
});
