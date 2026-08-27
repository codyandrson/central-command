/**
 * The interrupt tier's one hard property: TOAST EXACTLY ONCE per new thing
 * awaiting the operator, and never for something he has already been shown.
 *
 * The gateway is mocked at the context seam rather than driven through
 * `src/test/mock-gateway.ts` (a real WS server, used by the node-env proxy
 * test): the behaviour under test is push → refetch → diff, and a real socket
 * would add nondeterministic timing without exercising a single line of it.
 * What matters is that a push is treated as a refresh hint and the toast comes
 * from the refetched projection — which is exactly what this drives.
 */
import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GatewayEvent } from '@/types';
import { SEEN_KEY, TOAST_MODE_KEY } from './seen';

type Handler = (msg: GatewayEvent) => void;

const handlers = new Set<Handler>();
const subscribe = (h: Handler) => { handlers.add(h); return () => { handlers.delete(h); }; };
const rpc = vi.fn();
let connectionState = 'connected';

vi.mock('@/contexts/GatewayContext', () => ({
  useGateway: () => ({
    rpc,
    connectionState,
    // STABLE identity: a fresh closure each render would make the subscribe
    // effect tear down and re-add on every render, leaving the handler set
    // momentarily empty and the test flaky.
    subscribe,
  }),
}));

const playPing = vi.fn();
vi.mock('@/features/voice/audio-feedback', () => ({
  playPing,
  playWakePing: playPing,
  playSubmitPing: playPing,
  playCancelPing: playPing,
  ensureAudioContext: playPing,
}));

// Imported after the mocks so the provider picks them up.
const { NotificationsProvider } = await import('./NotificationsContext');
const { ToastHost, AUTO_DISMISS_MS } = await import('./ToastHost');

const PROPOSAL = { id: 'prop_1', agent_id: 'jira-expert', intent: 'Create JIRA-9' };
const ASK = {
  id: 'item_1', agent_id: 'coach', kind: 'question', body: 'Which board?',
  task_title: 'Sprint tidy', discussion_session_id: null,
};
const AGENT_LANE = {
  key: 'agent:coach:sess_a1', sessionKey: 'agent:coach:sess_a1', agentId: 'coach',
  status: 'AWAITING_OPERATOR', openedBy: 'agent', blockedTaskTitle: 'Sprint tidy',
};
const OPERATOR_LANE = {
  key: 'agent:coach:sess_o1', sessionKey: 'agent:coach:sess_o1', agentId: 'coach',
  status: 'AWAITING_OPERATOR', openedBy: 'operator', blockedTaskTitle: null,
};

function wire(
  decisions: { proposals?: unknown[]; operatorItems?: unknown[] },
  sessions: { sessions?: unknown[] },
) {
  rpc.mockImplementation((method: string) => {
    if (method === 'decisions.list') return Promise.resolve(decisions);
    if (method === 'sessions.list') return Promise.resolve(sessions);
    return Promise.resolve({});
  });
}

async function pushEvent(event: string, payload: unknown = {}) {
  await act(async () => {
    for (const h of handlers) h({ type: 'event', event, payload });
    // The interrupt path debounces (400ms) before refetching.
    await new Promise((r) => setTimeout(r, 550));
  });
}

function renderHost() {
  return render(
    <NotificationsProvider>
      <ToastHost onOpenDecision={() => {}} onOpenSession={() => {}} />
    </NotificationsProvider>,
  );
}

/** Let the mount-time cold-start diff settle before driving pushes. */
async function settle() {
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
}

beforeEach(() => {
  handlers.clear();
  rpc.mockReset();
  playPing.mockClear();
  connectionState = 'connected';
  localStorage.removeItem(SEEN_KEY);
  localStorage.removeItem(TOAST_MODE_KEY);
});

afterEach(() => { vi.useRealTimers(); });

