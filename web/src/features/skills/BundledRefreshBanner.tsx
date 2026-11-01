import { useState } from 'react';
import { AlertTriangle, ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { MOVED_HINT, bannerText, rowLabel, rowReason, type LatestItem } from './bundledRefresh';

interface Props {
  target: string;
  items: LatestItem[];
  retrying: boolean;
  error: string;
  onRetry: () => void;
}

/**
 * One banner when a release's bundled skill docs were not all refreshed:
 * the count, the per-row reasons on expand, and Retry. It clears when every
 * row has been applied (the parent renders it only while rows are pending).
 */
export function BundledRefreshBanner({ target, items, retrying, error, onRetry }: Props) {
  const [open, setOpen] = useState(false);
  const moved = items.some((i) => i.moved);
  return (
    <div
      role="status"
      aria-label="bundled skill docs not refreshed"
      className="shrink-0 border-b border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-[0.733rem]"
    >
      <div className="flex items-start gap-2">
        <AlertTriangle size={13} className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" />
        <span className="min-w-0 flex-1 text-foreground">{bannerText(items.length, target)}</span>
        <Button size="sm" variant="outline" disabled={retrying} onClick={onRetry}>
          {retrying ? 'Retrying…' : 'Retry'}
        </Button>
      </div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="mt-1 ml-5 flex items-center gap-1 text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronDown size={11} className={open ? '' : '-rotate-90'} />
        {open ? 'Hide reasons' : 'Show reasons'}
      </button>
      {open && (
        <ul className="mt-1.5 ml-5 space-y-1.5">
          {items.map((i) => (
            <li key={`${i.skill_id}/${i.doc_key}`}>
              <span className="font-mono text-foreground">{rowLabel(i)}</span>
              <span className="text-muted-foreground"> — {rowReason(i)}</span>
              {i.moved && <p className="mt-0.5 text-amber-700 dark:text-amber-300">{MOVED_HINT}</p>}
            </li>
          ))}
        </ul>
      )}
      {!open && moved && (
        <p className="mt-1 ml-5 text-amber-700 dark:text-amber-300">
          A plain retry cannot fix every row — expand for what to do.
        </p>
      )}
      {error && <p role="alert" className="mt-1.5 ml-5 text-destructive">{error}</p>}
    </div>
  );
}
