/**
 * useConnectionManager - Handles gateway connection lifecycle
 *
 * ZERO user input: this cockpit has exactly one gateway and the Node server
 * already knows it (GATEWAY_URL). On mount we connect with an EMPTY url, which
 * the /ws proxy resolves server-side. Nothing about the gateway is read from or
 * written to browser storage — a stale `oc-config` is how the old connect form
 * kept pointing at a gateway that had moved.
 *
 * The token stays EMPTY on purpose: the proxy injects `config.gatewayToken` for
 * trusted clients only when the browser supplies none, so a placeholder would
 * suppress the injection.
 *
 * Escape hatch: `?gateway=<ws-url>` on the page URL is passed through as the
 * proxy's `?target=` — still allowlist-checked server-side.
 */
import { useState, useCallback, useEffect, useRef } from 'react';
import { useGateway } from '@/contexts/GatewayContext';
import { areGatewayUrlsEquivalent } from '@/lib/gatewayUrls';

export interface ConnectionManagerState {
  dialogOpen: boolean;
  setDialogOpen: (open: boolean) => void;
  editableUrl: string;
  setEditableUrl: (url: string) => void;
  officialUrl: string | null;
  editableToken: string;
  setEditableToken: (token: string) => void;
  handleConnect: (url: string, token: string) => Promise<void>;
  handleReconnect: () => Promise<void>;
  serverSideAuth: boolean;
  authEnabled: boolean;
}

/** `?gateway=` override, or '' meaning "whatever the server is pointed at". */
function gatewayOverride(): string {
  if (typeof window === 'undefined') return '';
  return new URLSearchParams(window.location.search).get('gateway')?.trim() || '';
}

/** Create an AbortSignal that times out after `ms` milliseconds. */
function timeoutSignal(ms: number): AbortSignal {
  // AbortSignal.timeout() not supported in Safari <16.4
  if (typeof AbortSignal.timeout === 'function') return AbortSignal.timeout(ms);
  const controller = new AbortController();
  setTimeout(() => controller.abort(), ms);
  return controller.signal;
}

/** Fetch gateway connection defaults from the Nerve server (DISPLAY only). */
async function fetchConnectDefaults(): Promise<{ wsUrl: string; token: string | null; authEnabled?: boolean; serverSideAuth?: boolean } | null> {
  try {
    const resp = await fetch('/api/connect-defaults', { signal: timeoutSignal(3000) });
    if (!resp.ok) return null;
    return await resp.json();
  } catch {
    return null;
  }
}

export function useConnectionManager(): ConnectionManagerState {
  const { connectionState, connect, disconnect } = useGateway();

  // The connect form is not a gate any more — only handleReconnect's
  // no-target-to-fall-back-to branch can still raise it.
  const [dialogOpen, setDialogOpen] = useState(false);

  const [editableUrl, setEditableUrl] = useState(gatewayOverride);
  const [editableToken, setEditableToken] = useState('');
  const [serverSideAuth, setServerSideAuth] = useState(false);
  const [authEnabled, setAuthEnabled] = useState(true);
  const [officialUrl, setOfficialUrl] = useState<string | null>(null);

  // Track if we've attempted auto-connect to avoid re-running
  const autoConnectAttempted = useRef(false);

  const handleConnect = useCallback(async (url: string, token: string) => {
    await connect(url, token);
    setDialogOpen(false);
  }, [connect]);

  useEffect(() => {
    if (autoConnectAttempted.current) return;
    autoConnectAttempted.current = true;

    handleConnect(gatewayOverride(), '').catch(() => {
      // Connect failed — useWebSocket reports it via connectError and retries.
    });

    // Display only: what the settings drawer shows as the current gateway.
    fetchConnectDefaults().then((defaults) => {
      if (!defaults) return;
      setServerSideAuth(defaults.serverSideAuth ?? false);
      setAuthEnabled(defaults.authEnabled ?? true);
      const officialWsUrl = defaults.wsUrl?.trim();
      if (officialWsUrl) {
        setOfficialUrl(officialWsUrl);
        if (!gatewayOverride()) setEditableUrl(officialWsUrl);
      }
    });
  }, [handleConnect]);

  const handleReconnect = useCallback(async () => {
    // Don't reconnect if already connecting
    if (connectionState === 'connecting' || connectionState === 'reconnecting') {
      return;
    }

    // An operator-typed URL that differs from the server's own gateway is the
    // only case that needs an explicit target; otherwise reconnect exactly the
    // way the mount did.
    const typed = editableUrl.trim();
    const target = typed && !areGatewayUrlsEquivalent(typed, officialUrl)
      ? typed
      : gatewayOverride();

    disconnect();
    // Small delay to ensure clean disconnect
    await new Promise(r => setTimeout(r, 100));
    try {
      await connect(target, editableToken);
    } catch {
      // Connection failed - don't loop, just stay disconnected
    }
  }, [connect, disconnect, editableUrl, editableToken, connectionState, officialUrl]);

  return {
    dialogOpen,
    setDialogOpen,
    editableUrl,
    setEditableUrl,
    officialUrl,
    editableToken,
    setEditableToken,
    handleConnect,
    handleReconnect,
    serverSideAuth,
    authEnabled,
  };
}
