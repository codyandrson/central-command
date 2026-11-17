/**
 * useAgentLearned — the data behind the workspace Memory tab ("what this
 * agent has learned"): four independent reads, so one failing leaves the other
 * sections on screen.
 *
 *   notes      GET /api/cc/notes/:agent       the note store (DL-217)
 *   learned    GET /api/cc/learned/:agent     approved skill documents it
 *                                              proposed + its pending proposals
 *   episodes   GET /api/graph/episodes        its private graph partition
 *   charter    GET /api/cc/charter/:agent     the governed charter history
 *
 * Every read is a read. Nothing here writes: a note, a skill document, a
 * graph episode and a charter all change only through a proposal the operator
 * decides in the Decisions Inbox, so the tab has nothing to mutate.
 *
 * The cockpit hand-declares these shapes over untyped JSON (a field the
 * backend never sends is invisible to `tsc`); the backend tests that pin the
 * wire are `tests/test_agent_notes.py` (notes) and
 * `tests/test_agent_learned.py` (learned).
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useGateway } from '@/contexts/GatewayContext';
import { apiFetch } from '@/lib/apiFetch';
import { isPlaceholderWorkspaceAgent } from '../workspaceScope';

export interface AgentNote {
  id: string;
  /** The DRAFTER (for an operator-scope note, the agent that proposed it). */
  agent_id: string;
  scope: 'private' | 'operator';
  text: string;
  origin: string;
  proposal_id: string | null;
  source_session_id: string | null;
  source_quote: string | null;
  created_at: string;
  retired_at: string | null;
  retired_by: string | null;
}

export interface NotesPayload {
  agent_id: string;
  /** False for the auditors: they are shown no notes. */
  rendered: boolean;
  private: AgentNote[];
  operator: AgentNote[];
  budgets: { private: number; operator: number };
  usage: { private: number; operator: number };
}

export interface LearnedSkillDoc {
  id: string;
  skill_id: string;
  skill_title: string;
  skill_status: string;
  doc_key: string;
  kind: 'guidance' | 'reference';
  title: string;
  version: number;
  is_current: boolean;
  describes: string | null;
  created_at: string;
  retired_at: string | null;
}

export interface PendingAction {
  /** Base capability, no `@version`. */
  capability: string;
  label: string;
}

export interface PendingProposal {
  id: string;
  session_id: string;
  intent: string;
  status: string;
  created_at: string;
  actions: PendingAction[];
}

export interface LearnedPayload {
  agent_id: string;
  skill_docs: LearnedSkillDoc[];
  pending: PendingProposal[];
}

export interface GraphEpisode {
  uuid: string;
  name: string;
  content: string;
  created_at: string | null;
  source_description?: string;
}

export interface EpisodesPayload {
  episodes: GraphEpisode[];
  total: number;
  has_more: boolean;
}

export interface CharterVersionRow {
  version: number;
  status?: string;
  rationale: string | null;
  created_by: string;
  created_at: string | null;
  proposal_id?: string | null;
}

export interface CharterHistory {
  current: CharterVersionRow;
  versions: CharterVersionRow[];
}

