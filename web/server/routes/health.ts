/**
 * GET /health — Health check endpoint.
 * Includes optional gateway connectivity probe.
 */

import { Hono } from 'hono';
import { config } from '../lib/config.js';

const app = new Hono();

app.get('/health', async (c) => {
  let gateway: 'ok' | 'unreachable' = 'unreachable';
  try {
    const res = await fetch(`${config.gatewayUrl}/health`, {
      signal: AbortSignal.timeout(3000),
    });
    if (res.ok) gateway = 'ok';
  } catch {
    // gateway unreachable — not a server failure
  }

  return c.json({ status: 'ok', uptime: process.uptime(), gateway });
});

export default app;
