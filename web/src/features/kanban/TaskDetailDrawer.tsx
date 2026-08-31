import { useState, useCallback, useEffect, lazy, Suspense } from 'react';
import {
  X, Play, CheckCircle2, XCircle, Trash2, Loader2,
  Clock, User, Tag, AlertTriangle, MessageSquare, StopCircle,
  Compass, CornerDownRight,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { COLUMN_LABELS, type KanbanTask } from './types';
import { SourceEmailBlock } from './SourceEmailBlock';
import { getTaskPriorityLabel, getTaskPriorityTone, getTaskRunTone, getTaskStatusTone } from './tone';

const MarkdownRenderer = lazy(() =>
  import('@/features/markdown/MarkdownRenderer').then(m => ({ default: m.MarkdownRenderer })),
);

/* ── Elapsed time helper ── */
function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return `${m}m ${rs}s`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return `${h}h ${rm}m`;
}

function RunElapsed({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <span className="text-[0.667rem] text-muted-foreground tabular-nums">
      {formatElapsed(now - startedAt)}
    </span>
  );
}

interface TaskDetailDrawerProps {
  task: KanbanTask | null;
  onClose: () => void;
  onDelete: (id: string) => Promise<void>;
  onExecute?: (id: string, options?: { model?: string; thinking?: string }) => Promise<KanbanTask>;
  onApprove?: (id: string, note?: string) => Promise<KanbanTask>;
  onReject?: (id: string, note: string) => Promise<KanbanTask>;
  onAbort?: (id: string, note?: string) => Promise<KanbanTask>;
  onResume?: (id: string) => Promise<KanbanTask>;
  /** Open the run's transcript in chat. The session key was already on the
   *  wire and rendered as a copyable code span — 92 sessions were reachable
   *  only by pasting it somewhere. Optional so the drawer still renders
   *  standalone (tests, and the compact layout where chat is a separate view). */
  onOpenSession?: (sessionKey: string) => void;
}

/**
 * Read-only task detail view. Central Command task records are the
 * operator's ask, verbatim — cc-kanban.ts's PATCH endpoint 400s everything
 * except a status:"CANCELLED" transition, so there is no edit-and-save
 * affordance here, only the workflow levers that actually do something
 * (execute / stop / resume / approve / reject / cancel).
 */
