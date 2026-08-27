/**
 * cc-memories — the cockpit memory panel, backed by Central Command's shared
 * knowledge graph (Graphiti) instead of workspace MEMORY.md files.
 *
 * D11-r1: agents also have a private graph partition. When `agentId` is
 * given, the backend widens the read to that agent's private episodes too
 * and tags each one `scope: "shared"|"private"` — this route forwards the
 * param and passes `scope` through on the wire as a real field (defaulting
 * to 'shared' when the backend omits it) so the frontend can render a badge
 * instead of a text-mangled `[private]` prefix. With no `agentId` (e.g. a
 * global/dashboard caller) the response is shared episodes only, all
 * `scope: 'shared'`, exactly as before. The add/edit/delete
 * affordances still refuse with pointers at the gated write path — memory
 * stays read-only from the panel.
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
  try {
    const params = new URLSearchParams({ limit: '50' });
    if (agentId) params.set('agent_id', agentId);
    const res = await fetch(`${config.gatewayUrl.replace(/\/+$/, '')}/api/graph/episodes?${params.toString()}`);
    if (!res.ok) return c.json({ error: `Central Command HTTP ${res.status}` }, 502);
    const data = await res.json() as { episodes: CcEpisode[] };
    const memories = data.episodes.map((e) => ({
      type: 'item' as const,
      text: `${e.name} — ${e.content}`,
      date: e.created_at ? e.created_at.slice(0, 10) : undefined,
      scope: e.scope === 'private' ? 'private' as const : 'shared' as const,
    }));
    return c.json(memories);
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
