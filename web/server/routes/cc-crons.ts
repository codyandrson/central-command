/**
 * cc-crons — Central Command heartbeat adapter (D27).
 *
 * Serves the same /api/crons REST contract the Nerve crons frontend expects,
 * but backed by Central Command's heartbeat schedules (FastAPI on the gateway
 * URL) instead of the OpenClaw gateway's cron tool. Mounted in place of
 * ./crons.js while the Hono server remains scaffolding.
 *
 * Semantics, surfaced honestly:
 * - An "agentTurn" cron IS a scheduled assigned Task (heartbeat action
 *   `task.create`): message = the mission, agentId = the assignee. The
 *   heartbeat only schedules the work — any proposal the run makes still
 *   parks in the Decisions Inbox. Creating one requires an agent.
 * - Non-task actions (mail poll, drain window, agreement report) are authored
 *   and edited through the STRUCTURED path only: the dialog submits
 *   `{kind: 'heartbeatAction', actionKind, params}` against the action
 *   registry the API serves; a freeform payload edit on them is still
 *   refused, not faked. The registry stays the single source of what exists.
 * - The heartbeat ENGINE state rides every list response (`result.engine`) —
 *   an enabled schedule is dormant while the engine is stopped, and the tab
 *   must be able to say so. Start/stop proxies exist but last only until the
 *   next server restart; the durable switch is CC_HEARTBEAT_ENABLED.
 * - Run history is Central Command's event log (heartbeat.* events).
 */
import { Hono } from 'hono';
import type { Context } from 'hono';
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

interface CcSchedule {
  id: string;
  name: string;
  schedule_kind: 'cron' | 'every' | 'at';
  schedule: { expr?: string; tz?: string; every_seconds?: number; at?: string };
  action_kind: string;
  action_params: {
    agent_id?: string; instructions?: string; title?: string; minutes?: number;
    model?: string; thinking?: string;
  };
  enabled: boolean;
  created_by: string;
  last_fired_at: string | null;
  next_due: string | null;
  last_status: 'ok' | 'error' | null;
  last_error: string | null;
}

function toMs(iso: string | null | undefined): number | undefined {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? t : undefined;
}

function mapSchedule(s: CcSchedule) {
  const schedule =
    s.schedule_kind === 'cron'
      ? { kind: 'cron', expr: s.schedule.expr, tz: s.schedule.tz }
      : s.schedule_kind === 'every'
        ? { kind: 'every', everyMs: (s.schedule.every_seconds ?? 0) * 1000 }
        : { kind: 'at', at: s.schedule.at };
  const payload =
    s.action_kind === 'task.create'
      ? {
          kind: 'agentTurn',
          message: s.action_params.instructions ?? '',
          ...(s.action_params.model ? { model: s.action_params.model } : {}),
          ...(s.action_params.thinking ? { thinking: s.action_params.thinking } : {}),
        }
      : {
          kind: 'systemEvent',
          text: `[heartbeat action: ${s.action_kind}]`,
        };
  return {
    id: s.id,
    name: s.name,
    enabled: s.enabled,
    schedule,
    payload,
    agentId: s.action_params.agent_id,
    actionKind: s.action_kind,
    actionParams: s.action_params,
    state: {
      nextRunAtMs: s.enabled ? toMs(s.next_due) : undefined,
      lastRunAtMs: toMs(s.last_fired_at),
      lastStatus: s.last_status ?? undefined,
      lastError: s.last_error ?? undefined,
    },
  };
}

type NerveSchedule = { kind?: string; expr?: string; tz?: string; everyMs?: number; at?: string };

function mapscheduleIn(sched: NerveSchedule): { schedule_kind: string; schedule: Record<string, unknown> } | null {
  if (sched.kind === 'cron' && sched.expr) {
    const schedule: Record<string, unknown> = { expr: sched.expr };
    if (sched.tz) schedule.tz = sched.tz;
    return { schedule_kind: 'cron', schedule };
  }
  if (sched.kind === 'every' && sched.everyMs) {
    return { schedule_kind: 'every', schedule: { every_seconds: Math.round(sched.everyMs / 1000) } };
  }
  if (sched.kind === 'at' && sched.at) {
    return { schedule_kind: 'at', schedule: { at: sched.at } };
  }
  return null;
}

