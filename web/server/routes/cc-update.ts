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
 * Double-click guard is SERVER-SIDE (research: a UI-disabled button is not a
 * guard): apply refuses while a trigger is pending, while a run is in
 * flight, and for a target that equals the installed version (the stale-
 * button re-run trap).
 */
import { Hono } from 'hono';
import { readFileSync, writeFileSync, existsSync, renameSync, statSync } from 'node:fs';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { readProductVersion } from '../lib/release-source.js';

// Env-overridable for tests only; production uses the tmpfiles.conf paths.
const TRIGGER = process.env.CC_UPDATE_TRIGGER || '/run/cc-update/trigger';
const STATUS = process.env.CC_UPDATE_STATUS || '/var/lib/cc-update/status.json';
const STAGE_TRIGGER = process.env.CC_UPDATE_STAGE_TRIGGER || '/run/cc-update/stage-trigger';
const STAGE_STATUS = process.env.CC_UPDATE_STAGE_STATUS || '/var/lib/cc-update/stage.json';

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
  // trigger. The dir is tmpfs (cleared on boot) and codyslab-writable —
  // see deploy/k3s/cc-update-tmpfiles.conf.
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
        + '(deploy/k3s/cc-update-tmpfiles.conf + cc-update-stage.path; see deploy/k3s/README.md §6)',
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
  const body = await c.req.json().catch(() => ({})) as { target?: string; force?: boolean };
  // The stale-button trap: after a successful update the UI's version info
  // lags until its next check — a re-click must not re-run the whole
  // pipeline against the version that is already installed.
  if (body.target && body.target === readProductVersion()) {
    return c.json({ error: `v${body.target} is already installed` }, 409);
  }
  try {
    writeTrigger(TRIGGER, body.target ?? '', body.force === true);
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
