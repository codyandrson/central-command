import { useEffect, useState } from 'react';
import type { BeadDetail } from './types';
import type { BeadLinkTarget } from './links';

interface UseBeadDetailState {
  bead: BeadDetail | null;
  loading: boolean;
  error: string | null;
}

interface UseBeadDetailFetchState {
  bead: BeadDetail | null;
  error: string | null;
  requestKey: string | null;
}

export function useBeadDetail(target: BeadLinkTarget): UseBeadDetailState {
  const [state, setState] = useState<UseBeadDetailFetchState>({ bead: null, error: null, requestKey: null });

  const params = new URLSearchParams();
  if (target.explicitTargetPath) {
    params.set('targetPath', target.explicitTargetPath);
  }
  if (target.currentDocumentPath) {
    params.set('currentDocumentPath', target.currentDocumentPath);
  }
  if (target.workspaceAgentId) {
    params.set('workspaceAgentId', target.workspaceAgentId);
  }

  const suffix = params.toString() ? `?${params.toString()}` : '';
  const requestKey = `${target.beadId}${suffix}`;

  useEffect(() => {
    const controller = new AbortController();

    void fetch(`/api/beads/${encodeURIComponent(target.beadId)}${suffix}`, {
      signal: controller.signal,
    })
      .then(async (res) => {
        const data = await res.json().catch(() => null) as {
          ok?: boolean;
          bead?: BeadDetail;
          details?: string;
          error?: string;
        } | null;

        if (controller.signal.aborted) return;

        if (!res.ok || !data?.ok || !data.bead) {
          setState({
            bead: null,
            error: data?.details || data?.error || 'Failed to load bead',
            requestKey,
          });
          return;
        }

        setState({ bead: data.bead, error: null, requestKey });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setState({ bead: null, error: 'Network error', requestKey });
      });

    return () => {
      controller.abort();
    };
  }, [requestKey, suffix, target.beadId]);

  const loading = state.requestKey !== requestKey;

  return {
    bead: loading ? null : state.bead,
    loading,
    error: loading ? null : state.error,
  };
}
