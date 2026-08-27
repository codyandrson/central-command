import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import type { KanbanTask, TaskStatus, TaskPriority } from '../types';
import { COLUMNS, TERMINAL_TASK_STATUSES, boardColumnFor, enrichDelegation } from '../types';

/* ── API response shape ── */
interface TasksResponse {
  items: KanbanTask[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

/* ── Filter state ──
 * `priority` was removed as a filter (2026-08-07): the field is hardcoded
 * 'normal' server-side (cc-kanban.ts mapTask), so filtering by it never
 * narrowed anything. The `priority` field stays on KanbanTask/the card UI —
 * only the filter plumbing is gone.
 * `createdAfter`/`updatedAfter` are client-side only (the board already
 * fetches the full task list) — never sent to the server.
 */
export interface KanbanFilters {
  q: string;
  assignee: string;
  labels: string[];
  createdAfter?: string;
  updatedAfter?: string;
}

const EMPTY_FILTERS: KanbanFilters = { q: '', assignee: '', labels: [] };

/** A terminal task older than this is hidden by default (see showOldTerminal below). */
const OLD_TERMINAL_MS = 24 * 60 * 60 * 1000;
const SHOW_OLD_TERMINAL_KEY = 'cc-kanban-show-old-terminal';

/** Error with attached latest task from a 409 response */
export interface VersionConflictError extends Error {
  latest?: KanbanTask;
}

/* ── Create / Update payloads ── */
/** Wire shape mirrors chat's attachment seam exactly (`central_command/api/attachments.py`
 * `_decode`) — name/mimeType/content(base64), documents only. */
export interface TaskAttachment {
  name: string;
  mimeType: string;
  content: string; // base64
}

export interface CreateTaskPayload {
  title: string;
  description?: string;
  status?: TaskStatus;
  priority?: TaskPriority;
  labels?: string[];
  assignee?: string;
  attachments?: TaskAttachment[];
}

export interface UpdateTaskPayload {
  title?: string;
  description?: string | null;
  status?: TaskStatus;
  priority?: TaskPriority;
  labels?: string[];
  assignee?: string | null;
  version: number;
}

/* ── Build query string from filters ── */
function buildQuery(filters: KanbanFilters): string {
  const p = new URLSearchParams();
  if (filters.q) p.set('q', filters.q);
  if (filters.assignee) p.set('assignee', filters.assignee);
  for (const l of filters.labels) p.append('label', l);
  p.set('limit', '200');
  return p.toString();
}

/* ── Board config ── */
export interface BoardColumnConfig {
  key: string;
  title: string;
  wipLimit?: number;
  visible: boolean;
}

export interface BoardConfig {
  columns: BoardColumnConfig[];
  defaults: { status: string; priority: string };
  reviewRequired: boolean;
  allowDoneDragBypass: boolean;
  quickViewLimit: number;
  proposalPolicy: 'confirm' | 'auto';
}

/* ── Hook ──
 * `assignee` pins the assignee filter for the whole hook (the workspace panel
 * scopes its task list to the selected agent). Unpinned callers — the full
 * board — keep the filter under the header's control, unchanged.
 */
export function useKanban(assignee?: string) {
  const [tasks, setTasks] = useState<KanbanTask[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ownFilters, setFilters] = useState<KanbanFilters>(EMPTY_FILTERS);
  const filters = useMemo(
    () => (assignee ? { ...ownFilters, assignee } : ownFilters),
    [ownFilters, assignee],
  );
  const [boardConfig, setBoardConfig] = useState<BoardConfig | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  /* ── Fetch board config (columns are user-configurable) ── */
  useEffect(() => {
    fetch('/api/kanban/config')
      .then(r => r.ok ? r.json() : null)
      .then((cfg: BoardConfig | null) => { if (cfg) setBoardConfig(cfg); })
      .catch(() => {/* fallback to COLUMNS default */});
  }, []);

  /** Visible columns in display order, derived from board config when available. */
  const boardColumns = useMemo((): TaskStatus[] => {
    if (!boardConfig) return COLUMNS;
    return boardConfig.columns.filter(c => c.visible).map(c => c.key);
  }, [boardConfig]);

  /* ── Fetch ── */

  const fetchTasks = useCallback(async (f?: KanbanFilters, { silent = false }: { silent?: boolean } = {}) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    // Only show loading skeleton on first load or explicit filter changes, not background polls
    if (!silent) {
      setLoading(true);
      setError(null);
    }
    try {
      const qs = buildQuery(f ?? filters);
      const res = await fetch(`/api/kanban/tasks?${qs}`, { signal: controller.signal });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: TasksResponse = await res.json();
      setTasks(data.items);
      setTotal(data.total);
      if (!silent) setError(null);
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      // Only surface errors on explicit fetches, not silent polls
      if (!silent) setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      if (!silent) setLoading(false);
    }
  }, [filters]);

  /* Initial fetch + refetch on filter change */
  useEffect(() => {
    fetchTasks(filters);
    return () => abortRef.current?.abort();
  }, [filters, fetchTasks]);

  /* Auto-refresh every 5s so board stays current (silent — no loading flash) */
  useEffect(() => {
    const id = setInterval(() => fetchTasks(undefined, { silent: true }), 5_000);
    return () => clearInterval(id);
  }, [fetchTasks]);

  /* ── Mutations ── */
  const createTask = useCallback(async (payload: CreateTaskPayload): Promise<KanbanTask> => {
    const res = await fetch('/api/kanban/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.details || body.error || `HTTP ${res.status}`);
    }
    const created: KanbanTask = await res.json();
    // Refetch to get accurate ordering
    await fetchTasks();
    return created;
  }, [fetchTasks]);

  const updateTask = useCallback(async (id: string, payload: UpdateTaskPayload): Promise<KanbanTask> => {
    const res = await fetch(`/api/kanban/tasks/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      if (res.status === 409) {
        const err = new Error('version_conflict');
        (err as VersionConflictError).latest = body.latest;
        throw err;
      }
      throw new Error(body.details || body.error || `HTTP ${res.status}`);
    }
    const updated: KanbanTask = await res.json();
    await fetchTasks();
    return updated;
  }, [fetchTasks]);

  /** Reorder / move a task via the dedicated reorder endpoint. */
  const reorderTask = useCallback(async (
    id: string,
    version: number,
    targetStatus: TaskStatus,
    targetIndex: number,
  ): Promise<KanbanTask> => {
    const res = await fetch(`/api/kanban/tasks/${encodeURIComponent(id)}/reorder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ version, targetStatus, targetIndex }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      if (res.status === 409) {
        const err = new Error('version_conflict');
        (err as VersionConflictError).latest = body.latest;
        throw err;
      }
      throw new Error(body.details || body.error || `HTTP ${res.status}`);
    }
    const updated: KanbanTask = await res.json();
    // Refetch to sync all columnOrder values from server
    await fetchTasks(undefined, { silent: true });
    return updated;
  }, [fetchTasks]);

