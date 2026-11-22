/**
 * WebSocket proxy — bridges browser clients to the Central Command API's `/ws`.
 *
 * Clients connect to `ws(s)://host:port/ws[?target=<gateway-ws-url>]` and this
 * module opens a corresponding connection to the gateway, relaying messages
 * bidirectionally. `?target=` is OPTIONAL — omitted, the target is derived from
 * `config.gatewayUrl` (Central Command has exactly one gateway, and the browser
 * should need to know nothing about it).
 *
 * Frames are relayed unchanged, connect handshake included: the API's `/ws`
 * reads no connect parameters (`nerve_gateway._dispatch`), so the relay
 * injects no identity or token. Client frames that arrive before the gateway
 * socket opens are buffered and flushed in order.
 * @module
 */

import type { Server as HttpsServer } from 'node:https';
import type { Server as HttpServer } from 'node:http';
import { WebSocket, WebSocketServer } from 'ws';
import type { IncomingMessage } from 'node:http';
import type { Duplex } from 'node:stream';
import { randomUUID } from 'node:crypto';
import { config, WS_ALLOWED_HOSTS, SESSION_COOKIE_NAME } from './config.js';
import { verifySession, parseSessionCookie } from './session.js';
import { gatewayRpcCall } from './gateway-rpc.js';
import { isAllowedOrigin } from './origin-utils.js';

/**
 * Methods the gateway restricts for webchat clients.
 * We intercept these and proxy via `openclaw gateway call` (full CLI scopes).
 */
const RESTRICTED_METHODS = new Set([
  'sessions.patch',
  'sessions.delete',
  'sessions.reset',
  'sessions.compact',
]);
const CONTROL_UI_CLIENT_ID = 'openclaw-control-ui';

/**
 * Execute a gateway RPC call, bypassing webchat restrictions.
 * Delegates to the shared gateway-rpc module.
 */
