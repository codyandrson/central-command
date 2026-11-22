import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import { SessionProvider, useSessionContext } from './SessionContext';
import { getSessionKey, type GatewayEvent } from '@/types';
import { getSessionDisplayLabel } from '@/features/sessions/sessionKeys';

const mockUseGateway = vi.fn();
const mockUseSettings = vi.fn();
const playPingMock = vi.fn();
let rpcMock: ReturnType<typeof vi.fn>;
let subscribeMock: ReturnType<typeof vi.fn>;
let connectionStateValue: 'disconnected' | 'connecting' | 'connected' | 'reconnecting' = 'connected';
let subscribedHandler: ((msg: GatewayEvent) => void) | null = null;
let soundEnabledValue = true;

vi.mock('./GatewayContext', () => ({
  useGateway: () => mockUseGateway(),
}));

vi.mock('./SettingsContext', () => ({
  useSettings: () => mockUseSettings(),
}));

vi.mock('@/features/voice/audio-feedback', () => ({
  playPing: (...args: unknown[]) => playPingMock(...args),
}));

function jsonResponse(data: unknown): Response {
  return {
    ok: true,
    json: async () => data,
  } as Response;
}

function SessionLabels() {
  const { sessions, currentSession } = useSessionContext();

  return (
    <div>
      <div data-testid="current-session">{currentSession}</div>
      {sessions.map((session) => (
        <div key={getSessionKey(session)}>{session.label || session.displayName || getSessionKey(session)}</div>
      ))}
    </div>
  );
}

function SessionDisplayLabels() {
  const { sessions, agentName } = useSessionContext();

  return (
    <div>
      {sessions.map((session) => (
        <div key={getSessionKey(session)}>{getSessionDisplayLabel(session, agentName)}</div>
      ))}
    </div>
  );
}

function SessionRefreshProbe() {
  const { refreshSessions } = useSessionContext();

  return (
    <button data-testid="refresh-sessions" onClick={() => void refreshSessions()}>
      Refresh sessions
    </button>
  );
}

function SessionUnreadProbe() {
  const { currentSession, unreadSessions, setCurrentSession } = useSessionContext();

  return (
    <div>
      <div data-testid="current-session">{currentSession}</div>
      <div data-testid="reviewer-unread">{String(Boolean(unreadSessions['agent:reviewer:main']))}</div>
      <button data-testid="select-reviewer" onClick={() => setCurrentSession('agent:reviewer:main')}>
        Select reviewer
      </button>
    </div>
  );
}

function SessionStatusProbe() {
  const { agentStatus } = useSessionContext();
  return <div data-testid="reviewer-status">{agentStatus['agent:reviewer:main']?.status ?? 'NONE'}</div>;
}

