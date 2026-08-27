/**
 * Global error handler middleware.
 *
 * Catches unhandled errors thrown by route handlers and returns a consistent
 * response: JSON `{ error }` for `/api/*` routes, plain text otherwise.
 * In development mode, stack traces are logged to stderr.
 * @module
 */

import type { ErrorHandler } from 'hono';

const isDev = process.env.NODE_ENV !== 'production';

export const errorHandler: ErrorHandler = (err, c) => {
  console.error('[server] unhandled error:', err.message || err);
  if (isDev && err.stack) {
    console.error('[server] stack:', err.stack);
  }
  if (c.req.path.startsWith('/api/') || c.req.path.startsWith('/api')) {
    return c.json({ error: 'Internal server error' }, 500);
  }
  return c.text('Internal server error', 500);
};
