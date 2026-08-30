/**
 * useSources — data hook for the Sources screen. Two reads: the source list,
 * and the catalog documents of whichever source is selected. Polled on the
 * same 30s/visible cadence the sibling screens use.
 *
 * Mutations refresh fire-and-forget (useDecisions' documented pattern): the
 * action is DONE when the backend has recorded it; the list catching up is
 * bookkeeping, and awaiting it just holds the button.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/apiFetch';
import type { Source, CatalogDocument, WalkSummary } from './types';

const POLL_MS = 30_000;

export function useSources(selectedId: string | null) {
  const [sources, setSources] = useState<Source[]>([]);
  const [documents, setDocuments] = useState<CatalogDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const first = useRef(true);

  const refresh = useCallback(async () => {
    if (first.current) setLoading(true);
    try {
      const res = await apiFetch<{ sources: Source[] }>('/api/sources');
      setSources(res.sources ?? []);
      setError('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      first.current = false;
    }
  }, []);

  const refreshDocuments = useCallback(async () => {
    if (!selectedId) { setDocuments([]); return; }
    try {
      const res = await apiFetch<{ documents: CatalogDocument[] }>(
        `/api/catalog/documents?source_id=${encodeURIComponent(selectedId)}`,
      );
      setDocuments(res.documents ?? []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [selectedId]);

  useEffect(() => {
    refresh();
    const iv = setInterval(() => {
      if (document.visibilityState === 'visible') refresh();
    }, POLL_MS);
    return () => clearInterval(iv);
  }, [refresh]);

  useEffect(() => { refreshDocuments(); }, [refreshDocuments]);

  const post = useCallback(async <T,>(path: string, body?: unknown): Promise<T> => {
    return apiFetch<T>(path, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    });
  }, []);

  const addOrUpdateSource = useCallback(async (source: {
    id: string; name: string; config: Record<string, unknown>; enabled: boolean;
  }) => {
    await post('/api/sources', { ...source, kind: 'filesystem' });
    refresh();
  }, [post, refresh]);

  const setEnabled = useCallback(async (id: string, enabled: boolean) => {
    await post(`/api/sources/${encodeURIComponent(id)}/enable`, { enabled });
    refresh();
  }, [post, refresh]);

  const walkNow = useCallback(async (id: string): Promise<WalkSummary> => {
    const res = await post<{ summary: WalkSummary }>(
      `/api/sources/${encodeURIComponent(id)}/walk`,
    );
    refresh();
    refreshDocuments();
    return res.summary;
  }, [post, refresh, refreshDocuments]);

  const rescind = useCallback(async (documentId: string, reason: string) => {
    await post(`/api/catalog/documents/${encodeURIComponent(documentId)}/rescind`, { reason });
    refreshDocuments();
    refresh();
  }, [post, refresh, refreshDocuments]);

  const reinstate = useCallback(async (documentId: string) => {
    await post(`/api/catalog/documents/${encodeURIComponent(documentId)}/reinstate`);
    refreshDocuments();
    refresh();
  }, [post, refresh, refreshDocuments]);

  return {
    sources, documents, loading, error, refresh,
    addOrUpdateSource, setEnabled, walkNow, rescind, reinstate,
  };
}