interface CcActionSpec {
  description: string;
  params: Record<string, string>;
  required: string[];
}

interface CcEngineStatus {
  running: boolean;
  reason: string;
  tick_seconds?: number;
  next_due?: Record<string, string>;
}

async function fetchSchedules(): Promise<{ schedules: CcSchedule[]; actions: Record<string, CcActionSpec> }> {
  const res = await cc('/heartbeat/schedules');
  if (!res.ok) throw new Error(await gvError(res));
  const data = await res.json() as { schedules: CcSchedule[]; actions?: Record<string, CcActionSpec> };
  return { schedules: data.schedules, actions: data.actions ?? {} };
}

async function fetchEngine(): Promise<CcEngineStatus> {
  const res = await cc('/heartbeat');
  if (!res.ok) throw new Error(await gvError(res));
  return await res.json() as CcEngineStatus;
}

const fail = (c: Context, error: unknown, status: 404 | 422 | 502 = 502) =>
  c.json({ ok: false, error: error instanceof Error ? error.message : String(error) }, status);

/* ── Routes ── */

// Taskable agents for the dialog's "assign to" picker — roster truth (D24).
app.get('/api/crons/agents', rateLimitGeneral, async (c) => {
  try {
    const res = await cc('/agents');
    if (!res.ok) throw new Error(await gvError(res));
    const data = await res.json() as { agents: { id: string; name: string; taskable: boolean; status: string }[] };
    const agents = data.agents
      .filter(a => a.taskable && a.status === 'ACTIVE')
      .map(a => ({ id: a.id, name: a.name }));
    return c.json({ ok: true, result: { agents } });
  } catch (err) {
    return fail(c, err);
  }
});

app.get('/api/crons', rateLimitGeneral, async (c) => {
  try {
    const [{ schedules, actions }, engine] = await Promise.all([fetchSchedules(), fetchEngine()]);
    return c.json({
      ok: true,
      result: {
        jobs: schedules.map(mapSchedule),
        engine: { running: engine.running, reason: engine.reason, tickSeconds: engine.tick_seconds },
        actions,
      },
    });
  } catch (err) {
    return fail(c, err);
  }
});

// The heartbeat engine itself: an enabled schedule is dormant while the
// engine is stopped. Start/stop last until the next server restart — the
// durable switch is CC_HEARTBEAT_ENABLED in the Central Command .env.
app.post('/api/crons/engine', rateLimitGeneral, async (c) => {
  try {
    const { running } = await c.req.json() as { running: boolean };
    const res = await cc(`/heartbeat/${running ? 'start' : 'stop'}`, { method: 'POST' });
    if (!res.ok) throw new Error(await gvError(res));
    const status = await res.json() as CcEngineStatus;
    return c.json({ ok: true, result: { running: status.running, reason: status.reason } });
  } catch (err) {
    return fail(c, err);
  }
});

/** Keep only usable param values; blank optional fields never reach the API. */
function cleanActionParams(params: Record<string, unknown> | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v === undefined || v === null) continue;
    const s = String(v).trim();
    if (!s) continue;
    out[k] = /^\d+(\.\d+)?$/.test(s) ? Number(s) : s;
  }
  return out;
}

async function createSchedule(body: Record<string, unknown>, enabled: unknown): Promise<CcSchedule> {
  const res = await cc('/heartbeat/schedules', { method: 'POST', body: JSON.stringify(body) });
  if (!res.ok) throw new Error(await gvError(res));
  let row = await res.json() as CcSchedule;
  // Schedules are born disabled server-side; the dialog's "create" means
  // "create and start", so honor its enabled flag with an explicit,
  // event-recorded toggle rather than diverging from the vendored UX.
  if (enabled) {
    const t = await cc(`/heartbeat/schedules/${encodeURIComponent(row.id)}/toggle`, {
      method: 'POST',
      body: JSON.stringify({ enabled: true }),
    });
    if (t.ok) row = await t.json() as CcSchedule;
  }
  return row;
}

