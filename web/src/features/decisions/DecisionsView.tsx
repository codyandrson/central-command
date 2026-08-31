import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Check, X, Inbox, RefreshCw, ShieldAlert, RotateCcw,
  FileText, Mail, ChevronDown, CircleCheck, CircleAlert,
  Compass, MessageCircleQuestion, OctagonPause, ArrowRight, Hourglass, Ban,
  History,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { GoalPreviewCard } from '@/features/loe/GoalPreviewCard';
import type { GoalPreviewData } from '@/features/loe/extractGoalPreviews';
import { useDecisions } from './useDecisions';
import { BulkDismissDialog } from './BulkDismissDialog';
import { agingLevel, ageOf, oldestAge, STALE_HOURS, type OldestAge } from './aging';
import { ListDetailSplit } from '@/components/Splitter';
import type {
  DecisionSelection, OperatorItem, ProposalDetail, ProposalSummary, WorkItem,
} from './types';

/* The panes used to call `useDecisions()` themselves, which meant three live
   copies of the hook — three 30s polls, three push subscriptions, three full
   decisions.list fetches per event — so every action competed with the other
   two for the same Pi. There is ONE hook, in DecisionsView, and the panes take
   what they need as props. */
type Actions = ReturnType<typeof useDecisions>;

/** Bare address from a From header like `"Name" <a@b.com>` — the bulk-dismiss
 *  query needs the address alone, never the display name. */
function senderEmail(from?: string | null): string {
  if (!from) return '';
  const m = from.match(/<([^>]+)>/);
  return (m ? m[1] : from).trim();
}

/* ── Relative timestamp (same pattern as the kanban ProposalInbox) ── */
function RelativeTime({ iso }: { iso?: string | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);
  if (!iso) return null;
  const secs = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 1000));
  let label: string;
  if (secs < 60) label = 'just now';
  else if (secs < 3600) label = `${Math.floor(secs / 60)}m ago`;
  else if (secs < 86400) label = `${Math.floor(secs / 3600)}h ago`;
  else label = `${Math.floor(secs / 86400)}d ago`;
  return <span className="text-[0.667rem] text-muted-foreground tabular-nums">{label}</span>;
}

/* ── Aging (badge tier) ──
   The queue is oldest-first, so the oldest item is already at the top — what
   was invisible is HOW old, because every row's timestamp is the same muted
   grey. Same visual language as `session-stall-chip`: an amber pill that says
   how long, sitting inline so nothing the operator is reading moves. */
function AgeChip({ iso }: { iso?: string | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);
  const level = agingLevel(iso, now);
  if (level === 'fresh') return null;
  const label = ageOf(iso, now);
  if (!label) return null;
  const stale = level === 'stale';
  return (
    <span
      data-testid="decision-age-chip"
      data-level={level}
      title={stale
        ? `Waiting ${label} — well past the ${STALE_HOURS}h mark. Whoever raised this is still blocked.`
        : `Waiting ${label}.`}
      className={`shrink-0 rounded-sm px-1.5 py-0.5 text-[0.6rem] font-bold ${
        stale ? 'bg-destructive/20 text-destructive' : 'bg-orange/20 text-orange'
      }`}
    >
      {label}
    </span>
  );
}

function SectionLabel({ children, count, oldest }: {
  children: React.ReactNode; count: number; oldest?: OldestAge | null;
}) {
  return (
    <div className="flex items-center gap-2 px-4 pt-4 pb-1.5 text-[0.667rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
      {children}
      <span className="rounded-full bg-muted px-1.5 py-0.5 tabular-nums">{count}</span>
      {/* Named only when the section actually has something old in it: a label
          that always carries an age becomes decoration and stops being read. */}
      {oldest && (
        <span
          data-testid="decision-section-oldest"
          className={`normal-case tracking-normal ${
            oldest.level === 'stale' ? 'text-destructive' : 'text-orange'
          }`}
        >
          oldest: {oldest.label}
        </span>
      )}
    </div>
  );
}

