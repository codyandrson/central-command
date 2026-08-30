/**
 * Hono app definition + middleware stack.
 *
 * Assembles all middleware (CORS, security headers, body limits, compression,
 * cache-control) and mounts every API route under `/api/`. Also serves the
 * Vite-built SPA from `dist/` with a catch-all fallback to `index.html`.
 * @module
 */

import { Hono } from 'hono';
import { logger } from 'hono/logger';
import { cors } from 'hono/cors';
import { compress } from 'hono/compress';
import { bodyLimit } from 'hono/body-limit';
import { serveStatic } from '@hono/node-server/serve-static';

import { cacheHeaders } from './middleware/cache-headers.js';
import { errorHandler } from './middleware/error-handler.js';
import { securityHeaders } from './middleware/security-headers.js';
import { authMiddleware } from './middleware/auth.js';
import { config } from './lib/config.js';
import { resolveCorsOrigin } from './lib/origin-utils.js';

import healthRoutes from './routes/health.js';
import authRoutes from './routes/auth.js';
import ttsRoutes from './routes/tts.js';
import transcribeRoutes from './routes/transcribe.js';
import agentLogRoutes from './routes/agent-log.js';
// Central Command: usage numbers come from LiteLLM (the system of record).
import tokensRoutes from './routes/cc-tokens.js';
// Central Command: memory panel reads the shared knowledge graph (D11).
import memoriesRoutes from './routes/cc-memories.js';
import eventsRoutes from './routes/events.js';
import serverInfoRoutes from './routes/server-info.js';
import codexLimitsRoutes from './routes/codex-limits.js';
import claudeCodeLimitsRoutes from './routes/claude-code-limits.js';
import versionRoutes from './routes/version.js';
import channelsRoutes from './routes/channels.js';
import versionCheckRoutes from './routes/version-check.js';
import gatewayRoutes from './routes/gateway.js';
import connectDefaultsRoutes from './routes/connect-defaults.js';
import workspaceRoutes from './routes/workspace.js';
// The Config tab's per-agent config is the governed charter, not a filesystem
// workspace. workspaceRoutes stays registered: SessionContext (identity),
// App.tsx (chatPathLinks) and useDashboardData still call /api/workspace.
import charterRoutes from './routes/cc-charter.js';
// D27: /api/crons served by the Central Command heartbeat adapter (cc-crons), not
// the OpenClaw gateway cron tool — same replacement pattern as cc-kanban.
import cronsRoutes from './routes/cc-crons.js';
import sessionsRoutes from './routes/sessions.js';
import apiKeysRoutes from './routes/api-keys.js';
import filesRoutes from './routes/files.js';
import voicePhrasesRoutes from './routes/voice-phrases.js';
import fileBrowserRoutes from './routes/file-browser.js';
import uploadConfigRoutes from './routes/upload-config.js';
import uploadReferenceRoutes from './routes/upload-reference.js';
// Central Command: the board is backed by first-class Tasks, not the file store.
import kanbanRoutes from './routes/cc-kanban.js';
import beadsRoutes from './routes/beads.js';
// D2026-08-09: the Graph panel's read-only proxy to the API tier's bolt reader.
import graphRoutes from './routes/cc-graph.js';
// D2026-08-11: the Activity screen (Overview · Runs · Events) — the surface
// that makes every run and every event visible somewhere. Supersedes the old
// workspace activity tab, which was dropped.
import activityRoutes from './routes/cc-activity.js';
// Goals (lines of effort) screen — thin GET passthrough to the gateway's /api/loe.
import loeRoutes from './routes/cc-loe.js';
import skillsRoutes from './routes/cc-skills.js';
import decisionsRoutes from './routes/cc-decisions.js';
// Sources panel — document catalog sources plus the email feed's visibility row.
import sourcesRoutes from './routes/cc-sources.js';
import updateRoutes from './routes/cc-update.js';
// Systems view (launchpad): every deployed/integrated system, live status,
// and where its credential lives — never the value.
import systemsRoutes from './routes/cc-systems.js';

const app = new Hono();

// ── Middleware ────────────────────────────────────────────────────────

app.onError(errorHandler);
app.use('*', logger());
app.use(
  '*',
  cors({
    origin: resolveCorsOrigin,
    credentials: true,
    allowMethods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
    allowHeaders: ['Content-Type', 'Authorization'],
  }),
);
app.use('*', securityHeaders);
app.use(
  '/api/*',
  bodyLimit({
    maxSize: config.limits.maxBodyBytes,
    onError: (c) => c.text('Request body too large', 413),
  }),
);
// Authentication — after bodyLimit (reject oversized before auth), before compress/routes
app.use('*', authMiddleware);
// Apply compression to all routes except SSE (compression buffers chunks and breaks streaming)
app.use('*', async (c, next) => {
  if (c.req.path === '/api/events' || c.req.path === '/api/files/raw') return next();
  return compress()(c, next);
});
app.use('*', cacheHeaders);

// ── API routes ───────────────────────────────────────────────────────

const routes = [
  healthRoutes, authRoutes, ttsRoutes, transcribeRoutes, agentLogRoutes,
  tokensRoutes, memoriesRoutes, eventsRoutes, serverInfoRoutes,
  codexLimitsRoutes, claudeCodeLimitsRoutes, versionRoutes, versionCheckRoutes,
  gatewayRoutes, connectDefaultsRoutes,
  workspaceRoutes, charterRoutes, cronsRoutes, sessionsRoutes, filesRoutes, apiKeysRoutes,
  voicePhrasesRoutes, fileBrowserRoutes, uploadConfigRoutes, uploadReferenceRoutes, channelsRoutes, kanbanRoutes, beadsRoutes,
  graphRoutes, activityRoutes, loeRoutes, skillsRoutes, decisionsRoutes, sourcesRoutes,
  updateRoutes, systemsRoutes,
];
for (const route of routes) app.route('/', route);

// ── Static files + SPA fallback ──────────────────────────────────────

app.use('/assets/*', serveStatic({ root: './dist/' }));
// Serve static files but skip API routes
app.use('*', async (c, next) => {
  if (c.req.path.startsWith('/api/')) return next();
  return serveStatic({ root: './dist/' })(c, next);
});
// SPA fallback — serve index.html only for extensionless app routes.
// If a hashed asset or other static file is missing, return 404 instead of
// silently serving index.html. That avoids stale post-upgrade bundles loading
// HTML as JavaScript after a deploy/release switch.
app.get('*', async (c, next) => {
  if (c.req.path.startsWith('/api/')) return next();

  // Match a real file extension at the end of the path (e.g., `.js`, `.css`, `.map`)
  // rather than any dot anywhere in the path. The previous `.includes('.')`
  // would 404 paths like `/.well-known/acme-challenge/<token>` or any future
  // app route with a dot in a directory segment.
  const looksLikeStaticFile = c.req.path.startsWith('/assets/') || /\.[a-zA-Z0-9]+$/.test(c.req.path);
  if (looksLikeStaticFile) {
    return c.notFound();
  }

  return serveStatic({ root: './dist/', path: 'index.html' })(c, next);
});

export default app;
