import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { UngateDialog } from './UngateDialog';
import { fmtWhen, revokeWidens, useMcpServers } from './useMcpTools';
import type { McpServer, McpTool } from './useMcpTools';

type Pending =
  | { kind: 'default'; server: McpServer; tool: McpTool }
  | { kind: 'revoke'; server: McpServer; tool: McpTool; agentId: string }
  | null;

/**
 * Every registered MCP server and its tools: the DEFAULT gate (the fallback
 * every agent gets unless it holds its own override) with a control to change
 * it, and under each tool the active per-agent overrides with one-click
 * Revoke — the facts-and-revoke pattern CapabilitiesView uses. Making a
 * default ungated goes through the acknowledgement dialog, and so does a
 * revoke that would WIDEN (an agent's "human approval" override over an
 * ungated default); narrowing a default back and any other revoke are one
 * click. Every write re-reads from the server.
 */
export function McpToolsView() {
  const { servers, loading, error, setDefaultGate, revokeOverride } = useMcpServers();
  const [actionError, setActionError] = useState('');
  const [pending, setPending] = useState<Pending>(null);

  const act = async (fn: () => Promise<void>) => {
    setActionError('');
    try { await fn(); } catch (e: unknown) { setActionError(e instanceof Error ? e.message : String(e)); }
  };

  // A default change reaches every holder without an override of its own on this tool.
  const affectedBy = (p: NonNullable<Pending>) => {
    if (p.kind === 'revoke') return [p.agentId];
    const own = new Set(p.tool.overrides.map((o) => o.agent_id));
    return p.server.holders.filter((a) => !own.has(a));
  };

  return (
    <div className="space-y-2 p-3" data-testid="mcp-tools-view">
      {error && <p className="text-[0.667rem] text-red">{error}</p>}
      {actionError && <p className="text-[0.667rem] text-red" role="alert">{actionError}</p>}
      {servers.length === 0 && !error && !loading && (
        <p className="text-[0.7rem] text-muted-foreground">
          MCP servers appear here once one is registered through the MCP pipeline; nothing is
          gated differently until then.
        </p>
      )}
      {servers.map((s) => (
        <div key={s.id} className="rounded-lg border border-border/40 p-2 text-[0.7rem]">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="font-medium">{s.id}</span>
            <span className="cockpit-badge">{s.status}</span>
            {s.retired_at && <span className="cockpit-badge">retired</span>}
            <span className="text-muted-foreground">
              held by {s.holders.length} agent{s.holders.length === 1 ? '' : 's'}
            </span>
          </div>
          {s.tools.length === 0 && (
            <p className="mt-1.5 text-muted-foreground">No tools discovered for this server.</p>
          )}
          {s.tools.map((t) => (
            <div key={t.tool_name} className="mt-2 border-t border-border/30 pt-1.5">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="font-medium">{t.tool_name}</span>
                <span className="cockpit-badge">default: {t.default_gate}</span>
                {t.default_gate === 'ungated' ? (
                  <Button
                    size="sm" variant="outline"
                    aria-label={`Require approval by default for ${t.tool_name}`}
                    onClick={() => act(() => setDefaultGate(s.id, t.tool_name, 'human approval'))}
                  >
                    Require approval
                  </Button>
                ) : (
                  <Button
                    size="sm" variant="outline"
                    aria-label={`Make ${t.tool_name} ungated by default`}
                    onClick={() => setPending({ kind: 'default', server: s, tool: t })}
                  >
                    Make ungated…
                  </Button>
                )}
              </div>
              {t.description && <div className="mt-0.5 text-muted-foreground">{t.description}</div>}
              {t.overrides.length === 0 ? (
                <p className="mt-1 text-muted-foreground">No agent overrides.</p>
              ) : (
                <div className="mt-1 space-y-1">
                  {t.overrides.map((o) => (
                    <div key={o.agent_id} className="flex min-w-0 flex-wrap items-center gap-2">
                      <span className="cockpit-badge">{o.agent_id}</span>
                      <span className="cockpit-badge">{o.gate}</span>
                      <span className="text-muted-foreground">since {fmtWhen(o.granted_at)}</span>
                      <Button
                        size="sm" variant="outline"
                        aria-label={`Revoke ${o.agent_id} override on ${t.tool_name}`}
                        onClick={() => revokeWidens(o.gate, t.default_gate)
                          ? setPending({ kind: 'revoke', server: s, tool: t, agentId: o.agent_id })
                          : act(() => revokeOverride(o.agent_id, s.id, t.tool_name))}
                      >
                        Revoke
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      ))}
      <UngateDialog
        open={pending !== null}
        onOpenChange={(v) => { if (!v) setPending(null); }}
        serverId={pending?.server.id ?? ''}
        toolName={pending?.tool.tool_name ?? ''}
        scope={pending?.kind === 'revoke' ? 'revoke' : 'default'}
        agents={pending ? affectedBy(pending) : []}
        onConfirm={() => {
          if (!pending) return Promise.resolve();
          return pending.kind === 'revoke'
            ? revokeOverride(pending.agentId, pending.server.id, pending.tool.tool_name)
            : setDefaultGate(pending.server.id, pending.tool.tool_name, 'ungated');
        }}
      />
    </div>
  );
}
