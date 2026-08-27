import { useState, useEffect, useCallback, useRef } from 'react';
import { useGateway } from '@/contexts/GatewayContext';
import { apiFetch } from '@/lib/apiFetch';
import type { Gap, ImportResult, SiteImportResult, SkillDetail, SkillDoc, SkillRow } from './types';

const POLL_MS = 60_000;
const GAP_LIMIT = 100;

/**
 * Events that can change what this page shows.
 *
 * The gateway forwards every durable-log event as `cc.<kind>`
 * (`nerve_gateway._forward_events`), so the backend's `skill.granted` and
 * `gap.declared` arrive here as `cc.skill.granted` and `cc.gap.declared`.
 * Anything else is noise on this page.
 */
const SKILL_EVENT_PREFIXES = ['cc.skill', 'cc.gap'];

/** Agent identity as this page needs it — enough to offer a grant picker. */
export interface RosterEntry {
  id: string;
  name: string;
  status: string;
}

/**
 * Data for the Skills page.
 *
 * Ordering is FIXED — ACTIVE before RETIRED, then by id — and recomputed only
 * from the payload, never from arrival order, so the catalog cannot reshuffle
 * under the operator while they read a skill (the cockpit's no-displacement
 * rule). `skills.list` returns ACTIVE skills only today, so the status leg of
 * that comparison is live: `skills.list` returns retired skills too (the route
 * defaults `include_retired` on, as the roster does), because a retired skill
 * with no row to click cannot be reactivated. The gap queue keeps the
 * order the API returns it in (newest first); this hook never re-sorts it.
 *
 * The 60s poll matches the Agents page and for the same reason: the library
 * changes when the operator changes it, so pushes carry the load and the poll
 * is a backstop. Declared gaps arrive from agent runs, and those push too.
 */
