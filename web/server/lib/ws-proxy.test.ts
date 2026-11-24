// @vitest-environment node
/**
 * Integration tests for the `/ws` relay: a real upstream WebSocket server, the
 * real relay in front of it and a real client, all on ephemeral loopback ports.
 * Only the cockpit config is replaced (a plain object the tests point at the
 * upstream); sessions, the Origin check and http-proxy-3 run for real.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { createServer, type Server, type IncomingMessage } from 'node:http';
import type { AddressInfo, Socket } from 'node:net';
import { WebSocket, WebSocketServer, type RawData } from 'ws';

vi.mock('./config.js', () => ({
  config: {
    auth: false,
    host: '127.0.0.1',
    port: 3080,
    sslPort: 3443,
    sessionSecret: 'test-secret',
    gatewayUrl: '',
  },
  WS_ALLOWED_HOSTS: new Set(['localhost', '127.0.0.1', '::1']),
  SESSION_COOKIE_NAME: 'nerve_session_test',
}));

import { setupWebSocketProxy, closeAllWebSockets } from './ws-proxy.js';
import { config } from './config.js';
import { createSession } from './session.js';

const cfg = config as { auth: boolean; sessionSecret: string; gatewayUrl: string };
const ORIGIN = 'http://localhost:3080';

/** Everything a test starts, closed in afterEach. */
const servers: Server[] = [];
const clients: WebSocket[] = [];
const sockets = new Set<Socket>();

function listen(server: Server): Promise<number> {
  servers.push(server);
  server.on('connection', (s) => {
    sockets.add(s);
    s.on('close', () => sockets.delete(s));
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve((server.address() as AddressInfo).port));
  });
}

interface Upstream {
  port: number;
  wss: WebSocketServer;
  /** TCP connections the upstream accepted — 0 means the relay never dialled it. */
  tcpConnections: () => number;
  /** The upgrade requests the upstream saw. */
  requests: IncomingMessage[];
  /** Resolves with the next upstream-side socket. */
  nextConnection: () => Promise<WebSocket>;
}

async function startUpstream(): Promise<Upstream> {
  const http = createServer((_req, res) => { res.writeHead(404); res.end(); });
  const wss = new WebSocketServer({ server: http });
  let tcp = 0;
  http.on('connection', () => { tcp++; });
  const requests: IncomingMessage[] = [];
  const waiting: ((ws: WebSocket) => void)[] = [];
  const ready: WebSocket[] = [];
  wss.on('connection', (ws, req) => {
    requests.push(req);
    const w = waiting.shift();
    if (w) w(ws); else ready.push(ws);
  });
  const port = await listen(http);
  cfg.gatewayUrl = `http://127.0.0.1:${port}`;
  return {
    port,
    wss,
    tcpConnections: () => tcp,
    requests,
    nextConnection: () => new Promise((resolve) => {
      const r = ready.shift();
      if (r) resolve(r); else waiting.push(resolve);
    }),
  };
}

async function startRelay(): Promise<number> {
  const http = createServer((_req, res) => { res.writeHead(404); res.end(); });
  setupWebSocketProxy(http);
  return listen(http);
}

function connect(port: number, opts: { path?: string; origin?: string; cookie?: string } = {}): WebSocket {
  const headers: Record<string, string> = { Origin: opts.origin ?? ORIGIN };
  if (opts.cookie) headers.Cookie = opts.cookie;
  const ws = new WebSocket(`ws://127.0.0.1:${port}${opts.path ?? '/ws'}`, { headers });
  clients.push(ws);
  return ws;
}

function opened(ws: WebSocket): Promise<void> {
  return new Promise((resolve, reject) => {
    ws.once('open', () => resolve());
    ws.once('error', reject);
  });
}

function nextMessage(ws: WebSocket): Promise<{ data: Buffer; isBinary: boolean }> {
  return new Promise((resolve) => {
    ws.once('message', (data: RawData, isBinary: boolean) => resolve({ data: data as Buffer, isBinary }));
  });
}

