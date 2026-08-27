/**
 * cc-kanban — Central Command Task Board adapter.
 *
 * Serves the same /api/kanban/* REST contract the Nerve kanban frontend
 * expects, but backed by Central Command's first-class Tasks (FastAPI on the
 * gateway URL) instead of Nerve's file-backed kanban store. Mounted in
 * place of ./kanban.js while the Hono server remains scaffolding.
 *
 * Semantics differences are surfaced honestly, never faked:
 * - Task state changes through runs and approvals, so free drag between
 *   columns is refused with an explanatory error (409).
 * - "Execute" = assign + run for a NEW task with an assignee.
 * - "Approve" lives in the Decisions Inbox, not the board.
 * - Board proposals (Nerve's agent-proposed card edits) don't exist in
 *   Central Command — the list is always empty.
 */
import { Hono } from 'hono';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { config } from '../lib/config.js';

const app = new Hono();

const gvBase = () => config.gatewayUrl.replace(/\/+$/, '');

async function cc(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${gvBase()}/api${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
  });
}

async function gvError(res: Response): Promise<string> {
  try {
    const body = await res.json() as { detail?: string };
    return body.detail || `Central Command HTTP ${res.status}`;
  } catch {
    return `Central Command HTTP ${res.status}`;
  }
}

/* ── Shape mapping ── */

interface CcTask {
  id: string;
  title: string;
  instructions: string;
  agent_id: string | null;
  status: string;
  session_id: string | null;
  // D7 phase 2: set when the orchestrator assigned this task as a sub-task —
  // it points at the orchestration session that owns it. Lets the board link
  // a project task (its own session_id) to the sub-tasks it fanned out.
  parent_session_id: string | null;
  // Resolved server-side (batched) from parent_session_id via the ledger's
  // work_item table — the email that spawned this task, when there is one.
  source_email?: { subject: string | null; from: string | null; text: string | null } | null;
  outcome: string | null;
  created_at: string;
  started_at: string | null;
  terminal_at: string | null;
  // Derived from the joined session row (repo._task_row) — never stored,
  // so it can't drift from the session's actual status.
  session_status: string | null;
  stopped: boolean;
}

function toMs(iso: string | null): number {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? t : Date.now();
}

function mapTask(t: CcTask) {
  return {
    id: t.id,
    title: t.title,
    description: t.instructions,
    status: t.status,
    priority: 'normal' as const,
    createdBy: 'operator' as const,
    createdAt: toMs(t.created_at),
    updatedAt: toMs(t.terminal_at ?? t.started_at ?? t.created_at),
    version: 1,
    assignee: t.agent_id ? (`agent:${t.agent_id}` as const) : undefined,
    sourceSessionKey: t.agent_id && t.session_id ? `agent:${t.agent_id}:${t.session_id}` : undefined,
    // D7 phase 2 delegation linkage — the board derives project ↔ sub-task
    // relationships from these (see useKanban's enrichDelegation).
    sessionId: t.session_id ?? undefined,
    parentSessionId: t.parent_session_id ?? undefined,
    sourceEmail: t.source_email ?? undefined,
    // Operator stop/resume (2026-08-02): whether the task's live run is
    // parked STOPPED — the task itself stays IN_PROGRESS (it did not finish),
    // so the board keeps it in its lane and only the badge changes.
    stopped: t.stopped === true,
    // "An agent is working on this RIGHT NOW". A local 27B takes 3-5 min a
    // turn, so from the board a mid-turn system looked identical to a hung one
    // (2026-08-26). `session_status` already rides the /api/tasks row (joined
    // in repo.list_tasks); only RUNNING is emitted — every other session status
    // is a lane waiting on someone, and TaskRunLink has no honest word for it.
    run: t.session_status === 'RUNNING' && t.agent_id && t.session_id
      ? {
          sessionKey: `agent:${t.agent_id}:${t.session_id}`,
          sessionId: t.session_id,
          startedAt: toMs(t.started_at),
          status: 'running' as const,
        }
      : undefined,
    labels: [] as string[],
    columnOrder: 0,
    feedback: [] as unknown[],
    result: t.outcome ?? undefined,
    resultAt: t.terminal_at ? toMs(t.terminal_at) : undefined,
  };
}

async function fetchGvTasks(agentId?: string): Promise<CcTask[]> {
  const res = await cc(`/tasks${agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''}`);
  if (!res.ok) throw new Error(await gvError(res));
  const data = await res.json() as { tasks: CcTask[] };
  return data.tasks;
}

/* ── Routes ── */

