import { Hono } from 'hono';
import { rateLimitGeneral } from '../middleware/rate-limit.js';
import { getUploadFeatureConfig } from '../lib/upload-config.js';

const app = new Hono();

app.get('/api/upload-config', rateLimitGeneral, (c) => c.json(getUploadFeatureConfig()));

export default app;