  /** Optimistic state updater for drag-and-drop — applies immediately, no API call. */
  const setTasksOptimistic = useCallback((updater: (prev: KanbanTask[]) => KanbanTask[]) => {
    setTasks(updater);
  }, []);

  const deleteTask = useCallback(async (id: string): Promise<void> => {
    const res = await fetch(`/api/kanban/tasks/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.details || body.error || `HTTP ${res.status}`);
    }
    await fetchTasks();
  }, [fetchTasks]);

  /* ── Workflow mutations ── */

  const executeTask = useCallback(async (id: string, options?: { model?: string; thinking?: string }): Promise<KanbanTask> => {
    const res = await fetch(`/api/kanban/tasks/${encodeURIComponent(id)}/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(options ?? {}),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.details || body.error || `HTTP ${res.status}`);
    }
    const task: KanbanTask = await res.json();
    await fetchTasks(undefined, { silent: true });
    return task;
  }, [fetchTasks]);

  const approveTask = useCallback(async (id: string, note?: string): Promise<KanbanTask> => {
    const res = await fetch(`/api/kanban/tasks/${encodeURIComponent(id)}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(note ? { note } : {}),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.details || body.error || `HTTP ${res.status}`);
    }
    const task: KanbanTask = await res.json();
    await fetchTasks(undefined, { silent: true });
    return task;
  }, [fetchTasks]);

  const rejectTask = useCallback(async (id: string, note: string): Promise<KanbanTask> => {
    const res = await fetch(`/api/kanban/tasks/${encodeURIComponent(id)}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.details || body.error || `HTTP ${res.status}`);
    }
    const task: KanbanTask = await res.json();
    await fetchTasks(undefined, { silent: true });
    return task;
  }, [fetchTasks]);

  const abortTask = useCallback(async (id: string, note?: string): Promise<KanbanTask> => {
    const res = await fetch(`/api/kanban/tasks/${encodeURIComponent(id)}/abort`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(note ? { note } : {}),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.details || body.error || `HTTP ${res.status}`);
    }
    const task: KanbanTask = await res.json();
    await fetchTasks(undefined, { silent: true });
    return task;
  }, [fetchTasks]);

  /** Resume a task's run the operator stopped — proxies to the task's session. */
  const resumeTask = useCallback(async (id: string): Promise<KanbanTask> => {
    const res = await fetch(`/api/kanban/tasks/${encodeURIComponent(id)}/resume`, {
      method: 'POST',
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.details || body.error || `HTTP ${res.status}`);
    }
    const task: KanbanTask = await res.json();
    await fetchTasks(undefined, { silent: true });
    return task;
  }, [fetchTasks]);

  /* ── Helpers ── */
  // D7 phase 2: attach project ↔ sub-task delegation info so the board can
  // show which tasks fanned work out to sub-agents and how far along it is.
  const enrichedTasks = useMemo(() => enrichDelegation(tasks), [tasks]);

  const dateFilteredTasks = useMemo(() => {
    const createdAfter = filters.createdAfter ? new Date(filters.createdAfter).getTime() : undefined;
    const updatedAfter = filters.updatedAfter ? new Date(filters.updatedAfter).getTime() : undefined;
    if (createdAfter === undefined && updatedAfter === undefined) return enrichedTasks;
    return enrichedTasks.filter(t =>
      (createdAfter === undefined || t.createdAt >= createdAfter)
      && (updatedAfter === undefined || t.updatedAt >= updatedAfter));
  }, [enrichedTasks, filters.createdAfter, filters.updatedAfter]);

  /* Old-terminal filter — hides DONE/FAILED/CANCELLED tasks whose last update
   * is >24h old by default (persisted toggle to show them, same pattern as
   * SessionList's history filter). */
  const [showOldTerminal, setShowOldTerminalState] = useState(() => {
    try { return localStorage.getItem(SHOW_OLD_TERMINAL_KEY) === 'true'; } catch { return false; }
  });
  const setShowOldTerminal = useCallback((next: boolean) => {
    setShowOldTerminalState(next);
    try { localStorage.setItem(SHOW_OLD_TERMINAL_KEY, String(next)); } catch { /* ignore */ }
  }, []);

  const isOldTerminal = useCallback((t: KanbanTask) =>
    TERMINAL_TASK_STATUSES.has(t.status) && (Date.now() - t.updatedAt) > OLD_TERMINAL_MS, []);

  const { visibleTasks, oldTerminalHiddenCount } = useMemo(() => ({
    visibleTasks: showOldTerminal ? dateFilteredTasks : dateFilteredTasks.filter(t => !isOldTerminal(t)),
    oldTerminalHiddenCount: dateFilteredTasks.filter(isOldTerminal).length,
  }), [dateFilteredTasks, showOldTerminal, isOldTerminal]);

  const tasksByStatusMap = useMemo(() => {
    const map = new Map<TaskStatus, KanbanTask[]>();
    for (const t of visibleTasks) {
      const col = boardColumnFor(t);
      let list = map.get(col);
      if (!list) { list = []; map.set(col, list); }
      list.push(t);
    }
    for (const list of map.values()) list.sort((a, b) => a.columnOrder - b.columnOrder);
    return map;
  }, [visibleTasks]);

  const tasksByStatus = useCallback((status: TaskStatus): KanbanTask[] => {
    return tasksByStatusMap.get(status) ?? [];
  }, [tasksByStatusMap]);

  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const t of visibleTasks) counts[t.status] = (counts[t.status] || 0) + 1;
    return counts;
  }, [visibleTasks]);

  return {
    tasks: visibleTasks,
    setTasks,
    total,
    loading,
    error,
    filters,
    setFilters,
    fetchTasks,
    createTask,
    updateTask,
    deleteTask,
    reorderTask,
    setTasksOptimistic,
    tasksByStatus,
    statusCounts,
    boardColumns,
    boardConfig,
    executeTask,
    approveTask,
    rejectTask,
    abortTask,
    resumeTask,
    showOldTerminal,
    setShowOldTerminal,
    oldTerminalHiddenCount,
  };
}
