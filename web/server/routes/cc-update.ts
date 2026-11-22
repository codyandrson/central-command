/**
 * cc-update — staged one-click apply for Central Command updates.
 *
 * The cockpit NEVER applies an update itself (the setup-update contract; and
 * the research consensus — Portainer BE, Home Assistant, PackageKit all hand
 * off to an external privileged helper). This route's whole privileged act is
 * writing files under /run/cc-update:
 *
 *   stage-trigger  (v2.19.0) written the moment a version check sees a newer
 *                  release — cc-update-stage.path starts the root one-shot
 *                  stager, which prebuilds every changed image cache while
 *                  the system stays up and writes stage.json. The UI keeps
 *                  "Apply update now" hidden until stage.json says the
 *                  target is staged.
 *   trigger        the apply — cc-update.path starts the root one-shot
 *                  cc-update.service, whose script does prebuild (now a
 *                  cache fast-forward) → backup → stop → merge → schema →
 *                  rebuild → restart → health-check → auto-rollback, writing
 *                  status.json atomically at each phase. The UI polls
 *                  GET /api/update/status — including straight through its
 *                  own restart, since both records outlive every process
 *                  involved (the Portainer schedule-record pattern).
 *
 * The same trigger seam carries a RESTART of the API process (Settings >
 * Connection): the trigger gains `"action": "restart"`, the root updater
 * restarts cc-uvicorn only and records the outcome in restart.json — its own
 * file, because status.json is the last UPDATE's result. The `/api/restart/*`
 * routes below proxy the same API hold (kind restart) and read that file; they
 * are served by this Node process, which stays up while the API restarts.
 *
 * Double-click guard is SERVER-SIDE (research: a UI-disabled button is not a
 * guard): apply refuses while a trigger is pending, while a run is in
 * flight, and for a target that equals the installed version (the stale-
 * button re-run trap).
 */
import { Hono, type Context } from 'hono';
import { readFileSync, writeFileSync, existsSync, renameSync, statSync } from 'node:fs';
import { dirname } from 'node:path';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { readProductVersion } from '../lib/release-source.js';
import { config } from '../lib/config.js';

/** The API tier's base URL, without a trailing slash. */
const gatewayBase = () => config.gatewayUrl.replace(/\/+$/, '');

