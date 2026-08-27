/**
 * The tab badges must react to an agent asking. `cc.agent.*` was missing from
 * the push filter, so the one class of event where an agent is standing still
 * waiting for the operator was also the one the badge learned about last (up to 30s,
 * one poll).
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { GatewayEvent } from '@/types';

type Handler = (msg: GatewayEvent) => void;
const handlers = new Set<Handler>();
const subscribe = (h: Handler) => { handlers.add(h); return () => { handlers.delete(h); }; };
const rpc = vi.fn();

vi.mock('@/contexts/GatewayContext', () => ({
  useGateway: () => ({
    rpc,
    connectionState: 'connected',
    // STABLE identity: a fresh closure each render would make the subscribe
    // effect tear down and re-add on every render, leaving the handler set
    // momentarily empty and the test flaky.
    subscribe,
  }),
}));

const { useAttention } = await import('./useAttention');

beforeEach(() => {
  handlers.clear();
  rpc.mockReset();
  rpc.mockResolvedValue({ proposals: [], dismissals: [], operatorItems: [], sessions: [] });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));
});

async function push(event: string) {
  await act(async () => {
    for (const h of handlers) h({ type: 'event', event, payload: {} });
    await new Promise((r) => setTimeout(r, 550));   // the 400ms debounce
  });
}

describe('useAttention push filter', () => {
  it('refetches when an agent opens a discussion', async () => {
    renderHook(() => useAttention());
    await waitFor(() => expect(rpc).toHaveBeenCalled());
    rpc.mockClear();

    await push('cc.agent.discussion_opened');
    expect(rpc).toHaveBeenCalledWith('decisions.list');
  });

  it('refetches when an agent asks', async () => {
    renderHook(() => useAttention());
    await waitFor(() => expect(rpc).toHaveBeenCalled());
    rpc.mockClear();

    await push('cc.agent.asked');
    expect(rpc).toHaveBeenCalledWith('decisions.list');
  });

  it('still ignores chatter that changes none of the three counts', async () => {
    renderHook(() => useAttention());
    await waitFor(() => expect(rpc).toHaveBeenCalled());
    rpc.mockClear();

    await push('cc.feed.poll');
    expect(rpc).not.toHaveBeenCalled();
  });

  it('counts an AWAITING_OPERATOR lane as chat attention', async () => {
    rpc.mockImplementation((method: string) => {
      if (method === 'sessions.list') {
        return Promise.resolve({
          sessions: [
            { key: 'agent:coach:sess_a1', status: 'AWAITING_OPERATOR' },
            { key: 'agent:coach:sess_b1', status: 'DONE' },
          ],
        });
      }
      return Promise.resolve({ proposals: [], dismissals: [], operatorItems: [] });
    });

    const { result } = renderHook(() => useAttention());
    await waitFor(() => expect(result.current.chat).toBe(1));
  });
});
