import { useState, useEffect, useCallback, useRef } from 'react';
import { useGateway } from '@/contexts/GatewayContext';
import type { AgentDetail, AgentRow } from './types';
import type { ProposalDetail } from '../decisions/types';

const POLL_MS = 60_000;

/** Events that can change what this page shows. Anything else is noise here. */
const AGENT_EVENT_PREFIXES = [
  'cc.agent', 'cc.charter', 'cc.proposal', 'cc.session',
  // `cc.skill` covers skill.granted/revoked (the Skills panel's own writes)
  // AND skill.retired/reactivated, which change whether a grant this page
  // already shows is still delivered without any grant row changing.
  'cc.skill',
];

/**
 * Data for the Agents page.
 *
 * Ordering is FIXED — ACTIVE before RETIRED, then by id — and recomputed only
 * from the payload, never from arrival order. The roster must not reshuffle
 * under the operator while they are reading an agent's record (the cockpit's
 * no-displacement rule); a refresh may change a row's contents, never its seat.
 * This page is the ROSTER, so it stays scannable by name; "who is working" is
 * the AGENTS sidebar's job, and the `N active` badge answers it here without
 * moving anything (The operator, 2026-08-19).
 *
 * The 60s poll is slower than the inbox's 30s on purpose: a roster changes when
 * the operator changes it, so pushes carry the load and the poll is a backstop.
 */
