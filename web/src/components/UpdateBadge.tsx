import { useState, useEffect } from 'react';
import { ArrowUpCircle } from 'lucide-react';
import { useVersionCheck, type VersionCheck } from '@/lib/version-check';
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
  error?: string;
}

interface UpdateProgress {
  pending: boolean;
  inFlight: boolean;
  status: UpdateStatus | null;
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

  // Poll the durable status record while the modal is open and a run is
  // pending/in flight. Fetch failures are EXPECTED mid-update (the updater
  // restarts this very server) — keep polling straight through them; the
  // record outlives every process involved.
  useEffect(() => {
    if (!open || (!applying && !progress?.pending && !progress?.inFlight)) return;
    const iv = setInterval(async () => {
      try {
        const res = await fetch('/api/update/status');
        if (!res.ok) return;
        const data: UpdateProgress = await res.json();
        setProgress(data);
        if (data.status && data.status.state !== 'running' && !data.pending && !data.inFlight) {
          setApplying(false);
        }
      } catch {
        // server restarting under us — keep polling
      }
    }, PROGRESS_POLL_MS);
    return () => clearInterval(iv);
  }, [open, applying, progress?.pending, progress?.inFlight]);

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

  if (!versionInfo.latest || !versionInfo.projectDir) return null;

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
              <button
                onClick={applyNow}
                className="w-full rounded-md bg-primary text-primary-foreground py-2 text-sm font-semibold hover:bg-primary/90 transition-colors"
              >
                Apply update now
              </button>
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
