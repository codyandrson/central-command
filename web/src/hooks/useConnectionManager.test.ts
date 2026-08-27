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
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ wsUrl: 'ws://127.0.0.1:8080/ws', token: null, authEnabled: false, serverSideAuth: true }),
    });
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    localStorage.clear();
    setSearch('');
    vi.restoreAllMocks();
  });

  it('auto-connects on mount with no target and no token — stale storage ignored', async () => {
    const mod = await import('./useConnectionManager');
    const { result } = renderHook(() => mod.useConnectionManager());

    await waitFor(() => expect(connectMock).toHaveBeenCalledTimes(1));
    // Empty url = "the proxy's own gateway"; empty token lets the proxy inject.
    expect(connectMock).toHaveBeenCalledWith('', '');
    expect(result.current.dialogOpen).toBe(false);
  });

  it('does not wait for /api/connect-defaults to connect', async () => {
    globalThis.fetch = vi.fn(() => new Promise(() => {})) as unknown as typeof fetch;

    const mod = await import('./useConnectionManager');
    renderHook(() => mod.useConnectionManager());

    await waitFor(() => expect(connectMock).toHaveBeenCalledWith('', ''));
  });

  it('honours a ?gateway= override as an explicit target', async () => {
    setSearch('?gateway=ws%3A%2F%2F127.0.0.1%3A9999%2Fws');

    const mod = await import('./useConnectionManager');
    const { result } = renderHook(() => mod.useConnectionManager());

    await waitFor(() => expect(connectMock).toHaveBeenCalledWith('ws://127.0.0.1:9999/ws', ''));
    expect(result.current.editableUrl).toBe('ws://127.0.0.1:9999/ws');
  });

  it('shows the server-reported gateway for display', async () => {
    const mod = await import('./useConnectionManager');
    const { result } = renderHook(() => mod.useConnectionManager());

    await waitFor(() => expect(result.current.officialUrl).toBe('ws://127.0.0.1:8080/ws'));
    expect(result.current.editableUrl).toBe('ws://127.0.0.1:8080/ws');
    expect(result.current.serverSideAuth).toBe(true);
    expect(result.current.authEnabled).toBe(false);
  });

  it('reconnects through the proxy default when the url still names the official gateway', async () => {
    const mod = await import('./useConnectionManager');
    const { result } = renderHook(() => mod.useConnectionManager());

    await waitFor(() => expect(result.current.officialUrl).toBeTruthy());
    connectMock.mockClear();

    await act(async () => { await result.current.handleReconnect(); });

    expect(disconnectMock).toHaveBeenCalled();
    expect(connectMock).toHaveBeenCalledWith('', '');
  });

  it('reconnects to an operator-typed url as an explicit target', async () => {
    const mod = await import('./useConnectionManager');
    const { result } = renderHook(() => mod.useConnectionManager());

    await waitFor(() => expect(result.current.officialUrl).toBeTruthy());
    connectMock.mockClear();

    act(() => { result.current.setEditableUrl('ws://127.0.0.1:9999/ws'); });
    await act(async () => { await result.current.handleReconnect(); });

    expect(connectMock).toHaveBeenCalledWith('ws://127.0.0.1:9999/ws', '');
  });
});