describe('cold start', () => {
  it('SEEDS the seen-set and toasts nothing for a queue that was already there', async () => {
    wire({ proposals: [PROPOSAL], operatorItems: [ASK] }, { sessions: [AGENT_LANE] });
    renderHost();
    await settle();

    expect(screen.queryAllByTestId('cc-toast')).toHaveLength(0);
    // …and it recorded them, so they never toast later either.
    const seen = JSON.parse(localStorage.getItem(SEEN_KEY) || '{}') as Record<string, number>;
    expect(Object.keys(seen).sort()).toEqual([
      'item:item_1', 'proposal:prop_1', 'session:agent:coach:sess_a1',
    ]);
  });

  it('does not seed on a failed first fetch — the real first diff must still fire', async () => {
    rpc.mockRejectedValue(new Error('gateway down'));
    renderHost();
    await settle();
    expect(localStorage.getItem(SEEN_KEY)).toBeNull();

    wire({ proposals: [PROPOSAL] }, { sessions: [] });
    await pushEvent('cc.proposal.created');
    // Still the cold start (the first SUCCESSFUL fetch) — seeded, silent.
    expect(screen.queryAllByTestId('cc-toast')).toHaveLength(0);
  });
});

describe('one toast per new item', () => {
  it('toasts a proposal that appeared after the operator was last brought up to date', async () => {
    wire({ proposals: [] }, { sessions: [] });
    renderHost();
    await settle();

    wire({ proposals: [PROPOSAL] }, { sessions: [] });
    await pushEvent('cc.proposal.created');

    await waitFor(() => expect(screen.getAllByTestId('cc-toast')).toHaveLength(1));
    expect(screen.getByText('jira-expert proposed a change')).toBeInTheDocument();
  });

  it('does NOT toast the same item twice, however many pushes touch it', async () => {
    wire({ proposals: [] }, { sessions: [] });
    renderHost();
    await settle();

    wire({ proposals: [PROPOSAL] }, { sessions: [] });
    await pushEvent('cc.proposal.created');
    await pushEvent('cc.proposal.created');
    await pushEvent('cc.orchestration.parked');

    expect(screen.getAllByTestId('cc-toast')).toHaveLength(1);
  });

  it('stays silent when localStorage already says the item was shown', async () => {
    localStorage.setItem(SEEN_KEY, JSON.stringify({ 'proposal:prop_1': Date.now() }));
    // Not a cold start for this item: seed the empty projection first, then
    // have it appear — the seen-set is what suppresses it.
    wire({ proposals: [] }, { sessions: [] });
    renderHost();
    await settle();

    wire({ proposals: [PROPOSAL] }, { sessions: [] });
    await pushEvent('cc.proposal.created');

    expect(screen.queryAllByTestId('cc-toast')).toHaveLength(0);
  });
});

describe('agent-opened lanes', () => {
  it('toasts a lane the AGENT opened', async () => {
    wire({ proposals: [] }, { sessions: [] });
    renderHost();
    await settle();

    wire({ proposals: [] }, { sessions: [AGENT_LANE] });
    await pushEvent('cc.agent.discussion_opened');

    await waitFor(() => expect(screen.getByText('coach opened a discussion')).toBeInTheDocument());
  });

  it('does NOT toast a resting lane the OPERATOR opened — he knows it is there', async () => {
    wire({ proposals: [] }, { sessions: [] });
    renderHost();
    await settle();

    wire({ proposals: [] }, { sessions: [OPERATOR_LANE] });
    await pushEvent('cc.agent.discussion_opened');

    expect(screen.queryAllByTestId('cc-toast')).toHaveLength(0);
  });
});

describe('the reconnect gap', () => {
  it('toasts what arrived while the socket was down, on reconnect', async () => {
    wire({ proposals: [] }, { sessions: [] });
    const view = renderHost();
    await settle();

    // Socket drops. The proposal parks during the gap; its push is LOST —
    // `_forward_events` has no replay, so nothing will ever re-deliver it.
    connectionState = 'reconnecting';
    view.rerender(
      <NotificationsProvider>
        <ToastHost onOpenDecision={() => {}} onOpenSession={() => {}} />
      </NotificationsProvider>,
    );
    wire({ proposals: [PROPOSAL] }, { sessions: [] });

    connectionState = 'connected';
    await act(async () => {
      view.rerender(
        <NotificationsProvider>
          <ToastHost onOpenDecision={() => {}} onOpenSession={() => {}} />
        </NotificationsProvider>,
      );
      await new Promise((r) => setTimeout(r, 30));
    });

    await waitFor(() => expect(screen.getAllByTestId('cc-toast')).toHaveLength(1));
  });
});

