/**
 * GoalIntentDialog — the operator's entry point into agent-mediated goal
 * creation and recalibration. Collects a free-text intent, then hands it to
 * `accountability` (or, for an existing goal, whichever agent already owns
 * its check-ins — currently always the same one, but the row already carries
 * the truth so there is nothing to hardcode there) as the FIRST message of a
 * brand-new conversation lane.
 *
 * The seam is the one the chat "+" already uses (`SpawnAgentDialog.tsx`):
 * `sessions.new` opens a parallel lane (nothing closed, multi-session per
 * agent is the target architecture — CLAUDE.md). `sessions.new` deliberately
 * seeds no first message (`nerve_gateway.py`: "No first message, so no model
 * call: the session parks AWAITING_OPERATOR"), but `chat.send` is the exact
 * RPC the composer itself calls to deliver one
 * (`features/chat/operations/sendMessage.ts`) — nothing here is bespoke, it
 * is that same call fired programmatically instead of typed. No pre-fill
 * fallback was needed: this is a real seeded first message.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { InlineSelect } from '@/components/ui/InlineSelect';
import type { InlineSelectOption } from '@/components/ui/InlineSelect';
import { useGateway } from '@/contexts/GatewayContext';

// The accountability agent is a fixed, singleton roster member — never
// operator-named, never re-hired under another id (central_command/runtime/
// templates.py: ACCOUNTABILITY_TEMPLATE.id). A NEW goal has no existing row
// to read an agent_id off, so this is the one place that names it directly;
// discussing an EXISTING goal instead targets that goal's own `agent_id`.
export const ACCOUNTABILITY_AGENT_ID = 'accountability';

const CADENCE_OPTIONS: InlineSelectOption[] = [
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
];

// Same tiny busy/error runner as the Skills page's write dialogs
// (`features/skills/SkillDialogs.tsx`'s useAsyncAction) — not exported there,
// so reproduced rather than imported.
function useAsyncAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const run = async (fn: () => Promise<unknown>, onDone?: () => void) => {
    setBusy(true);
    setError('');
    try {
      await fn();
      onDone?.();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

interface GoalIntentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The agent this conversation opens with. */
  agentId: string;
  kicker: string;
  title: string;
  description: string;
  textareaLabel: string;
  placeholder: string;
  showCadence?: boolean;
  buildMessage: (text: string, cadence: string) => string;
  /** Fired once the lane is open and the intent delivered. */
  onOpened: (sessionKey: string) => void;
}

export function GoalIntentDialog({
  open, onOpenChange, agentId, kicker, title, description,
  textareaLabel, placeholder, showCadence, buildMessage, onOpened,
}: GoalIntentDialogProps) {
  const { rpc } = useGateway();
  const [text, setText] = useState('');
  const [cadence, setCadence] = useState('weekly');
  const { busy, error, run } = useAsyncAction();

  useEffect(() => {
    if (!open) { setText(''); setCadence('weekly'); }
  }, [open]);

  const trimmed = text.trim();

  const handleSubmit = useCallback(() => {
    if (!trimmed || busy) return;
    run(async () => {
      const opened = await rpc('sessions.new', { agentId }) as { sessionKey?: string };
      const sessionKey = opened.sessionKey;
      if (!sessionKey) throw new Error('Could not open a conversation');
      const idempotencyKey = `loe-intent-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      await rpc('chat.send', {
        sessionKey, message: buildMessage(trimmed, cadence), deliver: false, idempotencyKey,
      });
      onOpened(sessionKey);
    }, () => onOpenChange(false));
  }, [trimmed, busy, run, rpc, agentId, buildMessage, cadence, onOpened, onOpenChange]);

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v && !busy) onOpenChange(false); }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <div className="cockpit-kicker">
            <span className="text-primary">◆</span>
            {kicker}
          </div>
          <DialogTitle className="text-[1.35rem] font-semibold tracking-[-0.03em] text-foreground">
            {title}
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            {description}
          </DialogDescription>
        </DialogHeader>

        <div>
          <label htmlFor="loe-intent-text" className="cockpit-field-label mb-2 block">
            {textareaLabel}
          </label>
          <textarea
            id="loe-intent-text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={placeholder}
            rows={4}
            className="cockpit-textarea min-h-[120px]"
            autoFocus
          />
        </div>

        {showCadence && (
          <div>
            <label className="cockpit-field-label mb-2 block">Cadence guess</label>
            <InlineSelect
              value={cadence}
              onChange={setCadence}
              options={CADENCE_OPTIONS}
              ariaLabel="Cadence guess"
              inline
            />
          </div>
        )}

        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button disabled={busy || !trimmed} onClick={handleSubmit}>
            {busy ? 'Opening…' : 'Start conversation'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
