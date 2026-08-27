/**
 * useGraph — data hook for the cockpit Graph panel.
 *
 * Talks to the cc-graph Hono proxy (`/api/graph/*`), which forwards to the
 * Central Command API's read-only bolt reader. Explore-only, incremental: this
 * hook never fetches "the whole graph" — only search results and one-hop
 * neighborhoods the operator asked for. Every method degrades to an `error`
 * string on failure rather than throwing, so the view can render a fallback
 * instead of crashing.
 */
import { useState, useCallback } from 'react';
import { apiFetch } from '@/lib/apiFetch';

export interface GraphNode {
  uuid: string;
  name: string;
  labels: string[];
  summary: string;
  group_id: string;
}

export interface GraphEdge {
  uuid: string;
  source: string;
  target: string;
  name: string;
  fact: string;
  valid_at: string | null;
  invalid_at: string | null;
  created_at: string;
  invalid: boolean;
}

export interface GraphEpisode {
  uuid: string;
  name: string;
  source_description: string;
  valid_at: string | null;
  content_preview: string;
}

export interface GraphStatus {
  available: boolean;
  node_count: number;
  edge_count: number;
  group_ids: string[];
  graphistry: 'disabled' | 'unreachable' | 'live';
  graphistry_enabled: boolean;
  graphistry_url: string;
}

/** Every `/api/graph/*` GET/PUT response is `{ ok, result }` — fetch and unwrap it. */
async function fetchResult<T>(url: string, init?: RequestInit): Promise<T> {
  const { result } = await apiFetch<{ result: T }>(url, init);
  return result;
}

export function useGraph() {
  const [status, setStatus] = useState<GraphStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const result = await fetchResult<GraphStatus>('/api/graph/status');
      setStatus(result);
      setError(null);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return null;
    }
  }, []);

  const search = useCallback(async (q: string, groupId?: string, limit = 25): Promise<GraphNode[]> => {
    try {
      const params = new URLSearchParams({ q, limit: String(limit) });
      if (groupId) params.set('group_id', groupId);
      const result = await fetchResult<{ nodes: GraphNode[] }>(`/api/graph/search?${params.toString()}`);
      setError(null);
      return result.nodes;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return [];
    }
  }, []);

  const neighborhood = useCallback(async (
    uuid: string, at?: string, limit = 50,
  ): Promise<{ nodes: GraphNode[]; edges: GraphEdge[] }> => {
    try {
      const params = new URLSearchParams({ uuid, limit: String(limit) });
      if (at) params.set('at', at);
      const result = await fetchResult<{ nodes: GraphNode[]; edges: GraphEdge[] }>(`/api/graph/neighborhood?${params.toString()}`);
      setError(null);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return { nodes: [], edges: [] };
    }
  }, []);

  const loadAll = useCallback(async (
    groupId?: string, at?: string, limit = 2000,
  ): Promise<{ nodes: GraphNode[]; edges: GraphEdge[]; truncated: boolean }> => {
    try {
      const params = new URLSearchParams({ limit: String(limit) });
      if (groupId) params.set('group_id', groupId);
      if (at) params.set('at', at);
      const result = await fetchResult<{ nodes: GraphNode[]; edges: GraphEdge[]; truncated: boolean }>(`/api/graph/all?${params.toString()}`);
      setError(null);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return { nodes: [], edges: [], truncated: false };
    }
  }, []);

  const provenance = useCallback(async (uuid: string): Promise<GraphEpisode[]> => {
    try {
      const params = new URLSearchParams({ uuid });
      const result = await fetchResult<{ episodes: GraphEpisode[] }>(`/api/graph/provenance?${params.toString()}`);
      setError(null);
      return result.episodes;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return [];
    }
  }, []);

  const saveSettings = useCallback(async (
    patch: { graphistry_enabled?: boolean; graphistry_url?: string },
  ): Promise<GraphStatus | null> => {
    try {
      const result = await fetchResult<GraphStatus>('/api/graph/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      setStatus(result);
      setError(null);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return null;
    }
  }, []);

  // --- Curation writes (2026-08-15) ---------------------------------------
  // These THROW rather than swallowing into `error` like the reads above: a
  // failed read degrades to an empty panel, but a failed edit must reach the
  // form that submitted it, or the operator sees their correction vanish and
  // assumes it landed. The caller decides what to show.
  const write = useCallback(<T,>(
    path: string, method: 'POST' | 'PATCH' | 'DELETE', body?: unknown,
  ): Promise<T> => fetchResult<T>(path, {
    method,
    ...(body === undefined ? {} : {
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  }), []);

  const entityTypes = useCallback(async (): Promise<string[]> => {
    try {
      return (await fetchResult<{ entity_types: string[] }>('/api/graph/entity-types')).entity_types;
    } catch {
      return [];
    }
  }, []);

  const createNode = useCallback((body: {
    name: string; labels: string[]; summary: string; group_id: string;
  }) => write<{ uuid: string; embedded: boolean }>('/api/graph/node', 'POST', body), [write]);

  const updateNode = useCallback((body: {
    uuid: string; name?: string; summary?: string; labels?: string[];
  }) => write<{ uuid: string }>('/api/graph/node', 'PATCH', body), [write]);

  const deleteNode = useCallback((uuid: string) => write<{ uuid: string; edges_removed: number }>(
    `/api/graph/node?uuid=${encodeURIComponent(uuid)}`, 'DELETE'), [write]);

  const createEdge = useCallback((body: {
    source_uuid: string; target_uuid: string; name: string; fact: string;
  }) => write<{ uuid: string }>('/api/graph/edge', 'POST', body), [write]);

  const updateEdge = useCallback((body: {
    uuid: string; name?: string; fact?: string;
    source_uuid?: string; target_uuid?: string; clear_invalid?: boolean;
    valid_at?: string; invalid_at?: string; clear_valid?: boolean;
  }) => write<{ uuid: string }>('/api/graph/edge', 'PATCH', body), [write]);

  const deleteEdge = useCallback((uuid: string) => write<{ uuid: string }>(
    `/api/graph/edge?uuid=${encodeURIComponent(uuid)}`, 'DELETE'), [write]);

  const mergeNodes = useCallback((keep_uuid: string, drop_uuid: string) => write<{
    uuid: string; edges_moved: number; edges_discarded: number;
  }>('/api/graph/merge', 'POST', { keep_uuid, drop_uuid }), [write]);

  return {
    status, error, fetchStatus, search, neighborhood, provenance, saveSettings, loadAll,
    entityTypes, createNode, updateNode, deleteNode,
    createEdge, updateEdge, deleteEdge, mergeNodes,
  };
}
