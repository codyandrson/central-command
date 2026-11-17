/**
 * useDashboardData - Handles dashboard data fetching (tokens) and routes
 * file.changed events to the app.
 * 
 * Extracted from App.tsx to separate data fetching concerns from layout.
 * Includes AbortController support for proper cleanup on unmount.
 * 
 * Real-time updates via:
 * 1. SSE (Server-Sent Events) - for local file/API changes
 * 2. WebSocket (Gateway) - for gateway token events
 *
 * What an agent has learned (the workspace Memory tab) is not fetched here:
 * that tab owns its reads (`workspace/hooks/useAgentLearned`).
 * 
 * Polling is now much slower, serving only as a safety net.
 */
import { useState, useCallback, useEffect, useRef } from 'react';
import { useGateway } from '@/contexts/GatewayContext';
import { useServerEvents, type ServerEvent } from '@/hooks/useServerEvents';
import type { TokenData, GatewayEvent } from '@/types';

// Polling intervals - much longer now since SSE/WS provide real-time updates
// These are just safety nets for edge cases
const TOKEN_POLL_INTERVAL = 60000;  // 60s fallback (was 30s)

export type FileChangedHandler = (path: string, agentId: string) => void;

export interface DashboardDataOptions {
  agentId?: string;
  /** Called when a file.changed SSE event arrives */
  onFileChanged?: FileChangedHandler;
}

export interface DashboardDataState {
  tokenData: TokenData | null;
  refreshTokens: (signal?: AbortSignal) => Promise<void>;
}

interface FileChangedEventData {
  path?: string;
  agentId?: string;
}

export function useDashboardData(options: DashboardDataOptions = {}): DashboardDataState {
  const { subscribe, connectionState } = useGateway();
  const activeAgentId = options.agentId ?? 'main';
  const [tokenData, setTokenData] = useState<TokenData | null>(null);

  // Refs for callbacks (to avoid stale closures in subscriptions)
  const refreshTokensRef = useRef<((signal?: AbortSignal) => Promise<void>) | undefined>(undefined);
  const onFileChangedRef = useRef(options.onFileChanged);
  const agentIdRef = useRef(activeAgentId);

  const refreshTokens = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await fetch('/api/tokens', { signal });
      if (!signal?.aborted && res.ok) {
        setTokenData(await res.json());
      }
    } catch (err) {
      if (err instanceof Error && err.name !== 'AbortError') {
        console.debug('[DashboardData] Failed to refresh tokens:', err.message);
      }
    }
  }, []);

  // These refs back long-lived SSE/WS handlers and must update during render.
  // Waiting for a passive effect leaves a switch-gap where an old agent event can
  // be accepted under stale refs after the UI already rerendered.
  refreshTokensRef.current = refreshTokens;
  onFileChangedRef.current = options.onFileChanged;
  agentIdRef.current = activeAgentId;

  // SSE event handler for real-time updates from backend
  const handleSSEEvent = useCallback((event: ServerEvent) => {
    if (event.event === 'file.changed') {
      const data = event.data as FileChangedEventData | undefined;
      const eventAgentId = typeof data?.agentId === 'string' ? data.agentId : undefined;

      if (data?.path && eventAgentId === agentIdRef.current) {
        onFileChangedRef.current?.(data.path, eventAgentId);
      }
    }
  }, []);

  // Subscribe to SSE events from the backend
  // This catches local file changes and API-triggered updates
  useServerEvents(handleSSEEvent);

  // Subscribe to WebSocket events for real-time token updates
  useEffect(() => {
    if (connectionState !== 'connected') return;
    
    return subscribe((msg: GatewayEvent) => {
      const evt = msg.event;

      // Also trigger token refresh on chat.final events (response completed = tokens used)
      // This ensures token counts update after each agent response
      const payload = msg.payload as Record<string, unknown> | undefined;
      if (evt === 'chat' && payload?.state === 'final') {
        // Small delay to allow gateway to update token counts
        setTimeout(() => refreshTokensRef.current?.(), 500);
      }
    });
  }, [subscribe, connectionState]);

  // Initial fetch and polling
  useEffect(() => {
    const controller = new AbortController();
    
    // Initial fetch
    refreshTokens(controller.signal);

    // Polling as safety net — SSE/WS provide real-time updates
    const tokIv = setInterval(() => refreshTokens(controller.signal), TOKEN_POLL_INTERVAL);

    return () => {
      controller.abort();
      clearInterval(tokIv);
    };
  }, [refreshTokens]);

  return {
    tokenData,
    refreshTokens,
  };
}
