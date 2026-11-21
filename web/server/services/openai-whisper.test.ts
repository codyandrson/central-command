/**
 * transcribe (OpenAI-shaped): with OPENAI_STT_MODEL naming the deployment's
 * alias (cc-stt: one job at a time; LiteLLM 1.110 rejects a second concurrent
 * request with a 429) calls take a FIFO line; a plain OpenAI endpoint does not.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

async function load(env: Record<string, string>) {
  vi.resetModules();
  vi.unstubAllEnvs();
  vi.stubEnv('OPENAI_STT_MODEL', '');
  for (const [k, v] of Object.entries(env)) vi.stubEnv(k, v);
  vi.doMock('../lib/config.js', () => ({ config: { openaiApiKey: 'sk-test', language: 'en' } }));
  return import('./openai-whisper.js');
}

const tick = () => new Promise((r) => setTimeout(r, 5));
const audio = Buffer.from('RIFFxxxx');

function slowFetch() {
  const state = { inside: 0, peak: 0, calls: 0 };
  const releases: Array<() => void> = [];
  const fn = vi.fn(async () => {
    state.calls += 1;
    state.inside += 1;
    state.peak = Math.max(state.peak, state.inside);
    await new Promise<void>((resolve) => releases.push(resolve));
    state.inside -= 1;
    return new Response(JSON.stringify({ text: 'hello' }), { status: 200 });
  });
  return { fn, state, releases };
}

describe('transcribe', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it('with an STT alias, lets one call reach the proxy at a time', async () => {
    const mod = await load({ OPENAI_STT_MODEL: 'cc-stt' });
    const f = slowFetch();
    vi.stubGlobal('fetch', f.fn);
    const calls = [1, 2, 3].map(() => mod.transcribe(audio, 'a.webm'));
    await tick();
    expect(f.state.calls).toBe(1);
    f.releases.shift()!();
    await tick();
    expect(f.state.calls).toBe(2);
    f.releases.shift()!();
    await tick();
    f.releases.shift()!();
    const results = await Promise.all(calls);
    expect(results).toEqual([
      { ok: true, text: 'hello' },
      { ok: true, text: 'hello' },
      { ok: true, text: 'hello' },
    ]);
    expect(f.state.peak).toBe(1);
  });

  it('a call aborted while it waits sends nothing (status 499)', async () => {
    const mod = await load({ OPENAI_STT_MODEL: 'cc-stt' });
    const f = slowFetch();
    vi.stubGlobal('fetch', f.fn);
    const first = mod.transcribe(audio, 'a.webm');
    const gone = new AbortController();
    const abandoned = mod.transcribe(audio, 'b.webm', 'audio/webm', undefined, gone.signal);
    await tick();
    gone.abort();
    expect(await abandoned).toEqual({ ok: false, status: 499, message: 'client disconnected before its turn' });
    f.releases.shift()!();
    expect((await first).ok).toBe(true);
    expect(f.state.calls).toBe(1);
  });

  it('a 429 from the proxy is surfaced with its status and body, never retried', async () => {
    const mod = await load({ OPENAI_STT_MODEL: 'cc-stt' });
    const fn = vi.fn(async () => new Response('slots in use', { status: 429 }));
    vi.stubGlobal('fetch', fn);
    expect(await mod.transcribe(audio, 'a.webm')).toEqual({ ok: false, status: 429, message: 'slots in use' });
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('without OPENAI_STT_MODEL (plain OpenAI) is not gated', async () => {
    const mod = await load({});
    const f = slowFetch();
    vi.stubGlobal('fetch', f.fn);
    const calls = [mod.transcribe(audio, 'a.webm'), mod.transcribe(audio, 'b.webm')];
    await tick();
    expect(f.state.calls).toBe(2);
    f.releases.splice(0).forEach((r) => r());
    await Promise.all(calls);
  });
});
