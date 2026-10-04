/**
 * The Systems page's Self-check column and summary. `fetch` is mocked at the
 * network seam. Loading the page must only READ the last result: a run spends a
 * model request per model check, so only the button may POST.
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SystemsView, formatLocalTime } from './SystemsView';

const SYSTEMS = {
  systems: [
    { id: 'postgres', name: 'Postgres', kind: 'store', url: null, linkLabel: null, status: 'up', latencyMs: 3, credential: { label: 'DB URL', location: 'CC_DATABASE_URL' } },
    { id: 'litellm', name: 'LiteLLM', kind: 'api', url: null, linkLabel: null, status: 'up', latencyMs: 9, credential: { label: 'key', location: 'LITELLM_MASTER_KEY' } },
    { id: 'n8n', name: 'n8n', kind: 'ui', url: null, linkLabel: null, status: 'up', latencyMs: 5, detail: null, credential: { label: 'login', location: 'n8n' } },
    { id: 'graphiti', name: 'Graphiti (in-process)', kind: 'api', url: null, linkLabel: null, status: 'up', latencyMs: null, detail: 'ingest queue: 2 queued, 0 running, 0 failed — retrying after: 403 key not allowed to access model', credential: { label: 'key', location: 'CC_LLM_API_KEY' } },
  ],
};

const doc = (over: Record<string, unknown> = {}) => ({
  status: 'fail', running: false, ranAt: '2026-10-02T15:04:05Z', durationMs: 1234,
  version: '2.56.0', mode: 'cli',
  checks: [
    { name: 'spine', status: 'pass', message: 'database answers', remedy: null, system: 'postgres', durationMs: 12 },
    { name: 'proxy-as-app', status: 'fail', message: 'key rejected', remedy: 'rotate LITELLM_KEY', system: 'litellm', durationMs: 40 },
    { name: 'links', status: 'warn', message: '2 links blank', remedy: 'run ./setup.sh app', system: null, durationMs: 1 },
  ],
  ...over,
});

type Call = { url: string; method: string };

function mockFetch(handler: (c: Call) => unknown): Call[] {
  const calls: Call[] = [];
  global.fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const call = { url: String(url), method: init?.method ?? 'GET' };
    calls.push(call);
    const body = call.url.startsWith('/api/systems') ? SYSTEMS : handler(call);
    return { ok: true, status: 200, json: async () => body };
  }) as unknown as typeof fetch;
  return calls;
}

describe('SystemsView status line', () => {
  it('shows a row\'s detail (the graph ingest queue) and nothing on rows without one', async () => {
    mockFetch(() => doc());
    render(<SystemsView />);
    const detail = await screen.findByTestId('system-detail-graphiti');
    expect(detail).toHaveTextContent('ingest queue: 2 queued, 0 running, 0 failed');
    expect(detail).toHaveTextContent('retrying after: 403');
    expect(screen.queryByTestId('system-detail-n8n')).not.toBeInTheDocument();
    expect(screen.queryByTestId('system-detail-postgres')).not.toBeInTheDocument();
  });
});

describe('SystemsView self-check', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows pass/fail beside the mapped rows and nothing on unmapped rows', async () => {
    mockFetch(() => doc());
    render(<SystemsView />);
    const pg = await screen.findByTestId('selfcheck-col-postgres');
    expect(within(pg).getByText('spine: pass')).toBeInTheDocument();
    const ll = screen.getByTestId('selfcheck-col-litellm');
    expect(within(ll).getByText('proxy-as-app: fail')).toBeInTheDocument();
    expect(screen.queryByTestId('selfcheck-col-n8n')).not.toBeInTheDocument();
  });

  it('carries the message and remedy on hover', async () => {
    mockFetch(() => doc());
    render(<SystemsView />);
    const badge = await screen.findByTestId('selfcheck-proxy-as-app');
    expect(badge.getAttribute('title')).toContain('key rejected');
    expect(badge.getAttribute('title')).toContain('rotate LITELLM_KEY');
  });

  it('lists checks with no system above the grid, with status and message', async () => {
    mockFetch(() => doc());
    render(<SystemsView />);
    const summary = await screen.findByTestId('selfcheck-summary');
    await waitFor(() => expect(within(summary).getByText('links: warn')).toBeInTheDocument());
    expect(within(summary).getByText(/2 links blank/)).toBeInTheDocument();
    expect(within(summary).getByText('fail')).toBeInTheDocument();
  });

  it('renders never_run as "not run yet" with the button enabled', async () => {
    mockFetch(() => doc({ status: 'never_run', ranAt: null, durationMs: null, checks: [] }));
    render(<SystemsView />);
    expect(await screen.findByText('not run yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /run self-check/i })).toBeEnabled();
    expect(screen.queryByText(/last ran/)).not.toBeInTheDocument();
  });

  it('mounting issues no POST — only a GET of the last result', async () => {
    const calls = mockFetch(() => doc({ status: 'never_run', ranAt: null, checks: [] }));
    render(<SystemsView />);
    await screen.findByText('not run yet');
    expect(calls.filter((c) => c.method === 'POST')).toEqual([]);
    expect(calls.some((c) => c.url === '/api/selfcheck' && c.method === 'GET')).toBe(true);
  });

  it('the button POSTs, disables while running, then polls the GET until running is false', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let gets = 0;
    const calls = mockFetch((c) => {
      if (c.method === 'POST') return doc({ running: true, status: 'never_run', ranAt: null, checks: [] });
      gets += 1;
      if (gets === 1) return doc({ status: 'never_run', ranAt: null, checks: [] });
      if (gets === 2) return doc({ running: true, status: 'never_run', ranAt: null, checks: [] });
      return doc();
    });
    render(<SystemsView />);
    await screen.findByText('not run yet');
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    await user.click(screen.getByRole('button', { name: /run self-check/i }));
    const running = await screen.findByRole('button', { name: /running/i });
    expect(running).toBeDisabled();
    expect(calls.filter((c) => c.method === 'POST')).toEqual([{ url: '/api/selfcheck/run', method: 'POST' }]);

    // First poll: still running. Second poll: finished.
    await act(async () => { await vi.advanceTimersByTimeAsync(2_100); });
    expect(screen.getByRole('button', { name: /running/i })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(2_100); });
    await waitFor(() => expect(screen.getByRole('button', { name: /run self-check/i })).toBeEnabled());
    expect(screen.getByText('fail')).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
  });

  describe('timestamp', () => {
    const realTz = process.env.TZ;
    afterEach(() => {
      if (realTz === undefined) delete process.env.TZ; else process.env.TZ = realTz;
    });

    it('is rendered in the browser local time, 24-hour', async () => {
      process.env.TZ = 'America/Denver'; // UTC-6 in October (MDT)
      expect(formatLocalTime('2026-10-02T21:04:05Z')).toBe('2026-10-02 15:04:05');
      expect(formatLocalTime('2026-10-02T15:04:05Z')).toBe('2026-10-02 09:04:05');
      process.env.TZ = 'Asia/Kolkata'; // UTC+5:30
      expect(formatLocalTime('2026-10-02T15:04:05Z')).toBe('2026-10-02 20:34:05');
    });

    it('shows the formatted local time and no am/pm marker in the summary', async () => {
      process.env.TZ = 'America/Denver';
      mockFetch(() => doc({ ranAt: '2026-10-02T21:04:05Z' }));
      render(<SystemsView />);
      expect(await screen.findByText(/last ran 2026-10-02 15:04:05/)).toBeInTheDocument();
      expect(screen.queryByText(/\b[AP]M\b/i)).not.toBeInTheDocument();
    });
  });

  it('shows an error line when the self-check fetch fails, keeping the page', async () => {
    global.fetch = vi.fn(async (url: string) => {
      if (String(url).startsWith('/api/systems')) return { ok: true, status: 200, json: async () => SYSTEMS };
      return { ok: false, status: 502, json: async () => ({ error: 'boom' }) };
    }) as unknown as typeof fetch;
    render(<SystemsView />);
    expect(await screen.findByText('boom')).toBeInTheDocument();
    expect(screen.getByText('Postgres')).toBeInTheDocument();
  });
});