// Board columns mirror the Central Command task lifecycle (schema truth):
// NEW | ASSIGNED | IN_PROGRESS | REVIEW | DONE | FAILED | CANCELLED.
app.get('/api/kanban/config', rateLimitGeneral, (c) =>
  c.json({
    columns: [
      { key: 'NEW', title: 'Unassigned', visible: true },
      { key: 'ASSIGNED', title: 'Assigned', visible: true },
      { key: 'IN_PROGRESS', title: 'Running', visible: true },
      { key: 'REVIEW', title: 'Review', visible: true },
      { key: 'DONE', title: 'Done', visible: true },
      { key: 'FAILED', title: 'Failed', visible: true },
      { key: 'CANCELLED', title: 'Cancelled', visible: false },
    ],
    defaults: { status: 'NEW', priority: 'normal' },
    reviewRequired: true,
    allowDoneDragBypass: false,
    quickViewLimit: 5,
    proposalPolicy: 'confirm' as const,
  }));

app.put('/api/kanban/config', rateLimitGeneral, (c) =>
  c.json({ error: 'Board columns mirror the Central Command task lifecycle and are not editable here.' }, 501));

app.get('/api/kanban/tasks', rateLimitGeneral, async (c) => {
  try {
    // `assignee` is Nerve's `agent:<id>` form; Central Command filters in SQL on the
    // bare id, so it goes to the source rather than being sliced out here.
    const assignee = c.req.query('assignee') || '';
    let tasks = await fetchGvTasks(assignee.replace(/^agent:/, '') || undefined);
    const q = (c.req.query('q') || '').toLowerCase();
    if (q) {
      tasks = tasks.filter(t =>
        t.title.toLowerCase().includes(q) || t.instructions.toLowerCase().includes(q));
    }
    const limit = Math.max(1, Math.min(500, Number(c.req.query('limit')) || 200));
    const offset = Math.max(0, Number(c.req.query('offset')) || 0);
    const page = tasks.slice(offset, offset + limit);
    return c.json({
      items: page.map(mapTask),
      total: tasks.length,
      limit,
      offset,
      hasMore: offset + page.length < tasks.length,
    });
  } catch (e) {
    return c.json({ error: e instanceof Error ? e.message : String(e) }, 502);
  }
});

app.get('/api/kanban/tasks/:id', rateLimitGeneral, async (c) => {
  try {
    const task = (await fetchGvTasks()).find(t => t.id === c.req.param('id'));
    if (!task) return c.json({ error: 'task not found' }, 404);
    return c.json(mapTask(task));
  } catch (e) {
    return c.json({ error: e instanceof Error ? e.message : String(e) }, 502);
  }
});

app.post('/api/kanban/tasks', rateLimitGeneral, async (c) => {
  let body: { title?: string; description?: string; assignee?: string; attachments?: unknown };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: 'invalid JSON' }, 400);
  }
  const title = (body.title || '').trim();
  if (!title) return c.json({ error: 'title is required' }, 400);
  const agentId = body.assignee ? body.assignee.replace(/^agent:/, '') : null;

  // With an assignee this RUNS the task (Central Command semantics) — in the
  // background: the POST returns once the run is launched, the board shows
  // IN_PROGRESS, and the outcome (REVIEW / DONE / FAILED) lands on the task
  // record via the event stream. Unassigned tasks park for routing.
  const res = await cc('/tasks', {
    method: 'POST',
    body: JSON.stringify({
      title,
      instructions: (body.description || '').trim() || title,
      agent_id: agentId,
      background: true,
      ...(Array.isArray(body.attachments) && body.attachments.length > 0
        ? { attachments: body.attachments }
        : {}),
    }),
  });
  if (!res.ok) return c.json({ error: await gvError(res) }, res.status === 422 ? 400 : 502);
  const out = await res.json() as { task_id: string };
  try {
    const task = (await fetchGvTasks()).find(t => t.id === out.task_id);
    if (task) return c.json(mapTask(task), 201);
  } catch { /* fall through to minimal echo */ }
  return c.json({
    id: out.task_id, title, description: body.description || title, status: 'NEW',
    priority: 'normal', createdBy: 'operator', createdAt: Date.now(), updatedAt: Date.now(),
    version: 1, labels: [], columnOrder: 0, feedback: [],
  }, 201);
});

const IMMUTABLE_MSG =
  'Central Command task records are the operator\'s ask, verbatim — they change '
  + 'state through runs and approvals, not edits. Cancel and recreate instead.';

app.patch('/api/kanban/tasks/:id', rateLimitGeneral, async (c) => {
  let body: { status?: string };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: 'invalid JSON' }, 400);
  }
  if (body.status === 'CANCELLED') {
    const res = await cc(`/tasks/${encodeURIComponent(c.req.param('id'))}/cancel`, { method: 'POST' });
    if (!res.ok) return c.json({ error: await gvError(res) }, 409);
    const task = (await fetchGvTasks()).find(t => t.id === c.req.param('id'));
    return c.json(task ? mapTask(task) : { ok: true });
  }
  return c.json({ error: IMMUTABLE_MSG }, 400);
});

app.delete('/api/kanban/tasks/:id', rateLimitGeneral, async (c) => {
  const res = await cc(`/tasks/${encodeURIComponent(c.req.param('id'))}/cancel`, { method: 'POST' });
  if (!res.ok) return c.json({ error: await gvError(res) }, 409);
  return c.json({ ok: true });
});

