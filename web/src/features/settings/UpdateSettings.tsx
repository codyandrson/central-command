import { useState } from 'react';
import { RefreshCw, ArrowUpCircle } from 'lucide-react';
import { checkVersion, useVersionCheck } from '@/lib/version-check';
import { UpdateDialog } from '@/components/UpdateBadge';

/**
 * Settings › Updates: what's running, what's published, when we last looked —
 * and a button to look NOW instead of waiting for the hourly check. Applying
 * is the same dialog the status-bar badge opens; nothing here changes the
 * system on its own.
 */
export function UpdateSettings() {
  const { info, checking, failure } = useVersionCheck();
  const [open, setOpen] = useState(false);
  const canApply = !!(info?.updateAvailable && info.latest && info.projectDir);

  let verdict: string;
  if (failure) verdict = `Check failed: ${failure}`;
  else if (info?.error) verdict = `Check failed: ${info.error}`;
  else if (!info) verdict = checking ? 'Checking…' : 'Not checked yet';
  else if (info.updateAvailable) verdict = `v${info.latest} is available`;
  else verdict = 'Up to date';

  return (
    <>
      <div className="cockpit-divider my-2" />
      <div className="cockpit-row">
        <div className="min-w-0 flex-1">
          <span className="cockpit-kicker text-[0.6rem]">
            <span className="text-primary">◆</span>
            Updates
          </span>
          <p className="mt-2 text-sm font-medium text-foreground">
            {info ? <>Running <span className="font-mono">v{info.current}</span> — {verdict}</> : verdict}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {info?.checkedAt
              ? `Last checked ${new Date(info.checkedAt).toLocaleString()}. `
              : ''}
            Checked automatically every hour and when this tab regains focus.
          </p>
        </div>
        <div className="flex w-full flex-col gap-2 sm:w-auto">
          <button
            type="button"
            onClick={() => void checkVersion(true)}
            disabled={checking}
            className="cockpit-toolbar-button w-full justify-center sm:w-auto disabled:opacity-50"
            title="Check for updates now"
          >
            <RefreshCw size={14} className={checking ? 'animate-spin' : ''} aria-hidden="true" />
            Check for updates
          </button>
          {canApply && (
            <button
              type="button"
              onClick={() => setOpen(true)}
              className="cockpit-toolbar-button w-full justify-center sm:w-auto"
              title={`Apply v${info!.latest}`}
            >
              <ArrowUpCircle size={14} aria-hidden="true" />
              Update to v{info!.latest}
            </button>
          )}
        </div>
      </div>
      {canApply && <UpdateDialog versionInfo={info!} open={open} onOpenChange={setOpen} />}
    </>
  );
}
