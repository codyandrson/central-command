/**
 * GraphView — the cockpit Graph panel (rung 3 of the 2026-08-09 graph
 * inspection spec: docs/superpowers/specs/2026-08-09-graph-inspection-design.md).
 *
 * Explore-only, incremental: search finds nodes, clicking a result or a
 * rendered node fetches+merges its one-hop neighborhood — the whole graph is
 * never loaded. cytoscape.js renders the canvas; a side drawer shows
 * name/labels/summary + provenance for a selected node, or fact/valid range
 * for a selected edge. An "as of" filter re-fetches expansions with `at`;
 * with no filter, invalidated edges render dashed/dimmed rather than hidden.
 */
import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import cytoscape, { type Core, type NodeSingular, type EdgeSingular } from 'cytoscape';
import fcose from 'cytoscape-fcose';
import expandCollapse from 'cytoscape-expand-collapse';
import { Waypoints, Search, X, AlertTriangle, Plus, ShieldAlert, Boxes } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { useGraph, type GraphNode, type GraphEdge, type GraphEpisode, type GraphGroupScope } from './useGraph';
import { AuditPanel } from './AuditPanel';
import { communitiesFrom } from './clustering';

cytoscape.use(fcose);
cytoscape.use(expandCollapse);

/** The slice of the expand-collapse api this panel uses (the extension is
 *  untyped — see cytoscape-expand-collapse.d.ts). */
type ExpandCollapseApi = { collapseAll(): void; expandAll(): void };
type WithExpandCollapse = Core & { expandCollapse(options: unknown): ExpandCollapseApi };

/** Layout used both for incremental re-layouts and by the extension after an
 *  expand/collapse — same options, so the canvas settles the same way. */
const INCREMENTAL_FCOSE = {
  name: 'fcose', quality: 'default', animate: false, fit: false, padding: 40, randomize: false,
};

/** "Public" / "Private · <agent_id>" — the operator-facing label for a raw
 *  `group_id`, everywhere one is shown. The underlying id stays available
 *  via `title` so it's never fully hidden, just not the primary label. */
function scopeLabel(g: GraphGroupScope): string {
  return g.scope === 'private' ? `Private · ${g.agent_id}` : `Public (${g.id})`;
}
import { SplitGroup, SplitSeparator, Panel } from '@/components/Splitter';

const CY_STYLE: cytoscape.StylesheetJson = [
  {
    selector: 'node',
    style: {
      'background-color': '#6366f1',
      label: 'data(label)',
      'font-size': 9,
      'min-zoomed-font-size': 8,
      color: '#e5e7eb',
      'text-valign': 'bottom',
      'text-margin-y': 4,
      width: 18,
      height: 18,
      'border-width': 1,
      'border-color': 'rgba(255,255,255,0.25)',
    },
  },
  {
    selector: 'edge',
    style: {
      width: 1.5,
      'line-color': 'rgba(148,163,184,0.55)',
      'target-arrow-color': 'rgba(148,163,184,0.55)',
      'target-arrow-shape': 'triangle',
      'curve-style': 'bezier',
      label: 'data(label)',
      'font-size': 7,
      'min-zoomed-font-size': 8,
      color: 'rgba(148,163,184,0.9)',
      'text-rotation': 'autorotate',
    },
  },
  {
    selector: 'edge[?invalid]',
    style: {
      'line-style': 'dashed',
      'line-color': 'rgba(148,163,184,0.28)',
      'target-arrow-color': 'rgba(148,163,184,0.28)',
      opacity: 0.6,
    },
  },
  {
    // A community supernode: muted, so it reads as scaffolding rather than as
    // an entity the operator can curate.
    selector: 'node[?cluster]',
    style: {
      'background-color': 'rgba(99,102,241,0.10)',
      'background-opacity': 1,
      'border-color': 'rgba(148,163,184,0.45)',
      'border-width': 1,
      shape: 'round-rectangle',
      'text-valign': 'top',
      'text-margin-y': -4,
      color: 'rgba(148,163,184,0.95)',
      'font-size': 10,
      padding: '12px',
    },
  },
  {
    // Collapsed: it now stands in for its members, so give it real presence.
    selector: 'node[?cluster].cy-expand-collapse-collapsed-node',
    style: {
      'background-color': 'rgba(99,102,241,0.35)',
      'text-valign': 'bottom',
      'text-margin-y': 4,
      width: 34,
      height: 34,
      shape: 'round-rectangle',
    },
  },
  {
    selector: 'edge.cy-expand-collapse-meta-edge',
    style: { 'line-style': 'dotted', width: 2, label: '' },
  },
  {
    selector: 'node:selected',
    style: { 'border-width': 2, 'border-color': '#6366f1' },
  },
  {
    selector: 'edge:selected',
    style: { 'line-color': '#6366f1', 'target-arrow-color': '#6366f1', width: 2.5 },
  },
];

type Selection = { kind: 'node'; node: GraphNode } | { kind: 'edge'; edge: GraphEdge } | null;

/** What a completed curation write asks the canvas to do. The editors never
 *  touch cytoscape themselves — they report, GraphView applies. */
type DrawerChange =
  | { kind: 'node-updated'; node: GraphNode }
  | { kind: 'node-deleted'; uuid: string; keep?: string }
  | { kind: 'edge-updated'; edge: GraphEdge }
  | { kind: 'edge-deleted'; uuid: string }
  | { kind: 'refresh'; uuid: string };

/** The write half of useGraph, plus the nodes currently on the canvas — the
 *  pickers for "merge into" and "relate to" can only offer what is loaded,
 *  which is also what stops the operator wiring an edge to a node they have
 *  not looked at. */