function closed(ws: WebSocket): Promise<{ code: number; reason: string }> {
  return new Promise((resolve) => {
    if (ws.readyState === WebSocket.CLOSED) return resolve({ code: 1006, reason: '' });
    ws.once('close', (code, reason) => resolve({ code, reason: reason.toString() }));
  });
}

/** The HTTP status the relay refused the upgrade with, or the error if it gave none. */
function refusal(ws: WebSocket): Promise<{ status?: number; error?: string }> {
  return new Promise((resolve) => {
    ws.once('unexpected-response', (_req, res) => {
      resolve({ status: res.statusCode });
      res.resume();
      ws.terminate();
    });
    ws.once('error', (err) => resolve({ error: err.message }));
    ws.once('open', () => resolve({ error: 'opened' }));
  });
}

afterEach(async () => {
  closeAllWebSockets();
  for (const c of clients.splice(0)) c.terminate();
  for (const s of sockets) s.destroy();
  sockets.clear();
  await Promise.all(servers.splice(0).map((s) => new Promise<void>((r) => s.close(() => r()))));
  cfg.auth = false;
  cfg.gatewayUrl = '';
});

describe('ws-proxy relaying', () => {
  it('relays text and binary frames both ways unchanged', async () => {
    const up = await startUpstream();
    const relay = await startRelay();
    const client = connect(relay);
    await opened(client);
    const server = await up.nextConnection();

    const bin = Buffer.from([0, 1, 2, 250, 255]);
    let got = nextMessage(server);
    client.send('{"type":"req","id":"1"}');
    expect(await got).toEqual({ data: Buffer.from('{"type":"req","id":"1"}'), isBinary: false });
    got = nextMessage(server);
    client.send(bin);
    expect(await got).toEqual({ data: bin, isBinary: true });

    got = nextMessage(client);
    server.send('{"type":"event"}');
    expect(await got).toEqual({ data: Buffer.from('{"type":"event"}'), isBinary: false });
    got = nextMessage(client);
    server.send(bin);
    expect(await got).toEqual({ data: bin, isBinary: true });
  });

  it('delivers a frame the client sends the moment it opens, and one the API sends on accept', async () => {
    const up = await startUpstream();
    up.wss.on('connection', (ws) => ws.send('challenge'));
    const relay = await startRelay();
    const client = connect(relay);
    const fromApi = nextMessage(client);
    client.on('open', () => client.send('first'));
    const server = await up.nextConnection();
    const first = await nextMessage(server);
    expect(first.data.toString()).toBe('first');
    expect((await fromApi).data.toString()).toBe('challenge');
  });

  it('dials the configured /ws path, with the API host and without the cockpit cookie', async () => {
    const up = await startUpstream();
    const relay = await startRelay();
    const client = connect(relay, { cookie: 'nerve_session_test=abc; other=1' });
    await opened(client);
    const [req] = up.requests;
    expect(req.url).toBe('/ws');
    expect(req.headers.cookie).toBeUndefined();
    expect(req.headers.host).toBe(`127.0.0.1:${up.port}`);
    expect(req.headers.origin).toBe(ORIGIN);
  });
});

