/**
 * useLoe — data hook for the Goals screen. One read, refetched every 60s
 * (same cadence as Skills/Activity), plus a manual refresh.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/apiFetch';
import type { Loe } from './types';

const POLL_MS = 60_000;

export function useLoe() {
  const [loes, setLoes] = useState<Loe[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const first = useRef(true);

  const refresh = useCallback(async () => {
    if (first.current) setLoading(true);
    try {
      const { result } = await apiFetch<{ result: { loes: Loe[] } }>('/api/loe?history=50');
      setLoes(result.loes ?? []);
      setError('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      first.current = false;
    }
  }, []);

  useEffect(() => {
    refresh();
    const iv = setInterval(() => {
      if (document.visibilityState === 'visible') refresh();
    }, POLL_MS);
    return () => clearInterval(iv);
  }, [refresh]);

  return { loes, loading, error, refresh };
}
