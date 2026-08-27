/**
 * cc-kanban adapter — operator stop/resume/cancel wiring (2026-08-02).
 *
 * Focused on the new surface: `stopped` forwarded onto the wire task, and the
 * abort/resume routes proxying to the backend's `/tasks/{id}/stop` and
 * `/sessions/{id}/resume`. The rest of the adapter (create/execute/reorder)
 * is exercised elsewhere; this file only covers what landed with the
 * operator-stop slice.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';

const RUNNING_TASK = {
  id: 't1', title: 'Do the thing', instructions: 'do the thing',
  agent_id: 'jira-expert', status: 'IN_PROGRESS', session_id: 'sess_1',
  parent_session_id: null, outcome: null, created_at: '2026-08-02T00:00:00Z',
  started_at: '2026-08-02T00:00:00Z', terminal_at: null,
  session_status: 'RUNNING', stopped: false,
};

const STOPPED_TASK = { ...RUNNING_TASK, session_status: 'STOPPED', stopped: true };

describe('cc-kanban adapter — operator stop/resume', () => {
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
    const mod = await import('./cc-kanban.js');
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

  it('forwards `stopped` onto the wire task', async () => {
    const app = await buildApp();
    routeFetch({ '/api/tasks': () => respond({ tasks: [STOPPED_TASK] }) });

    const res = await app.request('/api/kanban/tasks');
    const body = await res.json();
    expect(body.items[0].stopped).toBe(true);
    expect(body.items[0].status).toBe('IN_PROGRESS');
  });

  it('POST /tasks/:id/abort proxies to the backend stop endpoint', async () => {
    const app = await buildApp();
    routeFetch({
      '/api/tasks': () => respond({ tasks: [STOPPED_TASK] }),
      '/api/tasks/t1/stop POST': () => respond({ ok: true }),
    });

    const res = await app.request('/api/kanban/tasks/t1/abort', { method: 'POST' });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.stopped).toBe(true);
  });

  it('POST /tasks/:id/abort surfaces a 409 from the backend (no live run)', async () => {
    const app = await buildApp();
    routeFetch({
      '/api/tasks/t1/stop POST': () => respond({ detail: 'not RUNNING' }, 409),
    });

    const res = await app.request('/api/kanban/tasks/t1/abort', { method: 'POST' });
    expect(res.status).toBe(409);
  });

  it('POST /tasks/:id/resume looks up the session and proxies the resume', async () => {
    const app = await buildApp();
    let resumedSession: string | null = null;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const u = new URL(url);
      if (u.pathname === '/api/tasks') return respond({ tasks: [STOPPED_TASK] });
      if (u.pathname === '/api/sessions/sess_1/resume' && init?.method === 'POST') {
        resumedSession = 'sess_1';
        return respond({ resumed: true });
      }
      throw new Error(`unexpected fetch: ${init?.method ?? 'GET'} ${url}`);
    });

    const res = await app.request('/api/kanban/tasks/t1/resume', { method: 'POST' });
    expect(res.status).toBe(200);
    expect(resumedSession).toBe('sess_1');
  });

  it('POST /tasks/:id/resume 404s when the task has no session', async () => {
    const app = await buildApp();
    const noSessionTask = { ...RUNNING_TASK, session_id: null };
    routeFetch({ '/api/tasks': () => respond({ tasks: [noSessionTask] }) });

    const res = await app.request('/api/kanban/tasks/t1/resume', { method: 'POST' });
    expect(res.status).toBe(409);
  });

  it('CANCELLED tasks are not filtered out of the list (fold into Done is a frontend concern)', async () => {
    const app = await buildApp();
    const cancelled = { ...RUNNING_TASK, status: 'CANCELLED', session_status: null, stopped: false };
    routeFetch({ '/api/tasks': () => respond({ tasks: [cancelled] }) });

    const res = await app.request('/api/kanban/tasks');
    const body = await res.json();
    expect(body.items).toHaveLength(1);
    expect(body.items[0].status).toBe('CANCELLED');
  });
});
