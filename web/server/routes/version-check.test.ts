/** Tests for the GET /api/version/check endpoint. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Hono } from 'hono';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// The route resolves the PRODUCT repo root (three levels above the compiled
// routes dir); from this test file's source location that is also '../..' up
// from web/ — i.e. the checkout root holding the VERSION file.
const TEST_REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');

describe('GET /api/version/check', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  async function buildApp() {
    vi.doMock('../middleware/rate-limit.js', () => ({
      rateLimitGeneral: vi.fn((_c: unknown, next: () => Promise<void>) => next()),
    }));

    vi.doMock('../lib/release-source.js', () => ({
      compareSemver: vi.fn(() => 1),
      resolveLatestVersion: vi.fn(async () => ({ version: '9.9.9', source: 'release' })),
    }));

    const mod = await import('./version-check.js');
    const app = new Hono();
    app.route('/', mod.default);
    return app;
  }

  it('returns the resolved project directory for copy-paste update commands', async () => {
    const app = await buildApp();
    const res = await app.request('/api/version/check');

    expect(res.status).toBe(200);

    const json = await res.json() as {
      current: string;
      latest: string;
      updateAvailable: boolean;
      projectDir: string;
    };

    expect(json.latest).toBe('9.9.9');
    expect(json.updateAvailable).toBe(true);
    expect(json.projectDir).toBe(TEST_REPO_ROOT);
    // current comes from the repo-root VERSION file, not the cockpit's
    // package.json — the wire shape the UpdateBadge renders.
    expect(json.current).toMatch(/^\d+\.\d+\.\d+$/);
  });
});
