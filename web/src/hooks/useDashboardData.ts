/**
 * useDashboardData - Handles dashboard data fetching (tokens).
 *
 * Extracted from App.tsx to separate data fetching concerns from layout.
 * Includes AbortController support for proper cleanup on unmount.
 *
 * Real-time updates ride the gateway WebSocket (the /ws relay): a chat
 * `final` frame triggers a token refresh.
 *
 * What an agent has learned (the workspace Memory tab) is not fetched here:
 * that tab owns its reads (`workspace/hooks/useAgentLearned`).
 * 
 * Polling is now much slower, serving only as a safety net.
 */
import { useState, useCallback, useEffect, useRef } from 'react';
import { useGateway } from '@/contexts/GatewayContext';
import type { TokenData, GatewayEvent } from '@/types';

// Polling interval - long, since the WS frames provide real-time updates;
// a safety net for edge cases
const TOKEN_POLL_INTERVAL = 60000;  // 60s fallback (was 30s)

export interface DashboardDataState {
  tokenData: TokenData | null;
  refreshTokens: (signal?: AbortSignal) => Promise<void>;
}

export function useDashboardData(): DashboardDataState {
  const { subscribe, connectionState } = useGateway();
  const [tokenData, setTokenData] = useState<TokenData | null>(null);

  // Refs for callbacks (to avoid stale closures in subscriptions)
  const refreshTokensRef = useRef<((signal?: AbortSignal) => Promise<void>) | undefined>(undefined);

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

  // This ref backs the long-lived WS handler and must update during render.
  refreshTokensRef.current = refreshTokens;

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

    // Polling as safety net — WS frames provide real-time updates
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
