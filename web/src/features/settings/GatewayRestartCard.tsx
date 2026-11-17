import { useCallback, useEffect, useRef, useState } from 'react';
import { RotateCw } from 'lucide-react';
import { ConfirmDialog } from '@/components/ConfirmDialog';

/** GET /api/restart/status — restart.json (or `none`) plus what the server knows. */
interface RestartInfo {
  state: 'none' | 'running' | 'restarted' | 'failed' | 'refused';
  message?: string;
  requested_at?: string;
  finished_at?: string;
  forced?: boolean;
  /** False where no root updater serves a restart (single-node, units not installed). */
  available: boolean;
  reason?: string;
  /** An update run or trigger is what a restart must not start beside. */
  updateBusy: boolean;
}

/** GET /api/restart/hold — the API's hold; `kind` says whose it is. */
interface HoldStatus {
  active: boolean;
  kind?: 'update' | 'restart' | null;
  running: Array<{
    id: string; agent_id: string; mode: string; stop_requested: boolean;
    last_step_age_s: number; task_id: string | null; dispatch: boolean;
  }>;
  triggered_at: string | null;
  trigger_error: string | null;
}

type Phase = 'idle' | 'holding' | 'restarting';

const POLL_MS = 3000;
// The updater's health poll is two minutes and the API's stop may take up to two
// more (TimeoutStopSec): past this the card stops waiting and says where to look.
const RESTART_TIMEOUT_MS = 6 * 60 * 1000;