type Curation = Pick<ReturnType<typeof useGraph>,
  'updateNode' | 'deleteNode' | 'createEdge' | 'updateEdge' | 'deleteEdge' | 'mergeNodes'
> & { loadedNodes: GraphNode[] };

function GraphistryBanner({ state }: { state: 'disabled' | 'unreachable' | 'live' }) {
  if (state === 'disabled') {
    return (
      <p className="px-4 py-1.5 text-[0.667rem] text-muted-foreground">
        Graphistry backend disabled — enable in Settings for GPU-scale views.
      </p>
    );
  }
  if (state === 'unreachable') {
    return (
      <p className="mx-4 mt-2 flex items-center gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-[0.7rem] text-amber-700 dark:text-amber-400">
        <AlertTriangle size={12} aria-hidden="true" />
        Graphistry enabled but not reachable (workstation off or GPU busy).
      </p>
    );
  }
  return (
    <span className="cockpit-badge border-green/30 bg-green/8 text-green">Graphistry live</span>
  );
}

const FIELD = 'w-full rounded-lg border border-input/80 bg-background/72 px-2 py-1.5 text-[0.733rem] text-foreground';

/** First 10 chars of an ISO timestamp is its date — what `<input type="date">`
 *  needs, and all a validity window needs in practice. */
function datePart(iso: string | null): string {
  return iso ? iso.slice(0, 10) : '';
}

/** HH:MM from an ISO timestamp, defaulting to midnight — a bare date or an
 *  empty bound both seed the time input with 00:00 so the operator is never
 *  forced to fill it. */
function timePart(iso: string | null): string {
  return (iso && iso.slice(11, 16)) || '00:00';
}

/** Combine the date + time inputs into a minute-precision ISO timestamp; a
 *  cleared date clears the whole bound, whatever the time field says. */
function stamp(date: string, time: string): string {
  return date ? `${date}T${time || '00:00'}` : '';
}

/** Curation controls live INSIDE the detail drawer rather than in a right-click
 *  menu: the drawer already resolves "which node/edge do you mean", already
 *  shows the values being edited, and needs no new dependency to do it. */
function NodeEditor({ node, entityTypes, curate, onDone }: {
  node: GraphNode;
  entityTypes: string[];
  curate: Curation;
  onDone: (change: DrawerChange) => void;
}) {
  const [name, setName] = useState(node.name);
  const [summary, setSummary] = useState(node.summary || '');
  const [labels, setLabels] = useState<string[]>(node.labels.filter((l) => l !== 'Entity'));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [mergeFrom, setMergeFrom] = useState('');
  const [edgeTarget, setEdgeTarget] = useState('');
  const [edgeName, setEdgeName] = useState('');
  const [edgeFact, setEdgeFact] = useState('');

  // Re-seed when the operator selects a different node without closing.
  useEffect(() => {
    setName(node.name);
    setSummary(node.summary || '');
    setLabels(node.labels.filter((l) => l !== 'Entity'));
    setErr(null);
  }, [node]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const others = curate.loadedNodes.filter((n) => n.uuid !== node.uuid);
  const dirty = name !== node.name || summary !== (node.summary || '')
    || labels.join() !== node.labels.filter((l) => l !== 'Entity').join();

  return (
    <div className="space-y-3 border-t border-border/40 px-4 py-3">
      <div className="text-[0.667rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        Edit entity
      </div>
      <input className={FIELD} value={name} onChange={(e) => setName(e.target.value)} aria-label="Entity name" />
      <textarea
        className={`${FIELD} min-h-[64px]`}
        value={summary}
        onChange={(e) => setSummary(e.target.value)}
        placeholder="Summary"
        aria-label="Entity summary"
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
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={busy || !dirty}
          onClick={() => run(async () => {
            await curate.updateNode({ uuid: node.uuid, name, summary, labels });
            onDone({ kind: 'node-updated', node: { ...node, name, summary, labels: [...labels, 'Entity'] } });
          })}
        >Save</Button>
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => run(async () => {
            if (!window.confirm(`Delete "${node.name}" and every edge touching it?`)) return;
            await curate.deleteNode(node.uuid);
            onDone({ kind: 'node-deleted', uuid: node.uuid });
          })}
        >Delete</Button>
      </div>

      {others.length > 0 && (
        <div className="space-y-1.5 border-t border-border/30 pt-3">
          <div className="text-[0.667rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Fold a duplicate into this one
          </div>
          <select className={FIELD} value={mergeFrom} onChange={(e) => setMergeFrom(e.target.value)} aria-label="Duplicate to fold in">
            <option value="">select the duplicate…</option>
            {others.map((n) => <option key={n.uuid} value={n.uuid}>{n.name}</option>)}
          </select>
          <Button
            size="sm"
            variant="outline"
            disabled={busy || !mergeFrom}
            onClick={() => run(async () => {
              await curate.mergeNodes(node.uuid, mergeFrom);
              onDone({ kind: 'node-deleted', uuid: mergeFrom, keep: node.uuid });
              setMergeFrom('');
            })}
          >Merge — keeps this entity, moves its edges</Button>
        </div>
      )}

      {others.length > 0 && (
        <div className="space-y-1.5 border-t border-border/30 pt-3">
          <div className="text-[0.667rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            New relationship from here
          </div>
          <select className={FIELD} value={edgeTarget} onChange={(e) => setEdgeTarget(e.target.value)} aria-label="Relationship target">
            <option value="">to…</option>
            {others.map((n) => <option key={n.uuid} value={n.uuid}>{n.name}</option>)}
          </select>
          <input className={FIELD} value={edgeName} onChange={(e) => setEdgeName(e.target.value)} placeholder="HAS_PARENT" aria-label="Relationship name" />
          <textarea
            className={`${FIELD} min-h-[52px]`}
            value={edgeFact}
            onChange={(e) => setEdgeFact(e.target.value)}
            placeholder="The fact, in a full sentence — this is what agents recall."
            aria-label="Relationship fact"
          />
          <Button
            size="sm"
            variant="outline"
            disabled={busy || !edgeTarget || !edgeName.trim() || !edgeFact.trim()}
            onClick={() => run(async () => {
              await curate.createEdge({
                source_uuid: node.uuid, target_uuid: edgeTarget,
                name: edgeName.trim(), fact: edgeFact.trim(),
              });
              onDone({ kind: 'refresh', uuid: node.uuid });
              setEdgeTarget(''); setEdgeName(''); setEdgeFact('');
            })}
          >Add relationship</Button>
        </div>
      )}
      {err && <p className="text-[0.7rem] text-destructive">{err}</p>}
    </div>
  );
}

