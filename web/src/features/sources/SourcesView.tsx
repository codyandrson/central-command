/**
 * SourcesView — Sources: the document catalog's registered sources plus the
 * email feed's visibility row (2026-08-23 sources spec, Decision 2).
 *
 * List + detail. The email row is read-only by ratified default: it is
 * synthesized from `.env` at read time, so the panel shows it and does not
 * offer to change it.
 *
 * Plain responsive grid, no `@container`/`@3xl:` split — see the CLAUDE.md
 * bite mark on container queries resolving against an ANCESTOR, never the
 * element's own; a flat grid has no such trap.
 */
import { useState } from 'react';
import { RefreshCw, FolderTree, Mail, Play } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { timeAgo } from '@/lib/formatting';
import { useSources } from './useSources';
import type { Source, CatalogDocument, WalkSummary } from './types';

function counts(s: Source): string {
  if (s.feed_overview) {
    const f = s.feed_overview;
    return `${f.enrolled} enrolled · ${f.pending} pending · ${f.processed} processed · ${f.failed} failed`;
  }
  const o = s.overview;
  if (!o) return '—';
  return `${o.documents} docs · ${o.versions} versions · ${o.unenrolled} unenrolled · ${o.missing} missing · ${o.rescinded} rescinded`;
}

function ConfigTable({ config }: { config: Record<string, unknown> }) {
  const entries = Object.entries(config);
  if (entries.length === 0) return <p className="text-xs text-muted-foreground">no configuration</p>;
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
      {entries.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="break-all font-mono">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function DocumentsTable({
  documents, onRescind, onReinstate,
}: {
  documents: CatalogDocument[];
  onRescind: (d: CatalogDocument) => void;
  onReinstate: (d: CatalogDocument) => void;
}) {
  if (documents.length === 0) {
    return <p className="text-xs text-muted-foreground">No catalog documents yet — walk the source.</p>;
  }
  return (
    <table className="w-full text-xs">
      <thead className="text-muted-foreground">
        <tr className="text-left">
          <th className="py-1 pr-2 font-normal">Lineage</th>
          <th className="py-1 pr-2 font-normal">Title</th>
          <th className="py-1 pr-2 font-normal">v</th>
          <th className="py-1 pr-2 font-normal">Locations</th>
          <th className="py-1 pr-2 font-normal">Missing</th>
          <th className="py-1 pr-2 font-normal">Status</th>
          <th className="py-1 font-normal" />
        </tr>
      </thead>
      <tbody>
        {documents.map((d) => (
          <tr key={d.id} className="border-t border-border/40 align-top">
            <td className="py-1 pr-2 font-mono break-all">{d.lineage_key}</td>
            <td className="py-1 pr-2">{d.title ?? '—'}</td>
            <td className="py-1 pr-2">{d.latest_version ?? '—'}</td>
            <td className="py-1 pr-2">{d.location_count}</td>
            <td className="py-1 pr-2">{d.missing_count}</td>
            <td className="py-1 pr-2">
              {d.status}
              {d.rescinded_reason && (
                <span className="block text-muted-foreground">{d.rescinded_reason}</span>
              )}
            </td>
            <td className="py-1">
              {d.status === 'RESCINDED' ? (
                <button className="underline" onClick={() => onReinstate(d)}>reinstate</button>
              ) : (
                <button className="underline" onClick={() => onRescind(d)}>rescind</button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AddSourceForm({ onAdd }: {
  onAdd: (s: { id: string; name: string; config: Record<string, unknown>; enabled: boolean }) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [id, setId] = useState('');
  const [name, setName] = useState('');
  const [root, setRoot] = useState('');
  const [include, setInclude] = useState('');
  const [exclude, setExclude] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState('');

  if (!open) {
    return <Button size="sm" variant="outline" onClick={() => setOpen(true)}>Add source</Button>;
  }

  const globs = (v: string) => v.split(',').map((s) => s.trim()).filter(Boolean);

  const submit = async () => {
    try {
      const config: Record<string, unknown> = { root };
      if (globs(include).length) config.include = globs(include);
      if (globs(exclude).length) config.exclude = globs(exclude);
      await onAdd({ id, name, config, enabled });
      setOpen(false);
      setId(''); setName(''); setRoot(''); setInclude(''); setExclude(''); setError('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const field = 'w-full rounded border border-border bg-background px-2 py-1 text-xs';
  return (
    <div className="space-y-2 rounded border border-border p-3">
      <input className={field} placeholder="id" aria-label="id" value={id} onChange={(e) => setId(e.target.value)} />
      <input className={field} placeholder="name" aria-label="name" value={name} onChange={(e) => setName(e.target.value)} />
      <input className={field} placeholder="/absolute/root/path" aria-label="root path" value={root} onChange={(e) => setRoot(e.target.value)} />
      <input className={field} placeholder="include globs (comma-separated)" aria-label="include globs" value={include} onChange={(e) => setInclude(e.target.value)} />
      <input className={field} placeholder="exclude globs (comma-separated)" aria-label="exclude globs" value={exclude} onChange={(e) => setExclude(e.target.value)} />
      <label className="flex items-center gap-2 text-xs">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        enabled
      </label>
      {error && <p className="text-xs text-destructive">{error}</p>}
      <div className="flex gap-2">
        <Button size="sm" onClick={submit}>Save</Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
      </div>
    </div>
  );
}

export function SourcesView() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const {
    sources, documents, loading, error, refresh,
    addOrUpdateSource, setEnabled, walkNow, rescind, reinstate,
  } = useSources(selectedId);
  const [walk, setWalk] = useState<WalkSummary | null>(null);
  const [walkError, setWalkError] = useState('');

  const selected = sources.find((s) => s.id === selectedId) ?? null;

  const doWalk = async (id: string) => {
    setWalk(null); setWalkError('');
    try {
      setWalk(await walkNow(id));
    } catch (e: unknown) {
      setWalkError(e instanceof Error ? e.message : String(e));
    }
  };

  const doRescind = async (d: CatalogDocument) => {
    const reason = window.prompt(`Rescind "${d.title ?? d.lineage_key}" — reason?`);
    if (!reason?.trim()) return;
    await rescind(d.id, reason.trim());
  };

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      <div className="flex items-center gap-2 border-b border-border px-4 py-2">
        <h2 className="text-sm font-semibold">Sources</h2>
        <span className="text-xs text-muted-foreground">document catalog &amp; feeds</span>
        <button className="ml-auto" onClick={() => refresh()} aria-label="Refresh sources">
          <RefreshCw size={14} />
        </button>
      </div>

      {error && <p className="px-4 py-2 text-xs text-destructive">{error}</p>}
      {loading && <p className="px-4 py-2 text-xs text-muted-foreground">Loading…</p>}

      <div className="grid flex-1 grid-cols-1 gap-4 overflow-auto p-4 lg:grid-cols-2">
        <div className="space-y-2">
          {sources.map((s) => (
            <button
              key={s.id}
              onClick={() => { setSelectedId(s.id); setWalk(null); setWalkError(''); }}
              data-active={s.id === selectedId}
              className="w-full rounded border border-border p-3 text-left data-[active=true]:border-primary"
            >
              <div className="flex items-center gap-2 text-sm">
                {s.kind === 'email' ? <Mail size={13} /> : <FolderTree size={13} />}
                <span className="font-medium">{s.name}</span>
                <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase">{s.kind}</span>
                <span className="text-xs text-muted-foreground">
                  {s.enabled ? 'enabled' : 'disabled'}
                </span>
                <span className="ml-auto text-xs text-muted-foreground">
                  {s.last_polled_at ? timeAgo(s.last_polled_at) : 'never'}
                </span>
              </div>
              <div className="mt-1 text-xs text-muted-foreground">{counts(s)}</div>
              {s.read_only && (
                <div className="mt-1 text-xs text-muted-foreground">
                  read-only — configured in .env — visibility only for now
                </div>
              )}
            </button>
          ))}
          <AddSourceForm onAdd={addOrUpdateSource} />
        </div>

        <div className="space-y-3">
          {!selected && <p className="text-xs text-muted-foreground">Select a source.</p>}
          {selected && (
            <>
              <h3 className="text-sm font-semibold">{selected.name}</h3>
              <ConfigTable config={selected.config} />
              {selected.cursor && (
                <div>
                  <p className="text-xs text-muted-foreground">cursor</p>
                  <ConfigTable config={selected.cursor} />
                </div>
              )}
              <p className="text-xs">{counts(selected)}</p>
              {selected.read_only ? (
                <p className="text-xs text-muted-foreground">
                  Read-only — configured in .env — visibility only for now.
                </p>
              ) : (
                <>
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setEnabled(selected.id, !selected.enabled)}
                    >
                      {selected.enabled ? 'Disable' : 'Enable'}
                    </Button>
                    <Button size="sm" onClick={() => doWalk(selected.id)}>
                      <Play size={12} /> Walk now
                    </Button>
                  </div>
                  {walkError && <p className="text-xs text-destructive">{walkError}</p>}
                  {walk && (
                    <p className="text-xs text-muted-foreground">
                      {Object.entries(walk).map(([k, v]) => `${k}: ${v}`).join(' · ')}
                    </p>
                  )}
                  <DocumentsTable
                    documents={documents}
                    onRescind={doRescind}
                    onReinstate={(d) => reinstate(d.id)}
                  />
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