function age(s: number): string {
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

const UNAVAILABLE_PLAIN = 'Restart is not available here.';

/**
 * Settings > Connection: restart the API process (cc-uvicorn) through the root
 * updater. Engages the API's restart hold (the team pauses as for an update),
 * waits for the agents to land, and shows the outcome the updater records in
 * restart.json. The cockpit's own Node server serves that record and stays up
 * while the API is down, so the card polls through the restart.
 */
export function GatewayRestartCard() {
  const [info, setInfo] = useState<RestartInfo | null>(null);
  const [infoError, setInfoError] = useState<string | null>(null);
  const [hold, setHold] = useState<HoldStatus | null>(null);
  const [phase, setPhaseState] = useState<Phase>('idle');
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const phaseRef = useRef<Phase>('idle');
  // restart.json's requested_at when this card engaged: the record outlives every
  // restart, so only a record that differs from it is THIS restart's outcome.
  const baseline = useRef<string | undefined>(undefined);
  const restartingSince = useRef(0);
  // The trigger stamp already acted on: the API keeps `triggered_at` while a hold
  // stands, so a refused restart must not read as a fresh trigger.
  const seenTrigger = useRef<string | null>(null);
  const infoRef = useRef<RestartInfo | null>(null);

  const setPhase = useCallback((next: Phase) => {
    if (next === 'restarting' && phaseRef.current !== 'restarting') restartingSince.current = Date.now();
    phaseRef.current = next;
    setPhaseState(next);
  }, []);

  const tick = useCallback(async () => {
    let next: RestartInfo | null = null;
    try {
      const res = await fetch('/api/restart/status');
      if (res.ok) {
        next = await res.json() as RestartInfo;
        infoRef.current = next;
        setInfo(next);
        setInfoError(null);
      } else {
        setInfoError(`HTTP ${res.status}`);
      }
    } catch (err) {
      setInfoError(err instanceof Error ? err.message : String(err));
    }

    let current: HoldStatus | null = null;
    let holdDown = false;
    try {
      const res = await fetch('/api/restart/hold');
      if (res.ok) current = await res.json() as HoldStatus;
      else holdDown = true;
    } catch {
      holdDown = true; // the API is down: during a restart, that is expected
    }
    setHold(current);

    const p = phaseRef.current;
    const ownHold = !!current?.active && current.kind === 'restart';
    if (p === 'idle' && ownHold) {
      // A restart hold this card did not start in this page load (a reload, or
      // another tab): show its wait panel, or the team stays paused with no
      // Cancel in sight.
      baseline.current = next?.requested_at;
      seenTrigger.current = current?.triggered_at ?? null;
      setPhase('holding');
      return;
    }
    if (p === 'idle' || !next) return;
    const changed = !!next.requested_at && next.requested_at !== baseline.current;
    if (changed && (next.state === 'restarted' || next.state === 'failed' || next.state === 'refused')) {
      if (next.state === 'restarted' || !ownHold) {
        setNotice(null);
        setPhase('idle');
        return;
      }
      // Refused or failed with the hold still standing (a late run slipped in:
      // the API re-triggers on its own): keep waiting and say why it did not go.
      baseline.current = next.requested_at;
      seenTrigger.current = current?.triggered_at ?? null;
      setNotice(next.message ?? 'The restart did not go ahead.');
      setPhase('holding');
      return;
    }
    if (changed && next.state === 'running') {
      setPhase('restarting');
      return;
    }
    if (p === 'holding') {
      if (ownHold && current?.triggered_at && current.triggered_at !== seenTrigger.current) setPhase('restarting');
      else if (holdDown) setPhase('restarting');
      else if (current && !current.active) setPhase('idle'); // cancelled elsewhere
    } else if (Date.now() - restartingSince.current > RESTART_TIMEOUT_MS) {
      setNotice('No result was recorded in time — check journalctl -u cc-update and journalctl -u cc-uvicorn on the host.');
      setPhase('idle');
    }
  }, [setPhase]);

  useEffect(() => {
    void tick();
    const iv = setInterval(() => void tick(), POLL_MS);
    return () => clearInterval(iv);
  }, [tick]);

  const failure = async (res: Response) => {
    const body = await res.json().catch(() => ({})) as { error?: string; detail?: string };
    return body.error ?? body.detail ?? `HTTP ${res.status}`;
  };

  const engage = async () => {
    setConfirming(false);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch('/api/restart/hold', { method: 'POST' });
      if (!res.ok) {
        setError(await failure(res));
        return;
      }
      baseline.current = infoRef.current?.requested_at;
      seenTrigger.current = null;
      setPhase('holding');
      void tick();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const holdAction = async (path: string) => {
    setError(null);
    try {
      const res = await fetch(path, { method: 'POST' });
      if (!res.ok) {
        setError(await failure(res));
        return;
      }
      if (path.endsWith('/cancel')) {
        setNotice(null);
        setPhase('idle');
      } else {
        setPhase('restarting');
      }
      void tick();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const updateHold = !!hold?.active && hold.kind !== 'restart';
  const unavailable = info === null || !info.available;
  const blockedBy = unavailable ? null
    : info.updateBusy || updateHold ? 'An update is running or waiting — restart after it has finished.'
    : info.state === 'running' && phase === 'idle' ? 'A restart is already running.'
    : null;
  const reason = info === null
    ? (infoError ? `Cannot read the restart status (${infoError}).` : 'Checking whether a restart is available…')
    : info.available ? null : (info.reason ?? UNAVAILABLE_PLAIN);
  const disabled = unavailable || blockedBy !== null || phase !== 'idle';

  return (
    <div className="space-y-3">
      <div className="cockpit-row">
        <div className="min-w-0 flex-1">
          <span className="cockpit-kicker text-[0.6rem]">
            <span className="text-primary">◆</span>
            Gateway Service{unavailable ? ' 🚧' : ''}
          </span>
          <p className="mt-2 text-sm font-medium text-foreground">Restart the local gateway</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {reason ?? blockedBy
              ?? 'Restarts the API process. The cockpit stays up and reconnects when it is back.'}
          </p>
        </div>
        <button
          type="button"
          disabled={disabled}
          onClick={() => setConfirming(true)}
          className="cockpit-toolbar-button w-full justify-center sm:w-auto disabled:opacity-50 disabled:cursor-not-allowed"
          title={unavailable ? `Restart 🚧 ${reason ?? UNAVAILABLE_PLAIN}` : 'Restart the API process'}
          aria-label="Restart the local gateway"
        >
          <RotateCw size={14} aria-hidden="true" className={phase === 'restarting' ? 'animate-spin' : ''} />
          Restart
        </button>
      </div>

      <ConfirmDialog
        open={confirming}
        title="Restart the local gateway?"
        message="Restarts the API process. Agents pause at their next step and resume afterwards; a run that cannot pause in time waits, or is interrupted if you choose Restart now."
        confirmLabel="Restart"
        variant="warning"
        onConfirm={() => void engage()}
        onCancel={() => setConfirming(false)}
      />

      {error && (
        <div role="alert" className="rounded-md border border-red-500/40 px-3 py-2 text-sm text-red-500">{error}</div>
      )}

      {phase === 'holding' && hold?.active && (
        <div className="space-y-3">
          <div className="rounded-md border border-amber-500/40 px-3 py-2 text-sm space-y-2">
            {hold.running.length === 0 ? (
              <p className="text-amber-500 animate-pulse">Nothing is running — requesting the restart…</p>
            ) : (
              <p className="text-amber-500">
                Paused for the restart — waiting for {hold.running.length} agent run
                {hold.running.length === 1 ? '' : 's'} to finish. Task runs and chats have been
                asked to pause at their next step; triage runs finish on their own. Nothing new
                starts until the restart.
              </p>
            )}
            {hold.running.length > 0 && (
              <ul className="text-xs text-muted-foreground space-y-0.5">
                {hold.running.map((r) => (
                  <li key={r.id} className="flex justify-between gap-2">
                    <span className="font-mono truncate">{r.agent_id} · {r.dispatch ? 'triage' : r.task_id ? 'task' : r.mode}</span>
                    <span className={r.last_step_age_s > 1800 ? 'text-red-500' : ''}>
                      last step {age(r.last_step_age_s)} ago{r.last_step_age_s > 1800 ? ' — likely dead' : ''}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {notice && <p className="text-xs text-amber-500">{notice}</p>}
            {hold.trigger_error && <p className="text-red-500 text-xs">{hold.trigger_error}</p>}
          </div>
          {hold.running.length > 0 && (
            <button
              type="button"
              onClick={() => void holdAction('/api/restart/hold/now')}
              className="w-full rounded-md border border-amber-500/60 text-amber-500 py-2 text-sm font-semibold hover:bg-amber-500/10 transition-colors"
            >
              Restart now (kills the runs above)
            </button>
          )}
          <button
            type="button"
            onClick={() => void holdAction('/api/restart/hold/cancel')}
            className="w-full rounded-md border border-border py-2 text-sm hover:bg-muted transition-colors"
          >
            Cancel restart — resume the team
          </button>
        </div>
      )}

      {phase === 'restarting' && (
        <p className="rounded-md border border-border px-3 py-2 text-sm text-muted-foreground animate-pulse">
          Restarting the API{info?.state === 'running' && info.message ? ` — ${info.message}` : ''}…
          The gateway connection drops meanwhile; this card keeps checking and the cockpit reconnects by itself.
        </p>
      )}

      {phase === 'idle' && notice && (
        <p className="rounded-md border border-amber-500/40 px-3 py-2 text-sm text-amber-500">{notice}</p>
      )}

      {phase === 'idle' && info && info.state !== 'none' && info.state !== 'running' && (
        <p
          className={`text-xs ${info.state === 'restarted' ? 'text-green-500' : info.state === 'refused' ? 'text-amber-500' : 'text-red-500'}`}
        >
          {info.state === 'restarted' ? 'Last restart succeeded' : info.state === 'refused' ? 'Last restart was not run' : 'Last restart failed'}
          {info.finished_at ? ` at ${new Date(info.finished_at).toLocaleString()}` : ''}
          {info.forced ? ' (forced)' : ''}
          {info.message ? ` — ${info.message}` : ''}
        </p>
      )}
    </div>
  );
}
