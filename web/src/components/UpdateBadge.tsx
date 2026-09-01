import { useState, useEffect } from 'react';
import { ArrowUpCircle } from 'lucide-react';
import { checkVersion, useVersionCheck, type VersionCheck } from '@/lib/version-check';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';

interface UpdateStatus {
  state: 'running' | 'success' | 'failed' | 'rolled_back';
  phase: string;
  target?: string;
  error?: string;
}

interface StageProgress {
  pending: boolean;
  inFlight: boolean;
  status: UpdateStatus | null;
}

interface UpdateProgress {
  pending: boolean;
  inFlight: boolean;
  status: UpdateStatus | null;
  /** Absent on servers older than v2.19.0 — treated as "staging unknown". */
  stage?: StageProgress;
}

const PROGRESS_POLL_MS = 3000;

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

/**
 * Shows an update badge next to the version in the status bar when a newer
 * Central Command is available (the shared version-check store does the
 * polling). Clicking it opens the update dialog.
 */
export function UpdateBadge() {
  const { info: versionInfo } = useVersionCheck();
  const [open, setOpen] = useState(false);

  if (!versionInfo?.updateAvailable || !versionInfo.latest || !versionInfo.projectDir) return null;

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1 text-[0.6rem] text-primary hover:text-primary/80 transition-colors cursor-pointer ml-1.5"
        title={`Update available: v${versionInfo.latest}`}
        aria-label={`Update available: version ${versionInfo.latest}. Click for instructions.`}
      >
        <ArrowUpCircle className="w-3 h-3" />
        <span className="uppercase tracking-wide font-bold">update</span>
      </button>
      <UpdateDialog versionInfo={versionInfo} open={open} onOpenChange={setOpen} />
    </>
  );
}

interface UpdateDialogProps {
  versionInfo: VersionCheck;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** The apply-or-instructions modal — opened from the badge and from Settings › Updates. */
export function UpdateDialog({ versionInfo, open, onOpenChange }: UpdateDialogProps) {
  const [progress, setProgress] = useState<UpdateProgress | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);

  // Poll the durable status records the whole time the modal is open — the
  // stage record decides whether "Apply update now" is offered at all. Fetch
  // failures are EXPECTED mid-update (the updater restarts this very server)
  // — keep polling straight through them; the records outlive every process
  // involved.
  useEffect(() => {
    if (!open) return;
    const tick = async () => {
      try {
        const res = await fetch('/api/update/status');
        if (!res.ok) return;
        const data: UpdateProgress = await res.json();
        setProgress(data);
        if (data.status && data.status.state !== 'running' && !data.pending && !data.inFlight) {
          setApplying((was) => {
            // The run this dialog started just finished — re-read VERSION so
            // the badge, Settings and this dialog stop offering the update
            // that is now installed (or, after a rollback, stay honest).
            if (was) void checkVersion(true);
            return false;
          });
        }
      } catch {
        // server restarting under us — keep polling
      }
    };
    void tick();
    const iv = setInterval(tick, PROGRESS_POLL_MS);
    return () => clearInterval(iv);
  }, [open]);

