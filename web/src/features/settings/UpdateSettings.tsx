import { useRef, useState } from 'react';
import { RefreshCw, ArrowUpCircle, FileUp } from 'lucide-react';
import { checkVersion, useVersionCheck, type VersionCheck } from '@/lib/version-check';
import { UpdateDialog } from '@/components/UpdateBadge';

/**
 * Settings › Updates: what's running, what's published, when we last looked —
 * a button to look NOW instead of waiting for the hourly check, and "Update
 * from file" for installs that can't reach the release source at all (the
 * air-gapped path: the operator downloads the source zip elsewhere and points
 * at it here; the server stages it via ./update.sh, nothing is unpacked by
 * hand). Applying is the same dialog the status-bar badge opens; nothing here
 * changes the system without the operator's explicit click.
 */
export function UpdateSettings() {
  const { info, checking, failure } = useVersionCheck();
  const [open, setOpen] = useState(false);
  const [fileInfo, setFileInfo] = useState<VersionCheck | null>(null);
  const [fileOpen, setFileOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const canApply = !!(info?.updateAvailable && info.latest && info.projectDir);

  let verdict: string;
  if (failure) verdict = `Check failed: ${failure}`;
  else if (info?.error) verdict = `Check failed: ${info.error}`;
  else if (!info) verdict = checking ? 'Checking…' : 'Not checked yet';
  else if (info.updateAvailable) verdict = `v${info.latest} is available`;
  else verdict = 'Up to date';

  const uploadZip = async (file: File) => {
    setUploading(true);
    setUploadMsg(null);
    try {
      const res = await fetch('/api/update/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/zip' },
        body: file,
      });
      const data = await res.json().catch(() => ({})) as {
        target?: string; current?: string; updateAvailable?: boolean;
        projectDir?: string; error?: string;
      };
      if (!res.ok) {
        setUploadMsg(data.error ?? `HTTP ${res.status}`);
        return;
      }
      if (!data.updateAvailable || !data.target) {
        setUploadMsg(`v${data.target} is not newer than the running v${data.current} — nothing to apply`);
        return;
      }
      setFileInfo({
        current: data.current ?? '?',
        latest: data.target,
        updateAvailable: true,
        projectDir: data.projectDir,
      });
      setFileOpen(true);
    } catch (err) {
      setUploadMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

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
            Air-gapped? Download the release zip elsewhere and use Update from file.
          </p>
          {uploadMsg && (
            <p className="mt-1 text-xs text-amber-500">{uploadMsg}</p>
          )}
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
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            className="cockpit-toolbar-button w-full justify-center sm:w-auto disabled:opacity-50"
            title="Apply an update from a downloaded source zip (the air-gapped path)"
          >
            <FileUp size={14} className={uploading ? 'animate-pulse' : ''} aria-hidden="true" />
            {uploading ? 'Staging zip…' : 'Update from file'}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".zip,application/zip"
            className="hidden"
            aria-label="Release zip file"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void uploadZip(f);
            }}
          />
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
      {fileInfo && <UpdateDialog versionInfo={fileInfo} open={fileOpen} onOpenChange={setFileOpen} />}
    </>
  );
}
