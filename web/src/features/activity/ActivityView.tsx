/**
 * ActivityView — Overview · Runs · Events
 * (2026-08-11 activity-coverage spec: docs/superpowers/specs/2026-08-11-activity-coverage-design.md).
 *
 * The audit behind the spec found 565 of 601 sessions reachable ONLY through
 * the chat sidebar's "show closed sessions" toggle, and the durable event log,
 * the dispatch valves and the work-item failures with no UI at all. This screen
 * is the answer, and its shape is deliberate: the SESSION is the unit of
 * activity, so a faceted session list covers every run *by construction* — a
 * new run producer cannot escape it. That property is pinned by
 * tests/test_activity_coverage.py, not by this file.
 *
 * Overview is CURRENT STATE only. History lives in the other two tabs, because
 * cadence liveness is `last_fired_at`, never a replay of `heartbeat.fired`
 * (the 2026-07-25 quiet-actions rule).
 *
 * Layout is a single column on purpose — no `@container`/`@3xl:` split pane, so
 * the 2026-08-10 container-query trap has no surface here.
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  Activity, RefreshCw, AlertTriangle, Play, Check, ChevronDown, ExternalLink,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { InlineSelect } from '@/components/ui/InlineSelect';
import { timeAgo } from '@/lib/formatting';
import { useActivity, useLiveRefresh, type RunRow, type EventRow, type WorkFailure } from './useActivity';

type Tab = 'overview' | 'runs' | 'events';

const TABS: { id: Tab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'runs', label: 'Runs' },
  { id: 'events', label: 'Events' },
];

/** Terminal statuses read as failures when they are; everything else is
 *  neutral. Deliberately not a full status→colour table: inventing one means
 *  a new status renders as "unknown" instead of just rendering. */
function statusTone(status: string): string {
  const s = status.toUpperCase();
  if (s === 'FAILED' || s === 'CANCELLED') return 'border-red/30 bg-red/8 text-red';
  if (s === 'DONE') return 'border-muted-foreground/25 bg-muted/40 text-muted-foreground';
  return 'border-green/30 bg-green/8 text-green';
}

function Pill({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={`inline-flex shrink-0 items-center rounded-full border px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase tracking-[0.08em] ${className}`}>
      {children}
    </span>
  );
}

function Tile({ label, value, tone = '', hint }: {
  label: string; value: React.ReactNode; tone?: string; hint?: string;
}) {
  return (
    <div className="rounded-lg border border-border/50 bg-background/40 px-3 py-2.5">
      <div className="text-[0.6rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground">{label}</div>
      <div className={`mt-1 text-[1.05rem] font-semibold tabular-nums ${tone}`}>{value}</div>
      {hint && <div className="mt-0.5 truncate text-[0.667rem] text-muted-foreground" title={hint}>{hint}</div>}
    </div>
  );
}

function SectionLabel({ children, count }: { children: React.ReactNode; count?: number }) {
  return (
    <div className="flex items-center gap-2 px-4 pt-5 pb-2 text-[0.667rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
      {children}
      {count !== undefined && (
        <span className="rounded-full bg-muted px-1.5 py-0.5 tabular-nums">{count}</span>
      )}
    </div>
  );
}

/* ── Overview ── */