  const applyNow = async () => {
    setApplyError(null);
    try {
      const res = await fetch('/api/update/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: versionInfo?.latest ?? '' }),
      });
      if (res.status === 202) {
        setApplying(true);
        setProgress({ pending: true, inFlight: false, status: null });
        return;
      }
      const body = await res.json().catch(() => ({})) as { error?: string };
      setApplyError(body.error ?? `HTTP ${res.status}`);
    } catch (err) {
      setApplyError(err instanceof Error ? err.message : String(err));
    }
  };

  const retryStage = async () => {
    setApplyError(null);
    try {
      await fetch('/api/update/stage', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: versionInfo?.latest ?? '' }),
      });
    } catch (err) {
      setApplyError(err instanceof Error ? err.message : String(err));
    }
  };

  if (!versionInfo.latest || !versionInfo.projectDir) return null;

  // Staging gate (v2.19.0): the stager prebuilds the image caches the moment
  // an update is seen; "Apply update now" waits for its verdict. A server
  // without the stage record (older release, stage units not installed)
  // reports nothing — fall back to the ungated button rather than hiding
  // apply forever.
  const stage = progress?.stage;
  const staged = stage?.status?.state === 'success'
    && (stage.status.target === versionInfo.latest || stage.status.phase === 'up-to-date');
  const staging = !!(stage && (stage.pending || stage.inFlight || stage.status?.state === 'running'));
  const stageFailed = !staging && stage?.status?.state === 'failed'
    && stage.status.target === versionInfo.latest;
  const stageUnknown = !staged && !staging && !stageFailed;

  // Central Command's update pipeline (2026-08-27 contract): the running app
  // NEVER applies its own update — update.sh is the external updater. It
  // version-gates, backs up the spine DB, refuses to run under a live API,
  // and ends at a restart gate. These are the exact commands, per install
  // shape.
  const quotedProjectDir = shellQuote(versionInfo.projectDir);
  const zipName = `central-command-${versionInfo.latest}.zip`;
  // One command (2026-08-28): update.sh <zip> = init-if-needed + import +
  // plan + the operator's explicit yes + apply.
  const updateCommand = `cd ${quotedProjectDir}/deploy/single && ./update.sh ~/Downloads/${zipName}`;

  return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Update Available</DialogTitle>
            <DialogDescription>
              Central Command <span className="font-mono font-semibold text-foreground">v{versionInfo.latest}</span> is
              available. You're running <span className="font-mono text-muted-foreground">v{versionInfo.current}</span>.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-2">
            {(applying || progress) && (
              <div className="rounded-md border border-border px-3 py-2 text-sm space-y-1">
                {progress?.status?.state === 'success' ? (
                  <p className="text-green-500">Update complete — the system is healthy on the new version.</p>
                ) : progress?.status?.state === 'rolled_back' ? (
                  <p className="text-amber-500">
                    Update failed its health check and was <b>rolled back</b> — you are on v{versionInfo.current}.
                    {progress.status.error ? ` (${progress.status.error})` : ''}
                  </p>
                ) : progress?.status?.state === 'failed' ? (
                  <p className="text-red-500">
                    Update failed at <span className="font-mono">{progress.status.phase}</span>
                    {progress.status.error ? ` — ${progress.status.error}` : ''}.
                    {' '}Details: <span className="font-mono">journalctl -u cc-update</span>
                  </p>
                ) : (
                  <p className="text-muted-foreground animate-pulse">
                    {progress?.status?.state === 'running'
                      ? `Updating — ${progress.status.phase}…`
                      : 'Update requested — waiting for the updater to start…'}
                    {' '}The cockpit restarts during the update; this window keeps polling through it.
                  </p>
                )}
              </div>
            )}
            {applyError && (
              <div className="rounded-md border border-red-500/40 px-3 py-2 text-sm text-red-500">
                {applyError}
              </div>
            )}
            {!applying && progress?.status?.state !== 'running' && (
              progress === null ? (
                <p className="text-sm text-muted-foreground animate-pulse">Checking update preparation…</p>
              ) : staging ? (
                <p className="text-sm text-muted-foreground animate-pulse">
                  Preparing the update — building images in the background. The system stays
                  up; the apply button appears when everything is ready.
                </p>
              ) : stageFailed ? (
                <>
                  <div className="rounded-md border border-red-500/40 px-3 py-2 text-sm text-red-500">
                    Preparation failed at <span className="font-mono">{stage?.status?.phase}</span>
                    {stage?.status?.error ? ` — ${stage.status.error}` : ''}.
                    {' '}Details: <span className="font-mono">journalctl -u cc-update-stage</span>
                  </div>
                  <button
                    onClick={retryStage}
                    className="w-full rounded-md bg-primary text-primary-foreground py-2 text-sm font-semibold hover:bg-primary/90 transition-colors"
                  >
                    Retry preparation
                  </button>
                </>
              ) : (
                <>
                  <button
                    onClick={applyNow}
                    className="w-full rounded-md bg-primary text-primary-foreground py-2 text-sm font-semibold hover:bg-primary/90 transition-colors"
                  >
                    Apply update now
                  </button>
                  {staged && (
                    <p className="text-xs text-muted-foreground">
                      v{versionInfo.latest} is prepared — the image builds are already cached, so
                      applying is mostly backup, restart and health-check time.
                    </p>
                  )}
                  {stageUnknown && (
                    <p className="text-xs text-muted-foreground">
                      No preparation record — the update stager isn't installed or hasn't run;
                      applying will do the full build first.
                    </p>
                  )}
                </>
              )
            )}
            <div className="text-xs text-muted-foreground space-y-1">
              <p>
                The cockpit never updates itself: this hands off to the external
                cc-update helper (a root one-shot systemd unit), which backs up
                the spine, LiteLLM and n8n databases (plus their decryption
                keys), applies v{versionInfo.latest}, rebuilds, restarts the
                services, health-checks, and <b>rolls back automatically</b> if
                the new version is unhealthy.
              </p>
            </div>
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer">Manual / zip-install path</summary>
              <div className="space-y-2 pt-2">
                <p className="mb-1">Project directory</p>
                <pre className="bg-secondary rounded-md px-3 py-2 font-mono select-all whitespace-pre-wrap break-all">
                  {versionInfo.projectDir}
                </pre>
                <pre className="bg-secondary rounded-md px-3 py-2 font-mono select-all whitespace-pre-wrap break-all">
{`${updateCommand}
# shows the version gate + plan, asks for your yes, applies;
# gates on a running API and offers the stop`}
                </pre>
                <p>Rollback: <span className="font-mono">./update.sh rollback</span> (zip installs) or <span className="font-mono">sudo journalctl -u cc-update</span> for the helper's log.</p>
              </div>
            </details>
          </div>
        </DialogContent>
      </Dialog>
  );
}
