/**
 * GET /api/version/check — Check if a newer Central Command is available.
 *
 * Uses latest published GitHub release first, then latest semver tag fallback
 * — resolved against the REPO ROOT's git origin (the central-command repo),
 * not the vendored cockpit. Current version comes from the root VERSION file
 * (`version=<semver>`, the 2026-08-27 setup-update contract), not the
 * cockpit's package.json — the vendored Nerve version is not the product's.
 */

import { Hono } from 'hono';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { compareSemver, resolveLatestVersion, readProductVersion, repoRoot } from '../lib/release-source.js';
import { requestStage } from './cc-update.js';

// Seeing an update IS the request to prepare it (v2.19.0): every check that
// finds a newer release asks the root stager to prebuild the image caches
// while the system stays up. requestStage is idempotent and quiet, so the
// hourly poll costs one stat when nothing changed.
function autoStage(latest: string, updateAvailable: boolean): void {
  if (updateAvailable) requestStage(latest);
}

interface VersionCache {
  latest: string;
  source: 'release' | 'tag';
  checkedAt: number;
}

const CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour
let cache: VersionCache | null = null;
const projectDir = repoRoot;

const app = new Hono();

app.get('/api/version/check', rateLimitGeneral, async (c) => {
  const now = Date.now();

  // Serve from cache if fresh — unless the operator asked (`?force=1`, the
  // settings page's "Check for updates"): the cache exists to spare the
  // hourly background poll a network round-trip, not to make a deliberate
  // check wait up to an hour. The route is rate-limited either way.
  const force = c.req.query('force') === '1';
  if (!force && cache && now - cache.checkedAt < CACHE_TTL_MS) {
    const updateAvailable = compareSemver(cache.latest, readProductVersion()) > 0;
    autoStage(cache.latest, updateAvailable);
    return c.json({
      current: readProductVersion(),
      latest: cache.latest,
      source: cache.source,
      updateAvailable,
      projectDir,
      checkedAt: cache.checkedAt,
    });
  }

  const latest = await resolveLatestVersion(projectDir);
  if (!latest) {
    return c.json({
      current: readProductVersion(),
      latest: null,
      source: null,
      updateAvailable: false,
      error: 'Could not fetch release or semver tags',
      projectDir,
      checkedAt: now,
    });
  }

  cache = {
    latest: latest.version,
    source: latest.source,
    checkedAt: now,
  };

  const updateAvailable = compareSemver(latest.version, readProductVersion()) > 0;
  autoStage(latest.version, updateAvailable);
  return c.json({
    current: readProductVersion(),
    latest: latest.version,
    source: latest.source,
    updateAvailable,
    projectDir,
    checkedAt: now,
  });
});

export default app;
