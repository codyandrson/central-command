import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

describe('testGatewayConnection', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('passes only when an authenticated gateway route accepts the token', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ status: 'ok' }))
      .mockResolvedValueOnce(jsonResponse({ ok: true, result: { session: 'main' } }));

    vi.stubGlobal('fetch', fetchMock);

    const { testGatewayConnection } = await import('./validators.js');
    const result = await testGatewayConnection('http://127.0.0.1:18789', 'real-token');

    expect(result.ok).toBe(true);
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://127.0.0.1:18789/tools/invoke',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer real-token',
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({ tool: 'sessions_list', args: { limit: 1 } }),
      }),
    );
  });

  it('fails when the auth token is wrong even if /health is healthy', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ status: 'ok' }))
      .mockResolvedValueOnce(new Response('unauthorized', { status: 401 }));

    vi.stubGlobal('fetch', fetchMock);

    const { testGatewayConnection } = await import('./validators.js');
    const result = await testGatewayConnection('http://127.0.0.1:18789', 'wrong-token');

    expect(result.ok).toBe(false);
    expect(result.message.toLowerCase()).toContain('token');
    expect(result.message.toLowerCase()).toContain('reject');
  });

  it('does not treat /health alone as sufficient auth validation when token correctness is being claimed', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ status: 'ok' }))
      .mockResolvedValueOnce(new Response('not found', { status: 404 }));

    vi.stubGlobal('fetch', fetchMock);

    const { testGatewayConnection } = await import('./validators.js');
    const result = await testGatewayConnection('http://127.0.0.1:18789', 'maybe-token');

    expect(result.ok).toBe(false);
    expect(result.message.toLowerCase()).toContain('auth');
    expect(result.message.toLowerCase()).toContain('confirm');
  });
});
