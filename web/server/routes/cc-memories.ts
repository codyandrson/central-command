/**
 * cc-memories — the cockpit memory panel, backed by Central Command's shared
 * knowledge graph (Graphiti) instead of workspace MEMORY.md files.
 *
 * D11-r1: agents also have a private graph partition. When `agentId` is
 * given, this route asks the backend for that agent's PRIVATE partition only
 * (scope=private, the panel's default) and tags each row `scope: "private"`
 * — the frontend can render a badge instead of a text-mangled `[private]`
 * prefix. With no `agentId` (e.g. a global/dashboard caller) `scope` is moot
 * server-side and the response is shared episodes only, all `scope:
 * 'shared'`, exactly as before. The response also carries `total` and
 * `hasMore` (2026-08-29) so the panel can show "showing N of TOTAL" instead
 * of silently truncating at `limit` (forwarded from the `?limit=` query
 * param, default 50). The add/edit/delete affordances still refuse with
 * pointers at the gated write path — memory stays read-only from the panel.
 */
import { Hono } from 'hono';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { config } from '../lib/config.js';

const app = new Hono();

interface CcEpisode {
  uuid: string;
  name: string;
  content: string;
  created_at: string | null;
  group_id?: string;
  source_description?: string;
  scope?: 'shared' | 'private';
}

const GATED_MSG =
  'Shared-graph memory is written through approved proposals (graph.add_episode '
  + 'riding the approval gateway), never edited from the panel.';

app.get('/api/memories', rateLimitGeneral, async (c) => {
  const agentId = c.req.query('agentId');
  const limit = c.req.query('limit') || '50';
  try {
    // scope=private: the panel's default per-agent view (only meaningful
    // with an agentId — the backend ignores it otherwise and stays shared-only).
    const params = new URLSearchParams({ limit, scope: 'private' });
    if (agentId) params.set('agent_id', agentId);
    const res = await fetch(`${config.gatewayUrl.replace(/\/+$/, '')}/api/graph/episodes?${params.toString()}`);
    if (!res.ok) return c.json({ error: `Central Command HTTP ${res.status}` }, 502);
    const data = await res.json() as { episodes: CcEpisode[]; total: number; has_more: boolean };
    const memories = data.episodes.map((e) => ({
      type: 'item' as const,
      text: `${e.name} — ${e.content}`,
      date: e.created_at ? e.created_at.slice(0, 10) : undefined,
      scope: e.scope === 'private' ? 'private' as const : 'shared' as const,
    }));
    return c.json({ memories, total: data.total, hasMore: data.has_more });
  } catch (e) {
    return c.json({ error: e instanceof Error ? e.message : String(e) }, 502);
  }
});

app.post('/api/memories', rateLimitGeneral, (c) => c.json({ error: GATED_MSG }, 501));
app.delete('/api/memories', rateLimitGeneral, (c) => c.json({ error: GATED_MSG }, 501));
app.get('/api/memories/section', rateLimitGeneral, (c) => c.json({ error: GATED_MSG }, 501));
app.put('/api/memories/section', rateLimitGeneral, (c) => c.json({ error: GATED_MSG }, 501));
app.post('/api/memories/section', rateLimitGeneral, (c) => c.json({ error: GATED_MSG }, 501));

export default app;
