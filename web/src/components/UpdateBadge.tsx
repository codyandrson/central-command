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

const CHECK_INTERVAL_MS = 60 * 60 * 1000; // 1 hour

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
            <div>
              <p className="text-xs text-muted-foreground mb-1">Project directory</p>
              <pre className="bg-secondary rounded-md px-3 py-2 text-xs font-mono text-muted-foreground select-all whitespace-pre-wrap break-all">
                {versionInfo.projectDir}
              </pre>
            </div>
            <div>
              <p className="text-sm text-muted-foreground mb-2">
                Download the release zip from GitHub, then run the updater —
                it version-gates, backs up the spine database, and stops for
                you at each gate:
              </p>
              <pre className="bg-secondary rounded-md px-3 py-2 text-xs font-mono select-all whitespace-pre-wrap break-all">
{`${importCommand}
${planCommand}   # dry-run: diff, flags, predicted conflicts
${applyCommand}  # stops if the API is running; restart is yours`}
              </pre>
            </div>
            <div className="text-xs text-muted-foreground space-y-1">
              <p>The running app never updates itself — update.sh is the external updater.</p>
              <p>Rollback: <span className="font-mono">./update.sh rollback</span> restores the pre-update tag.</p>
              <p>On the k3s deployment this checkout IS the source — update with git, not the zip.</p>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
