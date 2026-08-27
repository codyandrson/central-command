/**
 * GET /api/claude-code-limits — Claude Code rate limit information.
 *
 * Spawns the Claude CLI via PTY (see {@link getClaudeUsage}), captures the
 * `/usage` output, and normalises reset timestamps to epoch-ms so the
 * frontend doesn't need to parse Claude's human-readable time strings.
 * Results are cached via {@link createCachedFetch} (5 min TTL, 30 s on failure).
 * @module
 */

import { Hono } from 'hono';
import { createCachedFetch } from '../lib/cached-fetch.js';
import { getClaudeUsage } from '../services/claude-usage.js';

const app = new Hono();

// ── Types ────────────────────────────────────────────────────────────

interface RawLimitWindow {
  used_percent: number;
  left_percent: number;
  resets_at: string; // human-readable from CLI, e.g. "7:59pm (UTC)"
}

interface NormalisedLimitWindow {
  used_percent: number;
  left_percent: number;
  resets_at_epoch: number | null;
  resets_at_raw: string;
}

interface ClaudeCodeLimitsResponse {
  available: boolean;
  session_limit?: NormalisedLimitWindow;
  weekly_limit?: NormalisedLimitWindow;
  error?: string;
}

// ── Reset-time parser ────────────────────────────────────────────────
// Claude CLI outputs times like "7:59pm", "1am", "Feb 13, 6:59pm" — all UTC.

const MONTHS: Record<string, number> = {
  Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5,
  Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11,
};

function parseClaudeResetToEpochMs(raw: string): number | null {
  if (!raw) return null;
  const s = raw.replace(/\s*\([^)]*\)\s*/g, '').trim();

  // "7:59pm", "7:59 pm"
  const hhmm = s.match(/^(\d{1,2}):(\d{2})\s*(am|pm)$/i);
  if (hhmm) {
    const h24 = (parseInt(hhmm[1], 10) % 12) + (hhmm[3].toLowerCase() === 'pm' ? 12 : 0);
    const now = new Date();
    let ts = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), h24, parseInt(hhmm[2], 10));
    if (ts <= Date.now()) ts += 86_400_000;
    return ts;
  }

  // "1am", "7pm"
  const hOnly = s.match(/^(\d{1,2})\s*(am|pm)$/i);
  if (hOnly) {
    const h24 = (parseInt(hOnly[1], 10) % 12) + (hOnly[2].toLowerCase() === 'pm' ? 12 : 0);
    const now = new Date();
    let ts = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), h24, 0);
    if (ts <= Date.now()) ts += 86_400_000;
    return ts;
  }

  // "Feb 13, 6:59pm"
  const dt = s.match(/^([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{1,2}):(\d{2})\s*(am|pm)$/i);
  if (dt) {
    const mon = MONTHS[dt[1]];
    if (mon == null) return null;
    const h24 = (parseInt(dt[3], 10) % 12) + (dt[5].toLowerCase() === 'pm' ? 12 : 0);
    const y = new Date().getUTCFullYear();
    let ts = Date.UTC(y, mon, parseInt(dt[2], 10), h24, parseInt(dt[4], 10));
    if (ts <= Date.now() - 7 * 86_400_000) ts = Date.UTC(y + 1, mon, parseInt(dt[2], 10), h24, parseInt(dt[4], 10));
    return ts;
  }

  // "Feb 13, 7pm"
  const dh = s.match(/^([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{1,2})\s*(am|pm)$/i);
  if (dh) {
    const mon = MONTHS[dh[1]];
    if (mon == null) return null;
    const h24 = (parseInt(dh[3], 10) % 12) + (dh[4].toLowerCase() === 'pm' ? 12 : 0);
    const y = new Date().getUTCFullYear();
    let ts = Date.UTC(y, mon, parseInt(dh[2], 10), h24, 0);
    if (ts <= Date.now() - 7 * 86_400_000) ts = Date.UTC(y + 1, mon, parseInt(dh[2], 10), h24, 0);
    return ts;
  }

  return null;
}

function normaliseWindow(w: RawLimitWindow): NormalisedLimitWindow {
  return {
    used_percent: w.used_percent,
    left_percent: w.left_percent,
    resets_at_epoch: parseClaudeResetToEpochMs(w.resets_at),
    resets_at_raw: w.resets_at,
  };
}

// ── Fetch + cache ────────────────────────────────────────────────────

async function getClaudeCodeLimits(): Promise<ClaudeCodeLimitsResponse> {
  try {
    const raw = await getClaudeUsage();
    return {
      available: raw.available,
      session_limit: raw.session_limit ? normaliseWindow(raw.session_limit) : undefined,
      weekly_limit: raw.weekly_limit ? normaliseWindow(raw.weekly_limit) : undefined,
      error: raw.error,
    };
  } catch (error) {
    console.error('Error fetching Claude Code limits:', error);
    return {
      available: false,
      error: error instanceof Error ? error.message : 'Unknown error',
    };
  }
}

const getClaudeCodeLimitsCached = createCachedFetch(getClaudeCodeLimits, undefined, {
  isValid: (r) => r.available,
});

// ── Route ────────────────────────────────────────────────────────────

app.get('/api/claude-code-limits', async (c) => {
  try {
    return c.json(await getClaudeCodeLimitsCached());
  } catch (error) {
    console.error('Error in claude-code-limits endpoint:', error);
    return c.json({ available: false, error: 'Failed to fetch Claude Code limits' }, 500);
  }
});

export default app;