describe('SessionContext', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    subscribedHandler = null;
    soundEnabledValue = true;
    connectionStateValue = 'connected';

    rpcMock = vi.fn(async (method: string, params?: Record<string, unknown>) => {
      if (method === 'sessions.list') {
        const filtered = params && Object.prototype.hasOwnProperty.call(params, 'activeMinutes');
        return {
          sessions: filtered
            ? [
                { sessionKey: 'agent:main:main', label: 'Main' },
                { sessionKey: 'agent:main:cron:daily-digest', label: 'Cron: Daily Digest' },
              ]
            : [
                { sessionKey: 'agent:main:main', label: 'Main' },
                { sessionKey: 'agent:designer:main', label: 'Designer', updatedAt: 1774099479671 },
                { sessionKey: 'agent:main:cron:daily-digest', label: 'Cron: Daily Digest' },
              ],
        };
      }
      return {};
    });

    subscribeMock = vi.fn((handler: (msg: GatewayEvent) => void) => {
      subscribedHandler = handler;
      return () => {};
    });

    mockUseGateway.mockImplementation(() => ({
      connectionState: connectionStateValue,
      rpc: rpcMock,
      subscribe: subscribeMock,
    }));

    mockUseSettings.mockImplementation(() => ({
      soundEnabled: soundEnabledValue,
    }));

    globalThis.fetch = vi.fn((input: string | URL | Request) => {
      const url = typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;

      if (url.includes('/api/server-info')) return Promise.resolve(jsonResponse({ agentName: 'Jen' }));
      return Promise.resolve(jsonResponse({}));
    }) as typeof fetch;
  });

  it('uses the full gateway session list for sidebar refreshes so older agent chats stay visible', async () => {
    render(
      <SessionProvider>
        <SessionLabels />
      </SessionProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText('Designer')).toBeInTheDocument();
    });

    expect(rpcMock).toHaveBeenCalledWith('sessions.list', { limit: 1000 });
    expect(rpcMock).not.toHaveBeenCalledWith('sessions.list', expect.objectContaining({ activeMinutes: expect.any(Number) }));
  });

  it('shows each root agent by its roster name, the label sessions.list sends, and reads no identity file', async () => {
    rpcMock.mockImplementation(async (method: string) => {
      if (method === 'sessions.list') {
        return {
          sessions: [
            { sessionKey: 'agent:main:main', label: 'Main' },
            // nerve_gateway._sessions_list: one root row per roster agent, label = roster name.
            { sessionKey: 'agent:reviewer:main', label: 'Reviewer Prime', displayName: 'Reviewer Prime' },
            // A root with no roster name falls back to the agent id.
            { sessionKey: 'agent:builder:main' },
          ],
        };
      }
      return {};
    });

    render(
      <SessionProvider>
        <SessionDisplayLabels />
      </SessionProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText('Reviewer Prime')).toBeInTheDocument();
      expect(screen.getByText('builder')).toBeInTheDocument();
    });

    const urls = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map(([input]) => String(input));
    expect(urls.some((url) => url.includes('/api/workspace'))).toBe(false);
    expect(urls.some((url) => url.includes('/api/agentlog'))).toBe(false);
  });

  // The refresh merge preserves object references for unchanged sessions. It
  // must not decide "unchanged" from `state` alone: every non-RUNNING status
  // maps to state "idle", so AWAITING_HUMAN → DONE moves nothing there and a
  // closed session kept rendering as open until a page reload.
  it('picks up a status-only change (AWAITING_HUMAN -> DONE) on refresh', async () => {
    const sweepKey = 'agent:jira-expert:sess_d356c2c430ea';
    let listCalls = 0;
    rpcMock.mockImplementation(async (method: string) => {
      if (method !== 'sessions.list') return {};
      listCalls += 1;
      return {
        sessions: [
          { sessionKey: 'agent:jira-expert:main', label: 'Jira Expert', status: 'IDLE', state: 'idle' },
          {
            sessionKey: sweepKey,
            label: 'Nightly Jira hygiene sweep',
            status: listCalls >= 2 ? 'DONE' : 'AWAITING_HUMAN',
            state: 'idle',
          },
        ],
      };
    });

    function SweepStatusProbe() {
      const { sessions } = useSessionContext();
      const sweep = sessions.find((s) => getSessionKey(s) === sweepKey);
      return <div data-testid="sweep-status">{sweep?.status ?? 'NONE'}</div>;
    }

    const { getByTestId } = render(
      <SessionProvider>
        <SweepStatusProbe />
        <SessionRefreshProbe />
      </SessionProvider>,
    );

    await waitFor(() => expect(getByTestId('sweep-status').textContent).toBe('AWAITING_HUMAN'));

    await act(async () => {
      getByTestId('refresh-sessions').click();
    });

    await waitFor(() => expect(getByTestId('sweep-status').textContent).toBe('DONE'));
  });

  it('marks background top-level roots unread on start and pings when chat reaches a terminal event', async () => {
    rpcMock.mockImplementation(async (method: string) => {
      if (method === 'sessions.list') {
        return {
          sessions: [
            { sessionKey: 'agent:main:main', label: 'Main' },
            { sessionKey: 'agent:reviewer:main', label: 'Reviewer' },
          ],
        };
      }
      return {};
    });

    render(
      <SessionProvider>
        <SessionUnreadProbe />
      </SessionProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('current-session').textContent).toBe('agent:main:main');
    });

    act(() => {
      subscribedHandler?.({
        type: 'event',
        event: 'chat',
        payload: {
          sessionKey: 'agent:reviewer:main',
          state: 'started',
        },
      });
    });

    await waitFor(() => {
      expect(screen.getByTestId('reviewer-unread').textContent).toBe('true');
    });
    expect(playPingMock).not.toHaveBeenCalled();

    await act(async () => {
      subscribedHandler?.({
        type: 'event',
        event: 'chat',
        payload: {
          sessionKey: 'agent:reviewer:main',
          state: 'final',
        },
      });
      await Promise.resolve();
    });

    expect(playPingMock).toHaveBeenCalledTimes(1);
  });

  it('does not mark the currently viewed root unread or ping for its own chat events', async () => {
    rpcMock.mockImplementation(async (method: string) => {
      if (method === 'sessions.list') {
        return {
          sessions: [
            { sessionKey: 'agent:main:main', label: 'Main' },
            { sessionKey: 'agent:reviewer:main', label: 'Reviewer' },
          ],
        };
      }
      return {};
    });

    render(
      <SessionProvider>
        <SessionUnreadProbe />
      </SessionProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('current-session').textContent).toBe('agent:main:main');
    });

    act(() => {
      screen.getByTestId('select-reviewer').click();
    });

    await waitFor(() => {
      expect(screen.getByTestId('current-session').textContent).toBe('agent:reviewer:main');
    });

    act(() => {
      subscribedHandler?.({
        type: 'event',
        event: 'chat',
        payload: {
          sessionKey: 'agent:reviewer:main',
          state: 'started',
        },
      });
    });

    await waitFor(() => {
      expect(screen.getByTestId('reviewer-unread').textContent).toBe('false');
    });
    expect(playPingMock).not.toHaveBeenCalled();
  });

  it('does not mark unread or ping when a root becomes current in the same act as its chat event', async () => {
    rpcMock.mockImplementation(async (method: string) => {
      if (method === 'sessions.list') {
        return {
          sessions: [
            { sessionKey: 'agent:main:main', label: 'Main' },
            { sessionKey: 'agent:reviewer:main', label: 'Reviewer' },
          ],
        };
      }
      return {};
    });

    render(
      <SessionProvider>
        <SessionUnreadProbe />
      </SessionProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('current-session').textContent).toBe('agent:main:main');
    });

    act(() => {
      screen.getByTestId('select-reviewer').click();
      subscribedHandler?.({
        type: 'event',
        event: 'chat',
        payload: {
          sessionKey: 'agent:reviewer:main',
          state: 'started',
        },
      });
    });

    await waitFor(() => {
      expect(screen.getByTestId('current-session').textContent).toBe('agent:reviewer:main');
    });
    expect(screen.getByTestId('reviewer-unread').textContent).toBe('false');
    expect(playPingMock).not.toHaveBeenCalled();
  });

  it('keeps the DONE-to-IDLE timer alive when sound is toggled mid-response', async () => {
    rpcMock.mockImplementation(async (method: string) => {
      if (method === 'sessions.list') {
        return {
          sessions: [
            { sessionKey: 'agent:main:main', label: 'Main' },
            { sessionKey: 'agent:reviewer:main', label: 'Reviewer' },
          ],
        };
      }
      return {};
    });

    const view = render(
      <SessionProvider>
        <SessionStatusProbe />
      </SessionProvider>,
    );

    await waitFor(() => {
      expect(subscribedHandler).not.toBeNull();
    });

    vi.useFakeTimers();

    await act(async () => {
      subscribedHandler?.({
        type: 'event',
        event: 'chat',
        payload: {
          sessionKey: 'agent:reviewer:main',
          state: 'final',
        },
      });
      await Promise.resolve();
    });

    expect(screen.getByTestId('reviewer-status').textContent).toBe('DONE');

    await act(async () => {
      soundEnabledValue = false;
      view.rerender(
        <SessionProvider>
          <SessionStatusProbe />
        </SessionProvider>,
      );
      await Promise.resolve();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_100);
    });

    expect(screen.getByTestId('reviewer-status').textContent).toBe('IDLE');
  });

  it('uses the latest refresh callback for delayed refreshes after gateway changes', async () => {
    const rpcBeforeReconnect = vi.fn(async (method: string) => {
      if (method === 'sessions.list') {
        return {
          sessions: [
            { sessionKey: 'agent:main:main', label: 'Main' },
            { sessionKey: 'agent:reviewer:main', label: 'Reviewer' },
          ],
        };
      }
      return {};
    });
    const rpcAfterReconnect = vi.fn(async () => ({}));
    rpcMock = rpcBeforeReconnect;

    const view = render(
      <SessionProvider>
        <SessionStatusProbe />
      </SessionProvider>,
    );

    await waitFor(() => {
      expect(rpcBeforeReconnect).toHaveBeenCalledWith('sessions.list', { limit: 1000 });
    });

    vi.useFakeTimers();

    await act(async () => {
      subscribedHandler?.({
        type: 'event',
        event: 'chat',
        payload: {
          sessionKey: 'agent:reviewer:main',
          state: 'final',
        },
      });
      await Promise.resolve();
    });

    const preReconnectSessionsListCalls = rpcBeforeReconnect.mock.calls.filter(([method]) => method === 'sessions.list').length;

    await act(async () => {
      connectionStateValue = 'reconnecting';
      rpcMock = rpcAfterReconnect;
      view.rerender(
        <SessionProvider>
          <SessionStatusProbe />
        </SessionProvider>,
      );
      await Promise.resolve();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_600);
    });

    expect(rpcBeforeReconnect.mock.calls.filter(([method]) => method === 'sessions.list')).toHaveLength(preReconnectSessionsListCalls);
    expect(rpcAfterReconnect).not.toHaveBeenCalledWith('sessions.list', expect.anything());
  });

  it('uses the latest refresh callback for missing-session fallback refreshes after gateway changes', async () => {
    const rpcBeforeReconnect = vi.fn(async (method: string) => {
      if (method === 'sessions.list') {
        return {
          sessions: [
            { sessionKey: 'agent:main:main', label: 'Main' },
            { sessionKey: 'agent:reviewer:main', label: 'Reviewer' },
          ],
        };
      }
      return {};
    });
    const rpcAfterReconnect = vi.fn(async () => ({}));
    rpcMock = rpcBeforeReconnect;

    const view = render(
      <SessionProvider>
        <SessionStatusProbe />
      </SessionProvider>,
    );

    await waitFor(() => {
      expect(rpcBeforeReconnect).toHaveBeenCalledWith('sessions.list', { limit: 1000 });
    });

    vi.useFakeTimers();

    await act(async () => {
      subscribedHandler?.({
        type: 'event',
        event: 'chat',
        payload: {
          sessionKey: 'agent:fresh:main',
          state: 'started',
        },
      });
      await Promise.resolve();
    });

    const preReconnectSessionsListCalls = rpcBeforeReconnect.mock.calls.filter(([method]) => method === 'sessions.list').length;

    await act(async () => {
      connectionStateValue = 'reconnecting';
      rpcMock = rpcAfterReconnect;
      view.rerender(
        <SessionProvider>
          <SessionStatusProbe />
        </SessionProvider>,
      );
      await Promise.resolve();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(150);
    });

    expect(rpcBeforeReconnect.mock.calls.filter(([method]) => method === 'sessions.list')).toHaveLength(preReconnectSessionsListCalls);
    expect(rpcAfterReconnect).not.toHaveBeenCalledWith('sessions.list', expect.anything());
  });
});
