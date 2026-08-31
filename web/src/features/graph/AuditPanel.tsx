/**
 * AuditPanel — read-only graph quality report (duplicate entities, duplicate
 * edges, structural health) surfaced from `GET /api/graph/audit`. No writes
 * happen here: clicking a result loads the node(s) onto the existing canvas
 * via the same expand/addResult machinery GraphView already uses for search
 * results, so the operator curates through the DetailDrawer's existing
 * merge/edit tooling — this panel never calls a curation endpoint itself.
 */
import { useState } from 'react';
import { X, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type { GraphNode } from './useGraph';
import type {
  GraphAudit, AuditNodeRef, AuditHealthNode, useGraph,
} from './useGraph';

const DEFAULT_THRESHOLD = 0.87;

function toGraphNode(ref: AuditNodeRef | AuditHealthNode): GraphNode {
  return {
    uuid: ref.uuid,
    name: ref.name,
    labels: 'labels' in ref ? ref.labels : [],
    summary: '',
    group_id: ref.group_id,
  };
}

function Section({ title, count, empty, children }: {
  title: string; count: number; empty: string; children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5 border-t border-border/30 pt-3 first:border-t-0 first:pt-0">
      <div className="flex items-center justify-between">
        <span className="text-[0.667rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          {title}
        </span>
        <span className="cockpit-badge tabular-nums">{count}</span>
      </div>
      {count === 0 ? (
        <p className="text-[0.7rem] text-muted-foreground">{empty}</p>
      ) : children}
    </div>
  );
}

export function AuditPanel({ groupId, audit, onLoadNodes, onClose }: {
  groupId: string;
  audit: ReturnType<typeof useGraph>['audit'];
  onLoadNodes: (nodes: GraphNode[]) => void;
  onClose: () => void;
}) {
  const [threshold, setThreshold] = useState(DEFAULT_THRESHOLD);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [report, setReport] = useState<GraphAudit | null>(null);

  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      const result = await audit(groupId || undefined, threshold);
      if (!result) throw new Error('audit failed');
      setReport(result);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="absolute left-0 top-0 z-10 h-full w-[380px] max-w-[90vw] overflow-y-auto border-r border-border/60 bg-card/95 shadow-[12px_0_32px_rgba(0,0,0,0.24)]">
      <div className="flex items-center justify-between border-b border-border/40 px-4 py-3">
        <span className="text-[0.733rem] font-semibold uppercase tracking-[0.12em] text-foreground">
          Graph audit
        </span>
        <button
          onClick={onClose}
          aria-label="Close audit panel"
          className="flex size-6 items-center justify-center rounded-md text-muted-foreground hover:text-foreground"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>

      <div className="space-y-2 border-b border-border/40 px-4 py-3">
        <p className="text-[0.7rem] text-muted-foreground">
          {groupId ? `Scope: ${groupId}` : 'Scope: all groups'} · read-only —
          nothing here writes to the graph.
        </p>
        <label className="flex items-center gap-2 text-[0.667rem] text-muted-foreground">
          similarity threshold
          <Input
            type="number"
            min={0.5}
            max={1}
            step={0.01}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
            className="h-9 w-24"
          />
        </label>
        <Button size="sm" disabled={busy} onClick={() => { void run(); }}>
          {busy ? 'Running…' : 'Run audit'}
        </Button>
        {err && (
          <p className="flex items-center gap-1.5 text-[0.7rem] text-destructive">
            <AlertTriangle size={12} aria-hidden="true" /> {err}
          </p>
        )}
      </div>

      {report && (
        <div className="space-y-3 px-4 py-3">
          <Section
            title="Duplicate entities"
            count={report.duplicate_entities.length}
            empty="No duplicate candidates found."
          >
            <div className="space-y-1.5">
              {report.duplicate_entities.map((pair) => (
                <button
                  key={`${pair.a.uuid}-${pair.b.uuid}`}
                  onClick={() => onLoadNodes([toGraphNode(pair.a), toGraphNode(pair.b)])}
                  className="w-full rounded-md border border-border/40 bg-muted/20 px-2.5 py-2 text-left text-[0.7rem] hover:bg-muted/30"
                >
                  <div className="font-medium text-foreground">{pair.a.name} ↔ {pair.b.name}</div>
                  <div className="text-muted-foreground">
                    {pair.a.labels.join(', ')} / {pair.b.labels.join(', ')}
                  </div>
                  <div className="text-muted-foreground/80">
                    {(pair.similarity * 100).toFixed(0)}% similar · {pair.basis}
                  </div>
                </button>
              ))}
            </div>
          </Section>

          <Section
            title="Duplicate edges"
            count={report.duplicate_edges.length}
            empty="No competing relationships found."
          >
            <div className="space-y-1.5">
              {report.duplicate_edges.map((group) => (
                <button
                  key={`${group.source.uuid}-${group.target.uuid}`}
                  onClick={() => onLoadNodes([
                    toGraphNode({ ...group.source, labels: [], group_id: '' }),
                    toGraphNode({ ...group.target, labels: [], group_id: '' }),
                  ])}
                  className="w-full rounded-md border border-border/40 bg-muted/20 px-2.5 py-2 text-left text-[0.7rem] hover:bg-muted/30"
                >
                  <div className="font-medium text-foreground">{group.source.name} → {group.target.name}</div>
                  <ul className="mt-1 space-y-0.5 text-muted-foreground">
                    {group.edges.map((e) => <li key={e.uuid}>· {e.fact}</li>)}
                  </ul>
                </button>
              ))}
            </div>
          </Section>

          <Section
            title="Isolated entities"
            count={report.health.counts.isolated_nodes}
            empty="No isolated entities."
          >
            <HealthList nodes={report.health.isolated_nodes} onLoadNodes={onLoadNodes} />
          </Section>

          <Section
            title="Untyped entities"
            count={report.health.counts.untyped_nodes}
            empty="No untyped entities."
          >
            <HealthList nodes={report.health.untyped_nodes} onLoadNodes={onLoadNodes} />
          </Section>

          <Section
            title="Missing embedding"
            count={report.health.counts.missing_embedding_nodes}
            empty="Every entity has an embedding."
          >
            <HealthList nodes={report.health.missing_embedding_nodes} onLoadNodes={onLoadNodes} />
          </Section>

          <Section
            title="Dangling edges"
            count={report.health.counts.dangling_edges}
            empty="No dangling relationships."
          >
            <div className="space-y-1">
              {report.health.dangling_edges.map((e) => (
                <div key={e.uuid} className="rounded-md border border-border/40 bg-muted/10 px-2.5 py-1.5 text-[0.7rem] text-muted-foreground">
                  claims {e.claimed_source} → {e.claimed_target}, actually {e.actual_source} → {e.actual_target}
                </div>
              ))}
            </div>
          </Section>
        </div>
      )}
    </div>
  );
}

function HealthList({ nodes, onLoadNodes }: {
  nodes: AuditHealthNode[];
  onLoadNodes: (nodes: GraphNode[]) => void;
}) {
  return (
    <div className="space-y-1">
      {nodes.map((n) => (
        <button
          key={n.uuid}
          onClick={() => onLoadNodes([toGraphNode(n)])}
          className="block w-full truncate rounded-md border border-border/40 bg-muted/20 px-2.5 py-1.5 text-left text-[0.7rem] text-foreground hover:bg-muted/30"
        >
          {n.name}
        </button>
      ))}
    </div>
  );
}