function EdgeEditor({ edge, curate, onDone }: {
  edge: GraphEdge;
  curate: Curation;
  onDone: (change: DrawerChange) => void;
}) {
  const [name, setName] = useState(edge.name);
  const [fact, setFact] = useState(edge.fact);
  const [source, setSource] = useState(edge.source);
  const [target, setTarget] = useState(edge.target);
  // Each bound is a date input plus a time input (not one datetime-local,
  // whose value stays empty until BOTH halves are typed): the time seeds to
  // 00:00 so picking just a date works, and the backend's datetime() takes
  // the combined YYYY-MM-DDTHH:MM as-is.
  const [validDate, setValidDate] = useState(datePart(edge.valid_at));
  const [validTime, setValidTime] = useState(timePart(edge.valid_at));
  const [invalidDate, setInvalidDate] = useState(datePart(edge.invalid_at));
  const [invalidTime, setInvalidTime] = useState(timePart(edge.invalid_at));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setName(edge.name); setFact(edge.fact);
    setSource(edge.source); setTarget(edge.target);
    setValidDate(datePart(edge.valid_at)); setValidTime(timePart(edge.valid_at));
    setInvalidDate(datePart(edge.invalid_at)); setInvalidTime(timePart(edge.invalid_at));
    setErr(null);
  }, [edge]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setErr(null);
    try { await fn(); } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const validAt = stamp(validDate, validTime);
  const invalidAt = stamp(invalidDate, invalidTime);
  const wasValidAt = stamp(datePart(edge.valid_at), timePart(edge.valid_at));
  const wasInvalidAt = stamp(datePart(edge.invalid_at), timePart(edge.invalid_at));
  const dirty = name !== edge.name || fact !== edge.fact
    || source !== edge.source || target !== edge.target
    || validAt !== wasValidAt || invalidAt !== wasInvalidAt;

  return (
    <div className="space-y-3 border-t border-border/40 px-4 py-3">
      <div className="text-[0.667rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        Edit relationship
      </div>
      <input className={FIELD} value={name} onChange={(e) => setName(e.target.value)} aria-label="Relationship name" />
      <textarea className={`${FIELD} min-h-[64px]`} value={fact} onChange={(e) => setFact(e.target.value)} aria-label="Relationship fact" />
      <label className="block text-[0.667rem] text-muted-foreground">
        from
        <select className={FIELD} value={source} onChange={(e) => setSource(e.target.value)}>
          {curate.loadedNodes.map((n) => <option key={n.uuid} value={n.uuid}>{n.name}</option>)}
        </select>
      </label>
      <label className="block text-[0.667rem] text-muted-foreground">
        to
        <select className={FIELD} value={target} onChange={(e) => setTarget(e.target.value)}>
          {curate.loadedNodes.map((n) => <option key={n.uuid} value={n.uuid}>{n.name}</option>)}
        </select>
      </label>
      <label className="block text-[0.667rem] text-muted-foreground">
        valid from
        <span className="flex gap-2">
          <input
            type="date"
            className={FIELD}
            value={validDate}
            onChange={(e) => setValidDate(e.target.value)}
            aria-label="Valid from date"
          />
          <input
            type="time"
            className={FIELD}
            value={validTime}
            onChange={(e) => setValidTime(e.target.value)}
            aria-label="Valid from time"
          />
        </span>
      </label>
      <label className="block text-[0.667rem] text-muted-foreground">
        invalid at
        <span className="flex gap-2">
          <input
            type="date"
            className={FIELD}
            value={invalidDate}
            onChange={(e) => setInvalidDate(e.target.value)}
            aria-label="Invalid at date"
          />
          <input
            type="time"
            className={FIELD}
            value={invalidTime}
            onChange={(e) => setInvalidTime(e.target.value)}
            aria-label="Invalid at time"
          />
        </span>
      </label>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={busy || !dirty}
          onClick={() => run(async () => {
            // Only a field the operator actually touched is sent — the
            // backend treats a present value as "set this" and an absent one
            // as "leave unchanged", same convention as the other edge fields.
            // An operator clearing the date maps to clear_valid/clear_invalid,
            // since an empty string is not a valid ISO timestamp to "set".
            // Comparing against the seeded MINUTE-precision stamp (not the
            // full timestamp) means an untouched field never sends — so an
            // existing sub-minute timestamp is only truncated if the operator
            // actually edits it.
            await curate.updateEdge({
              uuid: edge.uuid, name, fact,
              source_uuid: source, target_uuid: target,
              ...(validAt !== wasValidAt
                ? (validAt ? { valid_at: validAt } : { clear_valid: true })
                : {}),
              ...(invalidAt !== wasInvalidAt
                ? (invalidAt ? { invalid_at: invalidAt } : { clear_invalid: true })
                : {}),
            });
            onDone({
              kind: 'edge-updated',
              edge: {
                ...edge, name, fact, source, target,
                valid_at: validAt || null, invalid_at: invalidAt || null,
                invalid: invalidAt ? true : edge.invalid,
              },
            });
          })}
        >Save</Button>
        {edge.invalid && (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => run(async () => {
              await curate.updateEdge({ uuid: edge.uuid, clear_invalid: true });
              onDone({ kind: 'edge-updated', edge: { ...edge, invalid: false, invalid_at: null } });
            })}
          >Restore — it was retired wrongly</Button>
        )}
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => run(async () => {
            if (!window.confirm(`Delete the relationship "${edge.name}"?`)) return;
            await curate.deleteEdge(edge.uuid);
            onDone({ kind: 'edge-deleted', uuid: edge.uuid });
          })}
        >Delete</Button>
      </div>
      {err && <p className="text-[0.7rem] text-destructive">{err}</p>}
    </div>
  );
}

