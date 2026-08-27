// Kanban type contracts — Frozen v1
// Change policy: coordinator approval + issue-file sync required.

/**
 * Status keys — Central Command's task lifecycle, verbatim. This is the wire
 * vocabulary the server actually speaks (`/api/kanban/config` columns and every
 * task's `status`), so the frontend keys on it rather than on Nerve's old
 * lowercase set. One vocabulary, no translation layer to drift.
 */
export const BUILT_IN_STATUSES = [
  'NEW', 'ASSIGNED', 'IN_PROGRESS', 'REVIEW', 'DONE', 'FAILED', 'CANCELLED',
] as const;
export type BuiltInStatus = typeof BUILT_IN_STATUSES[number];

/**
 * TaskStatus is a plain string so custom column keys are supported.
 * The board config (from /api/kanban/config) is the canonical source of truth
 * for which statuses are valid and how columns are ordered.
 */
export type TaskStatus = string;
export type TaskPriority = 'critical' | 'high' | 'normal' | 'low';

/**
 * Default column display order used as a fallback before the board config loads.
 * Consumers should prefer `config.columns` from useKanban() over this constant.
 */
export const COLUMNS: TaskStatus[] = ['NEW', 'ASSIGNED', 'IN_PROGRESS', 'REVIEW', 'DONE', 'FAILED'];

/** Human-readable labels for built-in columns. Custom columns use their `title` from config. */
export const COLUMN_LABELS: Record<string, string> = {
  NEW: 'New',
  ASSIGNED: 'Assigned',
  IN_PROGRESS: 'Running',
  REVIEW: 'Review',
  DONE: 'Done',
  FAILED: 'Failed',
  CANCELLED: 'Cancelled',
};
export type TaskActor = 'operator' | `agent:${string}`;

export interface TaskFeedback {
  at: number;
  by: TaskActor;
  note: string;
}

export interface TaskRunLink {
  sessionKey: string;
  sessionId?: string;
  runId?: string;
  startedAt: number;
  endedAt?: number;
  status: 'running' | 'done' | 'error' | 'aborted';
  error?: string;
}

/**
 * D7 phase 2 delegation: a project task (assigned to the orchestrator) fans
 * out sub-tasks to other agents. The board derives this from the tasks'
 * session/parent-session linkage (useKanban's enrichDelegation) so it can
 * show, at a glance, that a task delegated work — to whom, and how far along.
 */
export interface DelegationChild {
  id: string;
  title: string;
  assignee?: TaskActor;
  status: TaskStatus;
}
export interface DelegationInfo {
  role: 'project' | 'subtask';
  /** project: total sub-tasks fanned out, and how many reached a terminal state. */
  childCount?: number;
  childDone?: number;
  children?: DelegationChild[];
  /** subtask: the project it belongs to. */
  parentId?: string;
  parentTitle?: string;
}

export interface KanbanTask {
  id: string;
  title: string;
  description?: string;
  status: TaskStatus;
  priority: TaskPriority;
  createdBy: TaskActor;
  createdAt: number;
  updatedAt: number;
  version: number;
  sourceSessionKey?: string;
  assignee?: TaskActor;
  labels: string[];
  columnOrder: number;
  run?: TaskRunLink;
  result?: string;
  resultAt?: number;
  model?: string;
  thinking?: 'off' | 'low' | 'medium' | 'high';
  dueAt?: number;
  estimateMin?: number;
  actualMin?: number;
  feedback: TaskFeedback[];
  /** D7 phase 2 — the run session this task's transcript lives in. */
  sessionId?: string;
  /** D7 phase 2 — set on a sub-task: the orchestration session that owns it. */
  parentSessionId?: string;
  /** D7 phase 2 — derived project ↔ sub-task relationship (enrichDelegation). */
  delegation?: DelegationInfo;
  /** Operator stop/resume: the task's live run is parked STOPPED. Derived
   *  server-side from the session row, never stored — can't drift from it. */
  stopped?: boolean;
  /** The email that spawned this task, resolved server-side from
   *  `parentSessionId` (the work item the proposing session was processing).
   *  Absent for tasks with no parent session, or whose parent session
   *  wasn't processing a ledger email (e.g. an operator-created task). */
  sourceEmail?: TaskSourceEmail;
}

/** Minimal source-email shape the board needs — mirrors the Decisions
 *  Inbox's WorkItem fields it actually renders (subject, sender, body). */
export interface TaskSourceEmail {
  subject?: string | null;
  from?: string | null;
  text?: string | null;
}

/** Terminal task statuses (Central Command lifecycle) — a sub-task is "settled". */
export const TERMINAL_TASK_STATUSES = new Set(['DONE', 'FAILED', 'CANCELLED']);

/**
 * Which board column a task displays in. CANCELLED folds into DONE's lane —
 * the operator asked to see cancelled work alongside finished work, not
 * hidden in its own empty column — while the card's own badge keeps the
 * distinction (see KanbanCard). Everything else shows under its own status.
 */
export function boardColumnFor(task: KanbanTask): TaskStatus {
  return task.status === 'CANCELLED' ? 'DONE' : task.status;
}

/**
 * Attach delegation info to a flat task list: mark sub-tasks with their
 * parent project, and project tasks with their sub-task roster + progress.
 * Pure so it is unit-testable and cheap to memoize.
 */
export function enrichDelegation(tasks: KanbanTask[]): KanbanTask[] {
  const bySession = new Map<string, KanbanTask>();
  const childrenByParent = new Map<string, KanbanTask[]>();
  for (const t of tasks) {
    if (t.sessionId) bySession.set(t.sessionId, t);
    if (t.parentSessionId) {
      const arr = childrenByParent.get(t.parentSessionId);
      if (arr) arr.push(t);
      else childrenByParent.set(t.parentSessionId, [t]);
    }
  }
  return tasks.map((t) => {
    if (t.parentSessionId) {
      const parent = bySession.get(t.parentSessionId);
      return {
        ...t,
        delegation: {
          role: 'subtask' as const,
          parentId: parent?.id,
          parentTitle: parent?.title,
        },
      };
    }
    const kids = t.sessionId ? childrenByParent.get(t.sessionId) : undefined;
    if (kids && kids.length > 0) {
      return {
        ...t,
        delegation: {
          role: 'project' as const,
          childCount: kids.length,
          childDone: kids.filter((k) => TERMINAL_TASK_STATUSES.has(k.status)).length,
          children: kids.map((k) => ({
            id: k.id, title: k.title, assignee: k.assignee, status: k.status,
          })),
        },
      };
    }
    return t;
  });
}
