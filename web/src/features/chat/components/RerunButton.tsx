import { useCallback, useState } from 'react';
import { Loader2, RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ConfirmDialog';

interface RerunButtonProps {
  sessionId: string;
  onRerun: (sessionId: string) => Promise<void>;
  /** Which charter this would change, when it would. */
  hint?: string | null;
  /** The confirmation's body, where the default (a session's) is not the right
   *  words, as for a task. The hint is prefixed to either. */
  message?: string;
  /** Held off by the caller's own busy state; the button's own run disables it too. */
  disabled?: boolean;
}

const DEFAULT_MESSAGE = 'This session closes and its transcript is kept. Its open proposals and questions are withdrawn, and the same work runs again in a new session under the charter that is current now.';

/** Re-run, asked first: the session closes and the work runs again in a new one.
 *  Rendered only where the server's `composer.rerun` says it is on offer. */
export function RerunButton({ sessionId, onRerun, hint, message, disabled }: RerunButtonProps) {
  const [confirming, setConfirming] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    if (running) return;
    setRunning(true);
    setError(null);
    try {
      await onRerun(sessionId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Re-run failed');
    } finally {
      setRunning(false);
    }
  }, [onRerun, sessionId, running]);

  return (
    <>
      <Button variant="outline" size="xs" title={hint ?? undefined} onClick={() => setConfirming(true)} disabled={running || disabled}>
        {running ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />}
        Re-run
      </Button>
      {error && <span role="alert" className="text-[0.733rem] text-destructive">{error}</span>}
      <ConfirmDialog
        open={confirming}
        title="Re-run under the current charter?"
        message={`${hint ? `${hint} ` : ''}${message ?? DEFAULT_MESSAGE}`}
        confirmLabel="Re-run"
        cancelLabel="Keep it"
        onConfirm={() => { setConfirming(false); void run(); }}
        onCancel={() => setConfirming(false)}
      />
    </>
  );
}