function gatewayCall(method: string, params: Record<string, unknown>): Promise<unknown> {
  return gatewayRpcCall(method, params);
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

/** Active WSS instances — used for graceful shutdown */
const activeWssInstances: WebSocketServer[] = [];

/** Close all active WebSocket connections */
export function closeAllWebSockets(): void {
  for (const wss of activeWssInstances) {
    for (const client of wss.clients) client.close(1001, 'Server shutting down');
    wss.close();
  }
  activeWssInstances.length = 0;
}

/**
 * Set up the WS/WSS proxy on an HTTP or HTTPS server.
 * Proxies ws(s)://host:port/ws?target=ws://gateway/ws to the OpenClaw gateway.
 */
export function setupWebSocketProxy(server: HttpServer | HttpsServer): void {
  const wss = new WebSocketServer({ noServer: true });
  activeWssInstances.push(wss);

  server.on('upgrade', (req: IncomingMessage, socket: Duplex, head: Buffer) => {
    if (req.url?.startsWith('/ws')) {
      const originHeader = Array.isArray(req.headers.origin) ? req.headers.origin[0] : req.headers.origin;
      if (!isAllowedOrigin(originHeader)) {
        socket.write('HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\n\r\nOrigin not allowed');
        socket.destroy();
        return;
      }

      // Auth check for WebSocket connections
      if (config.auth) {
        const token = parseSessionCookie(req.headers.cookie, SESSION_COOKIE_NAME);
        if (!token || !verifySession(token, config.sessionSecret)) {
          socket.write('HTTP/1.1 401 Unauthorized\r\nContent-Type: text/plain\r\n\r\nAuthentication required');
          socket.destroy();
          return;
        }
      }
      wss.handleUpgrade(req, socket, head, (ws) => wss.emit('connection', ws, req));
    } else {
      socket.destroy();
    }
  });

  wss.on('connection', (clientWs: WebSocket, req: IncomingMessage) => {
    const connId = randomUUID().slice(0, 8);
    const tag = `[ws-proxy:${connId}]`;
    const url = new URL(req.url || '/', 'https://localhost');
    const target = url.searchParams.get('target');

    console.log(`${tag} New connection: target=${target ?? '(server default)'}`);

    let targetUrl: URL;
    if (target) {
      try {
        targetUrl = new URL(target);
      } catch {
        clientWs.close(1008, 'Invalid target URL');
        return;
      }
      // Allowlist applies to CLIENT-supplied targets only.
      if (!['ws:', 'wss:'].includes(targetUrl.protocol) || !WS_ALLOWED_HOSTS.has(targetUrl.hostname)) {
        console.warn(`${tag} Rejected: target not allowed: ${target}`);
        clientWs.close(1008, 'Target not allowed');
        return;
      }
    } else {
      const derived = defaultTarget();
      if (!derived) {
        clientWs.close(1008, 'Missing ?target= param and no usable GATEWAY_URL');
        return;
      }
      targetUrl = derived;
    }

    const targetPort = Number(targetUrl.port) || (targetUrl.protocol === 'wss:' ? 443 : 80);
    if (targetPort < 1 || targetPort > 65535) {
      console.warn(`${tag} Rejected: invalid port ${targetPort}`);
      clientWs.close(1008, 'Invalid target port');
      return;
    }

    const isEncrypted = !!(req.socket as unknown as { encrypted?: boolean }).encrypted;
    const scheme = isEncrypted ? 'https' : 'http';
    const clientOrigin = (Array.isArray(req.headers.origin) ? req.headers.origin[0] : req.headers.origin)
      || `${scheme}://${req.headers.host}`;

    createGatewayRelay(clientWs, targetUrl, clientOrigin, connId);
  });
}

/**
 * Create a relay between a browser WebSocket and the gateway.
 *
 * Client frames are buffered until the gateway socket opens, then flushed in
 * order; after that every frame is forwarded as it arrives.
 */
function createGatewayRelay(
  clientWs: WebSocket,
  targetUrl: URL,
  clientOrigin: string,
  connId: string,
): void {
  const tag = `[ws-proxy:${connId}]`;
  const connStartTime = Date.now();
  let clientToGatewayCount = 0;
  let gatewayToClientCount = 0;

  // ─── Keepalive: ping both sides every 30s, kill dead connections ────────
  const PING_INTERVAL = 30_000;
  let clientAlive = true;
  let gatewayAlive = true;

  clientWs.on('pong', () => { clientAlive = true; });

  const pingTimer = setInterval(() => {
    // Check client
    if (!clientAlive) {
      console.log(`${tag} Client pong timeout — terminating`);
      clientWs.terminate();
      return;
    }
    clientAlive = false;
    if (clientWs.readyState === WebSocket.OPEN) clientWs.ping();

    // Check gateway
    if (!gatewayAlive) {
      console.log(`${tag} Gateway pong timeout — terminating`);
      gwWs.terminate();
      return;
    }
    gatewayAlive = false;
    if (gwWs.readyState === WebSocket.OPEN) gwWs.ping();
  }, PING_INTERVAL);

  /** Whether this connection is using the privileged OpenClaw control UI client id */
  let isControlUiClient = false;

  // Buffer client messages until gateway connection is open (with cap)
  const MAX_PENDING = 100;
  const MAX_BYTES = 1024 * 1024; // 1 MB
  let pending: { data: Buffer | string; isBinary: boolean }[] = [];
  let pendingBytes = 0;

  /** Queue a client message for deferred forwarding. Returns false if limits exceeded. */
  function enqueuePending(data: Buffer | string, isBinary: boolean): boolean {
    const size = typeof data === 'string' ? Buffer.byteLength(data) : data.length;
    if (pending.length >= MAX_PENDING || pendingBytes + size > MAX_BYTES) {
      return false;
    }
    pendingBytes += size;
    pending.push({ data, isBinary });
    return true;
  }

  /** Flush buffered messages to gateway in FIFO order. */
  function flushPending(): void {
    if (gwWs.readyState !== WebSocket.OPEN) return;
    for (const msg of pending) {
      gwWs.send(msg.isBinary ? msg.data : msg.data.toString());
    }
    pending = [];
    pendingBytes = 0;
  }

  const gwWs = new WebSocket(targetUrl.toString(), {
    headers: { Origin: clientOrigin },
  });

  gwWs.on('pong', () => { gatewayAlive = true; });

  // Gateway → Client
  gwWs.on('message', (data: Buffer | string, isBinary: boolean) => {
    if (clientWs.readyState === WebSocket.OPEN) {
      gatewayToClientCount++;
      clientWs.send(isBinary ? data : data.toString());
    }
  });

  gwWs.on('open', () => {
    flushPending();
  });

  gwWs.on('error', (err) => {
    console.error(`${tag} Gateway error:`, err.message);
    clientWs.close();
  });

  gwWs.on('close', (code, reason) => {
    console.log(`${tag} Gateway closed: code=${code}, reason=${reason?.toString() || ''}`);
    clientWs.close();
  });

  // Client → Gateway
  clientWs.on('message', (data: Buffer | string, isBinary: boolean) => {
    let msg: { type?: unknown; id?: unknown; method?: unknown; params?: unknown } | null = null;
    if (!isBinary) {
      try { msg = JSON.parse(data.toString()); } catch { /* pass through */ }
    }

    if (msg && msg.type === 'req' && msg.method === 'connect') {
      const params = (msg.params || {}) as { client?: { id?: string } };
      isControlUiClient = params.client?.id === CONTROL_UI_CLIENT_ID;
    }

    if (gwWs.readyState !== WebSocket.OPEN) {
      if (!enqueuePending(data, isBinary)) {
        clientWs.close(1008, 'Too many pending messages');
      }
      return;
    }

    // Intercept restricted RPC methods for plain webchat clients only.
    // Control UI clients are allowed to call these directly on the gateway.
    if (msg && msg.type === 'req' && typeof msg.method === 'string'
      && RESTRICTED_METHODS.has(msg.method) && !isControlUiClient) {
      const reqId = msg.id;
      gatewayCall(msg.method, (msg.params || {}) as Record<string, unknown>)
        .then((result) => {
          if (clientWs.readyState === WebSocket.OPEN) {
            clientWs.send(JSON.stringify({ type: 'res', id: reqId, ok: true, payload: result }));
          }
        })
        .catch((err) => {
          if (clientWs.readyState === WebSocket.OPEN) {
            clientWs.send(JSON.stringify({
              type: 'res',
              id: reqId,
              ok: false,
              error: { code: -32000, message: (err as Error).message },
            }));
          }
        });
      return;
    }

    clientToGatewayCount++;
    gwWs.send(isBinary ? data : data.toString());
  });

  clientWs.on('close', (code, reason) => {
    clearInterval(pingTimer);
    const duration = Date.now() - connStartTime;
    console.log(`${tag} Client closed: code=${code}, reason=${reason?.toString()}`);
    console.log(`${tag} Summary: duration=${duration}ms, client->gw=${clientToGatewayCount}, gw->client=${gatewayToClientCount}`);
    gwWs.close();
  });
  clientWs.on('error', (err) => {
    clearInterval(pingTimer);
    console.error(`${tag} Client error:`, err.message);
    gwWs.close();
  });
}
