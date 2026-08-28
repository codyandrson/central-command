/**
 * cc-update — one-click apply for Central Command updates (2026-08-28).
 *
 * The cockpit NEVER applies an update itself (the setup-update contract; and
 * the research consensus — Portainer BE, Home Assistant, PackageKit all hand
 * off to an external privileged helper). This route's whole privileged act is
 * writing /run/cc-update/trigger; systemd's cc-update.path starts the root
 * one-shot cc-update.service, whose script does stop → backup → merge →
 * schema → rebuild → restart → health-check → auto-rollback, writing
 * /var/lib/cc-update/status.json atomically at each phase. The UI polls
 * GET /api/update/status — including straight through its own restart, since
 * the status record outlives every process involved (the Portainer
 * schedule-record pattern).
 *
 * Double-click guard is SERVER-SIDE (research: a UI-disabled button is not a
 * guard): apply refuses while a trigger is pending or a run is in flight.
 */
import { Hono } from 'hono';
import { readFileSync, writeFileSync, existsSync, renameSync } from 'node:fs';
import { rateLimitGeneral } from '../middleware/rate-limit.js';

// Env-overridable for tests only; production uses the tmpfiles.conf paths.
const TRIGGER = process.env.CC_UPDATE_TRIGGER || '/run/cc-update/trigger';
const STATUS = process.env.CC_UPDATE_STATUS || '/var/lib/cc-update/status.json';

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

function readStatus(): UpdateStatus | null {
  try {
    return JSON.parse(readFileSync(STATUS, 'utf-8')) as UpdateStatus;
  } catch {
    return null;
  }
}

// A run is "in flight" if the status says running AND is fresh enough that
// the updater plausibly still lives (its unit timeout is 1800s; a stale
// running record past that is a crash we must not let brick the button).
const STALE_RUNNING_MS = 1900 * 1000;

function inFlight(status: UpdateStatus | null): boolean {
  if (!status || status.state !== 'running') return false;
  const seen = Date.parse(status.updated_at ?? status.started_at ?? '');
  return Number.isFinite(seen) && Date.now() - seen < STALE_RUNNING_MS;
}

const app = new Hono();

app.get('/api/update/status', rateLimitGeneral, (c) => {
  const status = readStatus();
  return c.json({
    pending: existsSync(TRIGGER),
    inFlight: inFlight(status),
    status,
  });
});

app.post('/api/update/apply', rateLimitGeneral, async (c) => {
  const status = readStatus();
  if (existsSync(TRIGGER)) {
    return c.json({ error: 'an update trigger is already pending' }, 409);
  }
  if (inFlight(status)) {
    return c.json({ error: `an update is already running (phase: ${status?.phase})` }, 409);
  }
  const body = await c.req.json().catch(() => ({})) as { target?: string };
  try {
    // Atomic write (tmp + rename) so the path unit never sees a half-written
    // trigger. The dir is tmpfs (cleared on boot) and codyslab-writable —
    // see deploy/k3s/cc-update-tmpfiles.conf.
    const tmp = `${TRIGGER}.tmp`;
    writeFileSync(tmp, JSON.stringify({
      target: body.target ?? '',
      requested_at: new Date().toISOString(),
    }));
    renameSync(tmp, TRIGGER);
  } catch (err) {
    return c.json({
      error: `could not write ${TRIGGER} — is cc-update installed? `
        + '(deploy/k3s/cc-update-tmpfiles.conf + cc-update.path; see deploy/k3s/README.md §6) '
        + `[${err instanceof Error ? err.message : String(err)}]`,
    }, 503);
  }
  return c.json({ triggered: true }, 202);
});

export default app;
