import { useState, useEffect, useCallback, useRef } from 'react';
import { LayoutGrid, RefreshCw, ExternalLink, Play } from 'lucide-react';

interface SystemCredential {
  label: string;
  location: string;
}

interface SystemRow {
  id: string;
  name: string;
  kind: 'ui' | 'api' | 'store' | 'external';
  url: string | null;
  linkLabel: string | null;
  status: 'up' | 'down' | 'unknown';
  latencyMs: number | null;
  /** A short status line (the graph row's ingest queue); absent on most rows. */
  detail?: string | null;
  credential: SystemCredential;
}

type CheckStatus = 'pass' | 'warn' | 'fail' | 'skip';

interface SelfCheckCheck {
  name: string;
  status: CheckStatus;
  message: string;
  remedy: string | null;
  /** id of the Systems row this check belongs beside; null = listed above the grid. */
  system: string | null;
  durationMs: number | null;
}

interface SelfCheckDoc {
  status: CheckStatus | 'never_run';
  running: boolean;
  ranAt: string | null;
  durationMs: number | null;
  version: string | null;
  mode: 'cli' | 'api' | null;
  checks: SelfCheckCheck[];
}

const POLL_INTERVAL_MS = 30_000;
const SELFCHECK_POLL_MS = 2_000;

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

/** The application self-check. Mounting only READS the last result (GET spends
 *  nothing); a run — two of its checks spend a model request each — starts only
 *  from `run()`, i.e. the operator's button. After a run starts, poll the GET
 *  until `running` is false. */
function useSelfCheck() {
  const [doc, setDoc] = useState<SelfCheckDoc | null>(null);
  const [error, setError] = useState('');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);

  const stopPolling = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  const apply = useCallback(async (res: Response, poll: () => void) => {
    const body = await res.json() as SelfCheckDoc & { error?: string };
    if (!res.ok || body.error) throw new Error(body.error || `HTTP ${res.status}`);
    if (!alive.current) return;
    setDoc(body);
    setError('');
    if (body.running) {
      stopPolling();
      timer.current = setTimeout(poll, SELFCHECK_POLL_MS);
    }
  }, [stopPolling]);

  const load = useCallback(async () => {
    try {
      await apply(await fetch('/api/selfcheck'), load);
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : String(e));
    }
  }, [apply]);

  const run = useCallback(async () => {
    try {
      await apply(await fetch('/api/selfcheck/run', { method: 'POST' }), load);
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : String(e));
    }
  }, [apply, load]);

  useEffect(() => {
    alive.current = true;
    load();
    return () => { alive.current = false; stopPolling(); };
  }, [load, stopPolling]);

  return { doc, error, run };
}

const CHECK_TEXT: Record<CheckStatus, string> = {
  pass: 'text-green', warn: 'text-orange', fail: 'text-red', skip: 'text-muted-foreground',
};

/** An ISO instant rendered in the BROWSER's local time, 24-hour — the server
 *  sends instants and never formats them. */
export function formatLocalTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function checkTitle(k: SelfCheckCheck): string {
  return `${k.name}: ${k.message}${k.remedy ? `\nFix: ${k.remedy}` : ''}`;
}

function SelfCheckBadge({ check }: { check: SelfCheckCheck }) {
  return (
    <span
      className={`cursor-help text-[0.667rem] ${CHECK_TEXT[check.status]}`}
      title={checkTitle(check)}
      data-testid={`selfcheck-${check.name}`}
    >
      {check.name}: {check.status}
    </span>
  );
}

function SelfCheckSummary({ doc, error, onRun }: { doc: SelfCheckDoc | null; error: string; onRun: () => void }) {
  const unmapped = (doc?.checks ?? []).filter((k) => k.system === null);
  const neverRun = doc?.status === 'never_run';
  return (
    <div className="mb-3 flex flex-col gap-2 rounded-lg border border-border/40 p-3" data-testid="selfcheck-summary">
      <div className="flex flex-wrap items-center gap-2 text-[0.733rem]">
        <span className="text-[0.8rem] font-semibold text-foreground">Self-check</span>
        {!doc && !error && <span className="text-muted-foreground">Loading…</span>}
        {doc && neverRun && <span className="text-muted-foreground">not run yet</span>}
        {doc && !neverRun && (
          <>
            <span className={`font-semibold uppercase ${CHECK_TEXT[doc.status as CheckStatus]}`}>{doc.status}</span>
            {doc.ranAt && (
              <span className="tabular-nums text-muted-foreground">
                last ran {formatLocalTime(doc.ranAt)}
              </span>
            )}
          </>
        )}
        <button
          onClick={onRun}
          disabled={!doc || doc.running}
          className="ml-auto inline-flex items-center gap-1 rounded border border-border/60 px-2 py-0.5 text-[0.733rem] text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
        >
          {doc?.running
            ? <><RefreshCw size={11} className="animate-spin" /> Running…</>
            : <><Play size={11} /> Run self-check</>}
        </button>
      </div>
      {error && <p className="text-[0.733rem] text-destructive">{error}</p>}
      {unmapped.length > 0 && (
        <ul className="flex flex-col gap-0.5 text-[0.667rem]">
          {unmapped.map((k) => (
            <li key={k.name} title={checkTitle(k)}>
              <span className={`font-semibold ${CHECK_TEXT[k.status]}`}>{k.name}: {k.status}</span>
              <span className="text-muted-foreground"> — {k.message}</span>
              {k.remedy && k.status !== 'pass' && <span className="text-muted-foreground/70"> (fix: {k.remedy})</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
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

function SystemCard({ system, checks }: { system: SystemRow; checks: SelfCheckCheck[] }) {
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
        {checks.length > 0 && (
          <span className="ml-auto flex items-center gap-2" data-testid={`selfcheck-col-${system.id}`}>
            {checks.map((k) => <SelfCheckBadge key={k.name} check={k} />)}
          </span>
        )}
      </div>
      {system.url && (
        <a
          href={system.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-[0.733rem] text-primary hover:underline"
        >
          {system.linkLabel ?? 'Open'} <ExternalLink size={11} />
        </a>
      )}
      {system.detail && (
        <p className="cockpit-wrap text-[0.667rem] text-muted-foreground" data-testid={`system-detail-${system.id}`}>
          {system.detail}
        </p>
      )}
      <p className="text-[0.667rem] text-muted-foreground/70">
        credential: {system.credential.label} — {system.credential.location}
      </p>
    </div>
  );
}

export function SystemsView() {
  const { systems, error, loading, refresh } = useSystems();
  const selfCheck = useSelfCheck();

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
        <SelfCheckSummary doc={selfCheck.doc} error={selfCheck.error} onRun={selfCheck.run} />
        {error && <p className="mb-3 text-[0.733rem] text-destructive">{error}</p>}
        {!systems && !error && (
          <p className="text-[0.733rem] text-muted-foreground">Loading…</p>
        )}
        {systems && systems.length === 0 && (
          <p className="text-[0.733rem] text-muted-foreground">No systems configured.</p>
        )}
        {systems && systems.length > 0 && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {systems.map((s) => (
              <SystemCard
                key={s.id}
                system={s}
                checks={(selfCheck.doc?.checks ?? []).filter((k) => k.system === s.id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
