/**
 * EpisodeWalk — step through the graph one episode at a time (2026-09-13).
 *
 * The review unit is the ingestion episode: its source text on the left,
 * exactly the entities and relationships it produced on the canvas. Newest
 * first, because an episode is where extraction errors enter and the newest
 * ones are the ones nobody has looked at yet. Navigation, plus ONE write:
 * "Delete episode" (v2.61.0, design record D9) opens a confirm dialog that
 * renders exactly what the deletion removes; after it lands the walk moves
 * to the neighbouring episode. No "reviewed" mark exists; editing happens
 * through the same DetailDrawer as any other canvas selection.
 */
import { useCallback, useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight, Trash2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { EpisodeDeleteDialog } from './EpisodeDeleteDialog';
import type {
  EpisodeDeletePreview, EpisodeDeleteResult, EpisodeIndexRow, EpisodeSubgraph, GraphNode, GraphEdge,
} from './useGraph';

/** Server sends instants; the browser renders them in local time. */
function localTime(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function EpisodeWalk({
  groupId, episodeIndex, episodeSubgraph, onLoad, onClose, loadDeletePreview, deleteEpisode, onDeleted,
}: {
  groupId: string;
  episodeIndex: (groupId?: string) => Promise<EpisodeIndexRow[]>;
  episodeSubgraph: (uuid: string) => Promise<EpisodeSubgraph | null>;
  /** Replaces the canvas with this episode's subgraph. */
  onLoad: (nodes: GraphNode[], edges: GraphEdge[]) => void;
  onClose: () => void;
  loadDeletePreview: (uuid: string) => Promise<EpisodeDeletePreview>;
  deleteEpisode: (uuid: string, digest: string) => Promise<EpisodeDeleteResult>;
  /** After a deletion lands — the host refreshes its counts. */
  onDeleted?: (preview: EpisodeDeletePreview) => void;
}) {
  const [rows, setRows] = useState<EpisodeIndexRow[]>([]);
  const [idx, setIdx] = useState(0);
  const [current, setCurrent] = useState<EpisodeSubgraph | null>(null);
  const [deleting, setDeleting] = useState(false);

  /** The deleted episode leaves the index; the walk stays at the same
   *  position, which is now its neighbour (the next older one, or the newer
   *  one when it was the last). Changing `row` reloads the canvas. */
  const handleDeleted = useCallback((preview: EpisodeDeletePreview) => {
    const next = rows.filter((r) => r.uuid !== preview.episode.uuid);
    setRows(next);
    setIdx((i) => Math.max(0, Math.min(i, next.length - 1)));
    if (next.length === 0) onLoad([], []);
    onDeleted?.(preview);
  }, [rows, onDeleted, onLoad]);

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
          <div className="text-muted-foreground/80">{localTime(row.created_at)}</div>
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <span className="cockpit-badge" title={row.group_id}>{row.group_id}</span>
            <span className="cockpit-badge tabular-nums">
              {loaded ? `${loaded.nodes.length} entities · ${loaded.edges.length} relationships` : '…'}
            </span>
            <Button
              size="xs"
              variant="ghost"
              className="ml-auto text-destructive hover:text-destructive"
              onClick={() => setDeleting(true)}
              title="Delete this episode and what only it produced — shows exactly what goes first"
            >
              <Trash2 size={12} aria-hidden="true" /> Delete episode
            </Button>
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
      <EpisodeDeleteDialog
        episodeUuid={row?.uuid ?? null}
        open={deleting && !!row}
        onOpenChange={setDeleting}
        loadPreview={loadDeletePreview}
        deleteEpisode={deleteEpisode}
        onDeleted={handleDeleted}
      />
    </div>
  );
}