export function TaskDetailDrawer({ task, onClose, onDelete, onExecute, onApprove, onReject, onAbort, onResume, onOpenSession }: TaskDetailDrawerProps) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /* Reset transient state when task changes */
  useEffect(() => {
    if (task) {
      setError(null);
      setConfirmDelete(false);
    }
  }, [task]);

  /* Close on Escape */
  useEffect(() => {
    if (!task) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [task, onClose]);

  const [confirmDelete, setConfirmDelete] = useState(false);

  const handleDelete = useCallback(async () => {
    if (!task || deleting) return;
    setDeleting(true);
    try {
      await onDelete(task.id);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cancel failed');
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  }, [task, deleting, onDelete, onClose]);

  /* ── Workflow action state ── */
  const [workflowLoading, setWorkflowLoading] = useState<string | null>(null);
  const [rejectNote, setRejectNote] = useState('');
  const [showRejectInput, setShowRejectInput] = useState(false);

  const handleExecute = useCallback(async () => {
    if (!task || !onExecute || workflowLoading) return;
    setWorkflowLoading('execute');
    setError(null);
    try {
      await onExecute(task.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Execute failed');
    } finally {
      setWorkflowLoading(null);
    }
  }, [task, onExecute, workflowLoading]);

  const handleApprove = useCallback(async () => {
    if (!task || !onApprove || workflowLoading) return;
    setWorkflowLoading('approve');
    setError(null);
    try {
      await onApprove(task.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Approve failed');
    } finally {
      setWorkflowLoading(null);
    }
  }, [task, onApprove, workflowLoading]);

  const handleReject = useCallback(async () => {
    if (!task || !onReject || workflowLoading) return;
    if (!showRejectInput) {
      setShowRejectInput(true);
      return;
    }
    if (!rejectNote.trim()) return;
    setWorkflowLoading('reject');
    setError(null);
    try {
      await onReject(task.id, rejectNote.trim());
      setShowRejectInput(false);
      setRejectNote('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Reject failed');
    } finally {
      setWorkflowLoading(null);
    }
  }, [task, onReject, workflowLoading, showRejectInput, rejectNote]);

  const handleAbort = useCallback(async () => {
    if (!task || !onAbort || workflowLoading) return;
    setWorkflowLoading('abort');
    setError(null);
    try {
      await onAbort(task.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Abort failed');
    } finally {
      setWorkflowLoading(null);
    }
  }, [task, onAbort, workflowLoading]);

  const handleResume = useCallback(async () => {
    if (!task || !onResume || workflowLoading) return;
    setWorkflowLoading('resume');
    setError(null);
    try {
      await onResume(task.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Resume failed');
    } finally {
      setWorkflowLoading(null);
    }
  }, [task, onResume, workflowLoading]);

  /* Reset reject input when task changes */
  useEffect(() => {
    setShowRejectInput(false);
    setRejectNote('');
    setWorkflowLoading(null);
  }, [task?.id]);

  const isOpen = task !== null;
  const priorityTone = task ? getTaskPriorityTone(task.priority) : null;

  return (
    <>
      {/* Backdrop */}
      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 transition-opacity duration-200"
          onClick={onClose}
        />
      )}

      {/* Drawer */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Task details"
        className={`shell-panel fixed top-0 right-0 z-50 flex h-full w-[min(92vw,520px)] max-w-full flex-col overflow-hidden rounded-l-[32px] border-l border-border/70 shadow-[0_28px_72px_rgba(0,0,0,0.36)] transition-transform duration-[220ms] ease-[cubic-bezier(0.22,1,0.36,1)] ${
          isOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        {task && (
          <>
            <div className="panel-header min-h-[56px] justify-between gap-3 px-4">
              <div className="flex items-center gap-2 min-w-0">
                <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[0.667rem] font-semibold ${getTaskStatusTone(task.status).badgeClass}`}>
                  {COLUMN_LABELS[task.status as keyof typeof COLUMN_LABELS] ?? 'Task'}
                </span>
                <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[0.667rem] font-semibold ${priorityTone?.badgeClass ?? ''}`}>
                  {getTaskPriorityLabel(task.priority)}
                </span>
                {task.stopped && (
                  <span className="inline-flex items-center gap-1 rounded-full border border-orange/30 bg-orange/10 px-2.5 py-1 text-[0.667rem] font-semibold text-orange">
                    <StopCircle size={10} />
                    Stopped
                  </span>
                )}
              </div>
              <button
                onClick={onClose}
                className="shell-icon-button size-9 px-0"
                aria-label="Close drawer"
              >
                <X size={16} />
              </button>
            </div>

            <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
              {error && (
                <div className="cockpit-note flex items-center gap-2 text-sm" data-tone="danger">
                  <AlertTriangle size={12} />
                  {error}
                </div>
              )}

              <div className="cockpit-surface p-4 space-y-4">
                <div>
                  <span className="cockpit-field-label mb-2 block">Title</span>
                  <p className="text-sm font-semibold text-foreground">{task.title}</p>
                </div>

                {task.description && (
                  <div>
                    <span className="cockpit-field-label mb-2 block">Description</span>
                    <div className="min-h-[60px] rounded-2xl border border-border/60 bg-background/45 p-3 text-sm text-foreground">
                      <Suspense fallback={<div className="whitespace-pre-wrap cockpit-wrap">{task.description}</div>}>
                        <MarkdownRenderer content={task.description} suppressImages />
                      </Suspense>
                    </div>
                  </div>
                )}

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {task.labels.length > 0 && (
                    <div>
                      <span className="cockpit-field-label mb-2 block">
                        <Tag size={10} className="mr-1 inline" />
                        Labels
                      </span>
                      <div className="flex flex-wrap gap-1.5">
                        {task.labels.map((label) => (
                          <span
                            key={label}
                            className="rounded-full border border-border/55 bg-background/50 px-2 py-0.5 text-[0.667rem] font-medium text-muted-foreground"
                          >
                            {label}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {task.assignee && (
                    <div>
                      <span className="cockpit-field-label mb-2 block">
                        <User size={10} className="mr-1 inline" />
                        Assignee
                      </span>
                      <p className="text-sm text-foreground">
                        {task.assignee === 'operator' ? 'Operator' : task.assignee.replace('agent:', '@')}
                      </p>
                    </div>
                  )}
                </div>
              </div>

              <div className="cockpit-note space-y-2">
                <h4 className="cockpit-field-label">Metadata</h4>
                <div className="space-y-1 text-[0.733rem] text-muted-foreground">
                  <div className="flex items-center gap-1.5">
                    <Clock size={10} />
                    Created: {new Date(task.createdAt).toLocaleString()}
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Clock size={10} />
                    Updated: {new Date(task.updatedAt).toLocaleString()}
                  </div>
                  <div className="flex items-center gap-1.5">
                    <User size={10} />
                    By: {task.createdBy === 'operator' ? 'Operator' : task.createdBy}
                  </div>
                </div>
              </div>

              {task.sourceEmail && <SourceEmailBlock email={task.sourceEmail} />}

              {task.sourceSessionKey && (
                <div className="cockpit-note space-y-1 text-[0.733rem] text-muted-foreground">
                  <h4 className="cockpit-field-label">Session</h4>
                  {onOpenSession ? (
                    <button
                      type="button"
                      onClick={() => onOpenSession(task.sourceSessionKey!)}
                      title="Open this task's session"
                      className="cockpit-kbd cursor-pointer underline decoration-dotted underline-offset-2 hover:text-foreground"
                    >
                      {task.sourceSessionKey}
                    </button>
                  ) : (
                    <code className="cockpit-kbd select-all cursor-pointer">{task.sourceSessionKey}</code>
                  )}
                </div>
              )}

              {task.run && (
                <div className="cockpit-note space-y-2">
                  <h4 className="cockpit-field-label">Agent Run</h4>
                  <div className="space-y-1.5 text-[0.733rem] text-muted-foreground">
                    <div className="flex items-center gap-2">
                      <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[0.667rem] font-semibold ${getTaskRunTone(task.run.status).badgeClass}`}>
                        {task.run.status === 'running' && <Loader2 size={9} className="animate-spin" />}
                        {task.run.status.charAt(0).toUpperCase() + task.run.status.slice(1)}
                      </span>
                      {task.run.status === 'running' && task.run.startedAt && (
                        <RunElapsed startedAt={task.run.startedAt} />
                      )}
                    </div>
                    <div>
                      Session:{' '}
                      {onOpenSession ? (
                        <button
                          type="button"
                          onClick={() => onOpenSession(task.run!.sessionKey)}
                          title="Open this run's transcript"
                          className="cockpit-kbd cursor-pointer underline decoration-dotted underline-offset-2 hover:text-foreground"
                        >
                          {task.run.sessionKey}
                        </button>
                      ) : (
                        <code className="cockpit-kbd select-all cursor-pointer">{task.run.sessionKey}</code>
                      )}
                    </div>
                    {task.run.startedAt && (
                      <div>Started: {new Date(task.run.startedAt).toLocaleString()}</div>
                    )}
                  </div>
                </div>
              )}

              {/* D7 phase 2: a project's delegated sub-tasks — agent + status */}
              {task.delegation?.role === 'project' && task.delegation.children && (
                <div className="cockpit-note space-y-2">
                  <h4 className="cockpit-field-label text-balance">
                    <Compass size={10} className="mr-1 inline" />
                    Delegated sub-tasks ({task.delegation.childDone ?? 0}/{task.delegation.childCount ?? 0} settled)
                  </h4>
                  <div className="space-y-1.5">
                    {task.delegation.children.map((child) => (
                      <div
                        key={child.id}
                        className="flex items-center gap-2 rounded-2xl border border-border/60 bg-background/45 p-2.5 text-xs"
                      >
                        <CornerDownRight size={12} className="shrink-0 text-info/80" />
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-medium text-foreground">{child.title}</p>
                          <p className="truncate text-[0.667rem] text-muted-foreground">
                            {child.assignee === 'operator' ? 'Operator' : (child.assignee?.replace('agent:', '@') ?? 'unassigned')}
                          </p>
                        </div>
                        <span className={`shrink-0 inline-flex items-center rounded-full border px-2 py-0.5 text-[0.6rem] font-semibold ${getTaskStatusTone(child.status).badgeClass}`}>
                          {COLUMN_LABELS[child.status as keyof typeof COLUMN_LABELS] ?? child.status}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* D7 phase 2: a sub-task's origin. `parentSessionId` also rides on
                  a task created from an approved task.create proposal (linking
                  back to the proposing session, not necessarily the
                  orchestrator), so the copy names the source only when known. */}
              {task.delegation?.role === 'subtask' && (
                <div className="cockpit-note">
                  <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <CornerDownRight size={12} className="text-info/80" />
                    Linked to a parent session
                    {task.delegation.parentTitle && (
                      <>
                        {' '}as part of{' '}
                        <span className="font-medium text-foreground">“{task.delegation.parentTitle}”</span>
                      </>
                    )}
                  </p>
                </div>
              )}

              {task.result?.trim() && (
                <div className="cockpit-note space-y-2">
                  <h4 className="cockpit-field-label">Result</h4>
                  <div className="task-result-body rounded-2xl border border-border/60 bg-background/45 p-3 text-xs text-foreground">
                    <Suspense fallback={<div className="whitespace-pre-wrap cockpit-wrap">{task.result}</div>}>
                      <MarkdownRenderer content={task.result} suppressImages />
                    </Suspense>
                  </div>
                </div>
              )}

              {task.feedback.length > 0 && (
                <div className="cockpit-note space-y-3">
                  <h4 className="cockpit-field-label">
                    <MessageSquare size={10} className="mr-1 inline" />
                    Feedback
                  </h4>
                  <div className="space-y-2">
                    {task.feedback.map((fb, i) => (
                      <div key={i} className="rounded-2xl border border-border/60 bg-background/45 p-3 text-xs">
                        <div className="mb-1 flex items-center justify-between text-[0.667rem] text-muted-foreground">
                          <span>{fb.by === 'operator' ? 'Operator' : fb.by}</span>
                          <span>{new Date(fb.at).toLocaleString()}</span>
                        </div>
                        <p className="text-foreground">{fb.note}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="shrink-0 border-t border-border/60 bg-background/88 px-4 py-3">
              {/* Reject note input */}
              {showRejectInput && (
                <div className="mb-3 flex items-center gap-2">
                  <Input
                    value={rejectNote}
                    onChange={e => setRejectNote(e.target.value)}
                    placeholder="Rejection reason (required)…"
                    className="cockpit-input h-10 flex-1 text-sm"
                    onKeyDown={e => { if (e.key === 'Enter') handleReject(); if (e.key === 'Escape') { setShowRejectInput(false); setRejectNote(''); } }}
                    autoFocus
                  />
                  <Button size="xs" variant="outline" onClick={() => { setShowRejectInput(false); setRejectNote(''); }}>
                    Cancel
                  </Button>
                </div>
              )}

              <div className="flex items-center gap-2">
              {/* Workflow actions — gated on the real Central Command lifecycle
                  (NEW | ASSIGNED | IN_PROGRESS | REVIEW | DONE | FAILED |
                  CANCELLED) and `stopped`, which rides separately from status:
                  a stopped run stays IN_PROGRESS (it did not finish). */}
              {task.status === 'NEW' && onExecute && (
                <Button size="xs" onClick={handleExecute} disabled={workflowLoading !== null}>
                  {workflowLoading === 'execute' ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                  Execute
                </Button>
              )}
              {task.status === 'IN_PROGRESS' && !task.stopped && onAbort && (
                <Button size="xs" variant="outline" onClick={handleAbort} disabled={workflowLoading !== null} className="border-orange/30 bg-orange/8 text-orange hover:bg-orange/12">
                  {workflowLoading === 'abort' ? <Loader2 size={12} className="animate-spin" /> : <StopCircle size={12} />}
                  Stop
                </Button>
              )}
              {task.status === 'IN_PROGRESS' && task.stopped && onResume && (
                <Button size="xs" variant="outline" onClick={handleResume} disabled={workflowLoading !== null} className="border-info/30 bg-info/8 text-info hover:bg-info/12">
                  {workflowLoading === 'resume' ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                  Resume
                </Button>
              )}
              {task.status === 'REVIEW' && !task.stopped && (
                <>
                  {onApprove && (
                    <Button size="xs" variant="outline" onClick={handleApprove} disabled={workflowLoading !== null} className="border-green/30 bg-green/8 text-green hover:bg-green/12">
                      {workflowLoading === 'approve' ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />}
                      Approve
                    </Button>
                  )}
                  {onReject && (
                    <Button size="xs" variant="outline" onClick={handleReject} disabled={workflowLoading !== null || (showRejectInput && !rejectNote.trim())} className="border-destructive/30 bg-destructive/8 text-destructive hover:bg-destructive/12">
                      {workflowLoading === 'reject' ? <Loader2 size={12} className="animate-spin" /> : <XCircle size={12} />}
                      Reject
                    </Button>
                  )}
                </>
              )}

              <div className="flex-1" />

              {/* Cancel-for-good: legal for NEW/ASSIGNED (nothing ran yet),
                  REVIEW (not stopped — cancel resolves the pending proposal
                  too), and a stopped IN_PROGRESS run. A LIVE run must be
                  stopped first (backend 409s it), and a terminal task
                  (DONE/FAILED/CANCELLED) has nothing left to cancel. */}
              {(task.status === 'NEW' || task.status === 'ASSIGNED'
                || (task.status === 'REVIEW' && !task.stopped)
                || (task.status === 'IN_PROGRESS' && task.stopped)) && (
                confirmDelete ? (
                  <span className="inline-flex items-center gap-1.5">
                    <span className="text-[0.733rem] text-destructive font-medium">Cancel task?</span>
                    <Button
                      size="xs"
                      variant="destructive"
                      onClick={handleDelete}
                      disabled={deleting}
                    >
                      {deleting ? <Loader2 size={12} className="animate-spin" /> : 'Yes'}
                    </Button>
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() => setConfirmDelete(false)}
                      disabled={deleting}
                    >
                      No
                    </Button>
                  </span>
                ) : (
                  <Button
                    size="xs"
                    variant="destructive"
                    onClick={() => setConfirmDelete(true)}
                  >
                    <Trash2 size={12} />
                    Cancel task
                  </Button>
                )
              )}
              </div>
            </div>
          </>
        )}
      </div>
    </>
  );
}
