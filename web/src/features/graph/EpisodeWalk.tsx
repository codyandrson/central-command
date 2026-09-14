/**
 * EpisodeWalk — step through the graph one episode at a time (2026-09-13).
 *
 * The review unit is the ingestion episode: its source text on the left,
 * exactly the entities and relationships it produced on the canvas. Newest
 * first, because an episode is where extraction errors enter and the newest
 * ones are the ones nobody has looked at yet. Purely a navigation view —
 * nothing is written to the graph and no "reviewed" mark exists; editing
 * happens through the same DetailDrawer as any other canvas selection.
 */
import { useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { EpisodeIndexRow, EpisodeSubgraph, GraphNode, GraphEdge } from './useGraph';

export function EpisodeWalk({ groupId, episodeIndex, episodeSubgraph, onLoad, onClose }: {
  groupId: string;
  episodeIndex: (groupId?: string) => Promise<EpisodeIndexRow[]>;
  episodeSubgraph: (uuid: string) => Promise<EpisodeSubgraph | null>;
  /** Replaces the canvas with this episode's subgraph. */
  onLoad: (nodes: GraphNode[], edges: GraphEdge[]) => void;
  onClose: () => void;
}) {
  const [rows, setRows] = useState<EpisodeIndexRow[]>([]);
  const [idx, setIdx] = useState(0);
  const [current, setCurrent] = useState<EpisodeSubgraph | null>(null);

  // A group change restarts the walk from its newest episode.
  useEffect(() => {
    let cancelled = false;
    void episodeIndex(groupId || undefined).then((r) => {
      if (cancelled) return;
      setRows(r);
      setIdx(0);
    });
    return () => { cancelled = true; };
  }, [groupId, episodeIndex]);

  const row = rows[idx];
  useEffect(() => {
    if (!row) return;
    let cancelled = false;
    void episodeSubgraph(row.uuid).then((sg) => {
      if (cancelled) return;
      setCurrent(sg);
      if (sg) onLoad(sg.nodes, sg.edges);
    });
    return () => { cancelled = true; };
  }, [row, episodeSubgraph, onLoad]);
  // "Loading" is derived: the subgraph on hand belongs to a different step.
  const loaded = current && row && current.episode.uuid === row.uuid ? current : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-1 border-b border-border/40 px-3 py-2">
        <span className="text-[0.667rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">Episode walk</span>
        <span className="ml-auto text-[0.7rem] tabular-nums text-muted-foreground">
          {rows.length === 0 ? '0 of 0' : `${idx + 1} of ${rows.length}`}
        </span>
        <Button size="sm" variant="ghost" aria-label="Previous episode" disabled={idx <= 0}
          onClick={() => setIdx((i) => Math.max(0, i - 1))}>
          <ChevronLeft size={14} aria-hidden="true" />
        </Button>
        <Button size="sm" variant="ghost" aria-label="Next episode" disabled={idx >= rows.length - 1}
          onClick={() => setIdx((i) => Math.min(rows.length - 1, i + 1))}>
          <ChevronRight size={14} aria-hidden="true" />
        </Button>
        <button onClick={onClose} aria-label="Close episode walk"
          className="flex size-6 items-center justify-center rounded-md text-muted-foreground hover:text-foreground">
          <X size={14} aria-hidden="true" />
        </button>
      </div>
      {!row ? (
        <p className="px-3 py-2 text-[0.7rem] text-muted-foreground">No episodes{groupId ? ' in this group' : ''}.</p>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2 text-[0.7rem]">
          <div className="font-medium text-foreground">{row.name}</div>
          <div className="text-muted-foreground">{row.source_description}</div>
          <div className="text-muted-foreground/80">{row.created_at}</div>
          <div className="mt-1 flex flex-wrap gap-1">
            <span className="cockpit-badge" title={row.group_id}>{row.group_id}</span>
            <span className="cockpit-badge tabular-nums">
              {loaded ? `${loaded.nodes.length} entities · ${loaded.edges.length} relationships` : '…'}
            </span>
          </div>
          <select
            value={row.uuid}
            onChange={(e) => setIdx(rows.findIndex((r) => r.uuid === e.target.value))}
            aria-label="Jump to episode"
            className="mt-2 h-8 w-full rounded-md border border-input/80 bg-background/72 px-2 text-[0.7rem] text-foreground"
          >
            {rows.map((r, i) => (
              <option key={r.uuid} value={r.uuid}>{i + 1}. {r.name} ({r.entity_count})</option>
            ))}
          </select>
          <div className="mt-2 mb-1 text-[0.667rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">Source</div>
          <pre className="whitespace-pre-wrap cockpit-wrap break-words font-sans text-[0.7rem] text-foreground/90">
            {loaded ? loaded.episode.content || '(empty)' : '…'}
          </pre>
        </div>
      )}
    </div>
  );
}