describe('ws-proxy refusals open no upstream connection', () => {
  it('refuses an unauthenticated upgrade when auth is on', async () => {
    const up = await startUpstream();
    const relay = await startRelay();
    cfg.auth = true;
    expect(await refusal(connect(relay))).toEqual({ status: 401 });
    expect(await refusal(connect(relay, { cookie: 'nerve_session_test=forged.token' }))).toEqual({ status: 401 });
    expect(up.tcpConnections()).toBe(0);
  });

  it('lets a valid session through when auth is on', async () => {
    const up = await startUpstream();
    const relay = await startRelay();
    cfg.auth = true;
    const token = createSession(cfg.sessionSecret, 60_000);
    const client = connect(relay, { cookie: `nerve_session_test=${token}` });
    await opened(client);
    expect(up.tcpConnections()).toBe(1);
  });

  it('refuses a disallowed Origin', async () => {
    const up = await startUpstream();
    const relay = await startRelay();
    expect(await refusal(connect(relay, { origin: 'https://evil.example.com' }))).toEqual({ status: 403 });
    expect(up.tcpConnections()).toBe(0);
  });

  it('refuses a ?target= that is malformed, not ws, or not on an allowed host', async () => {
    const up = await startUpstream();
    const relay = await startRelay();
    const q = (t: string) => `/ws?target=${encodeURIComponent(t)}`;
    expect(await refusal(connect(relay, { path: q('not a url') }))).toEqual({ status: 400 });
    expect(await refusal(connect(relay, { path: q(`http://127.0.0.1:${up.port}/ws`) }))).toEqual({ status: 403 });
    expect(await refusal(connect(relay, { path: q('ws://gateway.example.com/ws') }))).toEqual({ status: 403 });
    expect(up.tcpConnections()).toBe(0);
  });

  it('accepts an allowlisted ?target= and dials exactly that URL', async () => {
    const up = await startUpstream();
    const relay = await startRelay();
    cfg.gatewayUrl = '';
    const client = connect(relay, { path: `/ws?target=${encodeURIComponent(`ws://localhost:${up.port}/ws`)}` });
    await opened(client);
    expect(up.requests[0].url).toBe('/ws');
  });

  it('refuses when there is no ?target= and no usable gateway url', async () => {
    const relay = await startRelay();
    cfg.gatewayUrl = '';
    expect(await refusal(connect(relay))).toEqual({ status: 502 });
  });

  it('drops an upgrade on any other path', async () => {
    const up = await startUpstream();
    const relay = await startRelay();
    const r = await refusal(connect(relay, { path: '/other' }));
    expect(r.error).toBeDefined();
    expect(r.error).not.toBe('opened');
    expect(up.tcpConnections()).toBe(0);
  });
});

describe('ws-proxy lifecycle', () => {
  it('fails promptly when the API is down', async () => {
    // A port that was just free: listen, note it, close.
    const probe = createServer();
    const deadPort = await new Promise<number>((r) => probe.listen(0, '127.0.0.1', () => r((probe.address() as AddressInfo).port)));
    await new Promise<void>((r) => probe.close(() => r()));
    cfg.gatewayUrl = `http://127.0.0.1:${deadPort}`;
    const relay = await startRelay();
    const t0 = Date.now();
    const client = connect(relay);
    const r = await refusal(client);
    expect(r.error).toBeDefined();
    expect(r.error).not.toBe('opened');
    await closed(client);
    expect(Date.now() - t0).toBeLessThan(2000);
  });

  it('closes the client when the API closes, passing the close code through', async () => {
    const up = await startUpstream();
    const relay = await startRelay();
    const client = connect(relay);
    await opened(client);
    const server = await up.nextConnection();
    const c = closed(client);
    server.close(4001, 'api says bye');
    expect(await c).toEqual({ code: 4001, reason: 'api says bye' });
  });

  it('closes the API side when the client closes, passing the close code through', async () => {
    const up = await startUpstream();
    const relay = await startRelay();
    const client = connect(relay);
    await opened(client);
    const server = await up.nextConnection();
    const s = closed(server);
    client.close(1000, 'tab closed');
    expect(await s).toEqual({ code: 1000, reason: 'tab closed' });
  });

  it('closeAllWebSockets closes live connections on both sides', async () => {
    const up = await startUpstream();
    const relay = await startRelay();
    const a = connect(relay);
    const b = connect(relay);
    await Promise.all([opened(a), opened(b)]);
    const sa = await up.nextConnection();
    const sb = await up.nextConnection();
    const all = Promise.all([closed(a), closed(b), closed(sa), closed(sb)]);
    closeAllWebSockets();
    const results = await all;
    expect(results).toHaveLength(4);
    expect([a, b, sa, sb].every((w) => w.readyState === WebSocket.CLOSED)).toBe(true);
  });
});
