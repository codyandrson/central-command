import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, render, waitFor, act } from '@testing-library/react';
import { createElement } from 'react';
import { useDashboardData } from './useDashboardData';
import type { ServerEvent } from '@/hooks/useServerEvents';

type FetchResponse = {
  ok: boolean;
  status?: number;
  json: () => Promise<unknown>;
};

function jsonResponse(data: unknown, init: { ok?: boolean; status?: number } = {}): FetchResponse {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => data,
  };
}

const subscribeMock = vi.fn(() => () => {});
let sseHandler: ((event: ServerEvent) => void) | undefined;

vi.mock('@/contexts/GatewayContext', () => ({
  useGateway: () => ({
    subscribe: subscribeMock,
    connectionState: 'disconnected',
  }),
}));

vi.mock('@/hooks/useServerEvents', () => ({
  useServerEvents: (handler: (event: ServerEvent) => void) => {
    sseHandler = handler;
    return { connected: true, reconnectAttempts: 0, lastEvent: null };
  },
}));

function DashboardDataRenderObserver({
  agentId,
  onFileChanged,
  emitDuringRender,
}: {
  agentId: string;
  onFileChanged?: (...args: unknown[]) => void;
  emitDuringRender?: () => void;
}) {
  useDashboardData({
    agentId,
    onFileChanged: onFileChanged as unknown as (path: string, targetAgentId: string) => void,
  });

  emitDuringRender?.();
  return null;
}

describe('useDashboardData', () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    subscribeMock.mockClear();
    sseHandler = undefined;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('fetches the token usage once on mount and again on a tokens.updated event', async () => {
    globalThis.fetch = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url === '/api/tokens') return Promise.resolve(jsonResponse({ totalTokens: 12 }));
      throw new Error(`Unexpected fetch: ${url}`);
    }) as typeof globalThis.fetch;

    const { result } = renderHook(() => useDashboardData({ agentId: 'alpha' }));

    await waitFor(() => expect(result.current.tokenData).toEqual({ totalTokens: 12 }));
    const fetchMock = vi.mocked(globalThis.fetch);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    act(() => { sseHandler?.({ event: 'tokens.updated', data: {}, ts: Date.now() }); });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it('no longer reads the memory list: memory.changed refreshes nothing', async () => {
    globalThis.fetch = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url === '/api/tokens') return Promise.resolve(jsonResponse({ totalTokens: 0 }));
      throw new Error(`Unexpected fetch: ${url}`);
    }) as typeof globalThis.fetch;

    renderHook(() => useDashboardData({ agentId: 'alpha' }));
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(1));
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockClear();

    act(() => { sseHandler?.({ event: 'memory.changed', data: { agentId: 'alpha' }, ts: Date.now() }); });

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('drops old-agent file.changed events that arrive during the workspace switch render gap', async () => {
    const alphaOnFileChanged = vi.fn();
    const bravoOnFileChanged = vi.fn();

    globalThis.fetch = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url === '/api/tokens') return Promise.resolve(jsonResponse({ totalTokens: 0 }));
      throw new Error(`Unexpected fetch: ${url}`);
    }) as typeof globalThis.fetch;

    const { rerender } = render(createElement(DashboardDataRenderObserver, {
      agentId: 'alpha',
      onFileChanged: alphaOnFileChanged,
    }));

    alphaOnFileChanged.mockClear();
    bravoOnFileChanged.mockClear();

    act(() => {
      rerender(createElement(DashboardDataRenderObserver, {
        agentId: 'bravo',
        onFileChanged: bravoOnFileChanged,
        emitDuringRender: () => {
          sseHandler?.({ event: 'file.changed', data: { path: 'shared.md', agentId: 'alpha' }, ts: Date.now() });
        },
      }));
    });

    expect(alphaOnFileChanged).not.toHaveBeenCalled();
    expect(bravoOnFileChanged).not.toHaveBeenCalled();
  });

  it('forwards file.changed only for the active agent, with its agent id', async () => {
    const onFileChanged = vi.fn();

    globalThis.fetch = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url === '/api/tokens') return Promise.resolve(jsonResponse({ totalTokens: 0 }));
      throw new Error(`Unexpected fetch: ${url}`);
    }) as typeof globalThis.fetch;

    renderHook(() => useDashboardData({ agentId: 'alpha', onFileChanged }));

    act(() => {
      sseHandler?.({ event: 'file.changed', data: { path: 'notes/a.md', agentId: 'bravo' }, ts: Date.now() });
      sseHandler?.({ event: 'file.changed', data: { path: 'notes/a.md' }, ts: Date.now() });
    });
    expect(onFileChanged).not.toHaveBeenCalled();

    act(() => {
      sseHandler?.({ event: 'file.changed', data: { path: 'notes/a.md', agentId: 'alpha' }, ts: Date.now() });
    });
    expect(onFileChanged).toHaveBeenCalledWith('notes/a.md', 'alpha');
  });
});
