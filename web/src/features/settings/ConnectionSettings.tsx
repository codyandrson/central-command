import { RefreshCw, RotateCw } from 'lucide-react';

interface ConnectionSettingsProps {
  url: string;
  token: string;
  onUrlChange: (url: string) => void;
  onTokenChange: (token: string) => void;
  onReconnect: () => void;
  connectionState: 'disconnected' | 'connecting' | 'connected' | 'reconnecting';
}

const STATUS_COLORS: Record<string, string> = {
  connected: 'bg-green',
  connecting: 'bg-orange animate-pulse',
  reconnecting: 'bg-orange animate-pulse',
  disconnected: 'bg-red',
};

const STATUS_LABELS: Record<string, string> = {
  connected: 'Connected',
  connecting: 'Connecting...',
  reconnecting: 'Reconnecting...',
  disconnected: 'Disconnected',
};

/** Settings section for gateway URL, auth token, reconnection, and gateway restart. */
export function ConnectionSettings({
  onReconnect,
  connectionState,
}: ConnectionSettingsProps) {
  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <span className="cockpit-kicker">
          <span className="text-primary">◆</span>
          Gateway
        </span>
      </div>

      {/* Status indicator */}
      <div className="cockpit-row">
        <div className="flex min-w-0 items-center gap-3">
          <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${STATUS_COLORS[connectionState]}`} />
          <div className="min-w-0">
            <p className="text-sm font-medium text-foreground">Gateway status</p>
            <p className="text-xs text-muted-foreground">{STATUS_LABELS[connectionState]}</p>
          </div>
        </div>
        <button
          onClick={onReconnect}
          disabled={connectionState === 'connecting' || connectionState === 'reconnecting'}
          className="cockpit-toolbar-button w-full justify-center sm:ml-auto sm:w-auto"
          title="Reconnect to gateway"
        >
          <RefreshCw size={14} className={connectionState === 'reconnecting' ? 'animate-spin' : ''} />
          <span className="hidden sm:inline">Reconnect</span>
        </button>
      </div>

      {/* Gateway Service — under construction: the vendored Nerve route shells
          out to an `openclaw` binary that does not exist in this deployment
          (the gateway is cc-uvicorn/FastAPI). Disabled (not hidden) until a
          real restart seam exists. */}
      <div className="cockpit-divider my-2" />
      <div className="cockpit-row">
        <div className="min-w-0 flex-1">
          <span className="cockpit-kicker text-[0.6rem]">
            <span className="text-primary">◆</span>
            Gateway Service 🚧
          </span>
          <p className="mt-2 text-sm font-medium text-foreground">Restart the local gateway</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Under construction — not wired to this deployment&apos;s gateway yet.
          </p>
        </div>
        <button
          type="button"
          disabled
          className="cockpit-toolbar-button w-full justify-center sm:w-auto disabled:opacity-50 disabled:cursor-not-allowed"
          title="Restart 🚧 under construction — no restart seam for cc-uvicorn yet"
          aria-label="Restart the local gateway (under construction)"
        >
          <RotateCw size={14} aria-hidden="true" />
          Restart
        </button>
      </div>
    </div>
  );
}
