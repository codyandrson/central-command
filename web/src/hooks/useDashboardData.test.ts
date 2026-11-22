import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useDashboardData } from './useDashboardData';

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

vi.mock('@/contexts/GatewayContext', () => ({
  useGateway: () => ({
    subscribe: subscribeMock,
    connectionState: 'disconnected',
  }),
}));

describe('useDashboardData', () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    subscribeMock.mockClear();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('fetches the token usage once on mount', async () => {
    globalThis.fetch = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url === '/api/tokens') return Promise.resolve(jsonResponse({ totalTokens: 12 }));
      throw new Error(`Unexpected fetch: ${url}`);
    }) as typeof globalThis.fetch;

    const { result } = renderHook(() => useDashboardData());

    await waitFor(() => expect(result.current.tokenData).toEqual({ totalTokens: 12 }));
    const fetchMock = vi.mocked(globalThis.fetch);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('subscribes to the gateway WebSocket only once connected, never to an SSE stream', async () => {
    globalThis.fetch = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url === '/api/tokens') return Promise.resolve(jsonResponse({ totalTokens: 0 }));
      throw new Error(`Unexpected fetch: ${url}`);
    }) as typeof globalThis.fetch;

    renderHook(() => useDashboardData());
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(1));
    // The mocked gateway is disconnected: no subscription, and the one fetch is /api/tokens.
    expect(subscribeMock).not.toHaveBeenCalled();
    expect(vi.mocked(globalThis.fetch).mock.calls.map((c) => String(c[0]))).toEqual(['/api/tokens']);
  });
});
