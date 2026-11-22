import { RefreshCw } from 'lucide-react';
import { GatewayRestartCard } from './GatewayRestartCard';

interface ConnectionSettingsProps {
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

/** Settings section for gateway status, reconnection, and gateway restart. The
 *  gateway is fixed at install (GATEWAY_URL); there is nothing to enter here. */
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

      {/* Gateway Service: restarts the API process through the root updater
          (k3s profile); the card disables itself, with the reason, elsewhere. */}
      <div className="cockpit-divider my-2" />
      <GatewayRestartCard />
    </div>
  );
}