/** Side drawer: node/edge details + provenance. Overlays the canvas — never
 *  displaces the search panel or shifts layout. */
function DetailDrawer({ selection, episodes, onClose, entityTypes, curate, onChange, groups, onRemoveFromCanvas }: {
  selection: Selection;
  episodes: GraphEpisode[];
  onClose: () => void;
  entityTypes: string[];
  curate: Curation;
  onChange: (change: DrawerChange) => void;
  groups: GraphGroupScope[];
  /** Display-only: drops a node off the canvas (and its dangling edges with
   *  it). Never calls a delete API — nothing in the graph changes. Kept far
   *  from NodeEditor's destructive "Delete" so the two can't be mistaken for
   *  each other. */
  onRemoveFromCanvas: (uuid: string) => void;
}) {
  if (!selection) return null;
  return (
    <div className="absolute right-0 top-0 z-10 h-full w-[320px] max-w-[85vw] overflow-y-auto border-l border-border/60 bg-card/95 shadow-[-12px_0_32px_rgba(0,0,0,0.24)]">
      <div className="flex items-center justify-between border-b border-border/40 px-4 py-3">
        <span className="text-[0.733rem] font-semibold uppercase tracking-[0.12em] text-foreground">
          {selection.kind === 'node' ? 'Entity' : 'Relationship'}
        </span>
        <button
          onClick={onClose}
          aria-label="Close detail panel"
          className="flex size-6 items-center justify-center rounded-md text-muted-foreground hover:text-foreground"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>
      {selection.kind === 'node' ? (
        <div className="space-y-3 px-4 py-3">
          <div>
            <div className="flex items-center justify-between gap-2">
              <div className="text-[0.85rem] font-semibold text-foreground">{selection.node.name}</div>
              <Button
                size="sm"
                variant="ghost"
                className="shrink-0 text-[0.667rem] text-muted-foreground hover:text-foreground"
                title="Drop this entity off the canvas only — the graph itself is unchanged."
                onClick={() => onRemoveFromCanvas(selection.node.uuid)}
              >Remove from view</Button>
            </div>
            <div className="mt-1 flex flex-wrap gap-1">
              {selection.node.labels.map((l) => (
                <span key={l} className="cockpit-badge">{l}</span>
              ))}
              <span className="cockpit-badge" title={selection.node.group_id}>
                {(() => {
                  const g = groups.find((x) => x.id === selection.node.group_id);
                  return g ? scopeLabel(g) : selection.node.group_id;
                })()}
              </span>
            </div>
          </div>
          {selection.node.summary && (
            <p className="text-[0.733rem] text-muted-foreground">{selection.node.summary}</p>
          )}
          <div>
            <div className="mb-1 text-[0.667rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
              Provenance
            </div>
            {episodes.length === 0 ? (
              <p className="text-[0.7rem] text-muted-foreground">No source episodes found.</p>
            ) : (
              <div className="space-y-2">
                {episodes.map((ep) => (
                  <div key={ep.uuid} className="rounded-md border border-border/40 bg-muted/20 px-2.5 py-2 text-[0.7rem]">
                    <div className="font-medium text-foreground">{ep.name}</div>
                    <div className="text-muted-foreground">{ep.source_description}</div>
                    {ep.valid_at && <div className="text-muted-foreground/80">{ep.valid_at}</div>}
                    {ep.content_preview && (
                      <p className="mt-1 text-muted-foreground/90">{ep.content_preview}</p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="space-y-3 px-4 py-3">
          <div className="text-[0.85rem] font-semibold text-foreground">{selection.edge.name}</div>
          <p className="text-[0.733rem] text-foreground">{selection.edge.fact}</p>
          <div className="space-y-1 text-[0.7rem] text-muted-foreground">
            <div>valid from: {selection.edge.valid_at || 'unknown'}</div>
            <div>invalid at: {selection.edge.invalid_at || (selection.edge.invalid ? 'superseded' : 'still valid')}</div>
            <div>created: {selection.edge.created_at}</div>
          </div>
        </div>
      )}
      {selection.kind === 'node' ? (
        <NodeEditor
          key={selection.node.uuid}
          node={selection.node}
          entityTypes={entityTypes}
          curate={curate}
          onDone={onChange}
        />
      ) : (
        <EdgeEditor key={selection.edge.uuid} edge={selection.edge} curate={curate} onDone={onChange} />
      )}
    </div>
  );
}

/** Add an entity that extraction never produced. Collapsed by default and
 *  opened from the toolbar, so the panel a reader sees is unchanged. */
function AddEntity({ entityTypes, groups, onCreate, onClose }: {
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
              setErr(e instanceof Error ? e.message : String(e));
            } finally {
              setBusy(false);
            }
          }}
        >Create</Button>
        {err && <span className="text-[0.7rem] text-destructive">{err}</span>}
      </div>
    </div>
  );
}

export function GraphView() {
  const graph = useGraph();
  const {
    status, error, fetchStatus, search, neighborhood, provenance, entityTypes, loadAll,
  } = graph;
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const ecRef = useRef<ExpandCollapseApi | null>(null);
  const nodeDataRef = useRef<Map<string, GraphNode>>(new Map());
  const edgeDataRef = useRef<Map<string, GraphEdge>>(new Map());

  const [query, setQuery] = useState('');
  const [groupId, setGroupId] = useState('');
  const [results, setResults] = useState<GraphNode[]>([]);
  const [asOf, setAsOf] = useState('');
  const [selection, setSelection] = useState<Selection>(null);
  const [episodes, setEpisodes] = useState<GraphEpisode[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [adding, setAdding] = useState(false);
  const [auditing, setAuditing] = useState(false);
  const [loadingAll, setLoadingAll] = useState(false);
  const [truncated, setTruncated] = useState(false);
  const [clustered, setClustered] = useState(false);
  // Mirrors nodeDataRef as STATE, because the merge/relate pickers have to
  // re-render when the canvas gains a node; a ref alone never triggers that.
  const [loadedNodes, setLoadedNodes] = useState<GraphNode[]>([]);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);
  useEffect(() => { void entityTypes().then(setTypes); }, [entityTypes]);

  // Init cytoscape once.
  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      style: CY_STYLE,
      wheelSensitivity: 0.25,
    });
    cyRef.current = cy;
    ecRef.current = (cy as WithExpandCollapse).expandCollapse({
      layoutBy: INCREMENTAL_FCOSE,
      fisheye: false,
      animate: false,
      // The undo-redo extension isn't installed; leaving this true is harmless
      // (undoRedoUtilities no-ops without cy.undoRedo) but says the wrong thing.
      undoable: false,
    });
    // cytoscape hard-sizes its canvases to the container at init and never
    // re-measures. Without this, the first search (results list appearing =
    // container narrowing) leaves stale 100%-width canvases that, combined
    // with flexbox min-width:auto, hold the wrapper at its old width — which
    // shoved the right-0 detail drawer off-screen (found live 2026-08-10).
    const ro = new ResizeObserver(() => {
      cy.resize();
      if (cy.elements().length > 0) cy.fit(undefined, 40);
    });
    ro.observe(containerRef.current);
    return () => { ro.disconnect(); cy.destroy(); cyRef.current = null; ecRef.current = null; };
  }, []);

  // `fresh` is true only for a from-scratch canvas (first layout, or a
  // rebuild that just wiped everything) — those randomize + fit. An
  // incremental merge keeps existing nodes' approximate positions
  // (`randomize: false`) so adding a neighborhood doesn't reshuffle the
  // whole canvas the operator was just looking at.
  const runLayout = useCallback((fresh: boolean) => {
    cyRef.current?.layout({
      ...INCREMENTAL_FCOSE, fit: fresh, randomize: fresh,
    } as cytoscape.LayoutOptions).run();
  }, []);

  /** Undo clustering: expand every supernode (which RESTORES the children the
   *  extension removed on collapse), orphan them, drop the supernodes. Returns
   *  whether anything was clustered, so callers can drop the toggle. A no-op
   *  when there are none, which is what lets the curation paths call it
   *  unconditionally. */
  const uncluster = useCallback(() => {
    const cy = cyRef.current;
    if (!cy || cy.nodes('[?cluster]').empty()) return false;
    ecRef.current?.expandAll();
    // Re-query after expandAll: `move()` and `restore()` both hand back new
    // element refs, so a collection captured before this is stale.
    cy.nodes('[?cluster]').children().move({ parent: null });
    cy.nodes('[?cluster]').remove();
    return true;
  }, []);

  const mergeIn = useCallback((nodes: GraphNode[], edges: GraphEdge[], fresh = false) => {
    const cy = cyRef.current;
    if (!cy) return;
    // An empty canvas (first-ever add, or right after `rebuild` wiped it)
    // has nothing worth preserving positions for — randomize + fit either way.
    fresh = fresh || cy.elements().length === 0;
    let changed = false;
    for (const n of nodes) {
      // `nodeDataRef` is what "on the canvas" means, NOT the cytoscape lookup:
      // expand-collapse `.remove()`s a collapsed cluster's children and stashes
      // them for restore, so `getElementById` reads empty for a node that is
      // very much still loaded — adding it again would duplicate it on expand.
      const known = nodeDataRef.current.has(n.uuid);
      nodeDataRef.current.set(n.uuid, n);
      if (!known && cy.getElementById(n.uuid).empty()) {
        cy.add({ data: { id: n.uuid, label: n.name } });
        changed = true;
      }
    }
    // Nodes are added FIRST, in full, before any edge is considered: a batch
    // carrying both (Show all) would otherwise drop every edge whose target
    // happens to sort after it — cytoscape rejects an edge with a missing
    // endpoint, and the check below reads the canvas, not this batch.
    for (const e of edges) {
      edgeDataRef.current.set(e.uuid, e);
      const existing = cy.getElementById(e.uuid);
      if (existing.empty()) {
        // Only add an edge once both endpoints exist in the graph.
        if (!cy.getElementById(e.source).empty() && !cy.getElementById(e.target).empty()) {
          cy.add({ data: { id: e.uuid, source: e.source, target: e.target, label: e.name, invalid: e.invalid } });
          changed = true;
        }
      } else {
        existing.data('invalid', e.invalid);
      }
    }
    setLoadedNodes([...nodeDataRef.current.values()]);
    if (changed) runLayout(fresh);
  }, [runLayout]);

  // Wipes the canvas and repopulates it from exactly the given data — unlike
  // mergeIn, this DROPS anything not in the new set. Live view never needs
  // that (invalidated edges still render, dashed); a time-filtered fetch
  // already excludes what fell outside the window at the query layer, so a
  // stale edge left over from a previous fetch would assert "this was true
  // then" when the backend just said otherwise. Keeps the selection if it
  // survives the rebuild, clears the drawer if it doesn't.
  const rebuild = useCallback((nodes: GraphNode[], edges: GraphEdge[]) => {
    const cy = cyRef.current;
    if (!cy) return;
    // Everything goes, supernodes included — so clustering is off afterwards.
    cy.elements().remove();
    setClustered(false);
    nodeDataRef.current.clear();
    edgeDataRef.current.clear();
    mergeIn(nodes, edges, true);
    setSelection((s) => {
      if (!s) return s;
      if (s.kind === 'node') {
        const kept = nodes.find((n) => n.uuid === s.node.uuid);
        return kept ? { kind: 'node', node: kept } : null;
      }
      const kept = edges.find((e) => e.uuid === s.edge.uuid);
      return kept ? { kind: 'edge', edge: kept } : null;
    });
  }, [mergeIn]);

  const expandNode = useCallback(async (uuid: string) => {
    const { nodes, edges } = await neighborhood(uuid, asOf || undefined);
    mergeIn(nodes, edges);
  }, [neighborhood, asOf, mergeIn]);

  // Tracks how the canvas was populated ('all' via Show all, 'neighborhood'
  // via search + expansion) so the as-of effect below knows which fetch to
  // re-run rather than guessing.
  const viewModeRef = useRef<'neighborhood' | 'all'>('neighborhood');

  // Re-run whichever view is showing when the as-of filter changes, so the
  // temporal view stays live rather than only affecting future loads.
  const asOfRef = useRef(asOf);
  useEffect(() => {
    if (asOfRef.current === asOf) return;
    asOfRef.current = asOf;
    if (nodeDataRef.current.size === 0) return;
    void (async () => {
      if (viewModeRef.current === 'all') {
        const { nodes, edges, truncated } = await loadAll(groupId || undefined, asOf || undefined);
        rebuild(nodes, edges);
        setTruncated(truncated);
        return;
      }
      // Re-fetch every currently-loaded node's neighborhood at the new `at`
      // and rebuild from the union — a per-node merge would leave behind
      // whatever the previous `at` had fetched that the new one excludes.
      const uuids = [...nodeDataRef.current.keys()];
      const nodeMap = new Map<string, GraphNode>();
      const edgeMap = new Map<string, GraphEdge>();
      for (const uuid of uuids) {
        const { nodes, edges } = await neighborhood(uuid, asOf || undefined);
        for (const n of nodes) nodeMap.set(n.uuid, n);
        for (const e of edges) edgeMap.set(e.uuid, e);
      }
      rebuild([...nodeMap.values()], [...edgeMap.values()]);
    })();
  }, [asOf, neighborhood, loadAll, groupId, rebuild]);

  // Wire cytoscape click handlers once.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    const onNodeTap = (evt: cytoscape.EventObject) => {
      const target = evt.target as NodeSingular;
      // A `cluster:*` supernode has no entry here, so this is also the guard
      // that stops a tap on one opening the DetailDrawer as if it were an
      // entity. Expanding it is the extension's cue's job, not ours.
      const node = nodeDataRef.current.get(target.id());
      if (!node) return;
      setSelection({ kind: 'node', node });
      void expandNode(node.uuid);
    };
    const onEdgeTap = (evt: cytoscape.EventObject) => {
      const target = evt.target as EdgeSingular;
      const edge = edgeDataRef.current.get(target.id());
      if (!edge) return;
      setSelection({ kind: 'edge', edge });
    };

    cy.on('tap', 'node', onNodeTap);
    cy.on('tap', 'edge', onEdgeTap);
    return () => {
      cy.off('tap', 'node', onNodeTap);
      cy.off('tap', 'edge', onEdgeTap);
    };
  }, [expandNode]);

  // Load provenance whenever a node is selected.
  useEffect(() => {
    if (selection?.kind !== 'node') { setEpisodes([]); return; }
    let cancelled = false;
    void provenance(selection.node.uuid).then((eps) => { if (!cancelled) setEpisodes(eps); });
    return () => { cancelled = true; };
  }, [selection, provenance]);

  const handleSearch = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    const nodes = await search(query.trim(), groupId || undefined);
    setResults(nodes);
  }, [query, groupId, search]);

  const addResult = useCallback((node: GraphNode) => {
    viewModeRef.current = 'neighborhood';
    mergeIn([node], []);
    setSelection({ kind: 'node', node });
    void expandNode(node.uuid);
  }, [mergeIn, expandNode]);

  // Audit panel results reuse the exact same expand-on-canvas path as a
  // search result click — the operator then curates via the DetailDrawer's
  // existing merge/edit tooling, same as any other node on the canvas.
  const loadNodesOnCanvas = useCallback((nodes: GraphNode[]) => {
    viewModeRef.current = 'neighborhood';
    mergeIn(nodes, []);
    if (nodes[0]) setSelection({ kind: 'node', node: nodes[0] });
    for (const n of nodes) void expandNode(n.uuid);
  }, [mergeIn, expandNode]);

  const groupOptions = useMemo(() => status?.groups ?? [], [status]);

  /** Load the WHOLE graph (or the selected group). The panel's default is
   *  explore-from-a-search, which assumes you know what you are looking for —
   *  useless for spotting a duplicate you have never searched. Isolated nodes
   *  come too: an orphaned duplicate identity is exactly what needs finding. */
  const showAll = useCallback(async () => {
    viewModeRef.current = 'all';
    setLoadingAll(true);
    try {
      const { nodes, edges, truncated } = await loadAll(groupId || undefined, asOf || undefined);
      // A time-filtered load-all replaces the canvas outright, same reason
      // as the as-of effect above; the live load-all still merges/adds.
      if (asOf) rebuild(nodes, edges); else mergeIn(nodes, edges);
      setTruncated(truncated);
    } finally {
      setLoadingAll(false);
    }
  }, [loadAll, groupId, asOf, mergeIn, rebuild]);

  /** Cluster / uncluster. Clustering is a snapshot of the canvas at the moment
   *  it is switched on: nodes merged in afterwards (a neighborhood expansion)
   *  arrive loose and stay loose, because re-running Louvain mid-exploration
   *  would reshuffle everything the operator was reading. Toggle off and on to
   *  re-cluster. */
  const toggleCluster = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return;
    if (clustered) {
      uncluster();
      setClustered(false);
      runLayout(false);
      return;
    }
    const clusters = communitiesFrom(
      cy.nodes().map((n) => ({ id: n.id(), label: String(n.data('label') ?? n.id()) })),
      cy.edges().map((e) => ({ source: e.source().id(), target: e.target().id() })),
    );
    if (clusters.length === 0) return;
    for (const c of clusters) {
      cy.add({ data: { id: c.id, label: c.label, cluster: true } });
      // Parent must exist before a child can move into it.
      for (const m of c.members) cy.getElementById(m).move({ parent: c.id });
    }
    setClustered(true);
    ecRef.current?.collapseAll();  // runs `layoutBy` itself
  }, [clustered, uncluster, runLayout]);

  const curate = useMemo(() => ({
    updateNode: graph.updateNode,
    deleteNode: graph.deleteNode,
    createEdge: graph.createEdge,
    updateEdge: graph.updateEdge,
    deleteEdge: graph.deleteEdge,
    mergeNodes: graph.mergeNodes,
    loadedNodes,
  }), [graph.updateNode, graph.deleteNode, graph.createEdge, graph.updateEdge,
      graph.deleteEdge, graph.mergeNodes, loadedNodes]);

  /** Apply a completed write to the canvas. Everything here is local except
   *  the deliberate re-expansions: a repointed or new edge changes topology
   *  the client cannot infer, so those re-read the neighborhood from Neo4j
   *  rather than guessing at it. */
  const applyChange = useCallback(async (change: DrawerChange) => {
    const cy = cyRef.current;
    // Curation edits topology, and every `getElementById` below would miss a
    // node hidden inside a collapsed cluster. Flatten first rather than teach
    // each case to look through the extension's stash.
    if (uncluster()) setClustered(false);
    switch (change.kind) {
      case 'node-updated':
        nodeDataRef.current.set(change.node.uuid, change.node);
        cy?.getElementById(change.node.uuid).data('label', change.node.name);
        setSelection({ kind: 'node', node: change.node });
        setLoadedNodes([...nodeDataRef.current.values()]);
        break;
      case 'node-deleted': {
        // Every edge that touched it is gone in Neo4j too, so drop them here
        // instead of leaving arrows pointing at nothing.
        for (const [uuid, e] of edgeDataRef.current) {
          if (e.source === change.uuid || e.target === change.uuid) edgeDataRef.current.delete(uuid);
        }
        nodeDataRef.current.delete(change.uuid);
        cy?.getElementById(change.uuid).remove();
        // Only if the deleted node is still what's on screen — a curate call
        // resolving late must not evict a selection made since (same rule the
        // Decisions Inbox learned, 2026-08-16).
        setSelection((s) => (s?.kind === 'node' && s.node.uuid === change.uuid ? null : s));
        setLoadedNodes([...nodeDataRef.current.values()]);
        if (change.keep) await expandNode(change.keep);
        break;
      }
      case 'edge-updated': {
        edgeDataRef.current.set(change.edge.uuid, change.edge);
        cy?.getElementById(change.edge.uuid).remove();
        setSelection({ kind: 'edge', edge: change.edge });
        await expandNode(change.edge.source);
        break;
      }
      case 'edge-deleted':
        edgeDataRef.current.delete(change.uuid);
        cy?.getElementById(change.uuid).remove();
        setSelection((s) => (s?.kind === 'edge' && s.edge.uuid === change.uuid ? null : s));
        break;
      case 'refresh':
        await expandNode(change.uuid);
        break;
    }
    void fetchStatus();
  }, [expandNode, fetchStatus, uncluster]);

  // Display-state only: drops a node (and its now-dangling edges — cytoscape
  // removes connected edges automatically) off the canvas without touching
  // the graph. Distinct from NodeEditor's "Delete", which calls the real
  // curation API.
  const removeFromCanvas = useCallback((uuid: string) => {
    const cy = cyRef.current;
    if (!cy) return;
    // Same reason as applyChange: a collapsed-away node isn't in the graph to
    // be removed, and a removed cluster member would leave a stale entry in
    // the extension's restore stash.
    if (uncluster()) setClustered(false);
    for (const [eid, e] of edgeDataRef.current) {
      if (e.source === uuid || e.target === uuid) edgeDataRef.current.delete(eid);
    }
    nodeDataRef.current.delete(uuid);
    cy.getElementById(uuid).remove();
    setSelection((s) => (s?.kind === 'node' && s.node.uuid === uuid ? null : s));
    setLoadedNodes([...nodeDataRef.current.values()]);
  }, [uncluster]);

  const createNode = useCallback(async (draft: GraphNode) => {
    const { uuid } = await graph.createNode({
      name: draft.name, labels: draft.labels,
      summary: draft.summary, group_id: draft.group_id,
    });
    const node: GraphNode = { ...draft, uuid, labels: [...draft.labels, 'Entity'] };
    mergeIn([node], []);
    setSelection({ kind: 'node', node });
    void fetchStatus();
  }, [graph, mergeIn, fetchStatus]);

  if (status && !status.available) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
        <Waypoints size={28} className="text-muted-foreground" aria-hidden="true" />
        <p className="text-[0.85rem] font-semibold text-foreground">Graph read path unavailable</p>
        <p className="max-w-md text-[0.733rem] text-muted-foreground">
          The API's read-only bolt connection isn't reachable right now. Check
          cc-graph-bolt.service and the Neo4j pod, then reload this page.
        </p>
      </div>
    );
  }

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-border/40 px-4 py-3">
        <Waypoints size={14} className="text-primary" aria-hidden="true" />
        <span className="text-[0.8rem] font-semibold uppercase tracking-[0.14em] text-foreground">Graph</span>
        {status && (
          <span className="cockpit-badge tabular-nums">{status.node_count} nodes · {status.edge_count} edges</span>
        )}
        {status && <GraphistryBanner state={status.graphistry} />}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setAdding((v) => !v)}>
            <Plus size={12} aria-hidden="true" /> Entity
          </Button>
          <label className="flex items-center gap-1.5 text-[0.667rem] text-muted-foreground">
            as of
            <Input
              type="date"
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
              className="h-9 w-auto min-w-0"
            />
          </label>
          {asOf && (
            <span className="cockpit-badge border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400">
              as of {asOf}
            </span>
          )}
          {groupOptions.length > 0 && (
            <select
              value={groupId}
              onChange={(e) => setGroupId(e.target.value)}
              className="h-9 min-h-9 rounded-xl border border-input/80 bg-background/72 px-2.5 text-[0.75rem] text-foreground"
            >
              <option value="">all groups</option>
              {groupOptions.map((g) => <option key={g.id} value={g.id} title={g.id}>{scopeLabel(g)}</option>)}
            </select>
          )}
        </div>
      </div>

      {adding && (
        <AddEntity
          entityTypes={types}
          groups={groupOptions}
          onCreate={createNode}
          onClose={() => setAdding(false)}
        />
      )}

      <form onSubmit={handleSearch} className="flex items-center gap-2 border-b border-border/40 px-4 py-2.5">
        <Search size={14} className="shrink-0 text-muted-foreground" aria-hidden="true" />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search entities…"
          className="h-9"
        />
        <Button type="submit" size="sm" variant="outline">Search</Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={loadingAll}
          onClick={() => { void showAll(); }}
          title={groupId ? `Load every entity and relationship in ${groupId}` : 'Load every entity and relationship'}
        >
          {loadingAll ? 'Loading…' : 'Show all'}
        </Button>
        <Button
          type="button"
          size="sm"
          variant={clustered ? 'default' : 'outline'}
          onClick={toggleCluster}
          title={clustered
            ? 'Expand every community back to its members'
            : 'Group the loaded graph into communities and collapse each into one node'}
        >
          <Boxes size={12} aria-hidden="true" /> {clustered ? 'Uncluster' : 'Cluster'}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => setAuditing((v) => !v)}
          title="Duplicate candidates and structural health, read-only"
        >
          <ShieldAlert size={12} aria-hidden="true" /> Audit
        </Button>
      </form>

      {truncated && (
        <p className="flex items-center gap-1.5 border-b border-border/40 px-4 py-1.5 text-[0.7rem] text-amber-700 dark:text-amber-400">
          <AlertTriangle size={12} aria-hidden="true" />
          Showing the first 2000 — the graph is larger. Filter by group to see the rest.
        </p>
      )}

      {error && <p className="px-4 py-1.5 text-[0.7rem] text-destructive">{error}</p>}

      {/* The results sidebar comes and goes, so the persisted layout is keyed on
          the panels actually present (`panelIds`) — otherwise the canvas-only
          shape would overwrite the two-pane one. */}
      <SplitGroup
        id="cc-graph-results"
        panelIds={results.length > 0 ? ['results', 'canvas'] : ['canvas']}
        className="relative min-h-0 flex-1"
      >
        {results.length > 0 && (
          <>
            <Panel id="results" defaultSize={260} minSize={180} maxSize={480} className="border-r border-border/40">
              {results.map((n) => (
                <button
                  key={n.uuid}
                  onClick={() => addResult(n)}
                  className="flex w-full min-h-11 flex-col gap-0.5 border-b border-border/20 px-3 py-2 text-left hover:bg-muted/30"
                >
                  <span className="truncate text-[0.75rem] font-medium text-foreground">{n.name}</span>
                  <span className="truncate text-[0.65rem] text-muted-foreground">{n.labels.join(', ')}</span>
                </button>
              ))}
            </Panel>
            <SplitSeparator inset={false} />
          </>
        )}
        <Panel id="canvas" className="relative min-h-0 min-w-0" style={{ overflow: 'hidden' }}>
          {/* h-full/w-full, NOT absolute inset-0: cytoscape injects a
              position:relative rule on its container class that beats the
              `absolute` utility, and a relative inset-0 box collapses to
              0-height — an invisible canvas (found live 2026-08-10). */}
          <div ref={containerRef} className="h-full w-full" />
          {auditing && (
            <AuditPanel
              groupId={groupId}
              audit={graph.audit}
              onLoadNodes={loadNodesOnCanvas}
              onClose={() => setAuditing(false)}
            />
          )}
          <DetailDrawer
            selection={selection}
            episodes={episodes}
            onClose={() => setSelection(null)}
            entityTypes={types}
            curate={curate}
            onChange={(change) => { void applyChange(change); }}
            groups={groupOptions}
            onRemoveFromCanvas={removeFromCanvas}
          />
        </Panel>
      </SplitGroup>
    </div>
  );
}
