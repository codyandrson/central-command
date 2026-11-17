import { getRootAgentId, getRootAgentSessionKey } from '@/features/sessions/sessionKeys';

const DEFAULT_WORKSPACE_AGENT_ID = 'main';
const DEFAULT_WORKSPACE_ROOT_SESSION_KEY = 'agent:main:main';

// Real Central Command conversation lanes (`agent:<id>:sess_<hex>`) are unknown to
// getRootAgentId on purpose — widening it would change session-tree nesting
// (see the CC_SESSION_RE note in sessionKeys.ts). The workspace pane only
// needs the owning agent, so parse the lane key here.
const AGENT_LANE_RE = /^agent:([^:]+):sess_[a-f0-9]+$/;

function laneAgentId(sessionKey: string): string | null {
  return sessionKey.match(AGENT_LANE_RE)?.[1] ?? null;
}

/**
 * True for the id `getWorkspaceAgentId` falls back to when no session is
 * selected yet (the upstream placeholder behind the `agent:main:main` alias).
 * It is not a Central Command agent, so a panel that reads agent-scoped data
 * (notes, learned, charter, graph episodes) must wait for a real id instead of
 * asking the API about it and showing the 404.
 */
export function isPlaceholderWorkspaceAgent(agentId: string): boolean {
  const id = agentId.trim();
  return id === '' || id === DEFAULT_WORKSPACE_AGENT_ID;
}

export function getWorkspaceAgentId(sessionKey: string): string {
  return getRootAgentId(sessionKey) ?? laneAgentId(sessionKey) ?? DEFAULT_WORKSPACE_AGENT_ID;
}

export function getWorkspaceRootSessionKey(sessionKey: string): string {
  const laneId = laneAgentId(sessionKey);
  return getRootAgentSessionKey(sessionKey)
    ?? (laneId ? `agent:${laneId}:main` : DEFAULT_WORKSPACE_ROOT_SESSION_KEY);
}

export function getWorkspaceStorageKey(kind: string, agentId: string): string {
  const scopedAgentId = agentId.trim() || DEFAULT_WORKSPACE_AGENT_ID;
  return `nerve:workspace:${scopedAgentId}:${kind}`;
}