app.post('/api/crons', rateLimitGeneral, async (c) => {
  try {
    const { job } = await c.req.json() as { job: Record<string, unknown> };
    const payload = (job.payload ?? {}) as {
      kind?: string; message?: string; text?: string;
      actionKind?: string; params?: Record<string, unknown>;
      model?: string; thinking?: string;
    };

    // Structured authoring of the built-in heartbeat actions (mail poll,
    // drain window, reports) — validated against the action registry by the
    // Central Command API, so a schedule can't be born broken.
    if (payload.kind === 'heartbeatAction') {
      const actionKind = String(payload.actionKind ?? '').trim();
      if (!actionKind) return fail(c, 'Pick a heartbeat action.', 422);
      if (actionKind === 'task.create') {
        return fail(c, 'Scheduled agent tasks are created via the "Agent task" execution type, not as a raw heartbeat action.', 422);
      }
      const mapped = mapscheduleInOrThrow(job.schedule as NerveSchedule);
      const row = await createSchedule({
        name: String(job.name ?? '').trim() || actionKind,
        ...mapped,
        action_kind: actionKind,
        action_params: cleanActionParams(payload.params),
      }, job.enabled);
      return c.json({ ok: true, result: mapSchedule(row) });
    }

    if (payload.kind !== 'agentTurn') {
      return fail(c, 'Only scheduled agent tasks and the built-in heartbeat actions can be created here.', 422);
    }
    const agentId = String(job.agentId ?? '').trim();
    if (!agentId) return fail(c, 'Pick an agent to assign the scheduled task to.', 422);
    const message = String(payload.message ?? '').trim();
    if (!message) return fail(c, 'The task needs instructions.', 422);
    const mapped = mapscheduleInOrThrow(job.schedule as NerveSchedule);

    const model = String(payload.model ?? '').trim();
    const thinking = String(payload.thinking ?? '').trim();

    const row = await createSchedule({
      name: String(job.name ?? '').trim() || message.slice(0, 60),
      ...mapped,
      action_kind: 'task.create',
      action_params: {
        agent_id: agentId, instructions: message, title: String(job.name ?? '').trim() || undefined,
        ...(model ? { model } : {}),
        ...(thinking ? { thinking } : {}),
      },
    }, job.enabled);
    return c.json({ ok: true, result: mapSchedule(row) });
  } catch (err) {
    return fail(c, err);
  }
});

function mapscheduleInOrThrow(sched: NerveSchedule | undefined): { schedule_kind: string; schedule: Record<string, unknown> } {
  const mapped = sched ? mapscheduleIn(sched) : null;
  if (!mapped) throw new Error('Unrecognized schedule — use cron, every, or at.');
  return mapped;
}