export interface Section<T> {
  data: T | null;
  /** True until the first answer for this agent arrives. */
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

/** One push burst triggers one silent refresh. */
const FOLLOW_DEBOUNCE_MS = 500;

interface SectionState<T> {
  key: string | null;
  data: T | null;
  error: string | null;
  loading: boolean;
}

/**
 * One read, keyed by `key` (the agent): a new key drops the previous agent's
 * data at once (never shown under the wrong name) while a changed `url` for
 * the SAME key (a wider episode page) keeps what is on screen until the new
 * answer lands. A late answer for an old request is ignored. `follow` lists
 * the gateway event prefixes (`cc.<kind>`) that make the section refetch
 * silently, debounced. A `null` url means "no real agent yet" (see
 * `isPlaceholderWorkspaceAgent`): nothing is requested and the section stays in
 * its loading state until a real agent id arrives.
 */
function useSection<T>(key: string, url: string | null, follow: string[]): Section<T> {
  const { subscribe } = useGateway();
  const [state, setState] = useState<SectionState<T>>({ key: null, data: null, error: null, loading: true });
  const seq = useRef(0);
  const followKey = follow.join('|');

  const load = useCallback(async (silent: boolean) => {
    if (url === null) return;
    const id = ++seq.current;
    if (!silent) {
      setState((s) => (s.key === key ? { ...s, loading: true } : { key, data: null, error: null, loading: true }));
    }
    try {
      const body = await apiFetch<{ result?: T }>(url, undefined, 'Failed to load');
      if (id !== seq.current) return;
      if (!body.result) throw new Error('Failed to load');
      setState({ key, data: body.result, error: null, loading: false });
    } catch (e: unknown) {
      if (id !== seq.current) return;
      const message = e instanceof Error ? e.message : String(e);
      setState((s) => ({ key, data: s.key === key ? s.data : null, error: message, loading: false }));
    }
  }, [key, url]);

  useEffect(() => { void load(false); }, [load]);

  useEffect(() => {
    if (typeof subscribe !== 'function') return;
    const prefixes = followKey.split('|').filter(Boolean);
    let timer: ReturnType<typeof setTimeout> | null = null;
    const unsubscribe = subscribe((msg) => {
      const kind = msg.event || '';
      if (!prefixes.some((p) => kind.startsWith(p))) return;
      if (timer) return;
      timer = setTimeout(() => { timer = null; void load(true); }, FOLLOW_DEBOUNCE_MS);
    });
    return () => { unsubscribe(); if (timer) clearTimeout(timer); };
  }, [subscribe, followKey, load]);

  const refresh = useCallback(() => { void load(false); }, [load]);
  const same = state.key === key;
  return {
    data: same ? state.data : null,
    loading: !same || state.loading,
    error: same ? state.error : null,
    refresh,
  };
}

const enc = encodeURIComponent;

/** The agent-scoped URL, or null while the id is only the "no session yet" placeholder. */
function agentUrl(agentId: string, path: string): string | null {
  return isPlaceholderWorkspaceAgent(agentId) ? null : path;
}

export function useNotes(agentId: string): Section<NotesPayload> {
  return useSection<NotesPayload>(agentId, agentUrl(agentId, `/api/cc/notes/${enc(agentId)}`), ['cc.note.']);
}

/** Skill documents the agent proposed and its pending proposals. */
export function useLearned(agentId: string): Section<LearnedPayload> {
  return useSection<LearnedPayload>(
    agentId, agentUrl(agentId, `/api/cc/learned/${enc(agentId)}`), ['cc.proposal.', 'cc.skill.'],
  );
}

export function useCharterHistory(agentId: string): Section<CharterHistory> {
  return useSection<CharterHistory>(agentId, agentUrl(agentId, `/api/cc/charter/${enc(agentId)}`), ['cc.charter.']);
}

export const EPISODE_PAGE = 20;

/** The agent's PRIVATE graph partition, newest first, a page at a time —
 *  `total` is the real count, so the tab never silently truncates. */
export function useEpisodes(agentId: string): Section<EpisodesPayload> & { loadMore: () => void } {
  const [wide, setWide] = useState<{ key: string; limit: number }>({ key: agentId, limit: EPISODE_PAGE });
  const limit = wide.key === agentId ? wide.limit : EPISODE_PAGE;
  const params = new URLSearchParams({ agent_id: agentId, scope: 'private', limit: String(limit) });
  const section = useSection<EpisodesPayload>(
    agentId, agentUrl(agentId, `/api/graph/episodes?${params.toString()}`), ['cc.proposal.', 'cc.graph.'],
  );
  const loadMore = useCallback(() => {
    setWide((w) => ({ key: agentId, limit: (w.key === agentId ? w.limit : EPISODE_PAGE) * 2 }));
  }, [agentId]);
  return { ...section, loadMore };
}