function AuditChip({ item }: { item: WorkItem }) {
  const audit = item.payload?.audit;
  if (!audit) return null;
  const concur = audit.verdict === 'concur';
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[0.6rem] font-semibold ${
        concur ? 'border-green/30 bg-green/8 text-green' : 'border-destructive/30 bg-destructive/8 text-destructive'
      }`}
      title={audit.rationale || ''}
    >
      {concur ? <CircleCheck size={9} /> : <CircleAlert size={9} />}
      auditor {audit.verdict}s
    </span>
  );
}

/* ── Consult provenance ──
   Drafter ≠ asker: a consulted specialist drafts the gated change itself, so
   the row's agent_id is the DRAFTER and `origin.consulted_by` is who asked.
   Without this the operator reads a Jira proposal from jira-expert with no clue
   it came out of triage's question. */
function OriginChip({ p }: { p: Pick<ProposalSummary, 'agent_id' | 'origin'> }) {
  const asker = p.origin?.consulted_by;
  if (!asker) return null;
  return (
    <span
      data-testid="proposal-origin-chip"
      className="cockpit-badge"
      title={`${p.agent_id} drafted this while being consulted by ${asker}. A rejection coaches ${p.agent_id}, the agent that owns these conventions.`}
    >
      drafted by {p.agent_id} · asked by {asker}
    </span>
  );
}

/* ── List rows ── */
function ProposalRowItem({ p, active, onSelect }: {
  p: ProposalSummary; active: boolean; onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      data-active={active}
      className="flex w-full items-start gap-2.5 border-b border-border/40 px-4 py-2.5 text-left transition-colors last:border-b-0 hover:bg-primary/[0.04] data-[active=true]:bg-primary/[0.08]"
    >
      <FileText size={13} className="mt-0.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium text-foreground">{p.intent || '(no intent)'}</p>
        <p className="mt-0.5 truncate text-[0.667rem] text-muted-foreground">
          {p.origin?.consulted_by ? `${p.agent_id} · asked by ${p.origin.consulted_by}` : p.agent_id}
        </p>
      </div>
      <AgeChip iso={p.created_at} />
      <RelativeTime iso={p.created_at} />
    </button>
  );
}

/* ── History (decisions.history) ── */
const STATUS_BADGE_CLASS: Record<string, string> = {
  EXECUTED: 'border-green/30 bg-green/8 text-green',
  APPROVED: 'border-green/30 bg-green/8 text-green',
  REJECTED: 'border-destructive/22 bg-destructive/8 text-destructive',
  FAILED: 'border-destructive/22 bg-destructive/8 text-destructive',
  WITHDRAWN: 'border-border/40 bg-muted/40 text-muted-foreground',
  EXECUTING: 'border-primary/30 bg-primary/8 text-primary',
};

function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_BADGE_CLASS[status] ?? 'border-border/40 bg-muted/40 text-muted-foreground';
  return (
    <span className={`inline-flex items-center rounded-full border px-1.5 py-0.5 text-[0.6rem] font-semibold ${cls}`}>
      {status}
    </span>
  );
}

/** An EXECUTED proposal that only ran in dry_run — the world never changed. */
function SimulatedBadge() {
  return (
    <span
      data-testid="simulated-badge"
      title="Executor was in dry_run: this action was simulated, nothing was written."
      className="inline-flex items-center rounded-full border border-orange/30 bg-orange/10 px-1.5 py-0.5 text-[0.6rem] font-semibold text-orange"
    >
      SIMULATED
    </span>
  );
}

/** A decided proposal — read-only, no approve/reject/dismiss affordance. */
function HistoryRowItem({ p, active, onSelect }: {
  p: ProposalSummary; active: boolean; onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      data-active={active}
      data-testid="history-row"
      className="flex w-full items-start gap-2.5 border-b border-border/40 px-4 py-2.5 text-left transition-colors last:border-b-0 hover:bg-primary/[0.04] data-[active=true]:bg-primary/[0.08]"
    >
      <FileText size={13} className="mt-0.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium text-foreground">{p.intent || '(no intent)'}</p>
        <div className="mt-0.5 flex items-center gap-1.5">
          <span className="truncate text-[0.667rem] text-muted-foreground">{p.agent_id}</span>
          <StatusBadge status={p.status} />
          {p.simulated && <SimulatedBadge />}
        </div>
      </div>
      <RelativeTime iso={p.decided_at} />
    </button>
  );
}

/** A confirmed dismissal (PROCESSED work item) — read-only, subject/source/when. */
function ProcessedRowItem({ item }: { item: WorkItem }) {
  return (
    <div
      data-testid="processed-row"
      className="flex w-full items-start gap-2.5 border-b border-border/40 px-4 py-2.5 text-left last:border-b-0"
    >
      <Mail size={13} className="mt-0.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium text-foreground">{item.subject || '(no subject)'}</p>
        <span className="truncate text-[0.667rem] text-muted-foreground">{item.source}</span>
      </div>
      <RelativeTime iso={item.terminal_at} />
    </div>
  );
}

function DismissalRowItem({ d, active, onSelect }: {
  d: WorkItem; active: boolean; onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      data-active={active}
      className="flex w-full items-start gap-2.5 border-b border-border/40 px-4 py-2.5 text-left transition-colors last:border-b-0 hover:bg-primary/[0.04] data-[active=true]:bg-primary/[0.08]"
    >
      <Mail size={13} className="mt-0.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium text-foreground">{d.subject || '(no subject)'}</p>
        <div className="mt-0.5 flex items-center gap-1.5">
          <span className="truncate text-[0.667rem] text-muted-foreground">{d.payload?.from || d.source}</span>
          <AuditChip item={d} />
        </div>
      </div>
      <AgeChip iso={d.enrolled_at} />
      <RelativeTime iso={d.enrolled_at} />
    </button>
  );
}

/* ── Orchestrator asks (D7 phase 2) ── */
const ITEM_KIND_META: Record<OperatorItem['kind'], {
  label: string; Icon: typeof Compass;
}> = {
  plan_review: { label: 'plan review', Icon: Compass },
  question: { label: 'question', Icon: MessageCircleQuestion },
  stall: { label: 'stalled — needs you', Icon: OctagonPause },
  continue: { label: 'out of rope — continue?', Icon: Hourglass },
};

function OperatorItemRowItem({ item, active, onSelect }: {
  item: OperatorItem; active: boolean; onSelect: () => void;
}) {
  const { label, Icon } = ITEM_KIND_META[item.kind] ?? ITEM_KIND_META.question;
  return (
    <button
      onClick={onSelect}
      data-active={active}
      className="flex w-full items-start gap-2.5 border-b border-border/40 px-4 py-2.5 text-left transition-colors last:border-b-0 hover:bg-primary/[0.04] data-[active=true]:bg-primary/[0.08]"
    >
      <Icon size={13} className={`mt-0.5 shrink-0 ${item.kind === 'stall' ? 'text-destructive' : 'text-muted-foreground'}`} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium text-foreground">{item.body || '(empty)'}</p>
        <p className="mt-0.5 truncate text-[0.667rem] text-muted-foreground">
          {item.agent_id} · {label}
          {/* The ask is blocking a task; without naming it the operator sees a
              question with nothing connecting it to the stalled work. */}
          {item.task_id && ` · blocks ${item.task_title || item.task_id}`}
        </p>
      </div>
      <AgeChip iso={item.created_at} />
      <RelativeTime iso={item.created_at} />
    </button>
  );
}

function OperatorItemPane({ item, resolved, onDone, onOpenTask, onOpenSession, answerOperatorItem }: {
  item: OperatorItem; resolved?: boolean; onDone: () => void;
  onOpenTask?: (taskId: string) => void;
  onOpenSession?: (sessionKey: string) => void;
  answerOperatorItem: Actions['answerOperatorItem'];
}) {
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [acting, setActing] = useState<'' | 'approve' | 'revise' | 'answer' | 'decline'>('');
  const isPlan = item.kind === 'plan_review';
  // A window grant is a yes/no, not an answer — the run asked for rope, not
  // for guidance, so the note is optional and both buttons are always live.
  const isContinue = item.kind === 'continue';
  const { label } = ITEM_KIND_META[item.kind] ?? ITEM_KIND_META.question;
  // The discussion door: a lane exists AND is still writable. `lane_open` is
  // null for tier-2/degraded-tier-3 items with no lane at all — those render
  // exactly as before. A closed lane (concluded, or its session went
  // terminal some other way) also falls back to the plain box: there is
  // nothing left to join.
  const hasOpenLane = Boolean(item.discussion_session_id) && item.lane_open === true;
  const [showAnswerBox, setShowAnswerBox] = useState(false);

  const act = useCallback(async (kind: 'approve' | 'revise' | 'answer' | 'decline') => {
    setActing(kind); setError('');
    try {
      if (kind === 'approve') await answerOperatorItem(item.id, text.trim(), 'approved');
      else if (kind === 'revise') await answerOperatorItem(item.id, text.trim(), 'revise');
      else if (kind === 'decline') await answerOperatorItem(item.id, text.trim(), 'declined');
      else await answerOperatorItem(item.id, text.trim());
      onDone();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActing('');
    }
  }, [item.id, text, answerOperatorItem, onDone]);

  // Enter submits the plain "answer" action (the box's default/primary use);
  // Shift+Enter still inserts a newline. The continue/plan panes keep their
  // own dedicated verdicts (approve/decline/revise) click-only.
  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing) return;
    e.preventDefault();
    if (isContinue || isPlan) return;
    if (acting !== '' || !text.trim()) return;
    act('answer');
  }, [isContinue, isPlan, acting, text, act]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        <div className="mb-1 flex items-center gap-2">
          <span className="cockpit-badge">{label}</span>
          <span className="text-[0.733rem] text-muted-foreground">{item.agent_id}</span>
          <RelativeTime iso={item.created_at} />
        </div>

        <DetailHeading>{isPlan ? `${item.agent_id}'s plan` : item.kind === 'stall' ? 'Why the loop paused' : isContinue ? 'Why the run stopped' : `${item.agent_id}'s question`}</DetailHeading>
        <p className="whitespace-pre-wrap cockpit-wrap text-[0.8rem] leading-relaxed text-foreground/85">{item.body}</p>

        {/* The email the ask is about, expandable like a dismissal's — a
            question about an email is unanswerable without the email. */}
        {item.source_email && (
          <div className="mt-4">
            <DetailHeading>Source {sourceLabel([item.source_email])}</DetailHeading>
            <EmailCard item={item.source_email} />
          </div>
        )}

        {/* What this ask is holding up. Since tier 2 any agent can ask, and the
            run it parked sits in REVIEW until this is answered — so answering
            here should read as "this unblocks that". */}
        {item.task_id && (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="text-[0.733rem] text-muted-foreground">Blocks</span>
            {onOpenTask ? (
              <Button
                variant="outline"
                size="xs"
                onClick={() => onOpenTask(item.task_id as string)}
              >
                {item.task_title || item.task_id}
                <ArrowRight size={12} />
              </Button>
            ) : (
              <span className="cockpit-badge">{item.task_title || item.task_id}</span>
            )}
          </div>
        )}

        {hasOpenLane ? (
          /* Tier 3, lane still open: the agent judged a one-line answer would
             not unblock it and opened a live conversation. That conversation
             is the primary door — join it in Chat. The answer field still
             works (orchestration.answer_item delivers it into the lane,
             resumes the paused run, and closes the lane) but it ends the
             discussion in one exchange, so it is demoted behind an explicit
             control rather than looking like the normal way to reply. */
          <div className="mt-5 rounded-lg border border-primary/40 bg-primary/5 p-3">
            <p className="text-[0.8rem] font-semibold text-foreground">
              {item.agent_id} opened a discussion for this
            </p>
            <p className="mt-1 text-[0.733rem] text-muted-foreground">
              It judged that a single answer would not unblock it. Join the
              conversation to go back and forth with it.
            </p>
            {onOpenSession && (
              <Button
                variant="outline"
                size="sm"
                className="mt-2.5 w-full border-primary/40 bg-primary/10 text-primary hover:bg-primary/16"
                onClick={() => onOpenSession(`agent:${item.agent_id}:${item.discussion_session_id}`)}
              >
                <MessageCircleQuestion size={13} />
                Join the discussion
              </Button>
            )}
            {!showAnswerBox && (
              <button
                type="button"
                onClick={() => setShowAnswerBox(true)}
                className="mt-2 text-[0.7rem] text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground"
              >
                Answer & close the discussion instead
              </button>
            )}
          </div>
        ) : item.discussion_session_id ? (
          /* Tier 3, lane already closed some other way: the answer field is
             the only lever left. */
          <div className="mt-5 rounded-lg border border-border/60 bg-muted/20 p-3">
            <p className="text-[0.733rem] text-muted-foreground">
              {item.agent_id} had opened a discussion for this, but that
              conversation is no longer open. Your answer below resumes the
              paused run as trusted guidance.
            </p>
          </div>
        ) : (
          <p className="mt-5 text-[0.733rem] text-muted-foreground">
            {isPlan
              ? 'Approving lets the orchestrator assign the planned work; sending it back replans from your note. Every world change the plan leads to still parks here for your approval.'
              : isContinue
              ? 'Continuing gives the run another window of the same size with its work intact — it changes nothing in the world by itself, and anything it reaches for still parks here for your approval. Declining ends the run and fails the task.'
              : 'Your answer resumes the paused run as trusted guidance.'}
          </p>
        )}
      </div>

      {resolved ? (
        <ResolvedNotice>
          Closed while you were reading — answered from the chat lane, or
          resolved with its task. Nothing here awaits you; it stays on screen
          until you select something else.
        </ResolvedNotice>
      ) : hasOpenLane && !showAnswerBox ? (
        <div className="shrink-0 border-t border-border/40 p-4">
          <p className="text-[0.7rem] text-muted-foreground">
            Answering here ends the conversation — it delivers your reply,
            resumes the paused run, and closes the lane in one exchange.
          </p>
        </div>
      ) : (
      <div className="shrink-0 border-t border-border/40 p-4">
        {hasOpenLane && (
          <p className="mb-2 text-[0.7rem] text-muted-foreground">
            Answering here ends the conversation — your reply is delivered
            into the lane, the paused run resumes with it, and the lane
            closes.
          </p>
        )}
        {error && <p className="mb-2 text-[0.733rem] text-destructive">{error}</p>}
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={item.discussion_session_id
            ? 'Answer here — delivered into the conversation the agent opened; the paused task resumes with it'
            : isContinue
            ? 'Optional note — recorded with your decision'
            : isPlan
            ? 'Optional note when approving — required to send the plan back for revision'
            : 'Your answer (required) — it resumes the run as trusted guidance'}
          rows={2}
          onKeyDown={handleKeyDown}
          className="mb-2 w-full resize-none rounded-lg border border-border/60 bg-background px-2.5 py-2 text-xs text-foreground placeholder:text-muted-foreground/70 focus:outline-none focus:ring-1 focus:ring-primary/50"
        />
        <div className="flex items-center gap-2">
          {isContinue ? (
            <>
              <Button
                variant="outline"
                onClick={() => act('approve')}
                disabled={acting !== ''}
                className="flex-1 border-green/30 bg-green/8 text-green hover:bg-green/12"
              >
                <Check size={14} />
                {acting === 'approve' ? 'Continuing…' : 'Continue'}
              </Button>
              <Button
                variant="outline"
                onClick={() => act('decline')}
                disabled={acting !== ''}
                className="flex-1 border-destructive/22 bg-destructive/8 text-destructive hover:bg-destructive/14"
              >
                <X size={14} />
                {acting === 'decline' ? 'Ending…' : 'Decline'}
              </Button>
            </>
          ) : isPlan ? (
            <>
              <Button
                variant="outline"
                onClick={() => act('approve')}
                disabled={acting !== ''}
                className="flex-1 border-green/30 bg-green/8 text-green hover:bg-green/12"
              >
                <Check size={14} />
                {acting === 'approve' ? 'Approving…' : 'Approve plan'}
              </Button>
              <Button
                variant="outline"
                onClick={() => act('revise')}
                disabled={acting !== '' || !text.trim()}
                className="flex-1 border-destructive/22 bg-destructive/8 text-destructive hover:bg-destructive/14"
              >
                <RotateCcw size={14} />
                {acting === 'revise' ? 'Sending…' : 'Send back'}
              </Button>
            </>
          ) : (
            <Button
              variant="outline"
              onClick={() => act('answer')}
              disabled={acting !== '' || !text.trim()}
              className="flex-1 border-green/30 bg-green/8 text-green hover:bg-green/12"
            >
              <Check size={14} />
              {acting === 'answer' ? 'Sending…' : 'Send answer'}
            </Button>
          )}
        </div>
      </div>
      )}
    </div>
  );
}

/**
 * Contract refs (target_ref, source_ref) can be strings or structured
 * objects like {system, id, read_version} — render either as a stable
 * label, never as a raw React child (minified error #31).
 */
function refLabel(ref: unknown): string {
  if (ref == null) return '';
  if (typeof ref === 'string') return ref;
  if (typeof ref === 'object') {
    const r = ref as Record<string, unknown>;
    const base = [r.system, r.id].filter(Boolean).map(String).join(':');
    const ver = r.read_version && r.read_version !== 'unknown' ? ` @${String(r.read_version)}` : '';
    return base ? base + ver : JSON.stringify(ref);
  }
  return String(ref);
}

/* ── Shared detail bits ── */
function DetailHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mb-1.5 mt-5 text-[0.667rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground first:mt-0">
      {children}
    </h3>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="overflow-x-auto whitespace-pre-wrap cockpit-wrap rounded-lg bg-muted/40 p-2.5 text-[0.7rem] leading-relaxed text-foreground/90">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function DiffBlock({ diff }: { diff: string }) {
  return (
    <pre className="overflow-x-auto whitespace-pre-wrap cockpit-wrap rounded-lg bg-muted/40 p-2.5 text-[0.7rem] leading-relaxed">
      {diff.split('\n').map((line, i) => (
        <div
          key={i}
          className={
            line.startsWith('+') && !line.startsWith('+++') ? 'text-green'
            : line.startsWith('-') && !line.startsWith('---') ? 'text-destructive'
            : line.startsWith('@@') ? 'text-primary'
            : 'text-foreground/80'
          }
        >
          {line || ' '}
        </div>
      ))}
    </pre>
  );
}

/** graph.add_episode: the episode body as a quoted claim, the routing fields
 *  as labelled rows (scope already renders as a badge above), anything else
 *  collapsed — an operator approving a graph write should read the claim,
 *  not decode JSON for it. */
function EpisodeBlock({ args }: { args: Record<string, unknown> }) {
  const { episode_body, source_description, name, ...rest } = args;
  delete rest.scope; // rendered as a badge above, not repeated here
  return (
    <div className="space-y-1.5">
      {typeof episode_body === 'string' && (
        <blockquote className="whitespace-pre-wrap cockpit-wrap rounded-lg border-l-2 border-primary/40 bg-muted/40 p-2.5 text-[0.7rem] leading-relaxed text-foreground/90">
          {episode_body}
        </blockquote>
      )}
      {(name != null || source_description != null) && (
        <dl className="space-y-0.5 text-[0.7rem] text-foreground/80">
          {name != null && (
            <div><dt className="inline font-semibold text-muted-foreground">name: </dt><dd className="inline">{String(name)}</dd></div>
          )}
          {source_description != null && (
            <div><dt className="inline font-semibold text-muted-foreground">source: </dt><dd className="inline">{String(source_description)}</dd></div>
          )}
        </dl>
      )}
      {Object.keys(rest).length > 0 && (
        <details className="text-[0.7rem] text-muted-foreground">
          <summary className="cursor-pointer">other arguments</summary>
          <JsonBlock value={rest} />
        </details>
      )}
    </div>
  );
}

/** skill.create / an undiffable skill.doc_add: readable content, no version
 *  to compare against. */
function SkillDocBlock({ args }: { args: Record<string, unknown> }) {
  const content = String(args.guidance_content ?? args.content ?? '');
  const heading = String(args.title ?? args.doc_key ?? '');
  return (
    <div>
      {heading && <p className="mb-1 text-[0.7rem] font-semibold text-foreground/90">{heading}</p>}
      <pre className="overflow-x-auto whitespace-pre-wrap cockpit-wrap rounded-lg bg-muted/40 p-2.5 text-[0.7rem] leading-relaxed text-foreground/90">
        {content}
      </pre>
    </div>
  );
}

/** loe.create: the draft goal through the same tile the chat marker renders
 *  (GoalPreviewCard), plus whatever routing args the card doesn't cover
 *  (agent_id, loe_id/id) as labelled fields — same pattern as EpisodeBlock. */
function LoeCreateBlock({ args }: { args: Record<string, unknown> }) {
  const { name, cadence, questions, thresholds, agent_id, loe_id, id, ...rest } = args;
  if (typeof name !== 'string' || !name) return <JsonBlock value={args} />;
  const preview: GoalPreviewData = {
    name,
    ...(typeof cadence === 'string' ? { cadence } : {}),
    ...(Array.isArray(questions) ? { questions: questions.filter((q): q is string => typeof q === 'string') } : {}),
    ...(typeof thresholds === 'string' ? { thresholds } : {}),
  };
  return (
    <div className="space-y-1.5">
      <GoalPreviewCard preview={preview} />
      {(agent_id != null || loe_id != null || id != null) && (
        <dl className="space-y-0.5 text-[0.7rem] text-foreground/80">
          {agent_id != null && (
            <div><dt className="inline font-semibold text-muted-foreground">agent: </dt><dd className="inline">{String(agent_id)}</dd></div>
          )}
          {(loe_id ?? id) != null && (
            <div><dt className="inline font-semibold text-muted-foreground">id: </dt><dd className="inline">{String(loe_id ?? id)}</dd></div>
          )}
        </dl>
      )}
      {Object.keys(rest).length > 0 && (
        <details className="text-[0.7rem] text-muted-foreground">
          <summary className="cursor-pointer">other arguments</summary>
          <JsonBlock value={rest} />
        </details>
      )}
    </div>
  );
}

/** mcp.sync_source: one DiffBlock per synced file, headed by its path. Falls
 *  back to raw JSON if the backend sent no diffs (shouldn't happen — guards
 *  against a payload shape mismatch rather than rendering nothing). */
function McpSyncBlock({ diffs, args }: { diffs: { path: string; diff: string }[]; args: Record<string, unknown> }) {
  if (diffs.length === 0) return <JsonBlock value={args} />;
  return (
    <div className="space-y-2">
      {diffs.map((d) => (
        <div key={d.path}>
          <p className="mb-1 font-mono text-[0.7rem] text-foreground/90">{d.path}</p>
          <DiffBlock diff={d.diff} />
        </div>
      ))}
    </div>
  );
}

/** What to call a work item in the reviewer's source pane. `kind` is what the
 *  ledger routed on; anything unset is email, the column's default. */
function sourceLabel(items: WorkItem[]): string {
  const kinds = new Set(items.map((i) => i.kind || 'email'));
  if (kinds.size === 1 && kinds.has('document')) return 'document';
  if (kinds.size > 1) return 'item';
  return 'email';
}

function EmailCard({ item }: { item: WorkItem }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mb-2 rounded-lg border border-border/40">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-primary/[0.04]"
      >
        <ChevronDown size={12} className={`shrink-0 text-muted-foreground transition-transform ${open ? '' : '-rotate-90'}`} />
        <span className="min-w-0 flex-1 truncate text-xs text-foreground">
          {item.subject || (item.kind === 'document' ? '(untitled document)' : '(no subject)')}
        </span>
        <span className="shrink-0 text-[0.667rem] text-muted-foreground">{item.payload?.from}</span>
      </button>
      {open && (
        <div className="whitespace-pre-wrap cockpit-wrap border-t border-border/40 px-3 py-2 text-[0.733rem] leading-relaxed text-foreground/85">
          {item.payload?.text || '(content not hydrated)'}
        </div>
      )}
    </div>
  );
}

/* ── Proposal detail pane ── */
function ProposalPane({
  id, pendingGone, onDone, onOpenSession, getProposal, approve, reject, dismiss,
}: {
  id: string;
  /** No longer in the pending list nor history — decided out from under the
   *  operator (task cancel → WITHDRAWN, etc.). Triggers a silent refetch so
   *  the pane swaps its action row for the decided-status footer. */
  pendingGone?: boolean;
  onDone: () => void;
  onOpenSession?: (sessionKey: string) => void;
  getProposal: Actions['getProposal'];
  approve: Actions['approve'];
  reject: Actions['reject'];
  dismiss: Actions['dismiss'];
}) {
  const [detail, setDetail] = useState<ProposalDetail | null>(null);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [acting, setActing] = useState<'' | 'approve' | 'reject' | 'dismiss'>('');

  useEffect(() => {
    let alive = true;
    setDetail(null); setError(''); setFeedback('');
    getProposal(id).then(
      (d) => { if (alive) setDetail(d); },
      (e: unknown) => { if (alive) setError(e instanceof Error ? e.message : String(e)); },
    );
    return () => { alive = false; };
  }, [id, getProposal]);

  // Re-load in place when the proposal is decided elsewhere: the detail keeps
  // rendering (never kick the reader out), only its status/footer update.
  useEffect(() => {
    if (!pendingGone) return;
    let alive = true;
    getProposal(id).then(
      (d) => { if (alive) setDetail(d); },
      () => {}, // keep the last-loaded detail on a transient fetch error
    );
    return () => { alive = false; };
  }, [pendingGone, id, getProposal]);

  const act = useCallback(async (kind: 'approve' | 'reject' | 'dismiss') => {
    setActing(kind); setError('');
    try {
      if (kind === 'approve') await approve(id);
      else if (kind === 'reject') await reject(id, feedback.trim());
      else await dismiss(id, feedback.trim());
      onDone();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActing('');
    }
  }, [id, feedback, approve, reject, dismiss, onDone]);

  // Enter reject-only: reject is the one action the typed feedback is FOR, and
  // approve must never fire from the keyboard. Shift+Enter is a newline.
  const handleFeedbackKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing) return;
    e.preventDefault();
    if (acting !== '' || !feedback.trim()) return;
    act('reject');
  }, [acting, feedback, act]);

  if (error && !detail) return <div className="p-6 text-xs text-destructive">{error}</div>;
  if (!detail) return <div className="p-6 text-xs text-muted-foreground">Loading proposal…</div>;

  const flags = detail.policy_flags ?? [];
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        <div className="mb-1 flex items-center gap-2">
          <span className="cockpit-badge">{detail.agent_id}</span>
          <OriginChip p={detail} />
          <RelativeTime iso={detail.created_at} />
          {/* The run that DRAFTED this. `agent_id` is the drafter, never the
              subject (CLAUDE.md), so the session key is built from it — the
              same pairing the proposal row itself belongs to. */}
          {onOpenSession && detail.session_id && (
            <button
              type="button"
              onClick={() => onOpenSession(`agent:${detail.agent_id}:${detail.session_id}`)}
              title="Open the run that drafted this proposal"
              className="cockpit-badge cursor-pointer underline decoration-dotted underline-offset-2 hover:text-foreground"
            >
              transcript
            </button>
          )}
        </div>
        <p className="text-sm font-medium leading-snug text-foreground">{detail.intent}</p>

        {detail.confidence && (
          <div className="mt-1.5 flex items-center gap-2">
            <span
              className={`cockpit-badge ${
                detail.confidence.level === 'low'
                  ? 'text-yellow-600 dark:text-yellow-400'
                  : detail.confidence.level === 'high'
                    ? 'text-foreground'
                    : 'text-muted-foreground'
              }`}
              title="The agent's OWN confidence in this proposal. Advisory — it gates nothing."
            >
              {detail.confidence.level} confidence
            </span>
            {detail.confidence.rationale && (
              <span className="min-w-0 flex-1 truncate text-[0.7rem] text-muted-foreground" title={detail.confidence.rationale}>
                {detail.confidence.rationale}
              </span>
            )}
          </div>
        )}

        {detail.audit?.rationale && (
          <>
            <DetailHeading>
              Auditor ({detail.audit.verdict}s{detail.audit.operator_hold ? ' · operator hold' : ''})
            </DetailHeading>
            <p className="text-[0.8rem] leading-relaxed text-foreground/85">{detail.audit.rationale}</p>
          </>
        )}

        {flags.length > 0 && (
          <div className="mt-3 rounded-lg border border-yellow-600/30 bg-yellow-500/8 p-2.5">
            {flags.map((f, i) => (
              <div key={i} className="flex items-start gap-2 py-0.5 text-[0.733rem] text-yellow-600 dark:text-yellow-400">
                <ShieldAlert size={12} className="mt-0.5 shrink-0" />
                <span><span className="font-semibold">{f.policy}:</span> {f.problem}</span>
              </div>
            ))}
          </div>
        )}

        <DetailHeading>Actions</DetailHeading>
        {detail.actions.map((a, i) => {
          const cap = a.capability.split('@')[0];
          return (
          <div key={i} className="mb-2">
            <div className="mb-1 flex items-center gap-2">
              <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.7rem] text-foreground/90">{a.capability}</span>
              {a.target_ref != null && <span className="font-mono text-[0.667rem] text-muted-foreground">{refLabel(a.target_ref)}</span>}
              {/* D11-r1: scope is a reviewable routing decision, not a detail
                  buried in the args JSON — approving a true fact into the wrong
                  partition is its own kind of mistake. */}
              {cap === 'graph.add_episode' && (typeof a.arguments?.scope === 'string' ? (
                <span
                  className="cockpit-badge"
                  title={a.arguments.scope === 'private'
                    ? "Written to this agent's own graph partition — read by it alone."
                    : 'Written to the shared team graph — read by every agent.'}
                >
                  scope: {String(a.arguments.scope)}
                </span>
              ) : (
                <span
                  className="cockpit-badge text-yellow-600 dark:text-yellow-400"
                  title="The proposal names no scope argument. The executor refuses a scope-less episode, so approving this will fail at execution — reject it and ask for an explicit 'shared' or 'private'."
                >
                  scope: none given
                </span>
              ))}
            </div>
            {a.charter_diff ? <DiffBlock diff={a.charter_diff} />
              : cap === 'graph.add_episode' ? <EpisodeBlock args={a.arguments ?? {}} />
              : cap === 'skill.doc_add' || cap === 'skill.create'
                ? (a.skill_diff ? <DiffBlock diff={a.skill_diff} /> : <SkillDocBlock args={a.arguments ?? {}} />)
              : cap === 'mcp.sync_source' ? <McpSyncBlock diffs={a.mcp_diffs ?? []} args={a.arguments ?? {}} />
              : cap === 'loe.create' ? <LoeCreateBlock args={a.arguments ?? {}} />
              : <JsonBlock value={a.arguments ?? {}} />}
          </div>
          );
        })}

        {detail.expected_effect && (
          <>
            <DetailHeading>Expected effect</DetailHeading>
            <p className="text-[0.8rem] leading-relaxed text-foreground/85">{detail.expected_effect}</p>
          </>
        )}

        <DetailHeading>Evidence</DetailHeading>
        {(detail.evidence ?? []).map((e, i) => (
          <div key={i} className="mb-2 rounded-lg border border-border/40 p-2.5">
            <div className="mb-1 flex min-w-0 flex-wrap items-center gap-2">
              <span className="cockpit-badge">{e.kind}</span>
              {/* A session-id citation (source_session_id in reflection.py's
                  graph.add_episode evidence) belongs to the DRAFTER's own run
                  — same pairing as the transcript button above — so it opens
                  the same way, instead of sitting as dead text. */}
              {typeof e.source_ref === 'string' && e.source_ref.startsWith('sess_') && onOpenSession ? (
                <button
                  type="button"
                  onClick={() => onOpenSession(`agent:${detail.agent_id}:${e.source_ref}`)}
                  title="Open the cited session"
                  className="min-w-0 wrap-anywhere font-mono text-[0.667rem] text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground"
                >
                  {refLabel(e.source_ref)}
                </button>
              ) : (
                e.source_ref != null && <span className="min-w-0 wrap-anywhere font-mono text-[0.667rem] text-muted-foreground">{refLabel(e.source_ref)}</span>
              )}
              {e.citation === 'discovered' && (
                <span className="cockpit-badge" title="The coach found this in the record; you did not select it. Verified as real, not as representative.">discovered</span>
              )}
              {e.citation === 'selected' && (
                <span className="cockpit-badge" title="From the signals you selected for this session.">selected</span>
              )}
              {e.claim_matches_source === false && (
                <span className="cockpit-badge text-destructive" title="The quote was not found in the recorded source, or the cited event does not exist.">unverified quote</span>
              )}
            </div>
            {e.claim && <p className="text-[0.8rem] leading-relaxed text-foreground/85">{e.claim}</p>}
            {e.source_feedback && (
              <p className="mt-1 border-l-2 border-primary/40 pl-2 text-[0.733rem] italic text-foreground/70">
                recorded feedback: “{e.source_feedback}”
              </p>
            )}
          </div>
        ))}

        {(detail.source_emails?.length ?? 0) > 0 && (
          <>
            <DetailHeading>
              Source {sourceLabel(detail.source_emails!)}{detail.source_emails!.length > 1 ? 's' : ''}
            </DetailHeading>
            {detail.source_emails!.map((it) => <EmailCard key={it.id} item={it} />)}
          </>
        )}

        {(detail.folds?.length ?? 0) > 0 && (
          <>
            <DetailHeading>Also resolves (folded)</DetailHeading>
            {detail.folds!.map((it) => <EmailCard key={it.id} item={it} />)}
          </>
        )}
      </div>

      {/* A decided proposal (history) offers nothing to act on — the action
          row only ever applies while it is still AWAITING_HUMAN. */}
      {detail.status === 'AWAITING_HUMAN' ? (
        <div className="shrink-0 border-t border-border/40 p-4">
          {error && <p className="mb-2 text-[0.733rem] text-destructive">{error}</p>}
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="Rejection feedback (required to reject) — or an optional note for Dismiss"
            rows={2}
            onKeyDown={handleFeedbackKeyDown}
            className="mb-2 w-full resize-none rounded-lg border border-border/60 bg-background px-2.5 py-2 text-xs text-foreground placeholder:text-muted-foreground/70 focus:outline-none focus:ring-1 focus:ring-primary/50"
          />
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={() => act('approve')}
              disabled={acting !== ''}
              className="flex-1 border-green/30 bg-green/8 text-green hover:bg-green/12"
            >
              <Check size={14} />
              {acting === 'approve' ? 'Executing…' : 'Approve'}
            </Button>
            <Button
              variant="outline"
              onClick={() => act('reject')}
              disabled={acting !== '' || !feedback.trim()}
              className="flex-1 border-destructive/22 bg-destructive/8 text-destructive hover:bg-destructive/14"
            >
              <X size={14} />
              {acting === 'reject' ? 'Sending…' : 'Reject'}
            </Button>
            <Button
              variant="outline"
              onClick={() => act('dismiss')}
              disabled={acting !== ''}
              title="No action needed — closes this out without asking the agent to redraft."
              className="flex-1"
            >
              <Ban size={14} />
              {acting === 'dismiss' ? 'Dismissing…' : 'Dismiss'}
            </Button>
          </div>
        </div>
      ) : (
        <div className="shrink-0 border-t border-border/40 p-4">
          <div className="flex items-center gap-2">
            <StatusBadge status={detail.status} />
            {detail.simulated && <SimulatedBadge />}
            {detail.decided_at && (
              <span className="text-[0.667rem] text-muted-foreground">
                decided <RelativeTime iso={detail.decided_at} />
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** The footer when the viewed item resolved elsewhere: say what happened,
 *  offer nothing — acting on a resolved item would only 409. */
function ResolvedNotice({ children }: { children: React.ReactNode }) {
  return (
    <div className="shrink-0 border-t border-border/40 p-4" data-testid="resolved-notice">
      <p className="text-[0.733rem] text-muted-foreground">{children}</p>
    </div>
  );
}

/* ── Dismissal detail pane ── */
function DismissalPane({ item, resolved, onDone, confirmDismissal, reopenDismissal, onBulkDismissSender }: {
  item: WorkItem; resolved?: boolean; onDone: () => void;
  confirmDismissal: Actions['confirmDismissal'];
  reopenDismissal: Actions['reopenDismissal'];
  onBulkDismissSender: (query: string) => void;
}) {
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [acting, setActing] = useState<'' | 'confirm' | 'reopen'>('');
  const audit = item.payload?.audit;

  const act = useCallback(async (kind: 'confirm' | 'reopen') => {
    setActing(kind); setError('');
    try {
      if (kind === 'confirm') await confirmDismissal(item.id);
      else await reopenDismissal(item.id, note.trim());
      onDone();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActing('');
    }
  }, [item.id, note, confirmDismissal, reopenDismissal, onDone]);

  // Enter reopens (the note is what reopen consumes); confirm stays click-only.
  const handleNoteKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing) return;
    e.preventDefault();
    if (acting !== '' || !note.trim()) return;
    act('reopen');
  }, [acting, note, act]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        <div className="mb-1 flex items-center gap-2">
          <span className="cockpit-badge">no-action claim</span>
          <AuditChip item={item} />
          <RelativeTime iso={item.enrolled_at} />
        </div>
        <p className="text-sm font-medium leading-snug text-foreground">{item.subject || '(no subject)'}</p>
        <div className="mt-0.5 flex items-center gap-2">
          <p className="text-[0.733rem] text-muted-foreground">{item.payload?.from}</p>
          {senderEmail(item.payload?.from) && (
            <button
              type="button"
              onClick={() => onBulkDismissSender(`from:${senderEmail(item.payload?.from)}`)}
              className="text-[0.667rem] text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground"
            >
              Bulk dismiss sender…
            </button>
          )}
        </div>

        <DetailHeading>Agent's rationale</DetailHeading>
        <p className="text-[0.8rem] leading-relaxed text-foreground/85">
          {item.payload?.dismissal_rationale || '(no rationale recorded)'}
        </p>

        {audit?.rationale && (
          <>
            <DetailHeading>Auditor ({audit.verdict}s{audit.operator_hold ? ' · operator hold' : ''})</DetailHeading>
            <p className="text-[0.8rem] leading-relaxed text-foreground/85">{audit.rationale}</p>
          </>
        )}

        {item.payload?.operator_note && (
          <>
            <DetailHeading>Your earlier reopen note</DetailHeading>
            <p className="border-l-2 border-primary/40 pl-2 text-[0.8rem] italic text-foreground/75">
              “{item.payload.operator_note}”
            </p>
          </>
        )}

        <DetailHeading>The email</DetailHeading>
        <EmailCard item={item} />
      </div>

      {resolved ? (
        <ResolvedNotice>
          Resolved while you were reading — the auditor confirmed this claim, or
          it was handled elsewhere. Nothing here awaits you; it stays on screen
          until you select something else.
        </ResolvedNotice>
      ) : (
      <div className="shrink-0 border-t border-border/40 p-4">
        {error && <p className="mb-2 text-[0.733rem] text-destructive">{error}</p>}
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Reopen note — what the agent should look at again (required to reopen)"
          rows={2}
          onKeyDown={handleNoteKeyDown}
          className="mb-2 w-full resize-none rounded-lg border border-border/60 bg-background px-2.5 py-2 text-xs text-foreground placeholder:text-muted-foreground/70 focus:outline-none focus:ring-1 focus:ring-primary/50"
        />
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={() => act('confirm')}
            disabled={acting !== ''}
            className="flex-1 border-green/30 bg-green/8 text-green hover:bg-green/12"
          >
            <Check size={14} />
            {acting === 'confirm' ? 'Confirming…' : 'Confirm dismissal'}
          </Button>
          <Button
            variant="outline"
            onClick={() => act('reopen')}
            disabled={acting !== '' || !note.trim()}
            className="flex-1 border-destructive/22 bg-destructive/8 text-destructive hover:bg-destructive/14"
          >
            <RotateCcw size={14} />
            {acting === 'reopen' ? 'Reopening…' : 'Reopen'}
          </Button>
        </div>
      </div>
      )}
    </div>
  );
}

/* ── Main view ── */
interface DecisionsViewProps {
  /** Select this ask on arrival — the operator was sent here from the run it
   *  blocks, so landing on an empty pane would lose what they came for. */
  initialItemId?: string | null;
  onInitialItemConsumed?: () => void;
  /** Open a task's card on the board. */
  onOpenTask?: (taskId: string) => void;
  /** Open the run that drafted a proposal, in chat. */
  onOpenSession?: (sessionKey: string) => void;
}

export function DecisionsView({
  initialItemId, onInitialItemConsumed, onOpenTask, onOpenSession,
}: DecisionsViewProps = {}) {
  const {
    proposals, dismissals, operatorItems, loading, error, refresh,
    confirmAllDismissals, getProposal, approve, reject, dismiss,
    confirmDismissal, reopenDismissal, answerOperatorItem,
    history, historyHasMore, historyLoading, showHistory, processedItems = [],
    toggleHistory, loadMoreHistory,
  } = useDecisions();
  const [selected, setSelected] = useState<DecisionSelection>(null);
  const [confirmingAll, setConfirmingAll] = useState(false);
  const consumedRef = useRef<string | null>(null);
  const [bulkDismissOpen, setBulkDismissOpen] = useState(false);
  const [bulkDismissQuery, setBulkDismissQuery] = useState('');
  const openBulkDismiss = useCallback((query: string) => {
    setBulkDismissQuery(query);
    setBulkDismissOpen(true);
  }, []);

  // Arriving with an item to show: select it once it has loaded. Once only —
  // re-selecting on every refresh would move the pane out from under an
  // operator who has since clicked elsewhere (the no-displacement rule).
  useEffect(() => {
    if (!initialItemId || initialItemId === consumedRef.current) return;
    if (!operatorItems.some((i) => i.id === initialItemId)) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time sync from prop
    setSelected({ kind: 'operator_item', id: initialItemId });
    consumedRef.current = initialItemId;
    onInitialItemConsumed?.();
  }, [initialItemId, operatorItems, onInitialItemConsumed]);

  // A selected item can resolve out of the pending pools WITHOUT the operator:
  // the auditor auto-confirms a concurred dismissal, a chat lane concludes an
  // ask, a cancelled task withdraws its proposal. This used to evict the
  // selection and blank the pane mid-read — the no-displacement rule broken
  // from the inside (2026-08-15, the operator: "it's being kicked out of reviewing it
  // that is annoying"). Now the pane keeps the last-seen record, marks it
  // resolved, and selection only ever changes by the operator's own hand.
  const liveDismissal = selected?.kind === 'dismissal'
    ? dismissals.find((d) => d.id === selected.id) ?? null
    : null;
  const liveOperatorItem = selected?.kind === 'operator_item'
    ? operatorItems.find((i) => i.id === selected.id) ?? null
    : null;
  const [dismissalSnap, setDismissalSnap] = useState<WorkItem | null>(null);
  const [operatorItemSnap, setOperatorItemSnap] = useState<OperatorItem | null>(null);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- snapshot of the live row, kept for after it resolves away
    if (liveDismissal) setDismissalSnap(liveDismissal);
  }, [liveDismissal]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- snapshot of the live row, kept for after it resolves away
    if (liveOperatorItem) setOperatorItemSnap(liveOperatorItem);
  }, [liveOperatorItem]);

  const selectedDismissal = liveDismissal
    ?? (selected?.kind === 'dismissal' && dismissalSnap?.id === selected.id
      ? dismissalSnap : null);
  const selectedOperatorItem = liveOperatorItem
    ?? (selected?.kind === 'operator_item' && operatorItemSnap?.id === selected.id
      ? operatorItemSnap : null);
  // Still selected but no longer in the live pool = resolved elsewhere.
  const dismissalResolved = selectedDismissal != null && liveDismissal == null;
  const operatorItemResolved = selectedOperatorItem != null && liveOperatorItem == null;
  // A proposal that left BOTH pending and history was decided out from under
  // the operator (e.g. task cancel → WITHDRAWN); the pane refetches and shows
  // the decided record read-only instead of stale action buttons.
  const proposalPendingGone = selected?.kind === 'proposal'
    && ![...proposals, ...history].some((p) => p.id === selected.id);

  const pending = proposals.length + dismissals.length + operatorItems.length;

  // An action resolves LATE — the rpc plus a full decisions.list refresh takes
  // seconds — and by then the operator has usually clicked the next item. A
  // bare setSelected(null) yanks that one out from under them (the
  // no-displacement rule, 2026-08-16, the operator). Close the pane only if the thing
  // that was acted on is still the thing on screen.
  const clearIfStill = useCallback((kind: string, id: string) => {
    setSelected((s) => (s?.kind === kind && s.id === id ? null : s));
  }, []);

  return (
    <>
    {/* The list/detail boundary is draggable now, so the side-by-side test can no
        longer be a container query — ListDetailSplit measures it and renders the
        plain stacked column below the breakpoint, exactly as this did before. */}
    <ListDetailSplit
      id="cc-decisions"
      aside={<>
        <div className="flex items-center gap-2 border-b border-border/40 px-4 py-3">
          <Inbox size={14} className="text-primary" />
          <span className="text-[0.8rem] font-semibold uppercase tracking-[0.14em] text-foreground">Decisions</span>
          <span className="cockpit-badge tabular-nums">{pending} pending</span>
          <button
            type="button"
            onClick={toggleHistory}
            aria-pressed={showHistory}
            aria-label={showHistory ? 'Hide decision history' : 'Show decision history'}
            title={showHistory ? 'Hide decision history' : 'Show decision history'}
            data-active={showHistory ? 'true' : 'false'}
            className="ml-auto text-muted-foreground transition-colors hover:text-foreground data-[active=true]:text-primary"
          >
            <History size={13} />
          </button>
          <button
            onClick={() => refresh()}
            title="Refresh"
            aria-label="Refresh decisions"
            className="text-muted-foreground transition-colors hover:text-foreground"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {error && <p className="px-4 py-2 text-[0.733rem] text-destructive">{error}</p>}

          <SectionLabel
            count={proposals.length}
            oldest={oldestAge(proposals.map((p) => p.created_at))}
          >Proposals</SectionLabel>
          {proposals.length === 0 && !loading && (
            <p className="px-4 py-2 text-[0.733rem] text-muted-foreground">No proposals awaiting review.</p>
          )}
          {proposals.map((p) => (
            <ProposalRowItem
              key={p.id}
              p={p}
              active={selected?.kind === 'proposal' && selected.id === p.id}
              onSelect={() => setSelected({ kind: 'proposal', id: p.id })}
            />
          ))}

          {operatorItems.length > 0 && (
            <>
              <SectionLabel
                count={operatorItems.length}
                oldest={oldestAge(operatorItems.map((i) => i.created_at))}
              >Agent asks</SectionLabel>
              {operatorItems.map((i) => (
                <OperatorItemRowItem
                  key={i.id}
                  item={i}
                  active={selected?.kind === 'operator_item' && selected.id === i.id}
                  onSelect={() => setSelected({ kind: 'operator_item', id: i.id })}
                />
              ))}
            </>
          )}

          <div className="flex items-center">
            <SectionLabel
              count={dismissals.length}
              oldest={oldestAge(dismissals.map((d) => d.enrolled_at))}
            >Dismissals</SectionLabel>
            <button
              onClick={() => openBulkDismiss('')}
              className="ml-auto mr-2 mt-2.5 text-[0.667rem] uppercase tracking-[0.08em] text-muted-foreground transition-colors hover:text-foreground"
            >
              Bulk dismiss…
            </button>
            {dismissals.length > 1 && (
              <button
                onClick={async () => {
                  setConfirmingAll(true);
                  try {
                    await confirmAllDismissals();
                    // The operator's own action — close the pane like any
                    // other decision, don't leave a "resolved elsewhere" note.
                    setSelected((s) => (s?.kind === 'dismissal' ? null : s));
                  } finally { setConfirmingAll(false); }
                }}
                disabled={confirmingAll}
                className="ml-auto mr-4 mt-2.5 text-[0.667rem] uppercase tracking-[0.08em] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
              >
                {confirmingAll ? 'Confirming…' : 'Confirm all'}
              </button>
            )}
          </div>
          {dismissals.length === 0 && !loading && (
            <p className="px-4 py-2 text-[0.733rem] text-muted-foreground">No no-action claims to review.</p>
          )}
          {dismissals.map((d) => (
            <DismissalRowItem
              key={d.id}
              d={d}
              active={selected?.kind === 'dismissal' && selected.id === d.id}
              onSelect={() => setSelected({ kind: 'dismissal', id: d.id })}
            />
          ))}

          {pending === 0 && !loading && (
            <div className="px-4 py-10 text-center">
              <div className="cockpit-badge mx-auto w-fit">Inbox clear</div>
              <p className="mt-3 text-sm text-muted-foreground">Nothing awaits your review.</p>
            </div>
          )}

          {showHistory && (
            <>
              <SectionLabel count={history.length + processedItems.length}>History</SectionLabel>
              {history.length === 0 && processedItems.length === 0 && !historyLoading && (
                <p className="px-4 py-2 text-[0.733rem] text-muted-foreground">No decided proposals yet.</p>
              )}
              {history.map((p) => (
                <HistoryRowItem
                  key={p.id}
                  p={p}
                  active={selected?.kind === 'proposal' && selected.id === p.id}
                  onSelect={() => setSelected({ kind: 'proposal', id: p.id })}
                />
              ))}
              {processedItems.map((item) => (
                <ProcessedRowItem key={item.id} item={item} />
              ))}
              {historyHasMore && (
                <div className="px-4 py-2">
                  <button
                    onClick={loadMoreHistory}
                    disabled={historyLoading}
                    className="w-full rounded-md border border-border/40 py-1.5 text-[0.667rem] uppercase tracking-[0.08em] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
                  >
                    {historyLoading ? 'Loading…' : 'Load more'}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </>}
    >
      <section className="min-w-0 flex-1 min-h-0">
        {/* `key` is load-bearing on all three: each pane holds per-item local
            state (`acting`, the feedback/answer draft) and none of it belongs
            to the NEXT item. Unkeyed, React reuses the instance across an id
            change and the previous item's in-flight `acting` leaves the new
            item's buttons reading "Sending…" and disabled — an action the
            operator never took, on a record it never applied to. */}
        {selected?.kind === 'proposal' && (
          <ProposalPane
            key={selected.id}
            id={selected.id}
            pendingGone={proposalPendingGone}
            onDone={() => clearIfStill('proposal', selected.id)}
            onOpenSession={onOpenSession}
            getProposal={getProposal}
            approve={approve}
            reject={reject}
            dismiss={dismiss}
          />
        )}
        {selectedDismissal && (
          <DismissalPane
            key={selectedDismissal.id}
            item={selectedDismissal}
            resolved={dismissalResolved}
            onDone={() => clearIfStill('dismissal', selectedDismissal.id)}
            confirmDismissal={confirmDismissal}
            reopenDismissal={reopenDismissal}
            onBulkDismissSender={openBulkDismiss}
          />
        )}
        {selectedOperatorItem && (
          <OperatorItemPane
            key={selectedOperatorItem.id}
            item={selectedOperatorItem}
            resolved={operatorItemResolved}
            onDone={() => clearIfStill('operator_item', selectedOperatorItem.id)}
            onOpenTask={onOpenTask}
            onOpenSession={onOpenSession}
            answerOperatorItem={answerOperatorItem}
          />
        )}
        {!selected && (
          <div className="flex h-full items-center justify-center">
            <p className="text-xs text-muted-foreground">Select an item to review its full record.</p>
          </div>
        )}
      </section>
    </ListDetailSplit>
      <BulkDismissDialog
        open={bulkDismissOpen}
        onOpenChange={setBulkDismissOpen}
        initialQuery={bulkDismissQuery}
        // Silent — refreshing the list must never move what the operator is
        // currently reading elsewhere in this view (no-displacement rule).
        onDismissed={() => refresh({ silent: true })}
      />
    </>
  );
}
