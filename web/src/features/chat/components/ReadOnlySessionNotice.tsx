/**
 * ReadOnlySessionNotice — what stands where the message box would be when the
 * selected session does not take operator input.
 *
 * Central Command (2026-07-25): the sessions panel lists PAUSED TASK RUNS beside
 * conversations, and until now selecting one showed an enabled composer that
 * the server refused on every send. A blocked task, a question in the Decisions
 * Inbox and an untouchable transcript were three unrelated things on screen.
 * This says what the transcript is, what is holding it, and where the operator
 * can actually act — reading the whole thing from the server's own composer
 * state, never re-deriving the rule client-side.
 */
import { useState, useCallback } from 'react';
import { Lock, ArrowRight, Loader2, Play, Square, XCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import type { ChatComposerState } from '@/types';

const TERMINAL_TASK = new Set(['DONE', 'FAILED', 'CANCELLED']);
const TERMINAL_SESSION = new Set(['DONE', 'FAILED']);

interface ReadOnlySessionNoticeProps {
  composer: ChatComposerState;
  /** Open the Decisions Inbox (optionally focused on the blocking item). */
  onOpenDecisions?: (itemId?: string) => void;
  /** Open the task's card on the board. */
  onOpenTask?: (taskId: string) => void;
  /** Resume a session the operator stopped (composer.session.id). */
  onResume?: (sessionId: string) => Promise<void>;
  /** Stop the task's LIVE run at its next node boundary (composer.task.id). */
  onStopTask?: (taskId: string) => Promise<void>;
  /** Cancel the task for good — closes its parked session (composer.task.id). */
  onCancelTask?: (taskId: string) => Promise<void>;
}

export function ReadOnlySessionNotice({
  composer, onOpenDecisions, onOpenTask, onResume, onStopTask, onCancelTask,
}: ReadOnlySessionNoticeProps) {
  const { session, task, blocking } = composer;
  const [resuming, setResuming] = useState(false);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const isStopped = composer.code === 'stopped';
  // Central Command (2026-09-13): the two levers a task run has, offered where
  // the operator is looking at it. A LIVE run can be stopped (cooperative,
  // parks STOPPED); any PARKED run can be cancelled (the server's
  // /tasks/{id}/cancel closes the session with its transcript kept). Forty
  // sessions parked AWAITING_RESUME sat "IDLE" in the panel with no lever
  // because the board's list had aged their tasks off the newest-100 page.
  const sessionStatus = session?.status ?? '';
  const openTask = !!task && !TERMINAL_TASK.has(task.status);
  const canStop = openTask && sessionStatus === 'RUNNING' && !!onStopTask;
  const canCancel = openTask && sessionStatus !== 'RUNNING'
    && !TERMINAL_SESSION.has(sessionStatus) && !!onCancelTask;

  const runAction = useCallback(async (fn: (id: string) => Promise<void>) => {
    if (!task || acting) return;
    setActing(true);
    setActionError(null);
    try {
      await fn(task.id);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Action failed');
    } finally {
      setActing(false);
    }
  }, [task, acting]);

  const handleResume = useCallback(async () => {
    if (!session || !onResume || resuming) return;
    setResuming(true);
    setResumeError(null);
    try {
      await onResume(session.id);
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : 'Resume failed');
    } finally {
      setResuming(false);
    }
  }, [onResume, session, resuming]);
  const isRun = composer.code === 'not_a_conversation';
  // The server's sentence for anything that is not a run (a pending proposal, a
  // failed conversation, a key with no lane) — it knows the rule; we don't.
  const headline = isRun
    ? composer.message ?? 'This session is a record of work, not a conversation.'
    : composer.message ?? 'This session is not accepting messages.';

  return (
    <div
      role="status"
      data-testid="readonly-session-notice"
      className="shrink-0 border-t border-border/40 bg-muted/20 px-4 py-3"
    >
      <div className="flex items-start gap-2.5">
        <Lock size={13} className="mt-0.5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <p className="text-[0.8rem] font-medium text-foreground">{headline}</p>

          {isRun && (
            <p className="mt-1 text-[0.733rem] leading-relaxed text-muted-foreground">
              Runs are the record of work an agent did — they resolve through
              their proposals and questions, never by being replied to. The
              transcript above stays readable.
            </p>
          )}

          {blocking && (
            <p className="mt-2 whitespace-pre-wrap cockpit-wrap rounded-lg border border-primary/30 bg-primary/5 p-2.5 text-[0.733rem] leading-relaxed text-foreground/85">
              <span className="font-semibold">
                {session?.agentId ?? 'The agent'} asked:
              </span>{' '}
              {blocking.body}
            </p>
          )}

          {(blocking || task) && (
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              {blocking && onOpenDecisions && (
                <Button
                  variant="outline"
                  size="xs"
                  onClick={() => onOpenDecisions(blocking.itemId)}
                >
                  {blocking.discussionSessionId
                    ? 'See it in the Decisions Inbox'
                    : 'Answer it in the Decisions Inbox'}
                  <ArrowRight size={12} />
                </Button>
              )}
              {task && onOpenTask && (
                <Button
                  variant="outline"
                  size="xs"
                  onClick={() => onOpenTask(task.id)}
                >
                  {task.title} · {task.status}
                  <ArrowRight size={12} />
                </Button>
              )}
              {canStop && (
                <Button
                  variant="outline"
                  size="xs"
                  disabled={acting}
                  onClick={() => runAction(onStopTask!)}
                  className="border-warning/30 bg-warning/8 text-warning hover:bg-warning/12"
                >
                  {acting ? <Loader2 size={12} className="animate-spin" /> : <Square size={12} />}
                  Stop run
                </Button>
              )}
              {canCancel && (
                <Button
                  variant="outline"
                  size="xs"
                  disabled={acting}
                  onClick={() => setConfirmCancel(true)}
                  className="border-destructive/30 bg-destructive/8 text-destructive hover:bg-destructive/12"
                >
                  {acting ? <Loader2 size={12} className="animate-spin" /> : <XCircle size={12} />}
                  Cancel task
                </Button>
              )}
            </div>
          )}
          {actionError && (
            <p className="mt-1.5 text-[0.733rem] text-destructive">{actionError}</p>
          )}
          {task && (
            <ConfirmDialog
              open={confirmCancel}
              title="Cancel this task?"
              message={`"${task.title}" ends here: its open proposals and questions are withdrawn and the session closes. The transcript is kept. This cannot be undone.`}
              confirmLabel="Cancel task"
              cancelLabel="Keep it"
              variant="danger"
              onConfirm={() => { setConfirmCancel(false); void runAction(onCancelTask!); }}
              onCancel={() => setConfirmCancel(false)}
            />
          )}

          {composer.code === 'pending_proposal' && onOpenDecisions && (
            <div className="mt-2.5">
              <Button
                variant="outline"
                size="xs"
                onClick={() => onOpenDecisions()}
              >
                Open the Decisions Inbox
                <ArrowRight size={12} />
              </Button>
            </div>
          )}

          {isStopped && onResume && (
            <div className="mt-2.5">
              <Button
                variant="outline"
                size="xs"
                onClick={handleResume}
                disabled={resuming}
                className="border-info/30 bg-info/8 text-info hover:bg-info/12"
              >
                {resuming ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                Resume
              </Button>
              {resumeError && (
                <p className="mt-1.5 text-[0.733rem] text-destructive">{resumeError}</p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
