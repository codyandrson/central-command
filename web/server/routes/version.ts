/**
 * GET /api/version — Returns the application version from package.json.
 */

import { Hono } from 'hono';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const pkg = JSON.parse(readFileSync(resolve(__dirname, '../../package.json'), 'utf-8')) as {
  version: string;
  name: string;
};

const app = new Hono();

app.get('/api/version', rateLimitGeneral, (c) => c.json({ version: pkg.version, name: pkg.name }));

export default app;