app.patch('/api/crons/:id', rateLimitGeneral, async (c) => {
  try {
    const id = c.req.param('id');
    const { patch } = await c.req.json() as { patch: Record<string, unknown> };
    const { schedules } = await fetchSchedules();
    const existing = schedules.find(s => s.id === id);
    if (!existing) return fail(c, 'No such schedule.', 404);

    const fields: Record<string, unknown> = {};
    if (typeof patch.name === 'string' && patch.name.trim()) fields.name = patch.name.trim();
    if (patch.schedule) Object.assign(fields, mapscheduleInOrThrow(patch.schedule as NerveSchedule));

    const payload = patch.payload as {
      kind?: string; message?: string; actionKind?: string; params?: Record<string, unknown>;
      model?: string; thinking?: string;
    } | undefined;
    const agentId = typeof patch.agentId === 'string' ? patch.agentId.trim() : undefined;
    if (payload?.kind === 'heartbeatAction') {
      // Structured edit of a built-in action's params (registry-validated by
      // the Central Command API). A task.create schedule stays on the agent-task
      // form; a built-in can't be morphed into one here either.
      const actionKind = String(payload.actionKind ?? '').trim() || existing.action_kind;
      if (existing.action_kind === 'task.create' || actionKind === 'task.create') {
        return fail(c, `"${existing.name}" is a scheduled agent task — edit it via the agent-task form.`, 422);
      }
      fields.action_kind = actionKind;
      fields.action_params = cleanActionParams(payload.params);
    } else if (payload?.message !== undefined || agentId !== undefined) {
      if (existing.action_kind !== 'task.create') {
        // The form can rename/retime/toggle a built-in action, but a freeform
        // payload edit on one is refused, not faked — structured param edits
        // go through the heartbeatAction path above.
        return fail(c, `"${existing.name}" is a built-in heartbeat action (${existing.action_kind}); edit its parameters via the action form, not free text.`, 422);
      }
      fields.action_params = {
        ...existing.action_params,
        ...(payload?.message !== undefined ? { instructions: String(payload.message) } : {}),
        ...(agentId ? { agent_id: agentId } : {}),
        // Empty string clears an override — same "blank means default" rule
        // the create path uses, so unsetting a model/effort on edit sticks.
        ...(payload?.model !== undefined ? { model: payload.model || undefined } : {}),
        ...(payload?.thinking !== undefined ? { thinking: payload.thinking || undefined } : {}),
      };
    }

    const res = await cc(`/heartbeat/schedules/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(fields),
    });
    if (!res.ok) throw new Error(await gvError(res));
    return c.json({ ok: true, result: mapSchedule(await res.json() as CcSchedule) });
  } catch (err) {
    return fail(c, err);
  }
});

app.delete('/api/crons/:id', rateLimitGeneral, async (c) => {
  try {
    const res = await cc(`/heartbeat/schedules/${encodeURIComponent(c.req.param('id'))}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(await gvError(res));
    return c.json({ ok: true });
  } catch (err) {
    return fail(c, err);
  }
});

app.post('/api/crons/:id/toggle', rateLimitGeneral, async (c) => {
  try {
    const { enabled } = await c.req.json() as { enabled: boolean };
    const res = await cc(`/heartbeat/schedules/${encodeURIComponent(c.req.param('id'))}/toggle`, {
      method: 'POST',
      body: JSON.stringify({ enabled: Boolean(enabled) }),
    });
    if (!res.ok) throw new Error(await gvError(res));
    return c.json({ ok: true, result: mapSchedule(await res.json() as CcSchedule) });
  } catch (err) {
    return fail(c, err);
  }
});

app.post('/api/crons/:id/run', rateLimitGeneral, async (c) => {
  try {
    const res = await cc(`/heartbeat/schedules/${encodeURIComponent(c.req.param('id'))}/run`, { method: 'POST' });
    if (!res.ok) throw new Error(await gvError(res));
    const outcome = await res.json() as { ok: boolean; error?: string };
    if (!outcome.ok) return fail(c, outcome.error ?? 'The action errored — see its run history.', 502);
    return c.json({ ok: true, result: outcome });
  } catch (err) {
    return fail(c, err);
  }
});

app.get('/api/crons/:id/runs', rateLimitGeneral, async (c) => {
  try {
    const res = await cc(`/heartbeat/schedules/${encodeURIComponent(c.req.param('id'))}/runs`);
    if (!res.ok) throw new Error(await gvError(res));
    const data = await res.json() as { events: { kind: string; created_at: string; payload: Record<string, unknown> }[] };
    // Completed/errored firings are the "runs"; fired/report/window events
    // remain visible in the audit-trail browser.
    const entries = data.events
      .filter(e => e.kind === 'heartbeat.completed' || e.kind === 'heartbeat.error')
      .map(e => ({
        timestamp: e.created_at,
        status: e.kind === 'heartbeat.error' ? 'error' : 'ok',
        error: e.kind === 'heartbeat.error' ? String(e.payload.error ?? '') : undefined,
        summary: e.kind === 'heartbeat.completed'
          ? `${String(e.payload.trigger ?? 'schedule')}: ${JSON.stringify(e.payload.result ?? {})}`
          : undefined,
      }));
    return c.json({ ok: true, result: { entries } });
  } catch (err) {
    return fail(c, err);
  }
});

export default app;
