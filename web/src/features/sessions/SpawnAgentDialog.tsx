import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { InlineSelect } from '@/components/ui/InlineSelect';
import type { InlineSelectOption } from '@/components/ui/InlineSelect';
import { useGateway } from '@/contexts/GatewayContext';
import { useSessionContext, type SpawnSessionOpts } from '@/contexts/SessionContext';

type RosterAgent = {
  id: string;
  name: string;
  role?: string;
  status?: string;
};

type AgentsResponse = { agents?: RosterAgent[] };
type NewSessionResponse = { sessionId?: string; sessionKey?: string };

interface SpawnAgentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Kept for call-site compatibility. */
  onSpawn?: (opts: SpawnSessionOpts) => Promise<void | boolean>;
}

/**
 * The chat page's "+" — start a conversation with a teammate. That is all it
 * does (the operator, 2026-07-25, the management split).
 *
 * It used to do three jobs; the other two moved to where they belong:
 *
 *  - HIRE AN AGENT → the **Agents page**. Hiring is team management, not a
 *    chat action: it lands a roster row, charter v1 and capability grants in
 *    one transaction, next to retire, grant/revoke and coaching.
 *  - ASSIGN WORK / ASSIGN A PROJECT → the **Tasks page**, which owns the board
 *    and the create path. Chatting and tasking are genuinely different acts: a
 *    task is work on the board with an assignee and a lifecycle; a
 *    conversation is a conversation. A chat can still END in work — the agent
 *    proposes `task.create` and the operator approves it in the Decisions
 *    Inbox — but that is the agent asking, not the operator filing.
 *
 * The vendored sub-agent card is gone with them rather than left disabled: the
 * capability it stood for shipped as D7 orchestration, and it is reached by
 * assigning a project on the Tasks page.
 *
 * Every ACTIVE roster agent is chattable — talking changes nothing on its own,
 * so there is nothing here to gate. Retired agents are absent; reactivate one
 * from the Agents page to talk to it again.
 */
export function SpawnAgentDialog({ open, onOpenChange }: SpawnAgentDialogProps) {
  const { rpc } = useGateway();
  const { refreshSessions, setCurrentSession } = useSessionContext();

  const [agents, setAgents] = useState<RosterAgent[]>([]);
  const [agentId, setAgentId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setError('');
    (async () => {
      try {
        const roster = await rpc('agents.list') as AgentsResponse;
        if (cancelled) return;
        const active = (roster.agents ?? []).filter((a) => a.status !== 'RETIRED');
        setAgents(active);
        if (active.length > 0 && !active.some((a) => a.id === agentId)) {
          setAgentId(active[0].id);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Could not load the roster');
        }
      }
    })();
    return () => { cancelled = true; };
    // agentId intentionally omitted: reloading would clobber the selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, rpc]);

  const options = useMemo<InlineSelectOption[]>(
    () => agents.map((a) => ({ value: a.id, label: a.name || a.id })),
    [agents],
  );
  const chosen = agents.find((a) => a.id === agentId);

  const handleStart = useCallback(async () => {
    if (!agentId || busy) return;
    setBusy(true);
    setError('');
    try {
      // A PARALLEL lane: sessions.new never closes an open conversation, and
      // nothing is ever destroyed. Multiple concurrent sessions per agent is
      // the target architecture, not a workaround.
      const res = await rpc('sessions.new', { agentId }) as NewSessionResponse;
      await refreshSessions();
      setCurrentSession(res.sessionKey || `agent:${agentId}:main`);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start the conversation');
    } finally {
      setBusy(false);
    }
  }, [agentId, busy, onOpenChange, refreshSessions, rpc, setCurrentSession]);

  return (
    <Dialog open={open} onOpenChange={(value) => { if (!value && !busy) onOpenChange(false); }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <div className="cockpit-kicker">
            <span className="text-primary">◆</span>
            Chat
          </div>
          <DialogTitle className="text-[1.35rem] font-semibold tracking-[-0.03em] text-foreground">
            Start a conversation
          </DialogTitle>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            Opens a new session with a teammate, alongside any already open —
            nothing is closed or replaced. To hire or coach an agent, use the
            Agents page; to put work on the board, use Tasks.
          </DialogDescription>
        </DialogHeader>

        <div>
          <label className="cockpit-field-label mb-2 block">Agent</label>
          <InlineSelect
            value={agentId}
            onChange={setAgentId}
            options={options.length ? options : [{ value: '', label: 'Loading roster…' }]}
            ariaLabel="Agent to talk to"
            inline
          />
          {chosen?.role && <p className="cockpit-note mt-2">{chosen.role}</p>}
        </div>

        {error && <p className="cockpit-note" data-tone="danger">{error}</p>}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button disabled={busy || !agentId} onClick={handleStart}>
            {busy ? 'Opening…' : 'Start conversation'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
