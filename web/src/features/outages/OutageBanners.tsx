import { useState } from 'react';
import { AlertTriangle, ChevronDown, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useOutages } from './useOutages';
import { downFor } from './duration';
import type { OutageDependency } from './types';

const KIND_LABELS: [keyof OutageDependency['waiting'], string][] = [
  ['sessions', 'sessions'], ['proposals', 'proposals'], ['tasks', 'tasks'],
  ['work_items', 'mail items'], ['ingest_jobs', 'graph ingest jobs'],
];

function Breakdown({ dep }: { dep: OutageDependency }) {
  return (
    <ul className="mt-1 text-[0.667rem] text-muted-foreground" data-testid={`outage-breakdown-${dep.name}`}>
      {KIND_LABELS.map(([key, label]) => (
        dep.waiting[key] > 0 && <li key={key}>{dep.waiting[key]} {label}</li>
      ))}
      {dep.interrupted > 0 && (
        <li>{dep.interrupted} interrupted by a restart (waiting for you)</li>
      )}
    </ul>
  );
}

function RetryButton({ name, busy, onRetry }: { name: string; busy: boolean; onRetry: () => void }) {
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={busy}
      onClick={onRetry}
      aria-label={`Retry now: ${name}`}
      title={`Probe ${name} now and retry the work waiting on it`}
    >
      <RefreshCw size={12} aria-hidden="true" className={busy ? 'animate-spin' : ''} />
      {busy ? 'Retrying…' : 'Retry now'}
    </Button>
  );
}

/**
 * One banner per DOWN dependency (its name, how long, how many items wait, and
 * Retry now), and one per dependency with work FAILING against a healthy one
 * (the loud rule: nothing is down, so nothing would otherwise say so). A
 * `dependency.up` removes the banner on the next refresh. Persistent and in the
 * page flow — unlike the transient 'Signal lost' overlay, these stay true until
 * the dependency is back.
 */
export function OutageBanners() {
  const { dependencies, results, retrying, retry } = useOutages();
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const down = dependencies.filter((d) => d.state === 'down');
  const failing = dependencies.filter((d) => d.failing.length > 0);
  if (down.length === 0 && failing.length === 0) return null;

  return (
    <div className="shrink-0" data-testid="outage-banners">
      {down.map((d) => (
        <div
          key={`down-${d.name}`}
          role="status"
          data-testid={`outage-banner-${d.name}`}
          className="flex flex-wrap items-center gap-2 bg-destructive/12 px-4 py-1.5 text-[0.733rem] font-semibold text-destructive"
        >
          <AlertTriangle size={13} aria-hidden="true" />
          <span>{d.name} down for {downFor(d.down_since)}</span>
          <button
            type="button"
            className="inline-flex items-center gap-1 font-normal text-foreground/80 underline decoration-dotted underline-offset-2"
            aria-expanded={!!open[d.name]}
            onClick={() => setOpen((o) => ({ ...o, [d.name]: !o[d.name] }))}
          >
            {d.total} {d.total === 1 ? 'item' : 'items'} waiting
            <ChevronDown size={11} aria-hidden="true" className={open[d.name] ? 'rotate-180' : ''} />
          </button>
          <span className="ml-auto flex items-center gap-2">
            {results[d.name] && (
              <span className="font-normal text-foreground/80" data-testid={`outage-result-${d.name}`}>
                {results[d.name]}
              </span>
            )}
            <RetryButton name={d.name} busy={retrying === d.name} onRetry={() => void retry(d.name)} />
          </span>
          {open[d.name] && <div className="basis-full"><Breakdown dep={d} /></div>}
        </div>
      ))}
      {failing.map((d) => (
        <div
          key={`failing-${d.name}`}
          role="status"
          data-testid={`failing-banner-${d.name}`}
          className="flex flex-wrap items-center gap-2 bg-orange/20 px-4 py-1.5 text-[0.733rem] font-semibold text-orange"
        >
          <AlertTriangle size={13} aria-hidden="true" />
          <span>
            {d.failing.length} {d.failing.length === 1 ? 'item keeps' : 'items keep'} failing against a
            healthy {d.name}{d.failing[0].last_error ? `: ${d.failing[0].last_error}` : ''}
          </span>
          <span className="ml-auto flex items-center gap-2">
            {results[d.name] && !down.some((x) => x.name === d.name) && (
              <span className="font-normal text-foreground/80" data-testid={`failing-result-${d.name}`}>
                {results[d.name]}
              </span>
            )}
            <RetryButton name={d.name} busy={retrying === d.name} onRetry={() => void retry(d.name)} />
          </span>
        </div>
      ))}
    </div>
  );
}
