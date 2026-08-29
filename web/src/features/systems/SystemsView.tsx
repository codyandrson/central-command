import { useState, useEffect, useCallback } from 'react';
import { LayoutGrid, RefreshCw, ExternalLink } from 'lucide-react';

interface SystemCredential {
  label: string;
  location: string;
}

interface SystemRow {
  id: string;
  name: string;
  kind: 'ui' | 'api' | 'store' | 'external';
  url: string | null;
  status: 'up' | 'down' | 'unknown';
  latencyMs: number | null;
  credential: SystemCredential;
}

const POLL_INTERVAL_MS = 30_000;

/** Poll GET /api/systems every 30s. Same shape as useLimits: keep the last
 *  good payload on a transient fetch failure rather than blanking the grid. */
function useSystems() {
  const [systems, setSystems] = useState<SystemRow[] | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/systems');
      const body = await res.json() as { systems?: SystemRow[]; error?: string };
      if (!res.ok || body.error) throw new Error(body.error || `HTTP ${res.status}`);
      setSystems(body.systems ?? []);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [load]);

  return { systems, error, loading, refresh: load };
}

const STATUS_STYLE: Record<SystemRow['status'], string> = {
  up: 'bg-green',
  down: 'bg-red',
  unknown: 'bg-muted-foreground/40',
};

const STATUS_LABEL: Record<SystemRow['status'], string> = {
  up: 'up', down: 'down', unknown: 'unknown',
};

const KIND_LABEL: Record<SystemRow['kind'], string> = {
  ui: 'UI', api: 'API', store: 'Store', external: 'External',
};

function SystemCard({ system }: { system: SystemRow }) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border/40 p-3">
      <div className="flex items-center gap-2">
        <span
          className={`h-2 w-2 shrink-0 rounded-full ${STATUS_STYLE[system.status]}`}
          title={STATUS_LABEL[system.status]}
        />
        <span className="text-[0.8rem] font-semibold text-foreground">{system.name}</span>
        <span className="cockpit-badge ml-auto">{KIND_LABEL[system.kind]}</span>
      </div>
      <div className="flex items-center gap-2 text-[0.667rem] text-muted-foreground">
        <span className="capitalize">{STATUS_LABEL[system.status]}</span>
        {system.latencyMs != null && <span className="tabular-nums">{system.latencyMs}ms</span>}
      </div>
      {system.url && (
        <a
          href={system.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-[0.733rem] text-primary hover:underline"
        >
          Open <ExternalLink size={11} />
        </a>
      )}
      <p className="text-[0.667rem] text-muted-foreground/70">
        credential: {system.credential.label} — {system.credential.location}
      </p>
    </div>
  );
}

export function SystemsView() {
  const { systems, error, loading, refresh } = useSystems();

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-border/40 px-4 py-3">
        <LayoutGrid size={14} className="text-primary" />
        <span className="text-[0.8rem] font-semibold uppercase tracking-[0.14em] text-foreground">Systems</span>
        {systems && <span className="cockpit-badge tabular-nums">{systems.length}</span>}
        <button
          onClick={() => refresh()}
          title="Refresh"
          aria-label="Refresh systems"
          className="ml-auto text-muted-foreground transition-colors hover:text-foreground"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {error && <p className="mb-3 text-[0.733rem] text-destructive">{error}</p>}
        {!systems && !error && (
          <p className="text-[0.733rem] text-muted-foreground">Loading…</p>
        )}
        {systems && systems.length === 0 && (
          <p className="text-[0.733rem] text-muted-foreground">No systems configured.</p>
        )}
        {systems && systems.length > 0 && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {systems.map((s) => <SystemCard key={s.id} system={s} />)}
          </div>
        )}
      </div>
    </div>
  );
}