app.post('/api/kanban/tasks/:id/reorder', rateLimitGeneral, async (c) => {
  let body: { targetStatus?: string };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ error: 'invalid JSON' }, 400);
  }
  const id = c.req.param('id');
  try {
    const task = (await fetchGvTasks()).find(t => t.id === id);
    if (!task) return c.json({ error: 'task not found' }, 404);
    if (body.targetStatus && body.targetStatus !== task.status) {
      if (body.targetStatus === 'CANCELLED') {
        const res = await cc(`/tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
        if (!res.ok) return c.json({ error: await gvError(res) }, 409);
        const after = (await fetchGvTasks()).find(t => t.id === id);
        return c.json(after ? mapTask(after) : { ok: true });
      }
      return c.json({ error: `Tasks move columns through runs and approvals, not drag (${task.status} stays put). Dragging to Cancelled works for unassigned tasks.` }, 409);
    }
    return c.json(mapTask(task)); // same-column reorder: display-only, no server order
  } catch (e) {
    return c.json({ error: e instanceof Error ? e.message : String(e) }, 502);
  }
});

app.post('/api/kanban/tasks/:id/execute', rateLimitGeneral, async (c) => {
  const id = c.req.param('id');
  try {
    const task = (await fetchGvTasks()).find(t => t.id === id);
    if (!task) return c.json({ error: 'task not found' }, 404);
    if (task.status !== 'NEW') return c.json({ error: `only NEW tasks can be executed (this one is ${task.status})` }, 409);
    if (!task.agent_id) {
      return c.json({ error: 'assign an agent first — unassigned tasks park for the orchestrator\'s recommendation' }, 409);
    }
    const res = await cc(`/tasks/${encodeURIComponent(id)}/assign`, {
      method: 'POST',
      body: JSON.stringify({ agent_id: task.agent_id }),
    });
    if (!res.ok) return c.json({ error: await gvError(res) }, 409);
    const after = (await fetchGvTasks()).find(t => t.id === id);
    return c.json(after ? mapTask(after) : { ok: true });
  } catch (e) {
    return c.json({ error: e instanceof Error ? e.message : String(e) }, 502);
  }
});

// Cooperative stop: pauses the task's live run (session -> STOPPED). The task
// stays IN_PROGRESS — it did not finish — so it stays in its lane; `stopped`
// on the wire row is what tells the board.
app.post('/api/kanban/tasks/:id/abort', rateLimitGeneral, async (c) => {
  const id = c.req.param('id');
  const res = await cc(`/tasks/${encodeURIComponent(id)}/stop`, { method: 'POST' });
  if (!res.ok) return c.json({ error: await gvError(res) }, 409);
  try {
    const task = (await fetchGvTasks()).find(t => t.id === id);
    return c.json(task ? mapTask(task) : { ok: true });
  } catch (e) {
    return c.json({ error: e instanceof Error ? e.message : String(e) }, 502);
  }
});

// Resume is a SESSION operation server-side, not a task one — the task record
// has no resume verb. Look the task's session_id up first, then proxy it.
app.post('/api/kanban/tasks/:id/resume', rateLimitGeneral, async (c) => {
  const id = c.req.param('id');
  try {
    const task = (await fetchGvTasks()).find(t => t.id === id);
    if (!task) return c.json({ error: 'task not found' }, 404);
    if (!task.session_id) return c.json({ error: `task ${id} has no session to resume` }, 409);
    const res = await cc(`/sessions/${encodeURIComponent(task.session_id)}/resume`, { method: 'POST' });
    if (!res.ok) return c.json({ error: await gvError(res) }, 409);
    const after = (await fetchGvTasks()).find(t => t.id === id);
    return c.json(after ? mapTask(after) : { ok: true });
  } catch (e) {
    return c.json({ error: e instanceof Error ? e.message : String(e) }, 502);
  }
});

app.post('/api/kanban/tasks/:id/approve', rateLimitGeneral, (c) =>
  c.json({ error: 'Approvals happen in the Decisions Inbox — the board never bypasses the gate.' }, 409));

/* Board proposals are a Nerve concept (agents proposing card edits);
 * Central Command agents propose world changes through the gateway instead. */
app.get('/api/kanban/proposals', rateLimitGeneral, (c) => c.json({ proposals: [] }));
app.post('/api/kanban/proposals', rateLimitGeneral, (c) =>
  c.json({ error: 'Central Command proposals flow through the approval gateway, not the board.' }, 501));
app.post('/api/kanban/proposals/:id/approve', rateLimitGeneral, (c) =>
  c.json({ error: 'Approvals happen in the Decisions Inbox.' }, 409));
app.post('/api/kanban/proposals/:id/reject', rateLimitGeneral, (c) =>
  c.json({ error: 'Rejections happen in the Decisions Inbox.' }, 409));

export default app;
