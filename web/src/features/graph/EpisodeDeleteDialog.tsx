/**
 * EpisodeDeleteDialog — delete one knowledge-graph episode, after seeing
 * exactly what goes (design record 2026-10-04, D9).
 *
 * The preview is the backend's: upstream's remove_episode rule (the facts the
 * episode was FIRST to create, the entities no other episode mentions), the
 * collateral facts that fall with a deleted entity, and the surviving facts
 * that only lose this episode as a source. Deleting is irreversible, so the
 * operator ticks an explicit acknowledgement, and the confirm carries the
 * preview's digest: if the graph moved since, the backend refuses (409) and
 * the dialog re-reads the preview instead of deleting something else.
 */
import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Loader2 } from 'lucide-react';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import type { EpisodeDeletePreview, EpisodeDeleteResult, PreviewFact } from './useGraph';

/** Server sends instants; the browser renders them in local time. */
function localTime(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function FactList({ facts }: { facts: PreviewFact[] }) {
  return (
    <ul className="space-y-0.5">
      {facts.map((f) => (
        <li key={f.uuid} className="text-[0.7rem] text-foreground/90">
          <span className="text-muted-foreground">{f.source_name ?? '?'} → {f.target_name ?? '?'}:</span>{' '}
          {f.fact ?? f.name ?? f.uuid}
        </li>
      ))}
    </ul>
  );
}

function Block({ title, count, tone, children }: {
  title: string; count: number; tone?: 'danger'; children: React.ReactNode;
}) {
  if (count === 0) return null;
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 text-[0.667rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        {title}
        <span className="cockpit-badge tabular-nums" data-tone={tone}>{count}</span>
      </div>
      {children}
    </div>
  );
}

export function EpisodeDeleteDialog({
  episodeUuid, open, onOpenChange, loadPreview, deleteEpisode, onDeleted,
}: {
  episodeUuid: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  loadPreview: (uuid: string) => Promise<EpisodeDeletePreview>;
  deleteEpisode: (uuid: string, digest: string) => Promise<EpisodeDeleteResult>;
  /** Fires once the backend reports the deletion DONE, with the preview it
   *  deleted — the host moves on and refreshes its canvas. */
  onDeleted: (preview: EpisodeDeletePreview) => void;
}) {
  const [preview, setPreview] = useState<EpisodeDeletePreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [ack, setAck] = useState(false);
  const [error, setError] = useState('');
  const [queued, setQueued] = useState<number | null>(null);

  const read = useCallback(async (uuid: string) => {
    setLoading(true);
    setPreview(null);
    setAck(false);
    try {
      setPreview(await loadPreview(uuid));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [loadPreview]);

  useEffect(() => {
    if (!open || !episodeUuid) return;
    setError('');
    setQueued(null);
    void read(episodeUuid);
  }, [open, episodeUuid, read]);

  const confirm = async () => {
    if (!preview || !ack) return;
    setDeleting(true);
    setError('');
    try {
      const result = await deleteEpisode(preview.episode.uuid, preview.digest);
      if (result.status === 'done') {
        onDeleted(preview);
        onOpenChange(false);
      } else {
        setQueued(result.job_id);
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setError(message);
      // The graph moved since the preview was read: show the new one rather
      // than letting the operator confirm a set that no longer exists.
      if (/changed since/i.test(message)) void read(preview.episode.uuid);
    } finally {
      setDeleting(false);
    }
  };

  const total = preview
    ? preview.facts.length + preview.collateral_facts.length + preview.entities.length
    : 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Delete episode</DialogTitle>
          <DialogDescription>
            Irreversible. The episode's text and what only it produced are deleted;
            to restore it, propose the episode again. Reworking an episode is this
            deletion plus a fresh episode with the corrected text.
          </DialogDescription>
        </DialogHeader>

        {loading && (
          <p className="flex items-center gap-1.5 text-[0.733rem] text-muted-foreground">
            <Loader2 size={12} className="animate-spin" aria-hidden="true" /> Reading what would be deleted…
          </p>
        )}

        {preview && (
          <div className="space-y-3 text-[0.733rem]">
            <div className="rounded-md border border-border/40 bg-muted/20 px-2.5 py-2">
              <div className="font-medium text-foreground">{preview.episode.name}</div>
              <div className="text-muted-foreground">
                {preview.episode.group_id}
                {preview.episode.created_at && ` · ${localTime(preview.episode.created_at)}`}
              </div>
              <p className="mt-1 whitespace-pre-wrap cockpit-wrap break-words text-foreground/90">{preview.episode.content}</p>
            </div>
            <p className="tabular-nums text-foreground" data-testid="delete-counts">
              Deletes the episode, {preview.facts.length} fact{preview.facts.length === 1 ? '' : 's'},{' '}
              {preview.entities.length} entit{preview.entities.length === 1 ? 'y' : 'ies'}
              {preview.collateral_facts.length > 0 && ` and ${preview.collateral_facts.length} collateral fact${preview.collateral_facts.length === 1 ? '' : 's'}`}.
              {preview.provenance_facts.length > 0
                && ` ${preview.provenance_facts.length} surviving fact${preview.provenance_facts.length === 1 ? '' : 's'} stop citing it.`}
            </p>
            <Block title="Facts it created" count={preview.facts.length}>
              <FactList facts={preview.facts} />
            </Block>
            <Block title="Entities only it mentions" count={preview.entities.length}>
              <ul className="space-y-0.5">
                {preview.entities.map((n) => (
                  <li key={n.uuid} className="text-[0.7rem] text-foreground/90">
                    {n.name} <span className="text-muted-foreground">({n.labels.filter((l) => l !== 'Entity').join(', ') || 'untyped'})</span>
                  </li>
                ))}
              </ul>
            </Block>
            <Block title="Collateral — other episodes' facts on a deleted entity" count={preview.collateral_facts.length} tone="danger">
              <p className="flex items-center gap-1.5 text-[0.7rem] text-amber-700 dark:text-amber-400">
                <AlertTriangle size={12} aria-hidden="true" />
                Not created by this episode — deleted because an entity they touch is deleted.
              </p>
              <FactList facts={preview.collateral_facts} />
            </Block>
            <Block title="Survive, but stop citing this episode" count={preview.provenance_facts.length}>
              <FactList facts={preview.provenance_facts} />
            </Block>
            {total === 0 && (
              <p className="text-[0.7rem] text-muted-foreground">Only the episode itself is deleted; every fact and entity it touched survives.</p>
            )}
            <label className="flex items-center gap-2 text-[0.733rem] text-foreground">
              <input
                type="checkbox"
                checked={ack}
                onChange={(e) => setAck(e.target.checked)}
                aria-label="I understand this deletion is irreversible"
              />
              I understand this deletion is irreversible.
            </label>
          </div>
        )}

        {queued !== null && (
          <p className="text-[0.733rem] text-muted-foreground" role="status">
            Queued (job {queued}) behind earlier work in this group — it runs in order.
          </p>
        )}
        {error && <p className="text-[0.733rem] text-destructive" role="alert">{error}</p>}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
          <Button
            variant="destructive"
            disabled={!preview || !ack || deleting || queued !== null}
            onClick={() => { void confirm(); }}
          >
            {deleting ? 'Deleting…' : 'Delete episode'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