export function useSkills() {
  const { rpc, connectionState, subscribe } = useGateway();
  const [skills, setSkills] = useState<SkillRow[]>([]);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [gaps, setGaps] = useState<Gap[]>([]);
  const [roster, setRoster] = useState<RosterEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState('');
  const rpcRef = useRef(rpc);
  useEffect(() => { rpcRef.current = rpc; }, [rpc]);

  const refresh = useCallback(async ({ silent = false }: { silent?: boolean } = {}) => {
    if (!silent) setLoading(true);
    try {
      const [cat, gapRes, rosterRes] = await Promise.all([
        rpcRef.current('skills.list') as Promise<{ skills: SkillRow[] }>,
        rpcRef.current('gaps.list', { limit: GAP_LIMIT }) as Promise<{ gaps: Gap[] }>,
        rpcRef.current('agents.roster') as Promise<{ agents: RosterEntry[] }>,
      ]);
      const rows = [...(cat.skills ?? [])].sort((a, b) => {
        const aRetired = a.status === 'RETIRED' ? 1 : 0;
        const bRetired = b.status === 'RETIRED' ? 1 : 0;
        return aRetired - bRetired || a.id.localeCompare(b.id);
      });
      setSkills(rows);
      // Newest-first is the API's order (`recent_gaps` reads the event log
      // descending). Kept as delivered — re-sorting here would be a second,
      // divergent opinion about an order the record already has.
      setGaps(gapRes.gaps ?? []);
      setRoster([...(rosterRes.agents ?? [])].sort((a, b) => a.id.localeCompare(b.id)));
      setError('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (skillId: string, { silent = false } = {}) => {
    if (!skillId) { setDetail(null); return; }
    if (!silent) setDetailLoading(true);
    try {
      const res = await rpcRef.current('skills.detail', { skill_id: skillId }) as SkillDetail;
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

  // Live-follow, debounced: one import emits a burst of doc events, and one
  // silent refresh covers the burst.
  useEffect(() => {
    if (connectionState !== 'connected') return;
    let t: ReturnType<typeof setTimeout> | null = null;
    const unsub = subscribe((msg) => {
      const evt = msg.event || '';
      if (!SKILL_EVENT_PREFIXES.some((p) => evt.startsWith(p))) return;
      if (t) return;  // one refresh per burst, not one per event
      t = setTimeout(() => { t = null; refresh({ silent: true }); }, 400);
    });
    return () => { unsub(); if (t) clearTimeout(t); };
  }, [connectionState, refresh, subscribe]);

  /**
   * Every mutation below is a DIRECT operator action with an event record, not
   * a proposal: the library is internal state, not a world write. Agents never
   * write here — they declare gaps, which is what the gap queue shows.
   *
   * Each one reloads the catalog AND the open skill, so the screen cannot
   * display a grant or a document version it did not just make.
   */
  const after = useCallback(async (skillId: string) => {
    await Promise.all([refresh({ silent: true }), loadDetail(skillId, { silent: true })]);
  }, [refresh, loadDetail]);

  const createSkill = useCallback(async (
    body: { id: string; title: string; summary: string },
  ) => {
    await rpcRef.current('skills.create', body);
    await refresh({ silent: true });
  }, [refresh]);

  const addDoc = useCallback(async (
    skillId: string,
    body: {
      doc_key: string; kind: string; title: string; content: string;
      source_url?: string | null; describes?: string | null;
    },
  ) => {
    const doc = await rpcRef.current('skills.add_doc', {
      skill_id: skillId, ...body,
    }) as SkillDoc;
    await after(skillId);
    return doc;
  }, [after]);

  /** End one reference doc: it leaves delivery and search for NEW sessions,
   *  while in-flight sessions keep what they loaded and every version stays
   *  readable in history. Guidance is refused server-side — supersede it or
   *  retire the whole skill. */
  const retireDoc = useCallback(async (skillId: string, docKey: string, reason: string) => {
    await rpcRef.current('skills.retire_doc', {
      skill_id: skillId, doc_key: docKey, reason,
    });
    await after(skillId);
  }, [after]);

  const retireSkill = useCallback(async (skillId: string, reason: string) => {
    await rpcRef.current('skills.retire', { skill_id: skillId, reason });
    await after(skillId);
  }, [after]);

  /** Bring a retired skill back. Every holder it had returns with it — the
   *  grants were never touched by the retirement — so there is nothing to
   *  re-grant afterwards, and the detail refresh below is what shows that. */
  const reactivateSkill = useCallback(async (skillId: string) => {
    await rpcRef.current('skills.reactivate', { skill_id: skillId });
    await after(skillId);
  }, [after]);

  const grant = useCallback(async (agentId: string, skillId: string) => {
    await rpcRef.current('skills.grant', { agent_id: agentId, skill_id: skillId });
    await after(skillId);
  }, [after]);

  const revoke = useCallback(async (agentId: string, skillId: string, reason: string) => {
    await rpcRef.current('skills.revoke', {
      agent_id: agentId, skill_id: skillId, reason,
    });
    await after(skillId);
  }, [after]);

  /** Import a SKILL.md + references/ folder that already exists on the API
   *  host's filesystem. Returns which skill it wrote into so the caller can
   *  select it. Optional fields are omitted rather than sent empty. */
  const importFolder = useCallback(async (
    body: { path: string; skill_id?: string; source_url?: string; describes?: string },
  ) => {
    const params: Record<string, unknown> = { path: body.path };
    if (body.skill_id) params.skill_id = body.skill_id;
    if (body.source_url) params.source_url = body.source_url;
    if (body.describes) params.describes = body.describes;
    const res = await rpcRef.current('skills.import', params) as ImportResult;
    await Promise.all([
      refresh({ silent: true }),
      loadDetail(res.skill_id, { silent: true }),
    ]);
    return res;
  }, [refresh, loadDetail]);

  /** Add one document from a URL (fetched server-side) or pasted content.
   *  Unlike every other write on this page, `/api/skills/import-doc` is not
   *  in the RPC dispatch table (`nerve_gateway.py`) — it goes over REST,
   *  through the `cc-skills` proxy in `web/server/routes/cc-skills.ts`. A
   *  direct fetch here needs that proxy to exist: this call and its two
   *  siblings shipped 2026-08-07 without one and 404'd at the cockpit for
   *  twelve days (`web/server/api-route-parity.test.ts` now guards it). */
  const importDoc = useCallback(async (
    body: {
      skill_id: string; doc_key: string; title?: string;
      url?: string; content?: string; describes?: string;
    },
  ) => {
    const doc = await apiFetch<SkillDoc>('/api/skills/import-doc', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    await after(body.skill_id);
    return doc;
  }, [after]);

  /** Add one document from an uploaded file (PDF/docx/pptx/xlsx/…),
   *  converted to markdown server-side. Same direct-REST shape as `importDoc`
   *  (same `cc-skills` proxy), but multipart instead of JSON since it
   *  carries a `File` — which is WHY these three are REST at all: a File
   *  doesn't ride the RPC channel. Effective size cap is the cockpit's
   *  ~13 MB bodyLimit, under the backend's 25 MB. */
  const importFile = useCallback(async (
    body: {
      skill_id: string; doc_key: string; title?: string;
      describes?: string; file: File;
    },
  ) => {
    const form = new FormData();
    form.set('skill_id', body.skill_id);
    form.set('doc_key', body.doc_key);
    if (body.title) form.set('title', body.title);
    if (body.describes) form.set('describes', body.describes);
    form.set('file', body.file);
    const doc = await apiFetch<SkillDoc>('/api/skills/import-file', { method: 'POST', body: form });
    await after(body.skill_id);
    return doc;
  }, [after]);

  /** Import a whole documentation site — same direct-REST shape as
   *  `importDoc` (same `cc-skills` proxy). Emits one `skill.site_imported`
   *  event server-side regardless of how many documents landed. */
  const importSite = useCallback(async (
    body: {
      skill_id: string; url: string; doc_key_prefix?: string;
      max_pages?: number; path_prefix?: string; describes?: string; render?: boolean;
      filter_mode?: string;
    },
  ) => {
    const result = await apiFetch<SiteImportResult>('/api/skills/import-site', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    await after(body.skill_id);
    return result;
  }, [after]);

  /** Every version of one document, newest first — a read, so it does not
   *  touch the page's state; the caller holds what it fetched. */
  const history = useCallback(async (skillId: string, docKey: string) => {
    const res = await rpcRef.current('skills.history', {
      skill_id: skillId, doc_key: docKey,
    }) as { versions: SkillDoc[] };
    return res.versions ?? [];
  }, []);

  return {
    skills, detail, gaps, roster, loading, detailLoading, error,
    refresh, loadDetail,
    createSkill, addDoc, retireDoc, retireSkill, reactivateSkill, grant, revoke,
    importFolder, importDoc, importFile, importSite, history,
  };
}
