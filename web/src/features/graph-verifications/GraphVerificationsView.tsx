/**
 * GraphVerificationsView — the cockpit Graph Verifications panel (Task 6,
 * docs/superpowers/plans/2026-08-19-graph-verification-auditor.md).
 *
 * The operator's decision on every row is "does the rendered delta match the
 * approved episode text" — so both sit on one screen (source-material-
 * inspectable-everywhere). Minimal on purpose: no list/detail split, no
 * canvas — every awaiting row renders in full, stacked; closed rows are a
 * flat read-only history list behind a toggle.
 */
import { useState, useCallback } from 'react';
import {
  ShieldCheck, CircleCheck, CircleAlert, AlertTriangle, RefreshCw,
  Check, Ban, History, Waypoints,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useGraphVerifications, type VerificationRow } from './useGraphVerifications';

function fmtTime(iso: string | null): string {
  if (!iso) return '';
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function VerdictChip({ verdict }: { verdict: VerificationRow['verdict'] }) {
  if (verdict === 'aligned') {
    return (
      <span className="cockpit-badge" data-tone="success">
        <CircleCheck size={12} aria-hidden="true" /> aligned
      </span>
    );
  }
  if (verdict === 'flag') {
    return (
      <span className="cockpit-badge" data-tone="warning">
        <CircleAlert size={12} aria-hidden="true" /> flag
      </span>
    );
  }
  return <span className="cockpit-badge">no verdict</span>;
}

function StatusChip({ status }: { status: VerificationRow['status'] }) {
  const tone = status === 'PROBLEM' ? 'danger' : status === 'VERIFIED' ? 'success' : 'primary';
  return <span className="cockpit-badge" data-tone={tone}>{status}</span>;
}

/** The mechanical-check messages, plainly worded — the spec's list, verbatim. */
function mechanicalMessages(m: VerificationRow['mechanical']): string[] {
  const out: string[] = [];
  if (m.missing) out.push('episode never landed');
  if (m.empty_delta) out.push('extraction produced nothing');
  if (m.unembedded && m.unembedded.length > 0) {
    out.push(`${m.unembedded.length} entities unembedded (invisible to semantic recall)`);
  }
  if (m.no_approved_text) out.push('no approved text on record');
  return out;
}

/** NEW = created by this episode; EXISTING = a pre-existing node/edge the
 *  episode touched (extraction dedupes onto what's already there). Absent on
 *  deltas audited before 2026-08-20 — no chip rather than a guess. */
function NewnessChip({ isNew }: { isNew?: boolean }) {
  if (isNew === true) return <span className="mr-1.5 cockpit-badge" data-tone="success">new</span>;
  if (isNew === false) return <span className="mr-1.5 cockpit-badge">existing</span>;
  return null;
}

function edgeValidity(e: { valid_at: string | null; invalid_at: string | null }): string {
  const parts: string[] = [];
  if (e.valid_at) parts.push(`valid from ${fmtTime(e.valid_at)}`);
  if (e.invalid_at) parts.push(`until ${fmtTime(e.invalid_at)}`);
  return parts.join(', ');
}

function DeltaBlock({ delta }: { delta: VerificationRow['delta'] }) {
  if (!delta) {
    return <p className="text-[0.733rem] text-muted-foreground">No delta recorded.</p>;
  }
  return (
    <div className="space-y-3">
      <div>
        <div className="mb-1 text-[0.667rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Entities ({delta.entities.length})
        </div>
        {delta.entities.length === 0 ? (
          <p className="text-[0.7rem] text-muted-foreground">none</p>
        ) : (
          <ul className="space-y-1">
            {delta.entities.map((e, i) => (
              <li key={i} className="text-[0.733rem] text-foreground/90">
                <NewnessChip isNew={e.new} />
                <span className="font-medium">{e.name}</span>
                {e.labels.filter((l) => l !== 'Entity').length > 0 && (
                  <span className="ml-1.5 text-muted-foreground">
                    ({e.labels.filter((l) => l !== 'Entity').join(', ')})
                  </span>
                )}
                {!e.has_embedding && (
                  <span className="ml-1.5 cockpit-badge" data-tone="warning">unembedded</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <div className="mb-1 text-[0.667rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Relationships ({delta.edges.length})
        </div>
        {delta.edges.length === 0 ? (
          <p className="text-[0.7rem] text-muted-foreground">none</p>
        ) : (
          <ul className="space-y-1">
            {delta.edges.map((e, i) => (
              <li key={i} className="text-[0.733rem] text-foreground/90">
                <NewnessChip isNew={e.new} />
                {e.source} —{e.name}→ {e.target}: <span className="text-foreground/75">{e.fact}</span>
                {edgeValidity(e) && (
                  <span className="ml-1.5 text-[0.667rem] text-muted-foreground">
                    ({edgeValidity(e)})
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <div className="mb-1 text-[0.667rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Invalidated ({delta.invalidated.length})
        </div>
        {delta.invalidated.length === 0 ? (
          <p className="text-[0.7rem] text-muted-foreground">none</p>
        ) : (
          <ul className="space-y-1">
            {delta.invalidated.map((e, i) => (
              <li key={i} className="text-[0.733rem] text-muted-foreground line-through decoration-destructive/60">
                {e.source} —{e.name}→ {e.target}: {e.fact}
                <span className="ml-1.5 no-underline text-[0.667rem] text-foreground/70">
                  (attributed by {e.attributed_by}
                  {e.expired_at ? `, retired ${fmtTime(e.expired_at)}` : ''})
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function AwaitingCard({ row, confirm, problem }: {
  row: VerificationRow;
  confirm: (id: string) => Promise<void>;
  problem: (id: string, note: string, remediate: boolean) => Promise<void>;
}) {
  const [note, setNote] = useState('');
  const [showProblem, setShowProblem] = useState(false);
  const [remediate, setRemediate] = useState(true);
  const [acting, setActing] = useState<'' | 'confirm' | 'problem'>('');
  const [error, setError] = useState('');
  const flags = mechanicalMessages(row.mechanical);

  const act = useCallback(async (kind: 'confirm' | 'problem') => {
    setActing(kind); setError('');
    try {
      if (kind === 'confirm') await confirm(row.id);
      else await problem(row.id, note.trim(), remediate);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActing('');
    }
  }, [row.id, note, remediate, confirm, problem]);

  return (
    <div className="rounded-lg border border-border/40 p-4">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium text-foreground">{row.episode_name}</p>
        <StatusChip status={row.status} />
        <VerdictChip verdict={row.verdict} />
        {row.remediation_of && (
          <span className="cockpit-badge" data-tone="primary" title="Fresh read-back after an approved curation fix">
            re-check after fix
          </span>
        )}
        {row.has_invalidations && (
          <span className="cockpit-badge" data-tone="danger">
            <AlertTriangle size={12} aria-hidden="true" /> invalidations
          </span>
        )}
        <span className="ml-auto text-[0.667rem] text-muted-foreground">{fmtTime(row.created_at)}</span>
      </div>

      {flags.length > 0 && (
        <ul className="mb-2 space-y-0.5">
          {flags.map((f, i) => (
            <li key={i} className="text-[0.733rem] text-destructive">{f}</li>
          ))}
        </ul>
      )}

      {row.approved_text != null && (
        <div className="mb-3">
          <div className="mb-1 text-[0.667rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Approved text
          </div>
          <blockquote className="whitespace-pre-wrap cockpit-wrap rounded-md border-l-2 border-primary/40 bg-muted/40 p-2.5 text-[0.733rem] leading-relaxed text-foreground/90">
            {row.approved_text || '(none on record)'}
          </blockquote>
        </div>
      )}

      <DeltaBlock delta={row.delta} />

      {row.verdict_rationale && (
        <div className="mt-3">
          <div className="mb-1 text-[0.667rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Auditor rationale
          </div>
          <p className="text-[0.733rem] leading-relaxed text-foreground/85">{row.verdict_rationale}</p>
        </div>
      )}

      <div className="mt-3 border-t border-border/40 pt-3">
        {error && <p className="mb-2 text-[0.733rem] text-destructive">{error}</p>}
        {showProblem && (
          <>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="What diverged from the approved claim (required)"
              rows={2}
              className="mb-2 w-full resize-none rounded-lg border border-border/60 bg-background px-2.5 py-2 text-xs text-foreground placeholder:text-muted-foreground/70 focus:outline-none focus:ring-1 focus:ring-primary/50"
            />
            <label className="mb-2 flex items-center gap-2 text-[0.733rem] text-foreground/90">
              <input
                type="checkbox"
                checked={remediate}
                onChange={(e) => setRemediate(e.target.checked)}
                className="size-3.5 accent-primary"
              />
              Task the graph curator to propose a fix (its proposals land in
              Decisions; the applied fix returns here as a re-check)
            </label>
          </>
        )}
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={acting !== ''}
            onClick={() => act('confirm')}
            className="border-green/30 bg-green/8 text-green hover:bg-green/12"
          >
            <Check size={13} />
            {acting === 'confirm' ? 'Confirming…' : 'Confirm'}
          </Button>
          {showProblem ? (
            <Button
              variant="outline"
              size="sm"
              disabled={acting !== '' || !note.trim()}
              onClick={() => act('problem')}
              className="border-destructive/22 bg-destructive/8 text-destructive hover:bg-destructive/14"
            >
              <Ban size={13} />
              {acting === 'problem' ? 'Sending…' : 'Submit problem'}
            </Button>
          ) : (
            <Button variant="outline" size="sm" onClick={() => setShowProblem(true)}>
              <Ban size={13} /> Problem…
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

function HistoryRow({ row }: { row: VerificationRow }) {
  return (
    <li className="flex flex-wrap items-center gap-2 border-b border-border/20 px-1 py-1.5 text-[0.733rem] last:border-b-0">
      <span className="min-w-0 flex-1 truncate text-foreground/90">{row.episode_name}</span>
      <StatusChip status={row.status} />
      {row.closed_by && <span className="text-muted-foreground">by {row.closed_by}</span>}
      {row.problem_note && (
        <span className="min-w-0 max-w-[40ch] truncate text-muted-foreground" title={row.problem_note}>
          “{row.problem_note}”
        </span>
      )}
    </li>
  );
}

export function GraphVerificationsView() {
  const {
    enabled, mode, awaiting, recent_closed: recentClosed, loading, error, refresh, confirm, problem,
  } = useGraphVerifications();
  const [showHistory, setShowHistory] = useState(false);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-border/40 px-4 py-3">
        <Waypoints size={14} className="text-primary" aria-hidden="true" />
        <span className="text-[0.8rem] font-semibold uppercase tracking-[0.14em] text-foreground">
          Graph Verifications
        </span>
        <span className="cockpit-badge" title="Whether the sweep and judgment agent are running, and what shadow/active does with a clean verdict.">
          <ShieldCheck size={12} aria-hidden="true" />
          graph auditor: {enabled ? mode : 'disabled'}{enabled && mode === 'shadow' ? ' (supervised)' : ''}
        </span>
        <span className="cockpit-badge tabular-nums">{awaiting.length} awaiting</span>
        <button
          onClick={() => refresh()}
          title="Refresh"
          aria-label="Refresh graph verifications"
          className="ml-auto text-muted-foreground transition-colors hover:text-foreground"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {error && <p className="mb-3 text-[0.733rem] text-destructive">{error}</p>}

        {awaiting.length === 0 && !loading ? (
          <p className="text-[0.733rem] text-muted-foreground">Nothing awaiting review.</p>
        ) : (
          <div className="space-y-3">
            {awaiting.map((row) => (
              <AwaitingCard key={row.id} row={row} confirm={confirm} problem={problem} />
            ))}
          </div>
        )}

        <div className="mt-5 border-t border-border/40 pt-3">
          <button
            type="button"
            onClick={() => setShowHistory((v) => !v)}
            aria-expanded={showHistory}
            className="flex items-center gap-1.5 text-[0.667rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground transition-colors hover:text-foreground"
          >
            <History size={12} aria-hidden="true" />
            History ({recentClosed.length})
          </button>
          {showHistory && (
            recentClosed.length === 0 ? (
              <p className="mt-2 text-[0.733rem] text-muted-foreground">No closed verifications yet.</p>
            ) : (
              <ul className="mt-2">
                {recentClosed.map((row) => <HistoryRow key={row.id} row={row} />)}
              </ul>
            )
          )}
        </div>
      </div>
    </div>
  );
}
