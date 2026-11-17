import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '@/lib/apiFetch';

/**
 * The operator's MCP tool gates, over the `/api/cc/mcp*` proxy (cc-mcp.ts).
 * Hand-declared interfaces: the wire shape is pinned by
 * tests/test_mcp_grants.py (`test_agent_mcp_overrides_route_wire_shape_…`,
 * `test_list_mcp_servers_route_adds_active_overrides_and_holders_…`).
 *
 * No optimistic state: every write re-reads from the server before the
 * promise resolves, and a server refusal (404/409/422) reaches the caller as
 * the thrown Error's message, to be shown inline.
 */

export type McpGate = 'human approval' | 'ungated';

export const UNGATED_MEANING =
  'Ungated means the agent calls this tool directly, with no proposal and no approval.';

export interface McpToolOverride {
  agent_id: string;
  gate: McpGate;
  granted_at: string | null;
}

export interface McpTool {
  server_id: string;
  tool_name: string;
  default_gate: McpGate;
  description: string;
  overrides: McpToolOverride[];
}

export interface McpServer {
  id: string;
  status: string;
  retired_at: string | null;
  tools: McpTool[];
  /** Agents holding an unrevoked `mcp:<id>` grant. */
  holders: string[];
}

export interface AgentMcpOverrideRow {
  agent_id: string;
  server_id: string;
  tool_name: string;
  gate: McpGate;
  granted_by: string;
  granted_at: string | null;
  revoked_at: string | null;
  /** The tool's current default gate (null if the tool is unknown). */
  default_gate: McpGate | null;
}

/**
 * Revoking an override returns the tool to its default for that agent, so a
 * revoke WIDENS exactly when the override says "human approval" over an
 * "ungated" default. Such a revoke takes the same acknowledgement as any
 * other move to ungated.
 */
export const revokeWidens = (overrideGate: McpGate, defaultGate: McpGate | null | undefined) =>
  overrideGate === 'human approval' && defaultGate === 'ungated';

export interface AgentMcpTool {
  tool_name: string;
  description: string;
  default_gate: McpGate;
  effective_gate: McpGate;
  source: 'default' | 'override';
}

export interface AgentMcpServer {
  server_id: string;
  status: string;
  retired_at: string | null;
  tools: AgentMcpTool[];
}

export interface AgentMcp {
  agent_id: string;
  mcp_grants: string[];
  overrides: AgentMcpOverrideRow[];
  servers: AgentMcpServer[];
}

const J = { 'content-type': 'application/json' };
const enc = encodeURIComponent;
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

async function result<T>(url: string, init?: RequestInit): Promise<T> {
  const body = await apiFetch<{ ok: boolean; result: T }>(url, init);
  return body.result;
}

/** Every registered server with its tools, default gates, active overrides and holders. */
export function useMcpServers() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await result<{ servers: McpServer[] }>('/api/cc/mcp/servers');
      setServers(res.servers ?? []);
      setError('');
    } catch (e: unknown) {
      setError(errText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const setDefaultGate = useCallback(async (serverId: string, toolName: string, gate: McpGate) => {
    await result(`/api/cc/mcp/servers/${enc(serverId)}/tools/${enc(toolName)}`, {
      method: 'PATCH', headers: J, body: JSON.stringify({ default_gate: gate }),
    });
    await refresh();
  }, [refresh]);

  const revokeOverride = useCallback(async (agentId: string, serverId: string, toolName: string) => {
    await result(
      `/api/cc/mcp-overrides/${enc(agentId)}?server_id=${enc(serverId)}&tool_name=${enc(toolName)}`,
      { method: 'DELETE' },
    );
    await refresh();
  }, [refresh]);

  return { servers, loading, error, refresh, setDefaultGate, revokeOverride };
}

/** One agent's MCP tool gates: the servers it holds, effective gates and override history. */
export function useAgentMcp(agentId: string | undefined) {
  const [data, setData] = useState<AgentMcp | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    if (!agentId) { setData(null); return; }
    setLoading(true);
    try {
      setData(await result<AgentMcp>(`/api/cc/mcp-overrides/${enc(agentId)}`));
      setError('');
    } catch (e: unknown) {
      setError(errText(e));
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => { setData(null); void refresh(); }, [refresh]);

  const setOverride = useCallback(async (
    forAgent: string, serverId: string, toolName: string, gate: McpGate,
  ) => {
    await result(`/api/cc/mcp-overrides/${enc(forAgent)}`, {
      method: 'PUT', headers: J,
      body: JSON.stringify({ server_id: serverId, tool_name: toolName, gate }),
    });
    await refresh();
  }, [refresh]);

  const revokeOverride = useCallback(async (forAgent: string, serverId: string, toolName: string) => {
    await result(
      `/api/cc/mcp-overrides/${enc(forAgent)}?server_id=${enc(serverId)}&tool_name=${enc(toolName)}`,
      { method: 'DELETE' },
    );
    await refresh();
  }, [refresh]);

  return { data, loading, error, refresh, setOverride, revokeOverride };
}

export function fmtWhen(iso: string | null | undefined) {
  if (!iso) return '—';
  return new Date(iso).toISOString().slice(0, 16).replace('T', ' ');
}
