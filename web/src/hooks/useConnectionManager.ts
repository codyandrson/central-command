/**
 * useConnectionManager - Handles gateway connection lifecycle
 *
 * ZERO user input: this cockpit has exactly one gateway and the Node server
 * already knows it (GATEWAY_URL), fixed at install. On mount we connect with an
 * EMPTY url, which the /ws proxy resolves server-side. Nothing about the
 * gateway is read from or written to browser storage, and nothing asks the
 * operator for an address or a token.
 *
 * The token stays EMPTY on purpose: the proxy injects `config.gatewayToken` for
 * trusted clients only when the browser supplies none, so a placeholder would
 * suppress the injection.
 *
 * A connection that drops after it was up retries on its own (useWebSocket)
 * and shows the "Signal lost" banner; one that cannot be made at all reads
 * Disconnected in the status bar and Settings > Connection, whose Reconnect
 * button calls handleReconnect.
 *
 * Escape hatch: `?gateway=<ws-url>` on the page URL is passed through as the
 * proxy's `?target=` — still allowlist-checked server-side.
 */
import { useCallback, useEffect, useRef } from 'react';
import { useGateway } from '@/contexts/GatewayContext';

export interface ConnectionManagerState {
  handleReconnect: () => Promise<void>;
}

/** `?gateway=` override, or '' meaning "whatever the server is pointed at". */
function gatewayOverride(): string {
  if (typeof window === 'undefined') return '';
  return new URLSearchParams(window.location.search).get('gateway')?.trim() || '';
}

export function useConnectionManager(): ConnectionManagerState {
  const { connectionState, connect, disconnect } = useGateway();

  // Track if we've attempted auto-connect to avoid re-running
  const autoConnectAttempted = useRef(false);

  useEffect(() => {
    if (autoConnectAttempted.current) return;
    autoConnectAttempted.current = true;

    connect(gatewayOverride(), '').catch(() => {
      // Connect failed — useWebSocket reports it via connectError and retries.
    });
  }, [connect]);

  const handleReconnect = useCallback(async () => {
    // Don't reconnect if already connecting
    if (connectionState === 'connecting' || connectionState === 'reconnecting') {
      return;
    }

    // Reconnect exactly the way the mount did.
    disconnect();
    // Small delay to ensure clean disconnect
    await new Promise(r => setTimeout(r, 100));
    try {
      await connect(gatewayOverride(), '');
    } catch {
      // Connection failed - don't loop, just stay disconnected
    }
  }, [connect, disconnect, connectionState]);

  return { handleReconnect };
}
