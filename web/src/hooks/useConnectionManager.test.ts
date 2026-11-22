/** Tests for useConnectionManager hook. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';

// Mock GatewayContext exports used by useConnectionManager
const connectMock = vi.fn(async () => {});
const disconnectMock = vi.fn();

vi.mock('@/contexts/GatewayContext', () => ({
  useGateway: () => ({
    connectionState: 'disconnected',
    connect: connectMock,
    disconnect: disconnectMock,
  }),
}));

/** Point window.location.search at `search` for the duration of a test. */
function setSearch(search: string) {
  window.history.replaceState({}, '', `/${search}`);
}

describe('useConnectionManager', () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    vi.resetModules();
    connectMock.mockClear();
    disconnectMock.mockClear();
    setSearch('');
    localStorage.setItem('oc-config', JSON.stringify({ url: 'ws://stale.host:1234/ws', token: 'stale' }));
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    localStorage.clear();
    setSearch('');
    vi.restoreAllMocks();
  });

  it('auto-connects on mount with no target and no token — stale storage ignored', async () => {
    const mod = await import('./useConnectionManager');
    renderHook(() => mod.useConnectionManager());

    await waitFor(() => expect(connectMock).toHaveBeenCalledTimes(1));
    // Empty url = "the proxy's own gateway"; empty token lets the proxy inject.
    expect(connectMock).toHaveBeenCalledWith('', '');
  });

  it('asks the server for no connect defaults: the gateway is fixed at install', async () => {
    const mod = await import('./useConnectionManager');
    renderHook(() => mod.useConnectionManager());

    await waitFor(() => expect(connectMock).toHaveBeenCalledWith('', ''));
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('honours a ?gateway= override as an explicit target', async () => {
    setSearch('?gateway=ws%3A%2F%2F127.0.0.1%3A9999%2Fws');

    const mod = await import('./useConnectionManager');
    renderHook(() => mod.useConnectionManager());

    await waitFor(() => expect(connectMock).toHaveBeenCalledWith('ws://127.0.0.1:9999/ws', ''));
  });

  it('reconnects through the proxy default, the way the mount did', async () => {
    const mod = await import('./useConnectionManager');
    const { result } = renderHook(() => mod.useConnectionManager());

    await waitFor(() => expect(connectMock).toHaveBeenCalledTimes(1));
    connectMock.mockClear();

    await act(async () => { await result.current.handleReconnect(); });

    expect(disconnectMock).toHaveBeenCalled();
    expect(connectMock).toHaveBeenCalledWith('', '');
  });

  it('reconnects to the ?gateway= override when one is on the page URL', async () => {
    setSearch('?gateway=ws%3A%2F%2F127.0.0.1%3A9999%2Fws');
    const mod = await import('./useConnectionManager');
    const { result } = renderHook(() => mod.useConnectionManager());

    await waitFor(() => expect(connectMock).toHaveBeenCalledTimes(1));
    connectMock.mockClear();

    await act(async () => { await result.current.handleReconnect(); });

    expect(connectMock).toHaveBeenCalledWith('ws://127.0.0.1:9999/ws', '');
  });
});
