import { useState, useCallback, useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

/* Presets FILL the query box, they never auto-run — a bulk dismissal is
   irreversible-in-spirit (2026-08-17 design), so the operator always sees
   the preview before committing. */
const PRESETS = [
  { label: 'Promotions before 2026', query: 'category:promotions before:2026/01/01' },
  { label: 'Unsubscribe, older than 2y', query: 'unsubscribe older_than:2y' },
];

interface PreviewSample {
  from?: string | null;
  subject?: string | null;
  date?: string | null;
}

interface PreviewResult {
  query: string;
  provider_matches: number;
  unprocessed: number;
  samples: PreviewSample[];
  match_digest: string;
}

interface ExecuteResult {
  provider_matches: number;
  dismissed: number;
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  // FastAPI's HTTPException(400, detail) — render the detail verbatim
  // (over-cap and digest-drift messages tell the operator what to do next).
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data as T;
}

interface BulkDismissDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Pre-fills the query — e.g. `from:<sender>` from a dismissal card. */
  initialQuery?: string;
  /** Fires after a successful execute — the host refreshes its own list/counters. */
  onDismissed: () => void;
}

export function BulkDismissDialog({
  open, onOpenChange, initialQuery, onDismissed,
}: BulkDismissDialogProps) {
  const [query, setQuery] = useState('');
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [result, setResult] = useState<ExecuteResult | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setQuery(initialQuery ?? '');
    setPreview(null);
    setResult(null);
    setError('');
  }, [open, initialQuery]);

  // Editing the query after a preview invalidates it — execute must never run
  // against a digest that was computed for a different query.
  const handleQueryChange = useCallback((value: string) => {
    setQuery(value);
    setPreview(null);
    setResult(null);
  }, []);

  const runPreview = useCallback(async () => {
    const trimmed = query.trim();
    if (!trimmed || previewing) return;
    setPreviewing(true); setError(''); setResult(null);
    try {
      const out = await postJSON<PreviewResult>('/api/work/bulk_dismiss/preview', { query: trimmed });
      setPreview(out);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPreview(null);
    } finally {
      setPreviewing(false);
    }
  }, [query, previewing]);

  const runExecute = useCallback(async () => {
    if (!preview || executing) return;
    setExecuting(true); setError('');
    try {
      const out = await postJSON<ExecuteResult>('/api/work/bulk_dismiss', {
        query: preview.query, match_digest: preview.match_digest,
      });
      setResult(out);
      setPreview(null);
      onDismissed();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setExecuting(false);
    }
  }, [preview, executing, onDismissed]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>Bulk dismiss</DialogTitle>
          <DialogDescription>
            Resolves a Gmail search to a pinned set of UNPROCESSED rows and dismisses
            exactly those — new mail matching later is untouched. Preview before you execute.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap gap-1.5">
          {PRESETS.map((p) => (
            <button
              key={p.query}
              type="button"
              onClick={() => handleQueryChange(p.query)}
              className="cockpit-badge cursor-pointer hover:text-foreground"
            >
              {p.label}
            </button>
          ))}
        </div>

        <Input
          value={query}
          onChange={(e) => handleQueryChange(e.target.value)}
          placeholder="from:sender@example.com, category:promotions before:2026/01/01, …"
          className="h-10"
          aria-label="Bulk dismiss query"
        />

        {error && <p className="text-[0.733rem] text-destructive">{error}</p>}

        {preview && (
          <div className="rounded-lg border border-border/40 p-3 text-xs" data-testid="bulk-dismiss-preview">
            <p className="text-foreground">
              {preview.provider_matches} matched in Gmail · {preview.unprocessed} still unprocessed
            </p>
            {preview.samples.length > 0 && (
              <table className="mt-2 w-full text-[0.7rem] text-muted-foreground">
                <tbody>
                  {preview.samples.map((s, i) => (
                    <tr key={i} className="border-t border-border/30">
                      <td className="max-w-[160px] truncate py-1 pr-2">{s.from}</td>
                      <td className="truncate py-1">{s.subject}</td>
                      <td className="whitespace-nowrap py-1 pl-2 text-right">{s.date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {result && (
          <p className="text-[0.8rem] text-green" data-testid="bulk-dismiss-result">
            Dismissed {result.dismissed} of {result.provider_matches} matched.
          </p>
        )}

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={runPreview}
            disabled={!query.trim() || previewing || executing}
          >
            {previewing && <Loader2 size={14} className="animate-spin" />}
            {previewing ? 'Previewing…' : 'Preview'}
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={runExecute}
            disabled={!preview || executing}
          >
            {executing && <Loader2 size={14} className="animate-spin" />}
            {executing ? 'Dismissing…' : 'Execute'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