describe('errors', () => {
  it('toasts a failure straight off the push — it has no projection row to diff', async () => {
    wire({ proposals: [] }, { sessions: [] });
    renderHost();
    await settle();

    await act(async () => {
      for (const h of handlers) {
        h({
          type: 'event',
          event: 'cc.proposal.failed',
          payload: { id: 'evt_9', payload: { error: 'jira 401' } },
        });
      }
    });

    await waitFor(() => expect(screen.getByText('jira 401')).toBeInTheDocument());
  });

  it('ignores digest-tier chatter entirely', async () => {
    wire({ proposals: [PROPOSAL] }, { sessions: [] });
    renderHost();
    await settle();
    rpc.mockClear();

    await pushEvent('cc.feed.poll');
    expect(rpc).not.toHaveBeenCalled();
    expect(screen.queryAllByTestId('cc-toast')).toHaveLength(0);
  });
});

describe('the toast-mode setting', () => {
  it('stays silent on "off" but still records the item as seen', async () => {
    localStorage.setItem(TOAST_MODE_KEY, 'off');
    wire({ proposals: [] }, { sessions: [] });
    renderHost();
    await settle();

    wire({ proposals: [PROPOSAL] }, { sessions: [] });
    await pushEvent('cc.proposal.created');

    expect(screen.queryAllByTestId('cc-toast')).toHaveLength(0);
    // Turning toasts back on must not replay the backlog.
    const seen = JSON.parse(localStorage.getItem(SEEN_KEY) || '{}') as Record<string, number>;
    expect(seen['proposal:prop_1']).toBeTypeOf('number');
  });

  it('drops attention toasts on "errors" and keeps failures', async () => {
    localStorage.setItem(TOAST_MODE_KEY, 'errors');
    wire({ proposals: [] }, { sessions: [] });
    renderHost();
    await settle();

    wire({ proposals: [PROPOSAL] }, { sessions: [] });
    await pushEvent('cc.proposal.created');
    expect(screen.queryAllByTestId('cc-toast')).toHaveLength(0);

    await act(async () => {
      for (const h of handlers) {
        h({ type: 'event', event: 'cc.proposal.failed', payload: { id: 'evt_9', payload: { error: 'jira 401' } } });
      }
    });
    await waitFor(() => expect(screen.getByText('jira 401')).toBeInTheDocument());
  });
});

describe('auto-dismiss', () => {
  it('fades an attention toast on its own, and leaves an error standing', async () => {
    // Fake timers from the top so the toast's countdown is one of them;
    // shouldAdvanceTime keeps the awaited debounce waits below working.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    wire({ proposals: [] }, { sessions: [] });
    renderHost();
    await settle();

    wire({ proposals: [PROPOSAL] }, { sessions: [] });
    await pushEvent('cc.proposal.created');
    await act(async () => {
      for (const h of handlers) {
        h({ type: 'event', event: 'cc.proposal.failed', payload: { id: 'evt_9', payload: { error: 'jira 401' } } });
      }
    });
    await waitFor(() => expect(screen.getAllByTestId('cc-toast')).toHaveLength(2));

    act(() => { vi.advanceTimersByTime(AUTO_DISMISS_MS + 100); });
    expect(screen.getAllByTestId('cc-toast')).toHaveLength(1);
    expect(screen.getByText('jira 401')).toBeInTheDocument();
  });
});

describe('no sound', () => {
  it('never plays a ping — the interrupt tier is visual only', async () => {
    wire({ proposals: [] }, { sessions: [] });
    renderHost();
    await settle();

    wire({ proposals: [PROPOSAL] }, { sessions: [] });
    await pushEvent('cc.proposal.created');
    await waitFor(() => expect(screen.getAllByTestId('cc-toast')).toHaveLength(1));

    expect(playPing).not.toHaveBeenCalled();
  });
});
