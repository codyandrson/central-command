import { useEffect, useState } from 'react';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { UNGATED_MEANING } from './useMcpTools';

/**
 * The confirmation before any change that makes an MCP tool UNGATED — a
 * default, or one agent's override. It names the server, the tool and who is
 * affected, says what ungated means, and keeps the confirm button disabled
 * until the operator ticks the acknowledgement (the pattern the gate-override
 * author dialog uses for an irreversible class). Narrowing, and a revoke that
 * does not widen, never come through here; a revoke that DOES widen (the
 * agent's "human approval" override over an ungated default) comes through
 * with scope `revoke`.
 */
export function UngateDialog({ open, onOpenChange, serverId, toolName, scope, agents, onConfirm }: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  serverId: string;
  toolName: string;
  /** `default`: every holder without an override of its own; `override`: the one agent;
   *  `revoke`: the one agent, whose "human approval" override is being revoked over an ungated default. */
  scope: 'default' | 'override' | 'revoke';
  /** The agents affected, named in the dialog. */
  agents: string[];
  onConfirm: () => Promise<void>;
}) {
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { if (open) { setAck(false); setError(''); setBusy(false); } }, [open]);

  const confirm = async () => {
    setBusy(true);
    setError('');
    try {
      await onConfirm();
      onOpenChange(false);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-[1.2rem] font-semibold text-foreground">
            Ungate {toolName}?
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            {scope === 'default' && (
              <>Changes the default gate of <span className="font-mono">{serverId}</span> / <span className="font-mono">{toolName}</span>.</>
            )}
            {scope === 'override' && (
              <>Sets an override for one agent on <span className="font-mono">{serverId}</span> / <span className="font-mono">{toolName}</span>.</>
            )}
            {scope === 'revoke' && (
              <>Revoking this agent&apos;s &quot;human approval&quot; override on <span className="font-mono">{serverId}</span> / <span className="font-mono">{toolName}</span> returns it to the tool&apos;s default, which is ungated.</>
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2 text-[0.733rem]" data-testid="ungate-affected">
          <p className="text-muted-foreground">
            {scope === 'default'
              ? 'Affected — every agent that holds this server and has no override of its own:'
              : 'Affected:'}
          </p>
          {agents.length > 0 ? (
            <ul className="list-disc pl-5">
              {agents.map((a) => <li key={a}>{a}</li>)}
            </ul>
          ) : (
            <p>
              No agent holds this server yet. Any agent granted it later, without an override
              of its own, gets this default.
            </p>
          )}
        </div>

        <p className="cockpit-note" data-tone="danger" data-testid="ungate-meaning">{UNGATED_MEANING}</p>

        <label className="flex items-start gap-2 text-[0.733rem]">
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
          <span>
            I understand {toolName} will run with no proposal and no approval for the agents above,
            and I acknowledge the risk.
          </span>
        </label>

        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
          <Button disabled={!ack || busy} onClick={() => void confirm()}>
            {busy ? 'Ungating…' : scope === 'revoke' ? 'Revoke and ungate' : 'Ungate'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