// The update hold lives in the API tier (it owns the loops and the run gate);
// these routes only proxy it so the cockpit reaches it same-origin.
async function holdProxy(method: string, sub = '', body?: unknown): Promise<Response> {
  return fetch(`${gatewayBase()}/api/update/hold${sub}`, {
    method,
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

// Env-overridable for tests only; production uses the tmpfiles.conf paths.
const TRIGGER = process.env.CC_UPDATE_TRIGGER || '/run/cc-update/trigger';
const STATUS = process.env.CC_UPDATE_STATUS || '/var/lib/cc-update/status.json';
const STAGE_TRIGGER = process.env.CC_UPDATE_STAGE_TRIGGER || '/run/cc-update/stage-trigger';
const STAGE_STATUS = process.env.CC_UPDATE_STAGE_STATUS || '/var/lib/cc-update/stage.json';
const RESTART_STATUS = process.env.CC_UPDATE_RESTART_STATUS || '/var/lib/cc-update/restart.json';

interface UpdateStatus {
  state: 'running' | 'success' | 'failed' | 'rolled_back';
  phase: string;
  current?: string;
  target?: string;
  started_at?: string;
  updated_at?: string;
  finished_at?: string;
  error?: string;
}

function readStatusFile(path: string): UpdateStatus | null {
  try {
    return JSON.parse(readFileSync(path, 'utf-8')) as UpdateStatus;
  } catch {
    return null;
  }
}

// A run is "in flight" if the status says running AND is fresh enough that
// the updater plausibly still lives (its unit timeout is 5400s; a stale
// running record past that is a crash we must not let brick the button).
const STALE_RUNNING_MS = 5700 * 1000;

function inFlight(status: UpdateStatus | null): boolean {
  if (!status || status.state !== 'running') return false;
  const seen = Date.parse(status.updated_at ?? status.started_at ?? '');
  return Number.isFinite(seen) && Date.now() - seen < STALE_RUNNING_MS;
}

/** restart.json, as cc-update.sh's restart_main writes it; `none` = never restarted. */
interface RestartStatus {
  state: 'none' | 'running' | 'restarted' | 'failed' | 'refused';
  message?: string;
  requested_at?: string;
  started_at?: string;
  updated_at?: string;
  finished_at?: string;
  forced?: boolean;
}

function readRestartStatus(): RestartStatus {
  try {
    return JSON.parse(readFileSync(RESTART_STATUS, 'utf-8')) as RestartStatus;
  } catch {
    return { state: 'none' };
  }
}

function restartInFlight(status: RestartStatus): boolean {
  if (status.state !== 'running') return false;
  const seen = Date.parse(status.updated_at ?? status.started_at ?? '');
  return Number.isFinite(seen) && Date.now() - seen < STALE_RUNNING_MS;
}

/** What the pending trigger asks for: null = no trigger. */
function triggerAction(): 'update' | 'restart' | null {
  if (!existsSync(TRIGGER)) return null;
  try {
    return JSON.parse(readFileSync(TRIGGER, 'utf-8')).action === 'restart' ? 'restart' : 'update';
  } catch {
    return 'update';
  }
}

/**
 * Whether a restart can be served here at all. The single-node profile
 * (CC_UPDATE_BACKEND=api) has no root updater, and a k3s install whose tmpfiles
 * directory is missing has not installed the units; both say so plainly.
 */
function restartSeam(): { available: boolean; reason?: string } {
  if (config.updateBackend === 'api') {
    return { available: false, reason: 'this install has no root updater unit to restart the API (single-node profile)' };
  }
  if (!existsSync(dirname(TRIGGER))) {
    return {
      available: false,
      reason: `the updater is not installed (${dirname(TRIGGER)} is missing) — see deploy/k3s/README.md §6`,
    };
  }
  return { available: true };
}

// A stage trigger nothing consumed (units not installed / not enabled) must
// not hide the apply button forever — past this age it reads as dead and the
// UI falls back to the ungated button.
const STALE_STAGE_TRIGGER_MS = 15 * 60 * 1000;

function stagePending(): boolean {
  try {
    return Date.now() - statSync(STAGE_TRIGGER).mtimeMs < STALE_STAGE_TRIGGER_MS;
  } catch {
    return false;
  }
}

function writeTrigger(path: string, target: string, force = false): void {
  // Atomic write (tmp + rename) so the path unit never sees a half-written
  // trigger. The dir is tmpfs (cleared on boot) and writable by the checkout owner —
  // see deploy/k3s/cc-update-tmpfiles.conf.in (rendered to /etc/tmpfiles.d/cc-update.conf).
  const tmp = `${path}.tmp`;
  // `force` is the operator's "update anyway": the updater refuses to stop the
  // API while agent runs are in flight (it would kill them) unless this is set.
  writeFileSync(tmp, JSON.stringify({ target, force, requested_at: new Date().toISOString() }));
  renameSync(tmp, path);
}

/**
 * Ask the root stager to prebuild for `target`. Idempotent and quiet — the
 * version-check route calls this on every check that sees an update, so it
 * no-ops when the target is already staged, staging, or requested.
 */
export function requestStage(target: string): 'requested' | 'already' | 'error' {
  const stage = readStatusFile(STAGE_STATUS);
  if (stagePending() || inFlight(stage)) return 'already';
  if (stage?.state === 'success' && stage.target === target) return 'already';
  try {
    writeTrigger(STAGE_TRIGGER, target);
    return 'requested';
  } catch {
    return 'error';
  }
}

const app = new Hono();

app.get('/api/update/status', rateLimitGeneral, (c) => {
  const status = readStatusFile(STATUS);
  const stage = readStatusFile(STAGE_STATUS);
  return c.json({
    mode: 'systemd',
    pending: existsSync(TRIGGER),
    inFlight: inFlight(status),
    status,
    stage: {
      pending: stagePending(),
      inFlight: inFlight(stage),
      status: stage,
    },
  });
});

app.post('/api/update/stage', rateLimitGeneral, async (c) => {
  const body = await c.req.json().catch(() => ({})) as { target?: string; force?: boolean };
  const result = requestStage(body.target ?? '');
  if (result === 'error') {
    return c.json({
      error: `could not write ${STAGE_TRIGGER} — is cc-update-stage installed? `
        + '(deploy/k3s/cc-update-tmpfiles.conf.in + cc-update-stage.path; see deploy/k3s/README.md §6)',
    }, 503);
  }
  return c.json({ staging: true, alreadyStaged: result === 'already' }, result === 'requested' ? 202 : 200);
});

// Update-from-file is the single-node profile's path (the FastAPI app owns
// it there — central_command/api/update.py); this server's updater is
// git-based, pulling the release from the repo's origin. A real route so the
// cockpit gets a truthful error instead of a silent 404 (and so the
// api-route-parity guard sees the seam).
app.post('/api/update/upload', rateLimitGeneral, (c) =>
  c.json({
    error: 'this install updates from its git origin — use Check for updates; '
      + 'Update from file exists for the single-node (podman) profile',
  }, 501));

app.post('/api/update/apply', rateLimitGeneral, async (c) => {
  const status = readStatusFile(STATUS);
  if (existsSync(TRIGGER)) {
    return c.json({ error: 'an update trigger is already pending' }, 409);
  }
  if (inFlight(status)) {
    return c.json({ error: `an update is already running (phase: ${status?.phase})` }, 409);
  }
  if (restartInFlight(readRestartStatus())) {
    return c.json({ error: 'a restart of the API is running — apply once it has finished' }, 409);
  }
  const body = await c.req.json().catch(() => ({})) as { target?: string; force?: boolean };
  // The stale-button trap: after a successful update the UI's version info
  // lags until its next check — a re-click must not re-run the whole
  // pipeline against the version that is already installed.
  if (body.target && body.target === readProductVersion()) {
    return c.json({ error: `v${body.target} is already installed` }, 409);
  }
  // Unforced: engage the hold instead of writing the trigger. The API pauses
  // the team, waits for the agents to land and writes the trigger itself;
  // "Update anyway"/"Update now" is the forced path, which writes it here.
  if (body.force !== true) {
    try {
      const res = await holdProxy('POST', '', { target: body.target ?? '' });
      if (res.status === 409) {
        // The other kind of hold (a restart) stands; its own detail says so.
        const refusal = await res.json().catch(() => ({})) as { detail?: string };
        return c.json({ error: refusal.detail ?? 'a restart hold is engaged' }, 409);
      }
      if (!res.ok) return c.json({ error: `could not engage the update hold (HTTP ${res.status})` }, 502);
      return c.json({ triggered: false, held: true }, 202);
    } catch (err) {
      return c.json({ error: `could not reach the API to engage the update hold [${err instanceof Error ? err.message : String(err)}]` }, 502);
    }
  }
  try {
    writeTrigger(TRIGGER, body.target ?? '', true);
  } catch (err) {
    return c.json({
      error: `could not write ${TRIGGER} — is cc-update installed? `
        + '(deploy/k3s/cc-update-tmpfiles.conf.in + cc-update.path; see deploy/k3s/README.md §6) '
        + `[${err instanceof Error ? err.message : String(err)}]`,
    }, 503);
  }
  return c.json({ triggered: true }, 202);
});

// Literal registrations, not a table: `api-route-parity.test.ts` reads
// `app.<method>('/api/...')` from source, and a loop hid these three from it.
function holdRoute(method: string, sub = '') {
  return async (c: Context) => {
    try {
      const res = await holdProxy(method, sub);
      return c.json(await res.json().catch(() => ({})), res.status as 200);
    } catch (err) {
      return c.json({ error: err instanceof Error ? err.message : String(err) }, 502);
    }
  };
}
app.get('/api/update/hold', rateLimitGeneral, holdRoute('GET'));
app.post('/api/update/hold/now', rateLimitGeneral, holdRoute('POST', '/now'));
app.post('/api/update/hold/cancel', rateLimitGeneral, holdRoute('DELETE'));

// ── restart the API process (Settings > Connection) ──────────────────────────
// The hold is the API's, kind "restart" (the API refuses the other kind's
// engage, force and cancel with a 409). Everything the card reads that must
// survive the API being down comes from restart.json, served from here.

app.get('/api/restart/status', rateLimitGeneral, (c) => {
  const status = readRestartStatus();
  const update = readStatusFile(STATUS);
  const action = triggerAction();
  return c.json({
    ...status,
    ...restartSeam(),
    inFlight: restartInFlight(status),
    pending: action === 'restart',
    // An update run or trigger is what a restart must not start beside.
    updateBusy: inFlight(update) || action === 'update',
  });
});

app.post('/api/restart/hold', rateLimitGeneral, async (c) => {
  const seam = restartSeam();
  if (!seam.available) return c.json({ error: seam.reason }, 501);
  if (inFlight(readStatusFile(STATUS)) || triggerAction() === 'update') {
    return c.json({ error: 'an update is running or pending — restart after it has finished' }, 409);
  }
  if (restartInFlight(readRestartStatus()) || triggerAction() === 'restart') {
    return c.json({ error: 'a restart is already running' }, 409);
  }
  try {
    const res = await holdProxy('POST', '', { kind: 'restart', target: '' });
    if (res.status === 409 || res.status === 501) {
      const refusal = await res.json().catch(() => ({})) as { detail?: string };
      return c.json({ error: refusal.detail ?? `the API refused the restart hold (HTTP ${res.status})` }, res.status);
    }
    if (!res.ok) return c.json({ error: `could not engage the restart hold (HTTP ${res.status})` }, 502);
    return c.json({ held: true }, 202);
  } catch (err) {
    return c.json({ error: `could not reach the API to engage the restart hold [${err instanceof Error ? err.message : String(err)}]` }, 502);
  }
});
app.get('/api/restart/hold', rateLimitGeneral, holdRoute('GET'));
app.post('/api/restart/hold/now', rateLimitGeneral, holdRoute('POST', '/now?kind=restart'));
app.post('/api/restart/hold/cancel', rateLimitGeneral, holdRoute('DELETE', '?kind=restart'));

export default app;
