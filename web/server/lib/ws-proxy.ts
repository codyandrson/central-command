/**
 * WebSocket proxy — bridges browser clients to the Central Command API's `/ws`.
 *
 * Clients connect to `ws(s)://host:port/ws[?target=<gateway-ws-url>]`. This
 * module runs the cockpit's own checks on the upgrade request (Origin, session
 * cookie, target) and only then hands it to `http-proxy-3`, which opens the
 * upstream connection, relays the API's handshake answer, and pipes the two
 * TCP sockets together. No upstream connection is opened for a refused upgrade.
 *
 * `?target=` is OPTIONAL — omitted, the target is derived from
 * `config.gatewayUrl` (Central Command has exactly one gateway, and the browser
 * should need to know nothing about it). A client-supplied target must be
 * `ws:`/`wss:` on a host in `WS_ALLOWED_HOSTS`.
 *
 * Because the sockets are piped, the WebSocket session is end to end between
 * the browser and the API: frames, close codes, keepalive pings and the message
 * size limit are the API's (uvicorn's), unchanged.
 * @module
 */

import type { Server as HttpsServer } from 'node:https';
import type { Server as HttpServer, IncomingMessage } from 'node:http';
import type { Duplex } from 'node:stream';
import type { Socket } from 'node:net';
import { createProxyServer } from 'http-proxy-3';
import { config, WS_ALLOWED_HOSTS, SESSION_COOKIE_NAME } from './config.js';
import { verifySession, parseSessionCookie } from './session.js';
import { isAllowedOrigin } from './origin-utils.js';

/** How long to wait for the API to accept the TCP connection. */
const CONNECT_TIMEOUT_MS = 10_000;

const proxy = createProxyServer({
  ws: true,
  changeOrigin: true, // Host: the API's, as a direct client would send
  ignorePath: true, // the upstream path is the target's `/ws`, never the client's query
  connectTimeout: CONNECT_TIMEOUT_MS,
});

// The cockpit session cookie is the cockpit's credential; the API never reads it.
// Neither is any other credential the client sent. And an upstream that
// answers with an ordinary HTTP response instead of the WebSocket handshake is
// NOT relayed: the library would copy that response to the client, which would
// turn the relay into a way to read any HTTP service on an allowed host. This
// listener is added before the library's own, so the client socket is already
// destroyed when the library looks at it (it writes only to a live socket).
proxy.on('proxyReqWs', (proxyReq, _req, socket) => {
  proxyReq.removeHeader('cookie');
  proxyReq.removeHeader('authorization');
  proxyReq.on('response', (res) => {
    res.resume();
    socket.destroy();
  });
});

// An upstream that refuses, times out or drops: the library destroys the
// client socket itself; this listener only logs (without one it would throw).
proxy.on('error', (err) => {
  console.error('[ws-proxy] upstream error:', err.message);
});

/** Live client sockets, for graceful shutdown. */
const liveSockets = new Set<Duplex>();

/** Close every proxied WebSocket connection (the library closes its upstream half). */
export function closeAllWebSockets(): void {
  for (const socket of liveSockets) socket.destroy();
  liveSockets.clear();
}

/**
 * The one gateway this cockpit serves, derived from server config.
 * Used when the client sends no `?target=` — server config is trusted, so it
 * skips WS_ALLOWED_HOSTS (which only guards client-supplied targets).
 */
function defaultTarget(): URL | null {
  try {
    const u = new URL(config.gatewayUrl);
    u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
    u.pathname = '/ws';
    return u;
  } catch {
    return null;
  }
}

/** The upstream URL for this request, or the HTTP refusal to send instead. */
function resolveTarget(req: IncomingMessage): URL | { status: string; why: string } {
  const target = new URL(req.url || '/', 'http://localhost').searchParams.get('target');
  if (!target) {
    return defaultTarget() ?? { status: '502 Bad Gateway', why: 'Missing ?target= param and no usable GATEWAY_URL' };
  }
  let url: URL;
  try {
    url = new URL(target);
  } catch {
    return { status: '400 Bad Request', why: 'Invalid target URL' };
  }
  if (!['ws:', 'wss:'].includes(url.protocol) || !WS_ALLOWED_HOSTS.has(url.hostname)) {
    console.warn(`[ws-proxy] Rejected: target not allowed: ${target}`);
    return { status: '403 Forbidden', why: 'Target not allowed' };
  }
  // A client names a HOST, never a path, query or credentials: the upstream is
  // always that host's `/ws`.
  url.pathname = '/ws';
  url.search = '';
  url.username = '';
  url.password = '';
  return url;
}

function refuse(socket: Duplex, status: string, why: string): void {
  socket.write(`HTTP/1.1 ${status}\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n${why}`);
  socket.destroy();
}

/** Set up the `/ws` proxy on an HTTP or HTTPS server. */
export function setupWebSocketProxy(server: HttpServer | HttpsServer): void {
  server.on('upgrade', (req: IncomingMessage, socket: Duplex, head: Buffer) => {
    if (!req.url?.startsWith('/ws')) {
      socket.destroy();
      return;
    }

    const originHeader = Array.isArray(req.headers.origin) ? req.headers.origin[0] : req.headers.origin;
    if (!isAllowedOrigin(originHeader)) {
      refuse(socket, '403 Forbidden', 'Origin not allowed');
      return;
    }

    if (config.auth) {
      const token = parseSessionCookie(req.headers.cookie, SESSION_COOKIE_NAME);
      if (!token || !verifySession(token, config.sessionSecret)) {
        refuse(socket, '401 Unauthorized', 'Authentication required');
        return;
      }
    }

    const target = resolveTarget(req);
    if (!(target instanceof URL)) {
      refuse(socket, target.status, target.why);
      return;
    }

    liveSockets.add(socket);
    socket.once('close', () => liveSockets.delete(socket));
    proxy.ws(req, socket as Socket, head, { target });
  });
}
