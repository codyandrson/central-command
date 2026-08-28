import { useState, useEffect } from 'react';
import { ArrowUpCircle } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';

interface VersionCheck {
  current: string;
  latest: string | null;
  updateAvailable: boolean;
  projectDir?: string | null;
}

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

const CHECK_INTERVAL_MS = 60 * 60 * 1000; // 1 hour
const PROGRESS_POLL_MS = 3000;

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

/**
 * Shows an update badge next to the version in the status bar
 * when a newer version of Nerve is available. Clicking it opens
 * a modal with update instructions.
 */
export function UpdateBadge() {
  const [versionInfo, setVersionInfo] = useState<VersionCheck | null>(null);
  const [open, setOpen] = useState(false);
  const [progress, setProgress] = useState<UpdateProgress | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);

  useEffect(() => {
    const ac = new AbortController();
    const check = async () => {
      try {
        const res = await fetch('/api/version/check', { signal: ac.signal });
        if (!res.ok) return;
        const data: VersionCheck = await res.json();
        setVersionInfo(data);
      } catch {
        // Silently ignore — aborted or network error
      }
    };
    check();
    const iv = setInterval(check, CHECK_INTERVAL_MS);
    return () => { ac.abort(); clearInterval(iv); };
  }, []);

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

  if (!versionInfo?.updateAvailable || !versionInfo.latest || !versionInfo.projectDir) return null;

  // Central Command's update pipeline (2026-08-27 contract): the running app
  // NEVER applies its own update — update.sh is the external updater. It
  // version-gates, backs up the spine DB, refuses to run under a live API,
  // and ends at a restart gate. These are the exact commands, per install
  // shape.
  const quotedProjectDir = shellQuote(versionInfo.projectDir);
  const zipName = `central-command-v${versionInfo.latest}.zip`;
  const importCommand = `cd ${quotedProjectDir}/deploy/single && ./update.sh import ~/Downloads/${zipName}`;
  const planCommand = `cd ${quotedProjectDir}/deploy/single && ./update.sh plan`;
  const applyCommand = `cd ${quotedProjectDir}/deploy/single && ./update.sh apply`;

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

      <Dialog open={open} onOpenChange={setOpen}>
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
                the spine database, applies v{versionInfo.latest}, rebuilds,
                restarts the services, health-checks, and <b>rolls back
                automatically</b> if the new version is unhealthy.
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
{`${importCommand}
${planCommand}   # dry-run: diff, flags, predicted conflicts
${applyCommand}  # stops if the API is running; restart is yours`}
                </pre>
                <p>Rollback: <span className="font-mono">./update.sh rollback</span> (zip installs) or <span className="font-mono">sudo journalctl -u cc-update</span> for the helper's log.</p>
              </div>
            </details>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
