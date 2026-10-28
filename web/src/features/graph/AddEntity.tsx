/**
 * The Graph panel's "new entity" form, in its own module so it can be tested
 * without the canvas. A write the backend refuses — the embedder down, which
 * since epic-outage-doctrine story 7 refuses the operator's direct write with
 * a 503 rather than storing a node without its vector — shows the refusal and
 * KEEPS the form filled (the operator's ruling, 2026-10-27).
 */
import { useState } from 'react';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { GraphGroupScope, GraphNode } from './useGraph';
import { FIELD, scopeLabel } from './graphUi';

/** Add an entity that extraction never produced. Collapsed by default and
 *  opened from the toolbar, so the panel a reader sees is unchanged. */
export function AddEntity({ entityTypes, groups, onCreate, onClose }: {
  entityTypes: string[];
  groups: GraphGroupScope[];
  onCreate: (node: GraphNode) => Promise<void>;
  onClose: () => void;
}) {
  const [name, setName] = useState('');
  const [summary, setSummary] = useState('');
  const [labels, setLabels] = useState<string[]>([]);
  const [groupId, setGroupId] = useState(groups[0]?.id || 'central_command');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  return (
    <div className="space-y-2 border-b border-border/40 bg-muted/10 px-4 py-3">
      <div className="flex items-center justify-between">
        <span className="text-[0.667rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          New entity
        </span>
        <button onClick={onClose} aria-label="Close new entity form" className="text-muted-foreground hover:text-foreground">
          <X size={13} aria-hidden="true" />
        </button>
      </div>
      <div className="flex flex-wrap gap-2">
        <input
          className={`${FIELD} max-w-[280px]`}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Canonical full name"
          aria-label="New entity name"
        />
        <select className={`${FIELD} max-w-[220px]`} value={groupId} onChange={(e) => setGroupId(e.target.value)} aria-label="Group">
          {groups.length
            ? groups.map((g) => <option key={g.id} value={g.id} title={g.id}>{scopeLabel(g)}</option>)
            : <option value="central_command">Public (central_command)</option>}
        </select>
      </div>
      <textarea
        className={`${FIELD} min-h-[52px]`}
        value={summary}
        onChange={(e) => setSummary(e.target.value)}
        placeholder="Summary"
        aria-label="New entity summary"
      />
      <div className="flex flex-wrap gap-1">
        {entityTypes.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setLabels((prev) => prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t])}
            className={`cockpit-badge ${labels.includes(t) ? 'border-primary/50 bg-primary/15 text-foreground' : 'opacity-60'}`}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          disabled={busy || !name.trim()}
          onClick={async () => {
            setBusy(true);
            setErr(null);
            try {
              await onCreate({
                uuid: '', name: name.trim(), summary, labels, group_id: groupId,
              });
              setName(''); setSummary(''); setLabels([]);
            } catch (e) {
              // The fields are cleared only on success, so a refused write
              // (the embedder down: the backend's 503 says nothing was written)
              // leaves the operator's entry exactly as typed — one click to
              // save it again once the dependency is back.
              setErr(e instanceof Error ? e.message : String(e));
            } finally {
              setBusy(false);
            }
          }}
        >Create</Button>
        {err && <span role="alert" className="text-[0.7rem] text-destructive">{err}</span>}
      </div>
    </div>
  );
}