export function useAgents() {
  const { rpc, connectionState, subscribe } = useGateway();
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState('');
  const rpcRef = useRef(rpc);
  useEffect(() => { rpcRef.current = rpc; }, [rpc]);

  const refresh = useCallback(async ({ silent = false }: { silent?: boolean } = {}) => {
    if (!silent) setLoading(true);
    try {
      const res = await rpcRef.current('agents.roster') as { agents: AgentRow[] };
      const rows = [...(res.agents ?? [])].sort((a, b) => {
        const aRetired = a.status === 'RETIRED' ? 1 : 0;
        const bRetired = b.status === 'RETIRED' ? 1 : 0;
        return aRetired - bRetired || a.id.localeCompare(b.id);
      });
      setAgents(rows);
      setError('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (agentId: string, { silent = false } = {}) => {
    if (!agentId) { setDetail(null); return; }
    if (!silent) setDetailLoading(true);
    try {
      const res = await rpcRef.current('agents.detail', { agentId }) as AgentDetail;
      setDetail(res);
      setError('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (connectionState !== 'connected') return;
    refresh();
    const iv = setInterval(() => {
      if (document.visibilityState === 'visible') refresh({ silent: true });
    }, POLL_MS);
    return () => clearInterval(iv);
  }, [connectionState, refresh]);

  // Live-follow, debounced: hiring or coaching an agent emits several events
  // back to back, and one silent refresh covers the burst.
  useEffect(() => {
    if (connectionState !== 'connected') return;
    let t: ReturnType<typeof setTimeout> | null = null;
    const unsub = subscribe((msg) => {
      const evt = msg.event || '';
      if (!AGENT_EVENT_PREFIXES.some((p) => evt.startsWith(p))) return;
      if (t) return;  // one refresh per burst, not one per event
      t = setTimeout(() => { t = null; refresh({ silent: true }); }, 400);
    });
    return () => { unsub(); if (t) clearTimeout(t); };
  }, [connectionState, refresh, subscribe]);

  /**
   * Every mutation below is a DIRECT operator action with an event record, not
   * a proposal — the approval gate governs what agents want to do, while the
   * operator governs the team directly (D24/D25).
   *
   * Each one refreshes the roster AND the open profile, because they all move
   * both: a grant changes the pack chips and the detail's capability list, a
   * charter save changes the version shown in the list row.
   */
  const after = useCallback(async (agentId: string) => {
    await Promise.all([refresh({ silent: true }), loadDetail(agentId, { silent: true })]);
  }, [refresh, loadDetail]);

  const hire = useCallback(async (
    body: { name: string; mission: string; template: string; packs?: string[] },
  ) => {
    const res = await rpcRef.current('agents.hire', body) as { agent?: { id: string } };
    await refresh({ silent: true });
    return res;
  }, [refresh]);

  const retire = useCallback(async (agentId: string, reason: string) => {
    await rpcRef.current('agents.retire', { agentId, reason });
    await after(agentId);
  }, [after]);

  const reactivate = useCallback(async (agentId: string) => {
    await rpcRef.current('agents.reactivate', { agentId });
    await after(agentId);
  }, [after]);

  /** Sets (or clears, with null) an agent's model/thinking override — a
   *  direct operator action, not a proposal, same as grant/revoke. */
  const setModel = useCallback(async (
    agentId: string, modelId: string | null, thinking: string | null,
  ) => {
    await rpcRef.current('agents.setModel', { id: agentId, model: modelId, thinking });
    await after(agentId);
  }, [after]);

  const grant = useCallback(async (agentId: string, pack: string) => {
    await rpcRef.current('agents.grant', { agentId, pack });
    await after(agentId);
  }, [after]);

  const revoke = useCallback(async (agentId: string, pack: string, reason: string) => {
    await rpcRef.current('agents.revoke', { agentId, pack, reason });
    await after(agentId);
  }, [after]);

  /**
   * Skill grants go through `skills.grant` / `skills.revoke` — the SAME
   * handlers the Skills page calls, addressed by `agent_id`. One relation, one
   * write path, both directions: a grant made here shows up in the Skills
   * page's holder list and its `holder_count`, and vice versa. Do not add an
   * agent-side gateway method for this; there would then be two.
   */
  const grantSkill = useCallback(async (agentId: string, skillId: string) => {
    await rpcRef.current('skills.grant', { agent_id: agentId, skill_id: skillId });
    await after(agentId);
  }, [after]);

  const revokeSkill = useCallback(async (
    agentId: string, skillId: string, reason: string,
  ) => {
    await rpcRef.current('skills.revoke', {
      agent_id: agentId, skill_id: skillId, reason,
    });
    await after(agentId);
  }, [after]);

  /** Returns the save result so the caller can surface `warnings` — a charter
   *  naming a capability the registry lacks must not save silently. */
  const saveCharter = useCallback(async (
    agentId: string, content: string, rationale: string,
  ) => {
    const res = await rpcRef.current('agents.charter.save', {
      agentId, content, rationale,
    }) as { version: number; warnings?: unknown[] };
    await after(agentId);
    return res;
  }, [after]);

  /**
   * D5 — the one action here that is NOT a direct operator action: it starts a
   * real agent run whose output is a proposal for the Decisions Inbox.
   *
   * The run is slow (minutes), so the backend launches it behind a TASK record
   * and this returns as soon as it is IN_PROGRESS. What comes back is the task
   * to watch, never the run's outcome — which lands on the task and, for a
   * drafted edit, in the Decisions Inbox.
   */
  const coach = useCallback(async (
    agentId: string, signalIds: number[], note: string,
  ) => {
    const res = await rpcRef.current('agents.coach', {
      agentId, signalIds, note: note || null,
    }) as { task_id: string; target?: string };
    await after(agentId);
    return res;
  }, [after]);

  const revertCharter = useCallback(async (agentId: string, version: number) => {
    const res = await rpcRef.current('agents.charter.revert', { agentId, version });
    await after(agentId);
    return res as { version: number; restored: number };
  }, [after]);

  // Same RPC the Decisions Inbox uses (proposals.get is generic, not scoped
  // to that feature) — lets a charter version link back to the proposal that
  // produced it without duplicating decisions' fetch logic.
  const getProposal = useCallback(
    (id: string) => rpcRef.current('proposals.get', { id }) as Promise<ProposalDetail>,
    [],
  );

  return {
    agents, detail, loading, detailLoading, error, refresh, loadDetail,
    hire, retire, reactivate, grant, revoke, grantSkill, revokeSkill,
    saveCharter, revertCharter, coach, getProposal, setModel,
  };
}
