/**
 * GET/POST /api/agentlog — Agent log persistence.
 *
 * GET:  Returns the full agent log as JSON array.
 * POST: Appends an entry (validated with Zod).
 */

import { Hono } from 'hono';
import { zValidator } from '@hono/zod-validator';
import { z } from 'zod';
import { config } from '../lib/config.js';
import { readJSON, writeJSON } from '../lib/files.js';
import { createMutex } from '../lib/mutex.js';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import type { AgentLogEntry } from '../types.js';

const withLock = createMutex();

const app = new Hono();

/** Validation schema for agent log entries */
const agentLogSchema = z.object({
  ts: z.number().optional(),
  type: z.string().optional(),
  message: z.string().optional(),
  level: z.string().optional(),
  icon: z.string().optional(),
  text: z.string().optional(),
  data: z.record(z.string(), z.unknown()).optional(),
});

async function readAgentLog(): Promise<AgentLogEntry[]> {
  return readJSON<AgentLogEntry[]>(config.agentLogPath, []);
}

async function writeAgentLog(entries: AgentLogEntry[]): Promise<void> {
  const trimmed = entries.slice(-config.agentLogMax);
  await writeJSON(config.agentLogPath, trimmed);
}

app.get('/api/agentlog', async (c) => {
  try {
    const log = await readAgentLog();
    return c.json(log);
  } catch (err) {
    console.error('[agentlog] read error:', (err as Error).message);
    return c.json([]);
  }
});

app.post(
  '/api/agentlog',
  rateLimitGeneral,
  zValidator('json', agentLogSchema, (result, c) => {
    if (!result.success) {
      const msg = result.error.issues[0]?.message || 'Invalid log entry';
      return c.json({ ok: false, error: msg }, 400);
    }
  }),
  async (c) => {
    try {
      const entry = c.req.valid('json') as AgentLogEntry;
      await withLock(async () => {
        const log = await readAgentLog();
        log.push({ ...entry, ts: entry.ts || Date.now() });
        await writeAgentLog(log);
      });
      return c.json({ ok: true });
    } catch (err) {
      console.error('[agentlog] write error:', (err as Error).message);
      return c.json({ ok: false, error: 'Failed to write log' }, 500);
    }
  },
);

export default app;
