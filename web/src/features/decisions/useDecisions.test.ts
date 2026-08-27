/**
 * History pagination lives in the hook, not the view: `loadMoreHistory`
 * computes its offset from the currently-loaded rows, so this is exercised
 * against the real hook rather than the view-level mock.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useDecisions } from './useDecisions';

const rpcMock = vi.fn();

vi.mock('@/contexts/GatewayContext', () => ({
  useGateway: () => ({
    rpc: rpcMock,
    connectionState: 'connected',
    subscribe: () => () => {},
  }),
}));

function page(n: number, offset: number) {
  return {
    proposals: Array.from({ length: n }, (_, i) => ({
      id: `p${offset + i}`, session_id: 's', agent_id: 'a', intent: 'x',
      status: 'EXECUTED', created_at: null, decided_at: null,
    })),
    hasMore: true,
  };
}

beforeEach(() => {
  rpcMock.mockReset();
  rpcMock.mockImplementation((method: string) => {
    if (method === 'decisions.list') {
      return Promise.resolve({ proposals: [], dismissals: [], operatorItems: [] });
    }
    return Promise.resolve({ proposals: [], hasMore: false });
  });
});

describe('useDecisions history', () => {
  it('does not fetch history until toggled on', async () => {
    renderHook(() => useDecisions());
    await waitFor(() => expect(rpcMock).toHaveBeenCalledWith('decisions.list'));
    expect(rpcMock).not.toHaveBeenCalledWith('decisions.history', expect.anything());
  });

  it('fetches the first page (offset 0) on toggle-on', async () => {
    rpcMock.mockImplementation((method: string) => {
      if (method === 'decisions.list') return Promise.resolve({ proposals: [], dismissals: [], operatorItems: [] });
      return Promise.resolve(page(50, 0));
    });
    const { result } = renderHook(() => useDecisions());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => { result.current.toggleHistory(); });

    expect(rpcMock).toHaveBeenCalledWith('decisions.history', { limit: 50, offset: 0 });
    await waitFor(() => expect(result.current.history).toHaveLength(50));
    expect(result.current.historyHasMore).toBe(true);
  });

  it('loadMoreHistory requests offset = current history length', async () => {
    rpcMock.mockImplementation((method: string) => {
      if (method === 'decisions.list') return Promise.resolve({ proposals: [], dismissals: [], operatorItems: [] });
      return Promise.resolve(page(50, 0));
    });
    const { result } = renderHook(() => useDecisions());
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => { result.current.toggleHistory(); });
    await waitFor(() => expect(result.current.history).toHaveLength(50));

    rpcMock.mockImplementation((method: string) => {
      if (method === 'decisions.history') return Promise.resolve(page(10, 50));
      return Promise.resolve({ proposals: [], dismissals: [], operatorItems: [] });
    });
    await act(async () => { await result.current.loadMoreHistory(); });

    expect(rpcMock).toHaveBeenCalledWith('decisions.history', { limit: 50, offset: 50 });
    expect(result.current.history).toHaveLength(60);
  });

  it('toggling off keeps loaded rows without refetching', async () => {
    rpcMock.mockImplementation((method: string) => {
      if (method === 'decisions.list') return Promise.resolve({ proposals: [], dismissals: [], operatorItems: [] });
      return Promise.resolve(page(5, 0));
    });
    const { result } = renderHook(() => useDecisions());
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => { result.current.toggleHistory(); });
    await waitFor(() => expect(result.current.history).toHaveLength(5));

    rpcMock.mockClear();
    await act(async () => { result.current.toggleHistory(); });

    expect(result.current.showHistory).toBe(false);
    expect(result.current.history).toHaveLength(5);
    expect(rpcMock).not.toHaveBeenCalledWith('decisions.history', expect.anything());
  });
});
