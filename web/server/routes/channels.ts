/**
 * GET /api/channels — List messaging channels configured in OpenClaw.
 *
 * Reads channel keys from ~/.openclaw/openclaw.json. Returns an array
 * of channel names (e.g. ["whatsapp", "discord"]).
 * Cached for 5 minutes to avoid repeated disk reads.
 */

import { Hono } from 'hono';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { rateLimitGeneral } from '../middleware/rate-limit.js';

interface ChannelsCache {
  channels: string[];
  checkedAt: number;
}

const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes
let cache: ChannelsCache | null = null;

/** Read configured channel names from openclaw.json. */
async function readConfiguredChannels(): Promise<string[]> {
  try {
    const configPath = join(homedir(), '.openclaw', 'openclaw.json');
    const raw = await readFile(configPath, 'utf-8');
    const config = JSON.parse(raw) as { channels?: Record<string, unknown> };
    if (!config.channels || typeof config.channels !== 'object') return [];
    return Object.keys(config.channels).filter(k => k !== 'webchat');
  } catch {
    return [];
  }
}

const app = new Hono();

app.get('/api/channels', rateLimitGeneral, async (c) => {
  const now = Date.now();
  if (cache && now - cache.checkedAt < CACHE_TTL_MS) {
    return c.json({ channels: cache.channels });
  }

  const channels = await readConfiguredChannels();
  cache = { channels, checkedAt: now };
  return c.json({ channels });
});

export default app;
