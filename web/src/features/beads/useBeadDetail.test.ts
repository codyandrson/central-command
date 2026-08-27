import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useBeadDetail } from './useBeadDetail';
import type { BeadLinkTarget } from './links';

type PendingRequest = {
  signal: AbortSignal;
  resolve: (value: Response) => void;
  reject: (reason?: unknown) => void;
};

describe('useBeadDetail', () => {
  const fetchMock = vi.fn<typeof fetch>();
  const pendingRequests: PendingRequest[] = [];

  beforeEach(() => {
    pendingRequests.length = 0;
    fetchMock.mockReset();
    fetchMock.mockImplementation(((_input: RequestInfo | URL, init?: RequestInit) => new Promise((resolve, reject) => {
      pendingRequests.push({
        signal: init?.signal as AbortSignal,
        resolve: resolve as (value: Response) => void,
        reject,
      });
    })) as typeof fetch);
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('aborts stale bead-detail fetches when the target changes', async () => {
    const initialTarget: BeadLinkTarget = { beadId: 'nerve-old', workspaceAgentId: 'main' };
    const nextTarget: BeadLinkTarget = { beadId: 'nerve-new', workspaceAgentId: 'main' };

    const { result, rerender } = renderHook(({ target }) => useBeadDetail(target), {
      initialProps: { target: initialTarget },
    });

    expect(result.current.loading).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(pendingRequests[0]?.signal.aborted).toBe(false);

    rerender({ target: nextTarget });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(pendingRequests[0]?.signal.aborted).toBe(true);
    expect(pendingRequests[1]?.signal.aborted).toBe(false);

    pendingRequests[0]?.reject(new DOMException('Aborted', 'AbortError'));
    pendingRequests[1]?.resolve({
      ok: true,
      json: async () => ({
        ok: true,
        bead: {
          id: 'nerve-new',
          title: 'New bead',
        },
      }),
    } as Response);

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.error).toBeNull();
      expect(result.current.bead).toEqual({
        id: 'nerve-new',
        title: 'New bead',
      });
    });
  });
});
