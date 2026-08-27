/**
 * LoeView — Goals (lines of effort).
 *
 * One tile per LOE: status border + dot, cadence chip, confidence, latest
 * summary, relative check-in date, and a bare sparkline of confidence over
 * time. Misses render neutrally — no streaks, no "FAILED" styling, no guilt
 * copy — the check-in cadence is informational, not a scorecard.
 *
 * Plain responsive grid, no `@container`/`@3xl:` split — see the CLAUDE.md
 * bite mark on container queries resolving against an ANCESTOR, never the
 * element's own; a flat grid has no such trap.
 */
import { useMemo, useState } from 'react';
import { RefreshCw, Target } from 'lucide-react';
import {
  LineChart, Line, ResponsiveContainer, AreaChart, Area, ReferenceArea, ReferenceLine,
  XAxis, YAxis, Tooltip,
} from 'recharts';
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { timeAgo } from '@/lib/formatting';
import { useLoe } from './useLoe';
import { lightTone } from './lightTone';
import { GoalIntentDialog, ACCOUNTABILITY_AGENT_ID } from './GoalIntentDialog';
import type { Loe } from './types';

const NEW_GOAL_MESSAGE = (text: string, cadence: string) =>
  `I want to set up a new goal. ${text} (cadence guess: ${cadence}). Interview me `
  + 'as needed, show me a [goal-preview:...] draft as we refine it, and propose it '
  + 'when we agree.';

const discussMessage = (loe: Loe) => (text: string) => {
  const questions = loe.semantic.questions.map((q, i) => `${i + 1}. ${q}`).join(' ');
  return `I want to discuss changes to the goal "${loe.name}" (id: ${loe.id}). `
    + `Current questions: ${questions} Current thresholds: ${loe.semantic.thresholds}. `
    + `What I want to change: ${text}. Interview me as needed, show me a `
    + '[goal-preview:...] draft as we refine it, and propose it when we agree.';
};

