import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GatewayRestartCard } from './GatewayRestartCard';

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
}

/** The cockpit server and the API behind it, as the card sees them. */
function world() {
  const state = {
    status: { state: 'none', available: true, updateBusy: false } as Record<string, unknown>,
    hold: { active: false, kind: null, running: [], triggered_at: null, trigger_error: null } as Record<string, unknown>,
    holdDown: false,
    posts: [] as string[],
    engageResponse: () => json({ held: true }, 202),
  };
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const method = init?.method ?? 'GET';
    if (url === '/api/restart/status') return json(state.status);
    if (url === '/api/restart/hold' && method === 'GET') {
      if (state.holdDown) throw new Error('connection refused');
      return json(state.hold);
    }
    if (method === 'POST') {
      state.posts.push(url);
      if (url === '/api/restart/hold') return state.engageResponse();
      return json(state.hold);
    }
    return json({}, 404);
  });
  return { state, fetchMock };
}

const RUNNING = [{
  id: 'sess_1', agent_id: 'ea', mode: 'oneshot', stop_requested: true,
  last_step_age_s: 40, task_id: 'task_1', dispatch: false,
}];

const tickPoll = () => act(async () => { await vi.advanceTimersByTimeAsync(3100); });

describe('GatewayRestartCard', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  async function mount(w: ReturnType<typeof world>) {
    global.fetch = w.fetchMock;
    render(<GatewayRestartCard />);
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  }

  it('stays disabled with the 🚧 mark and a plain reason where the updater is unavailable', async () => {
    const w = world();
    w.state.status = { state: 'none', available: false, reason: 'this install has no root updater unit (single-node profile)', updateBusy: false };
    await mount(w);
    const button = screen.getByRole('button', { name: /restart the local gateway/i });
    expect(button).toBeDisabled();
    expect(screen.getByText(/Gateway Service 🚧/)).toBeInTheDocument();
    expect(screen.getByText(/no root updater unit/)).toBeInTheDocument();
    expect(screen.queryByText(/under construction/i)).not.toBeInTheDocument();
  });

  it('is disabled with a reason while an update is running or waiting', async () => {
    const w = world();
    w.state.status = { state: 'none', available: true, updateBusy: true };
    await mount(w);
    expect(screen.getByRole('button', { name: /restart the local gateway/i })).toBeDisabled();
    expect(screen.getByText(/update is running or waiting/i)).toBeInTheDocument();

    w.state.status = { state: 'none', available: true, updateBusy: false };
    w.state.hold = { active: true, kind: 'update', running: [], triggered_at: null, trigger_error: null };
    await tickPoll();
    expect(screen.getByRole('button', { name: /restart the local gateway/i })).toBeDisabled();
  });

  it('asks for confirmation, then engages the restart hold and lists what is still running', async () => {
    const w = world();
    await mount(w);
    const button = screen.getByRole('button', { name: /restart the local gateway/i });
    expect(button).toBeEnabled();

    fireEvent.click(button);
    expect(screen.getByRole('alertdialog')).toHaveTextContent(
      'Restarts the API process. Agents pause at their next step and resume afterwards; '
      + 'a run that cannot pause in time waits, or is interrupted if you choose Restart now.');
    expect(w.state.posts).toEqual([]);

    w.state.hold = { active: true, kind: 'restart', running: RUNNING, triggered_at: null, trigger_error: null };
    fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });

    expect(w.state.posts).toEqual(['/api/restart/hold']);
    expect(screen.getByText(/waiting for 1 agent run/)).toBeInTheDocument();
    expect(screen.getByText(/ea · task/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /restart now/i }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(w.state.posts).toEqual(['/api/restart/hold', '/api/restart/hold/now']);
  });

  it('cancel releases the hold and returns to idle', async () => {
    const w = world();
    await mount(w);
    fireEvent.click(screen.getByRole('button', { name: /restart the local gateway/i }));
    w.state.hold = { active: true, kind: 'restart', running: RUNNING, triggered_at: null, trigger_error: null };
    fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });

    w.state.hold = { active: false, kind: null, running: [], triggered_at: null, trigger_error: null };
    fireEvent.click(screen.getByRole('button', { name: /cancel restart/i }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(w.state.posts).toContain('/api/restart/hold/cancel');
    expect(screen.queryByText(/waiting for/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /restart the local gateway/i })).toBeEnabled();
  });

  it('shows the server refusal when the hold cannot be engaged', async () => {
    const w = world();
    w.state.engageResponse = () => json({ error: 'an update is running or pending' }, 409);
    await mount(w);
    fireEvent.click(screen.getByRole('button', { name: /restart the local gateway/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(screen.getByRole('alert')).toHaveTextContent('an update is running or pending');
  });

  it('survives the API going down and shows the outcome from the status route', async () => {
    const w = world();
    w.state.status = { state: 'restarted', available: true, updateBusy: false, requested_at: 'T0', finished_at: '2026-11-16T09:00:00+00:00', message: 'old' };
    await mount(w);
    fireEvent.click(screen.getByRole('button', { name: /restart the local gateway/i }));
    w.state.hold = { active: true, kind: 'restart', running: [], triggered_at: null, trigger_error: null };
    fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });

    // The API stops: the hold route fails, the card moves to the restarting state.
    w.state.holdDown = true;
    w.state.status = { state: 'running', available: true, updateBusy: false, requested_at: 'T1', message: 'restarting cc-uvicorn' };
    await tickPoll();
    expect(screen.getByText(/Restarting the API/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /restart the local gateway/i })).toBeDisabled();

    // The old record (requested_at T0) never counted as the outcome; this one does.
    w.state.holdDown = false;
    w.state.hold = { active: false, kind: null, running: [], triggered_at: null, trigger_error: null };
    w.state.status = {
      state: 'restarted', available: true, updateBusy: false, requested_at: 'T1',
      finished_at: '2026-11-16T10:00:40+00:00', message: 'cc-uvicorn restarted and healthy', forced: false,
    };
    await tickPoll();
    expect(screen.queryByText(/Restarting the API/)).not.toBeInTheDocument();
    expect(screen.getByText(/Last restart succeeded at/)).toBeInTheDocument();
    expect(screen.getByText(/cc-uvicorn restarted and healthy/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /restart the local gateway/i })).toBeEnabled();
  });

  it('reports a failed restart and where to look when the record says so', async () => {
    const w = world();
    await mount(w);
    fireEvent.click(screen.getByRole('button', { name: /restart the local gateway/i }));
    w.state.hold = { active: true, kind: 'restart', running: [], triggered_at: '2026-11-16T10:00:00+00:00', trigger_error: null };
    fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    await tickPoll();
    expect(screen.getByText(/Restarting the API/)).toBeInTheDocument();

    w.state.hold = { active: false, kind: null, running: [], triggered_at: null, trigger_error: null };
    w.state.status = {
      state: 'failed', available: true, updateBusy: false, requested_at: 'T2', finished_at: '2026-11-16T10:03:00+00:00',
      message: 'cc-uvicorn restarted but did not answer /health within 2 minutes — see journalctl -u cc-uvicorn',
    };
    await tickPoll();
    expect(screen.getByText(/Last restart failed at/)).toBeInTheDocument();
    expect(screen.getByText(/journalctl -u cc-uvicorn/)).toBeInTheDocument();
  });

  it('a refused restart with the hold still standing goes back to waiting, with the reason', async () => {
    const w = world();
    await mount(w);
    fireEvent.click(screen.getByRole('button', { name: /restart the local gateway/i }));
    w.state.hold = { active: true, kind: 'restart', running: [], triggered_at: 'T-1', trigger_error: null };
    fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    await tickPoll();
    expect(screen.getByText(/Restarting the API/)).toBeInTheDocument();

    w.state.hold = { active: true, kind: 'restart', running: RUNNING, triggered_at: 'T-1', trigger_error: null };
    w.state.status = {
      state: 'refused', available: true, updateBusy: false, requested_at: 'T3',
      message: '1 agent run(s) in flight (ea) — wait for them to land, or apply again with \'Update anyway\'',
    };
    await tickPoll();
    await tickPoll();
    expect(screen.queryByText(/Restarting the API/)).not.toBeInTheDocument();
    expect(screen.getByText(/waiting for 1 agent run/)).toBeInTheDocument();
    expect(screen.getByText(/in flight \(ea\)/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /cancel restart/i })).toBeInTheDocument();
  });

  it('gives up waiting after the timeout and says where to look', async () => {
    const w = world();
    await mount(w);
    fireEvent.click(screen.getByRole('button', { name: /restart the local gateway/i }));
    w.state.hold = { active: true, kind: 'restart', running: [], triggered_at: 'now', trigger_error: null };
    fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    w.state.holdDown = true;
    await tickPoll();
    expect(screen.getByText(/Restarting the API/)).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(6 * 60 * 1000); });
    expect(screen.getByText(/No result was recorded in time/)).toBeInTheDocument();
  });
});
