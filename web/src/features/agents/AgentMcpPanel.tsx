import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { UngateDialog } from '../gates/UngateDialog';
import { fmtWhen, revokeWidens } from '../gates/useMcpTools';
import type { AgentMcp, AgentMcpOverrideRow, McpGate } from '../gates/useMcpTools';

type Pending =
  | { kind: 'ungate' | 'revoke'; serverId: string; toolName: string }
  | null;

/**
 * An agent's MCP tool gates, beside its capability-gate overrides: for each
 * MCP server the agent holds a grant for, every tool with the gate that
 * applies and whether it comes from the default or from this agent's own
 * override. Setting an override to "ungated" goes through the acknowledgement
 * dialog, and so does a revoke that would WIDEN (this agent's "human approval"
 * override over an ungated default, held server or not); "human approval" and
 * any other revoke are one click. Revoked rows are never
 * deleted: they show only as a collapsed history line. Every write re-reads.
 */
export function AgentMcpPanel({ agentId, data, error, onSetOverride, onRevoke }: {
  agentId: string;
  data: AgentMcp | null;
  error: string;
  onSetOverride: (serverId: string, toolName: string, gate: McpGate) => Promise<void>;
  onRevoke: (serverId: string, toolName: string) => Promise<void>;
}) {
  const [actionError, setActionError] = useState('');
  const [pending, setPending] = useState<Pending>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  const act = async (fn: () => Promise<void>) => {
    setActionError('');
    try { await fn(); } catch (e: unknown) { setActionError(e instanceof Error ? e.message : String(e)); }
  };

  // Revoke = return the tool to its default for this agent: one click, unless
  // that is a widening (then the acknowledgement dialog does the revoke).
  const revoke = (o: { server_id: string; tool_name: string; gate: McpGate; default_gate: McpGate | null }) =>
    revokeWidens(o.gate, o.default_gate)
      ? setPending({ kind: 'revoke', serverId: o.server_id, toolName: o.tool_name })
      : act(() => onRevoke(o.server_id, o.tool_name));
  const activeRow = (serverId: string, toolName: string) =>
    (data?.overrides ?? []).find((o) => !o.revoked_at && o.server_id === serverId && o.tool_name === toolName);

  const held = new Set((data?.servers ?? []).map((s) => s.server_id));
  const revoked: AgentMcpOverrideRow[] = (data?.overrides ?? []).filter((o) => o.revoked_at);
  // A standing decision on a server this agent no longer holds: no effect now,
  // but it takes effect again the moment the grant does.
  const dormant = (data?.overrides ?? []).filter((o) => !o.revoked_at && !held.has(o.server_id));

  return (
    <div className="space-y-2 px-4" data-testid="agent-mcp-panel">
      {error && <p className="text-[0.733rem] text-destructive">{error}</p>}
      {actionError && <p className="text-[0.733rem] text-destructive" role="alert">{actionError}</p>}
      {data && data.servers.length === 0 && (
        <p className="text-[0.733rem] text-muted-foreground">
          This agent holds no MCP server grant, so MCP tool overrides do not apply to it.
        </p>
      )}
      {data?.servers.map((s) => (
        <div key={s.server_id} className="rounded-lg border border-border/40 p-2 text-[0.7rem]">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="font-medium">{s.server_id}</span>
            <span className="cockpit-badge">{s.status}</span>
            {s.retired_at && <span className="cockpit-badge">retired</span>}
          </div>
          {s.tools.length === 0 && (
            <p className="mt-1.5 text-muted-foreground">No tools discovered for this server.</p>
          )}
          {s.tools.map((t) => (
            <div key={t.tool_name} className="mt-2 border-t border-border/30 pt-1.5">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="font-medium">{t.tool_name}</span>
                <span className="cockpit-badge">{t.effective_gate}</span>
                <span className="text-muted-foreground">
                  {t.source === 'override' ? "from this agent's override" : 'from the default'}
                </span>
              </div>
              {t.description && <div className="mt-0.5 text-muted-foreground">{t.description}</div>}
              <div className="mt-1.5 flex flex-wrap items-center gap-2">
                {t.effective_gate !== 'ungated' && (
                  <Button
                    size="sm" variant="outline"
                    aria-label={`Set ${t.tool_name} ungated for ${agentId}`}
                    onClick={() => setPending({ kind: 'ungate', serverId: s.server_id, toolName: t.tool_name })}
                  >
                    Set ungated…
                  </Button>
                )}
                {t.effective_gate !== 'human approval' && (
                  <Button
                    size="sm" variant="outline"
                    aria-label={`Require approval for ${t.tool_name} for ${agentId}`}
                    onClick={() => act(() => onSetOverride(s.server_id, t.tool_name, 'human approval'))}
                  >
                    Require approval
                  </Button>
                )}
                {t.source === 'override' && (
                  <Button
                    size="sm" variant="outline"
                    aria-label={`Revoke override on ${t.tool_name}`}
                    onClick={() => {
                      const row = activeRow(s.server_id, t.tool_name);
                      return row ? revoke(row) : act(() => onRevoke(s.server_id, t.tool_name));
                    }}
                  >
                    Revoke override
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      ))}
      {dormant.length > 0 && (
        <div className="text-[0.7rem]">
          <div className="pb-1 text-muted-foreground">
            Overrides on servers this agent holds no grant for — no effect until it is granted one:
          </div>
          {dormant.map((o) => (
            <div key={`${o.server_id}/${o.tool_name}`} className="flex min-w-0 flex-wrap items-center gap-2 py-0.5">
              <span className="cockpit-badge">{o.server_id} / {o.tool_name}</span>
              <span className="cockpit-badge">{o.gate}</span>
              <Button
                size="sm" variant="outline"
                aria-label={`Revoke override on ${o.server_id} / ${o.tool_name}`}
                onClick={() => revoke(o)}
              >
                Revoke override
              </Button>
            </div>
          ))}
        </div>
      )}
      {revoked.length > 0 && (
        <div className="pt-1">
          <button
            className="text-[0.667rem] uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground"
            aria-expanded={historyOpen}
            onClick={() => setHistoryOpen((v) => !v)}
          >
            {revoked.length} revoked override{revoked.length === 1 ? '' : 's'} — kept on the record
          </button>
          {historyOpen && revoked.map((o) => (
            <div
              key={`${o.server_id}/${o.tool_name}/${o.granted_at}`}
              className="flex min-w-0 flex-wrap items-center gap-2 py-0.5 text-[0.7rem] text-muted-foreground"
            >
              <span className="cockpit-badge line-through">{o.server_id} / {o.tool_name}</span>
              <span className="cockpit-badge">{o.gate}</span>
              <span>granted {fmtWhen(o.granted_at)} · revoked {fmtWhen(o.revoked_at)}</span>
            </div>
          ))}
        </div>
      )}
      <UngateDialog
        open={pending !== null}
        onOpenChange={(v) => { if (!v) setPending(null); }}
        serverId={pending?.serverId ?? ''}
        toolName={pending?.toolName ?? ''}
        scope={pending?.kind === 'revoke' ? 'revoke' : 'override'}
        agents={[agentId]}
        onConfirm={() => {
          if (!pending) return Promise.resolve();
          return pending.kind === 'revoke'
            ? onRevoke(pending.serverId, pending.toolName)
            : onSetOverride(pending.serverId, pending.toolName, 'ungated');
        }}
      />
    </div>
  );
}