function FailureRow({ item, onAct }: {
  item: WorkFailure;
  onAct: (id: string, action: 'requeue' | 'ack') => Promise<boolean>;
}) {
  const [busy, setBusy] = useState<'' | 'requeue' | 'ack'>('');
  const [open, setOpen] = useState(false);
  const act = async (action: 'requeue' | 'ack') => {
    setBusy(action);
    try { await onAct(item.id, action); } finally { setBusy(''); }
  };
  return (
    <div className="mx-4 mb-2 rounded-lg border border-red/25 bg-red/[0.03]">
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <ChevronDown size={12} className={`shrink-0 text-muted-foreground transition-transform ${open ? '' : '-rotate-90'}`} />
          <span className="min-w-0 flex-1 truncate text-xs text-foreground">
            {item.subject || '(no subject)'}
          </span>
        </button>
        <span className="shrink-0 text-[0.667rem] text-muted-foreground tabular-nums">
          {item.attempts} attempts · {timeAgo(item.enrolled_at)}
        </span>
        <Button size="sm" variant="outline" disabled={!!busy} onClick={() => act('requeue')} className="h-6 px-2 text-[0.667rem]">
          <Play size={10} className="mr-1" />{busy === 'requeue' ? '…' : 'Requeue'}
        </Button>
        <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => act('ack')} className="h-6 px-2 text-[0.667rem]">
          <Check size={10} className="mr-1" />{busy === 'ack' ? '…' : 'Ack'}
        </Button>
      </div>
      {open && item.last_error && (
        <pre className="cockpit-wrap overflow-x-auto whitespace-pre-wrap border-t border-red/20 px-3 py-2 text-[0.667rem] leading-relaxed text-foreground/80">
          {item.last_error}
        </pre>
      )}
    </div>
  );
}

function OverviewTab({ activity }: { activity: ReturnType<typeof useActivity> }) {
  const { overview, overviewLoading, loadOverview, actOnFailure } = activity;

  useEffect(() => { loadOverview(); }, [loadOverview]);
  // Tiles, not a list — nothing to displace, so this one is always live.
  useLiveRefresh(loadOverview);

  if (!overview) {
    return (
      <div className="px-4 py-6 text-[0.733rem] text-muted-foreground">
        {overviewLoading ? 'Loading…' : 'No overview available.'}
      </div>
    );
  }

  const { dispatch } = overview;
  const queued = dispatch.ledger.UNPROCESSED ?? 0;
  // A valve holding IS the interesting state — say which one, not just "no".
  const dispatchHint = dispatch.may_dispatch
    ? (dispatch.running ? 'draining' : 'idle — not started')
    : `held: ${dispatch.reason ?? 'unknown valve'}`;

  return (
    <div className="pb-6">
      <SectionLabel>Dispatch</SectionLabel>
      <div className="grid grid-cols-2 gap-2 px-4 sm:grid-cols-4">
        <Tile
          label="Queue"
          value={queued}
          tone={queued > 0 ? 'text-orange' : ''}
          hint={overview.oldest_unprocessed_at
            ? `oldest ${timeAgo(overview.oldest_unprocessed_at)}`
            : 'empty'}
        />
        <Tile
          label="Dispatcher"
          value={dispatch.may_dispatch ? 'Open' : 'Held'}
          tone={dispatch.may_dispatch ? 'text-green' : 'text-orange'}
          hint={dispatchHint}
        />
        <Tile label="In flight" value={dispatch.in_flight} hint={`concurrency ${dispatch.valves.concurrency}`} />
        <Tile
          label="Awaiting you"
          value={dispatch.awaiting_human}
          tone={dispatch.awaiting_human >= dispatch.valves.approval_limit ? 'text-orange' : ''}
          hint={`approval limit ${dispatch.valves.approval_limit}`}
        />
      </div>

      <SectionLabel>Ledger</SectionLabel>
      <div className="grid grid-cols-2 gap-2 px-4 sm:grid-cols-4">
        {Object.entries(dispatch.ledger).map(([state, n]) => (
          <Tile key={state} label={state.toLowerCase()} value={n}
                tone={state === 'FAILED' && n > 0 ? 'text-red' : ''} />
        ))}
        <Tile label="Dismissals pending" value={overview.dismiss_pending}
              tone={overview.dismiss_pending > 0 ? 'text-orange' : ''}
              hint="awaiting your confirmation" />
      </div>

      <SectionLabel count={overview.failures.length}>
        <AlertTriangle size={11} className={overview.failures.length ? 'text-red' : ''} />
        Needs attention
      </SectionLabel>
      {overview.failures.length === 0 ? (
        <div className="px-4 text-[0.733rem] text-muted-foreground">
          Nothing failed and unacknowledged.
        </div>
      ) : (
        overview.failures.map(item => (
          <FailureRow key={item.id} item={item} onAct={actOnFailure} />
        ))
      )}
    </div>
  );
}