function ConfidenceSparkline({ history, color }: { history: Loe['history']; color: string }) {
  if (history.length < 2) return null;
  // API delivers newest-first; the sparkline reads left-to-right in time.
  const data = [...history].reverse().map((h, i) => ({ i, confidence: h.confidence }));
  return (
    <div className="mt-2 h-10 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <Line
            type="monotone"
            dataKey="confidence"
            stroke={color}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * A horizontal strip of check-in dots positioned by REAL date, not bucketed
 * into a fake calendar — a goal checked in three times in one week and then
 * not again for a month should read as exactly that gap, not an evenly
 * spaced row. Dated check-ins are placed by elapsed time; any undated one
 * (should not happen post-2026-08-12, but the wire type allows it) falls
 * back to even spacing among themselves at the strip's start.
 */
function CheckinDotTimeline({ history }: { history: Loe['history'] }) {
  if (history.length === 0) return null;
  const dated = [...history].reverse().filter((h) => h.created_at);
  const undated = history.filter((h) => !h.created_at);
  if (dated.length < 2) {
    // Not enough real dates to place meaningfully — even spacing is honest here.
    const all = [...history].reverse();
    return (
      <div className="mt-1.5 flex items-center gap-1" title="check-in history">
        {all.map((h, i) => (
          <span key={i} className={`h-1.5 w-1.5 shrink-0 rounded-full ${lightTone(h.light).dot}`} aria-hidden="true" />
        ))}
      </div>
    );
  }
  const times = dated.map((h) => new Date(h.created_at as string).getTime());
  const min = Math.min(...times);
  const max = Math.max(...times);
  const span = max - min || 1;
  return (
    <div className="relative mt-1.5 h-3 w-full" title="check-ins over time">
      {undated.map((h, i) => (
        <span
          key={`u${i}`}
          className={`absolute top-0.5 h-1.5 w-1.5 -translate-x-1/2 rounded-full ${lightTone(h.light).dot}`}
          style={{ left: 0 }}
          aria-hidden="true"
        />
      ))}
      {dated.map((h, i) => {
        const pct = ((new Date(h.created_at as string).getTime() - min) / span) * 100;
        return (
          <span
            key={i}
            className={`absolute top-0.5 h-1.5 w-1.5 -translate-x-1/2 rounded-full ${lightTone(h.light).dot}`}
            style={{ left: `${pct}%` }}
            aria-hidden="true"
          />
        );
      })}
    </div>
  );
}

/**
 * The detail dialog's trend chart: confidence over time (0-100), a shaded
 * "healthy" band at 70-100, and a vertical marker at every check-in whose
 * semantic_version differs from the one before it — a confidence jump right
 * after a recalibration is a new yardstick, not new progress.
 */
function ConfidenceTrendChart({ history, color }: { history: Loe['history']; color: string }) {
  if (history.length < 2) return null;
  // Newest-first on the wire; charts read left-to-right in time.
  const chronological = [...history].reverse();
  const data = chronological.map((h, i) => ({
    i, confidence: h.confidence, created_at: h.created_at,
  }));
  const recalibrations = chronological
    .map((h, i) => ({ i, semantic_version: h.semantic_version }))
    .filter((h, idx, arr) => idx > 0 && h.semantic_version !== arr[idx - 1].semantic_version);

  return (
    <div className="h-40 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
          <XAxis dataKey="i" hide />
          <YAxis domain={[0, 100]} width={28} tick={{ fontSize: 10 }} />
          <Tooltip
            formatter={(value) => [`${value ?? ''}%`, 'confidence']}
            labelFormatter={(i) => {
              const at = data[Number(i)]?.created_at;
              return at ? `${timeAgo(at)} ago` : '';
            }}
          />
          <ReferenceArea y1={70} y2={100} fill={color} fillOpacity={0.08} strokeOpacity={0} />
          {recalibrations.map((r) => (
            <ReferenceLine
              key={r.i}
              x={r.i}
              stroke="var(--color-muted-foreground)"
              strokeOpacity={0.5}
              strokeDasharray="3 3"
            />
          ))}
          <Area
            type="monotone"
            dataKey="confidence"
            stroke={color}
            fill={color}
            fillOpacity={0.15}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Mean latest confidence across ACTIVE goals, with the delta versus each
 * goal's own previous check-in — a compact header rollup, not a dashboard. */
function useConfidenceRollup(loes: Loe[]) {
  return useMemo(() => {
    const active = loes.filter((l) => l.status === 'ACTIVE' && l.latest !== null);
    if (active.length === 0) return null;
    const mean = active.reduce((s, l) => s + (l.latest?.confidence ?? 0), 0) / active.length;
    const withPrev = active.filter((l) => l.history.length > 1);
    let delta: number | null = null;
    if (withPrev.length > 0) {
      const prevMean = withPrev.reduce((s, l) => s + l.history[1].confidence, 0) / withPrev.length;
      const curMean = withPrev.reduce((s, l) => s + (l.latest?.confidence ?? 0), 0) / withPrev.length;
      delta = curMean - prevMean;
    }
    return { mean, delta, count: active.length };
  }, [loes]);
}

/**
 * The detail panel — questions/thresholds, full check-in history, and the
 * semantic version history. A dialog overlay on purpose: it displaces
 * nothing in the grid (UI-stability rule) and the id stays live across the
 * 60s poll by re-resolving from `loes` in the parent.
 */
function LoeDetailDialog({ loe, onClose, onDiscuss }: {
  loe: Loe | null; onClose: () => void; onDiscuss: (loe: Loe) => void;
}) {
  return (
    <Dialog open={loe !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        {loe && (
          <>
            <DialogHeader>
              <div className="flex items-start justify-between gap-2">
                <DialogTitle className="flex items-center gap-2 text-[1.2rem] font-semibold text-foreground">
                  <span
                    className={`h-2.5 w-2.5 shrink-0 rounded-full ${lightTone(loe.latest?.light).dot}`}
                    aria-hidden="true"
                  />
                  {loe.name}
                </DialogTitle>
                <Button variant="outline" size="sm" onClick={() => onDiscuss(loe)} className="shrink-0">
                  Discuss changes
                </Button>
              </div>
              <DialogDescription className="text-sm text-muted-foreground">
                {loe.cadence} check-ins by <span className="font-mono">{loe.agent_id}</span>
                {loe.status !== 'ACTIVE' && ` · ${loe.status}`}
                {' · semantic v'}{loe.semantic_version}
              </DialogDescription>
            </DialogHeader>

            {loe.history.length >= 2 && (
              <section>
                <h3 className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                  Confidence trend
                </h3>
                <p className="mt-0.5 text-[0.667rem] text-muted-foreground/70">
                  shaded band = healthy (70%+) · dashed line = recalibration
                </p>
                <ConfidenceTrendChart history={loe.history} color={lightTone(loe.latest?.light).line} />
              </section>
            )}

            <section>
              <h3 className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                Questions
              </h3>
              <ul className="mt-1.5 list-disc space-y-1 pl-5 text-[0.8rem] text-foreground">
                {loe.semantic.questions.map((q, i) => <li key={i}>{q}</li>)}
              </ul>
              <h3 className="mt-3 text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                Thresholds
              </h3>
              <p className="mt-1.5 text-[0.8rem] text-foreground">{loe.semantic.thresholds}</p>
            </section>

            <section>
              <h3 className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                Check-ins
              </h3>
              {loe.history.length === 0 && (
                <p className="mt-1.5 text-[0.733rem] text-muted-foreground">No check-ins yet.</p>
              )}
              <ul className="mt-1.5 space-y-2">
                {loe.history.map((c, i) => (
                  <li key={i} className="flex gap-2">
                    <span
                      className={`mt-1 h-2 w-2 shrink-0 rounded-full ${lightTone(c.light).dot}`}
                      aria-hidden="true"
                    />
                    <div className="min-w-0">
                      <div className="text-[0.667rem] text-muted-foreground/80">
                        {c.created_at ? `${timeAgo(c.created_at)} ago` : ''}
                        {' · '}{c.confidence}% confidence
                        {c.semantic_version !== undefined && ` · scored under v${c.semantic_version}`}
                      </div>
                      {c.summary && <p className="text-[0.767rem] text-foreground">{c.summary}</p>}
                    </div>
                  </li>
                ))}
              </ul>
            </section>

            <section>
              <h3 className="text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                Version history
              </h3>
              <ul className="mt-1.5 space-y-2">
                {loe.semantic_history.map((v) => (
                  <li key={v.version} className="rounded-md border border-border/50 bg-background/40 p-2">
                    <div className="text-[0.7rem] font-semibold text-foreground">
                      v{v.version}
                      <span className="ml-2 font-normal text-muted-foreground">
                        {v.superseded_at ? `superseded ${timeAgo(v.superseded_at)} ago` : 'current'}
                      </span>
                    </div>
                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[0.733rem] text-muted-foreground">
                      {v.semantic.questions.map((q, i) => <li key={i}>{q}</li>)}
                    </ul>
                    <p className="mt-1 text-[0.7rem] text-muted-foreground">{v.semantic.thresholds}</p>
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function GoalTile({ loe, onOpen }: { loe: Loe; onOpen: () => void }) {
  const tone = lightTone(loe.latest?.light);
  return (
    <button
      type="button"
      onClick={onOpen}
      className={`rounded-lg border border-l-4 border-border/50 ${tone.border} bg-background/40 p-3 text-left transition-colors hover:bg-background/70`}
    >
      <div className="flex items-center gap-2">
        <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${tone.dot}`} aria-hidden="true" />
        <span className="min-w-0 truncate text-[0.8rem] font-semibold text-foreground">{loe.name}</span>
        {loe.latest && (
          <span className="cockpit-badge shrink-0" data-tone={tone.badge || undefined}>
            {loe.latest.light}
          </span>
        )}
        <span className="cockpit-badge ml-auto shrink-0">{loe.cadence}</span>
      </div>

      {loe.latest ? (
        <>
          <div className="mt-2 text-[1.6rem] font-semibold tabular-nums text-foreground">
            {loe.latest.confidence}%
          </div>
          {loe.latest.summary && (
            <p className="mt-1 line-clamp-3 text-[0.733rem] text-muted-foreground">{loe.latest.summary}</p>
          )}
          {loe.latest.created_at && (
            <div className="mt-1 text-[0.667rem] text-muted-foreground/80">
              last check-in {timeAgo(loe.latest.created_at)} ago
            </div>
          )}
          <ConfidenceSparkline history={loe.history} color={tone.line} />
          <CheckinDotTimeline history={loe.history} />
        </>
      ) : (
        <p className="mt-2 text-[0.733rem] text-muted-foreground">No check-ins yet.</p>
      )}
    </button>
  );
}

interface LoeViewProps {
  /** Navigate to a freshly-opened conversation lane — the same lever
   * ActivityView/DecisionsView/KanbanPanel already hand up to App.tsx
   * (switch to the chat view, select the session). Optional so the view
   * still renders standalone (e.g. in isolation tests). */
  onOpenSession?: (sessionKey: string) => void;
}

export function LoeView({ onOpenSession }: LoeViewProps) {
  const { loes, loading, error, refresh } = useLoe();
  // Hold the id, not the object — the 60s poll then refreshes the open panel.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = loes.find((l) => l.id === selectedId) ?? null;
  const rollup = useConfidenceRollup(loes);

  const [newGoalOpen, setNewGoalOpen] = useState(false);
  const [discussTarget, setDiscussTarget] = useState<Loe | null>(null);

  const openSession = (sessionKey: string) => onOpenSession?.(sessionKey);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-border/40 px-4 py-3">
        <Target size={14} className="text-primary" />
        <span className="text-[0.8rem] font-semibold uppercase tracking-[0.14em] text-foreground">Goals</span>
        <span className="cockpit-badge tabular-nums">{loes.length}</span>
        {rollup && (
          <span
            className="cockpit-badge tabular-nums"
            title="mean latest confidence across active goals"
          >
            {Math.round(rollup.mean)}% avg
            {rollup.delta !== null && (
              <span className={rollup.delta >= 0 ? 'text-green' : 'text-red'}>
                {rollup.delta >= 0 ? ' ▲' : ' ▼'}{Math.abs(Math.round(rollup.delta))}
              </span>
            )}
          </span>
        )}
        <Button variant="outline" size="sm" onClick={() => setNewGoalOpen(true)} className="ml-auto">
          New Goal
        </Button>
        <button
          onClick={() => refresh()}
          title="Refresh"
          aria-label="Refresh goals"
          className="text-muted-foreground transition-colors hover:text-foreground"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {error && <p className="mb-3 text-[0.733rem] text-destructive">{error}</p>}
        {loes.length === 0 && !loading && !error && (
          <p className="text-[0.733rem] text-muted-foreground">No lines of effort defined yet.</p>
        )}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {loes.map((loe) => (
            <GoalTile key={loe.id} loe={loe} onOpen={() => setSelectedId(loe.id)} />
          ))}
        </div>
      </div>
      <LoeDetailDialog
        loe={selected}
        onClose={() => setSelectedId(null)}
        onDiscuss={(loe) => { setDiscussTarget(loe); setSelectedId(null); }}
      />

      <GoalIntentDialog
        open={newGoalOpen}
        onOpenChange={setNewGoalOpen}
        agentId={ACCOUNTABILITY_AGENT_ID}
        kicker="Goals"
        title="Set up a new goal"
        description="Tell accountability what you want to track and why — it will interview you, refine the questions and thresholds, and propose the goal when you agree."
        textareaLabel="What's the goal, and why now?"
        placeholder="e.g. I want to keep shipping to the Pi cluster weekly without letting the backlog grow…"
        showCadence
        buildMessage={NEW_GOAL_MESSAGE}
        onOpened={openSession}
      />

      <GoalIntentDialog
        open={discussTarget !== null}
        onOpenChange={(open) => { if (!open) setDiscussTarget(null); }}
        agentId={discussTarget?.agent_id ?? ACCOUNTABILITY_AGENT_ID}
        kicker="Goals"
        title={discussTarget ? `Discuss "${discussTarget.name}"` : 'Discuss changes'}
        description="Say what should change — the questions, the thresholds, the cadence, or whether it still belongs on the board — and the agent will interview you and propose a recalibration."
        textareaLabel="What do you want to change?"
        placeholder="e.g. the threshold is too easy to hit, I want it scored on outcomes not activity…"
        buildMessage={discussTarget ? discussMessage(discussTarget) : (text) => text}
        onOpened={openSession}
      />
    </div>
  );
}
