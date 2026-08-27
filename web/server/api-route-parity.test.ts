/**
 * Every `fetch('/api/…')` the frontend makes must have a route behind it in
 * this server, or the browser gets a 404 the type checker and every render
 * test are blind to — the cockpit hand-declares its interfaces over untyped
 * HTTP exactly as it does over RPC, and the failure is silent by the same
 * mechanism.
 *
 * This is how the Skills page's import-doc / import-file / import-site
 * dialogs shipped dead on 2026-08-07 and stayed dead for twelve days: the
 * FastAPI routes existed and were tested, the dialogs existed and rendered,
 * and no Hono route connected them. Backend tests drive FastAPI directly and
 * frontend tests mock fetch, so only a source walk sees the gap.
 *
 * Scope, honestly: this catches string-literal calls through `fetch` AND the
 * house wrappers (`apiFetch`, `postJSON`, `fetchResult` — a wrapper the regex
 * doesn't know is a hole: bulk_dismiss hid behind `postJSON` for exactly this
 * reason, found 2026-08-20), matching paths only, not methods. A URL built in
 * a variable before the call is invisible to it — a guard, not a proof.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sourceFiles(full, out);
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

/** Literal fetch targets: fetch('/api/x'), fetch(`/api/x?${q}`), … — path
 *  cut at the first `?` or `${`. Group 2 marks a `${`-truncated path, which
 *  is a PREFIX of the real one (`/api/beads/${id}` fetches `/api/beads/:id`)
 *  and must be matched as such, not as an exact path. */
const FETCH_RE = /(?:\bfetch|\bapiFetch|\bpostJSON|\bfetchResult)\(\s*[`'"](\/api\/[^`'"?$]+)(\$)?/g;

/** Routes this server registers: app.get('/api/x'), app.post(`/api/:id`), …
 *  `app.use` is deliberately NOT here — middleware like app.ts's
 *  `app.use('/api/*', bodyLimit)` matches every path and would make this
 *  test vacuously green (it did, first cut). */
const ROUTE_RE = /\bapp\.(?:get|post|put|patch|delete|all)\(\s*[`'"](\/api[^`'"]*)[`'"]/g;

interface Fetched { path: string; prefix: boolean; file: string }

function collect(files: string[], re: RegExp): Fetched[] {
  const out: Fetched[] = [];
  for (const file of files) {
    const text = readFileSync(file, 'utf8');
    for (const m of text.matchAll(re)) {
      let path = m[1];
      const prefix = m[2] === '$';
      // A `${`-truncated path whose last segment is itself cut mid-word
      // (`/api/foo/ba${x}`) can only vouch for its complete segments.
      if (prefix && !path.endsWith('/')) path = path.slice(0, path.lastIndexOf('/'));
      out.push({ path: path.replace(/\/$/, ''), prefix, file });
    }
  }
  return out;
}

/** Does a registered pattern (`:param` and `*` segments allowed) serve this
 *  fetch? An exact path must match every segment; a `${`-truncated prefix
 *  only its own. */
function serves(pattern: string, { path, prefix }: Fetched): boolean {
  if (pattern.endsWith('/*')) return path.startsWith(pattern.slice(0, -2));
  const p = pattern.split('/');
  const q = path.split('/');
  if (prefix ? p.length < q.length : p.length !== q.length) return false;
  return q.every((seg, i) => seg === p[i] || p[i].startsWith(':') || p[i] === '*');
}

describe('frontend/server API route parity', () => {
  it('serves every /api path the frontend fetches by string literal', () => {
    const fetched = collect(sourceFiles(join(__dirname, '..', 'src')), FETCH_RE);
    const registered = collect(sourceFiles(__dirname), ROUTE_RE).map((r) => r.path);

    const orphans = fetched.filter((f) => !registered.some((r) => serves(r, f)));
    expect(
      orphans.map(({ path, file }) => `${path} (fetched in ${file})`),
      'frontend fetches with no server route behind them',
    ).toEqual([]);
  });
});