/* ── Runs ── */

/** What this run was FOR. A task title if it has one, else the work item it
 *  drained, else the uuid — which is the honest answer for the handful of runs
 *  that genuinely have no subject. */
function runLabel(run: RunRow): string {
  return run.task_title || run.work_subject || run.id;
}

function RunsTab({ activity, onOpenSession }: {
  activity: ReturnType<typeof useActivity>;
  onOpenSession?: (sessionId: string, agentId: string) => void;
}) {
  const { runs, runAgents, runsLoading, runsExhausted, loadRuns } = activity;
  const [agent, setAgent] = useState('');
  const [status, setStatus] = useState('');
  const [mode, setMode] = useState('');

  const facets = useMemo(() => ({
    agent_id: agent || undefined,
    status: status || undefined,
    mode: mode || undefined,
  }), [agent, status, mode]);

  useEffect(() => { loadRuns(facets); }, [facets, loadRuns]);

  // Live only at the top of the list: refetching resets to page 1 and prepends
  // new runs, both of which would move rows out from under a scrolled reader.
  const scrollRef = useRef<HTMLDivElement>(null);
  useLiveRefresh(() => {
    if ((scrollRef.current?.scrollTop ?? 0) > 0) return;
    loadRuns(facets);
  });

  const agentOptions = useMemo(() => [
    { value: '', label: 'All agents' },
    ...runAgents.map(a => ({ value: a, label: a })),
  ], [runAgents]);

  // Status vocabulary comes from the rows on screen, plus whatever is selected
  // so the facet can always be cleared. Hardcoding the enum here would go stale
  // the first time a new status ships.
  const statusOptions = useMemo(() => {
    const seen = new Set(runs.map(r => r.status));
    if (status) seen.add(status);
    return [{ value: '', label: 'Any status' },
            ...[...seen].sort().map(s => ({ value: s, label: s }))];
  }, [runs, status]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-border/40 px-4 py-2">
        <InlineSelect value={agent} onChange={setAgent} options={agentOptions} ariaLabel="Filter runs by agent" />
        <InlineSelect value={status} onChange={setStatus} options={statusOptions} ariaLabel="Filter runs by status" />
        <InlineSelect
          value={mode}
          onChange={setMode}
          options={[
            { value: '', label: 'Any kind' },
            { value: 'conversation', label: 'Conversations' },
            { value: 'oneshot', label: 'Runs' },
          ]}
          ariaLabel="Filter runs by mode"
        />
        <span className="ml-auto text-[0.667rem] text-muted-foreground tabular-nums">
          {runs.length}{runsExhausted ? '' : '+'} shown
        </span>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {runs.length === 0 && !runsLoading ? (
          <div className="px-4 py-6 text-[0.733rem] text-muted-foreground">No runs match these filters.</div>
        ) : runs.map(run => (
          <button
            key={run.id}
            onClick={() => onOpenSession?.(run.id, run.agent_id)}
            className="flex w-full items-center gap-2 border-b border-border/30 px-4 py-2 text-left hover:bg-primary/[0.04]"
          >
            <Pill className={statusTone(run.status)}>{run.status}</Pill>
            <span className="min-w-0 flex-1 truncate text-[0.733rem] text-foreground">{runLabel(run)}</span>
            <span className="shrink-0 text-[0.667rem] text-muted-foreground">{run.agent_id}</span>
            {run.proposal_count > 0 && (
              <span className="shrink-0 text-[0.6rem] text-muted-foreground tabular-nums" title={`${run.proposal_count} proposals`}>
                ◆{run.proposal_count}
              </span>
            )}
            <span className="w-16 shrink-0 text-right text-[0.667rem] text-muted-foreground">
              {timeAgo(run.created_at)}
            </span>
          </button>
        ))}
        {!runsExhausted && runs.length > 0 && (
          <div className="px-4 py-3">
            <Button size="sm" variant="outline" disabled={runsLoading}
                    onClick={() => loadRuns(facets, runs[runs.length - 1])}>
              {runsLoading ? 'Loading…' : 'Load more'}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Events ── */

/** The run an event is about, when the log actually says so.
 *
 *  A session key needs BOTH halves (`agent:<agent>:<sess>`), so this returns
 *  null unless the row carries both — 1,099 of the 1,252 session-referencing
 *  events do. Guessing the agent from a session id prefix would produce a key
 *  that 404s, which is worse than no link: the operator would read the absence
 *  of a transcript as the absence of a run. This is also what replaced the
 *  planned cron-dialog link — one rule here covers heartbeat `task.create`
 *  runs and every other session-referencing kind at once. */
function eventSession(e: EventRow): { sessionId: string; agentId: string } | null {
  const payload = e.payload ?? {};
  const sessionId = e.ref_id?.startsWith('sess')
    ? e.ref_id
    : (typeof payload.session_id === 'string' ? payload.session_id : null);
  const agentId = typeof payload.agent_id === 'string' ? payload.agent_id : null;
  return sessionId && agentId ? { sessionId, agentId } : null;
}

function eventSummary(e: EventRow): string {
  const p = e.payload ?? {};
  for (const key of ['title', 'intent', 'subject', 'name', 'reason', 'error', 'verdict']) {
    const v = p[key];
    if (typeof v === 'string' && v) return v;
  }
  return e.ref_id ?? '';
}

function EventsTab({ activity, onOpenSession }: {
  activity: ReturnType<typeof useActivity>;
  onOpenSession?: (sessionId: string, agentId: string) => void;
}) {
  const {
    events, eventKinds, eventsLoading, eventsExhausted, loadEvents, loadEventKinds,
  } = activity;
  const [kind, setKind] = useState('');

  useEffect(() => { loadEventKinds(); }, [loadEventKinds]);
  useEffect(() => { loadEvents(kind || undefined); }, [kind, loadEvents]);

  // Same rule as Runs: live at the top, frozen once he has scrolled or paged.
  const scrollRef = useRef<HTMLDivElement>(null);
  useLiveRefresh(() => {
    if ((scrollRef.current?.scrollTop ?? 0) > 0) return;
    loadEvents(kind || undefined);
  });

  const kindOptions = useMemo(() => [
    { value: '', label: 'All kinds' },
    ...eventKinds.map(k => ({ value: k.kind, label: `${k.kind} (${k.count})` })),
  ], [eventKinds]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-border/40 px-4 py-2">
        <InlineSelect value={kind} onChange={setKind} options={kindOptions} ariaLabel="Filter events by kind" />
        <span className="ml-auto text-[0.667rem] text-muted-foreground tabular-nums">
          {events.length}{eventsExhausted ? '' : '+'} shown
        </span>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto" role="log" aria-label="Durable event log">
        {events.length === 0 && !eventsLoading ? (
          <div className="px-4 py-6 text-[0.733rem] text-muted-foreground">No events match this filter.</div>
        ) : events.map(e => {
          const run = onOpenSession ? eventSession(e) : null;
          return (
            <div key={e.id} className="flex items-center gap-2 border-b border-border/30 px-4 py-1.5 text-[0.733rem]">
              <span className="w-44 shrink-0 truncate font-mono text-[0.667rem] text-primary/80">{e.kind}</span>
              <span className="min-w-0 flex-1 truncate text-muted-foreground">{eventSummary(e)}</span>
              {run && (
                <button
                  type="button"
                  onClick={() => onOpenSession!(run.sessionId, run.agentId)}
                  title="Open the run this event is about"
                  className="shrink-0 text-[0.6rem] text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground"
                >
                  transcript
                </button>
              )}
              <span className="shrink-0 text-[0.6rem] text-muted-foreground">{e.actor}</span>
              <span className="w-16 shrink-0 text-right text-[0.667rem] text-muted-foreground">
                {timeAgo(e.created_at)}
              </span>
            </div>
          );
        })}
        {!eventsExhausted && events.length > 0 && (
          <div className="px-4 py-3">
            <Button size="sm" variant="outline" disabled={eventsLoading}
                    onClick={() => loadEvents(kind || undefined, events[events.length - 1])}>
              {eventsLoading ? 'Loading…' : 'Load more'}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Shell ── */

export function ActivityView({ onOpenSession }: {
  /** Open a run's transcript in chat. Optional so the view renders standalone
   *  in tests and in any future embed. */
  onOpenSession?: (sessionId: string, agentId: string) => void;
}) {
  const activity = useActivity();
  const [tab, setTab] = useState<Tab>('overview');

  const refresh = useCallback(() => {
    if (tab === 'overview') activity.loadOverview();
    else if (tab === 'runs') activity.loadRuns({});
    else activity.loadEvents(undefined);
  }, [tab, activity]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-border/40 px-4 py-3">
        <Activity size={14} className="text-primary" />
        <span className="text-[0.8rem] font-semibold uppercase tracking-[0.14em] text-foreground">Activity</span>
        <div className="ml-3 flex items-center gap-3" role="tablist" aria-label="Activity tabs">
          {TABS.map(t => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              tabIndex={tab === t.id ? 0 : -1}
              onClick={() => setTab(t.id)}
              className={`panel-label uppercase transition-colors ${
                tab === t.id ? 'text-primary' : 'text-muted-foreground opacity-70 hover:opacity-100'
              }`}
              data-active={tab === t.id}
            >
              {t.label}
            </button>
          ))}
        </div>
        {/* Quick links to sibling systems on the same host. Deriving from
            the current location (rather than hard-coding the Pi's tailnet
            name) works from any host without configuration. n8n (8443) is
            tailscale-serve TLS; LiteLLM (4000) and VictoriaLogs (9428) use
            explicit http on purpose — 4000 has no tailscale serve entry at
            all, and both ports are ServiceLB raw-TCP on cluster nodes, so
            plain http is the one scheme that answers from every tailnet
            vantage point (traffic is WireGuard-encrypted regardless). */}
        <div className="ml-auto flex items-center gap-3">
          <a
            href={`${window.location.protocol}//${window.location.hostname}:8443`}
            target="_blank"
            rel="noreferrer"
            title="n8n workflows"
            className="flex items-center gap-1 panel-label uppercase text-muted-foreground opacity-70 transition-colors hover:opacity-100 hover:text-foreground"
          >
            n8n <ExternalLink size={11} />
          </a>
          <a
            href={`http://${window.location.hostname}:4000/ui/`}
            target="_blank"
            rel="noreferrer"
            title="LiteLLM admin UI"
            className="flex items-center gap-1 panel-label uppercase text-muted-foreground opacity-70 transition-colors hover:opacity-100 hover:text-foreground"
          >
            LiteLLM <ExternalLink size={11} />
          </a>
          <a
            href={`http://${window.location.hostname}:9428/select/vmui/`}
            target="_blank"
            rel="noreferrer"
            title="journald + pod logs for both nodes (VictoriaLogs)"
            className="flex items-center gap-1 panel-label uppercase text-muted-foreground opacity-70 transition-colors hover:opacity-100 hover:text-foreground"
          >
            Infra logs <ExternalLink size={11} />
          </a>
        </div>
        <button
          onClick={refresh}
          title="Refresh"
          aria-label="Refresh activity"
          className="text-muted-foreground transition-colors hover:text-foreground"
        >
          <RefreshCw size={13} className={
            activity.overviewLoading || activity.runsLoading || activity.eventsLoading
              ? 'animate-spin' : ''} />
        </button>
      </div>

      {activity.error && (
        <div className="border-b border-red/30 bg-red/10 px-4 py-2 text-[0.733rem] text-red">
          {activity.error}
        </div>
      )}

      <div className="flex-1 overflow-y-auto min-h-0">
        {tab === 'overview' && <OverviewTab activity={activity} />}
        {tab === 'runs' && <RunsTab activity={activity} onOpenSession={onOpenSession} />}
        {tab === 'events' && <EventsTab activity={activity} onOpenSession={onOpenSession} />}
      </div>
    </div>
  );
}
