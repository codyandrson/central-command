import { useState } from 'react';
import { AlertTriangle, ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DiffView } from '@/features/chat/DiffView';
import {
  bannerText, canEdit, changeWord, rowLabel, takeLabel, type Decision, type ReleaseFlag,
} from './bundledRefresh';
import type { ResolveResult } from './useBundledRefresh';

type Resolve = (flag: ReleaseFlag, decision: Decision, content?: string) => Promise<ResolveResult>;

function FlagCard({ flag, resolve }: { flag: ReleaseFlag; resolve: Resolve }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [posting, setPosting] = useState(false);
  const label = rowLabel(flag);

  const post = async (decision: Decision, content?: string) => {
    setPosting(true);
    setError(null);
    const out = await resolve(flag, decision, content);
    setPosting(false);
    if (out.ok) setEditing(null);
    else setError(out.message);
  };

  return (
    <li aria-label={label} className="rounded border border-border bg-background px-3 py-2 space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-mono text-foreground">{label}</span>
        <span className="text-muted-foreground">{changeWord(flag)}</span>
      </div>
      {editing !== null ? (
        <textarea
          aria-label={`Edited text for ${label}`}
          value={editing}
          onChange={(e) => setEditing(e.target.value)}
          rows={14}
          className="w-full rounded border border-border bg-background px-2 py-1 font-mono text-[0.7rem]"
        />
      ) : (
        <div className="max-h-72 overflow-auto">
          <DiffView
            oldText={flag.ours ?? ''}
            newText={flag.theirs ?? ''}
            language="markdown"
            oldLabel="YOURS (LIBRARY)"
            newLabel="THE RELEASE"
          />
        </div>
      )}
      {error && <p role="alert" className="text-destructive">{error}</p>}
      <div className="flex flex-wrap gap-2">
        {editing !== null ? (
          <>
            <Button size="sm" disabled={posting} onClick={() => post('edit', editing)}>Save</Button>
            <Button size="sm" variant="outline" disabled={posting} onClick={() => { setEditing(null); setError(null); }}>
              Cancel
            </Button>
          </>
        ) : (
          <>
            <Button size="sm" variant="outline" disabled={posting} onClick={() => post('take_release')}>
              {takeLabel(flag)}
            </Button>
            <Button size="sm" variant="outline" disabled={posting} onClick={() => post('keep_mine')}>
              Keep mine
            </Button>
            {canEdit(flag) && (
              <Button size="sm" variant="outline" disabled={posting}
                onClick={() => setEditing(flag.ours ?? flag.theirs ?? '')}>
                Edit
              </Button>
            )}
          </>
        )}
      </div>
    </li>
  );
}

/**
 * One banner while bundled docs the operator edited wait on a decision after a
 * release changed them (DL-232): the count, and on expand one card per doc with
 * the library's text beside the release's and the three decisions. It clears
 * when every flag is decided (the parent renders it only while flags are open).
 */
export function BundledRefreshBanner({ flags, resolve }: { flags: ReleaseFlag[]; resolve: Resolve }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      role="status"
      aria-label="bundled skill docs kept for your decision"
      className="shrink-0 border-b border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-[0.733rem]"
    >
      <div className="flex items-start gap-2">
        <AlertTriangle size={13} className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" />
        <span className="min-w-0 flex-1 text-foreground">{bannerText(flags.length)}</span>
      </div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="mt-1 ml-5 flex items-center gap-1 text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronDown size={11} className={open ? '' : '-rotate-90'} />
        {open ? 'Hide docs' : 'Show docs'}
      </button>
      {open && (
        <ul className="mt-1.5 ml-5 max-h-[60vh] space-y-2 overflow-y-auto pr-1">
          {flags.map((f) => <FlagCard key={rowLabel(f)} flag={f} resolve={resolve} />)}
        </ul>
      )}
    </div>
  );
}
