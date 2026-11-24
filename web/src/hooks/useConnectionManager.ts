/**
 * useConnectionManager - Handles gateway connection lifecycle
 *
 * ZERO user input: this cockpit has exactly one gateway and the Node server
 * already knows it (GATEWAY_URL), fixed at install. On mount we connect to the
 * /ws proxy, which dials that gateway. Nothing about the gateway is read from
 * or written to browser storage or the page URL, and nothing asks the operator
 * for an address or a token.
 *
 * A connection that drops after it was up retries on its own (useWebSocket)
 * and shows the "Signal lost" banner; one that cannot be made at all reads
 * Disconnected in the status bar and Settings > Connection, whose Reconnect
 * button calls handleReconnect.
 */
import { useCallback, useEffect, useRef } from 'react';
import { useGateway } from '@/contexts/GatewayContext';

export interface ConnectionManagerState {
  handleReconnect: () => Promise<void>;
}

export function useConnectionManager(): ConnectionManagerState {
  const { connectionState, connect, disconnect } = useGateway();

  // Track if we've attempted auto-connect to avoid re-running
  const autoConnectAttempted = useRef(false);

  useEffect(() => {
    if (autoConnectAttempted.current) return;
    autoConnectAttempted.current = true;

    connect().catch(() => {
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
      await connect();
    } catch {
      // Connection failed - don't loop, just stay disconnected
    }
  }, [connect, disconnect, connectionState]);

  return { handleReconnect };
}
