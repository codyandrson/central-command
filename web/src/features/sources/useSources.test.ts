/**
 * walkNow must POST the path the Hono route actually registers
 * (/api/sources/:id/walk) and refetch both reads afterwards — a walk that
 * left the list showing the pre-walk counts is the bug this pins.
 */
import { renderHook, waitFor, act } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useSources } from './useSources';

const EMPTY = { sources: [], documents: [], ok: true, summary: { files_seen: 2 } };

describe('useSources', () => {
  let calls: { url: string; method: string }[];

  beforeEach(() => {
    calls = [];
    global.fetch = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), method: init?.method ?? 'GET' });
      return { ok: true, json: async () => EMPTY };
    }) as unknown as typeof fetch;
  });

  it('posts the walk and refreshes the list and the documents', async () => {
    const { result } = renderHook(() => useSources('docs-drive'));
    await waitFor(() => expect(result.current.loading).toBe(false));
    calls.length = 0;

    let summary;
    await act(async () => { summary = await result.current.walkNow('docs-drive'); });

    expect(summary).toEqual({ files_seen: 2 });
    expect(calls[0]).toEqual({ url: '/api/sources/docs-drive/walk', method: 'POST' });
    await waitFor(() => {
      expect(calls.some((c) => c.url === '/api/sources' && c.method === 'GET')).toBe(true);
      expect(calls.some((c) => c.url.startsWith('/api/catalog/documents?source_id=docs-drive'))).toBe(true);
    });
  });
});
