/**
 * Sentence-by-sentence read-aloud: chunks are requested one ahead and played
 * strictly in order, `speaking` holds across the gaps, and stop / replace /
 * toggle-off / unmount cancel everything.
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CLIENT_ERROR_EVENT, type ClientErrorDetail } from '@/features/notifications/clientToast';
import { SPEECH_TOTAL_MAX_CHARS } from './speechChunks';
import { useTTS } from './useTTS';

interface Call {
  text: string;
  provider: string;
  signal: AbortSignal;
  resolve: (r: Response) => void;
  reject: (e: unknown) => void;
}

let calls: Call[];
let audios: FakeAudio[];
let events: ClientErrorDetail[];
const onEvent = (e: Event) => events.push((e as CustomEvent<ClientErrorDetail>).detail);

class FakeAudio {
  src: string;
  paused = false;
  played = false;
  listeners: Record<string, Array<() => void>> = {};
  constructor(src = '') {
    this.src = src;
    audios.push(this);
  }
  addEventListener(type: string, fn: () => void) { (this.listeners[type] ??= []).push(fn); }
  pause() { this.paused = true; }
  play() { this.played = true; return Promise.resolve(); }
  fire(type: string) { for (const fn of this.listeners[type] ?? []) fn(); }
}

const mp3 = () => new Response(new Uint8Array([1, 2, 3]), { status: 200, headers: { 'Content-Type': 'audio/mpeg' } });
const refused = () => new Response('speech engine busy', { status: 503 });

/** Let resolved promises and the Response body reads run. */
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const answer = async (i: number, make: () => Response = mp3) => { calls[i].resolve(make()); await settle(); };
const endAudio = async (i: number) => { audios[i].fire('ended'); await settle(); };

// Three sentences, each long enough to stay its own chunk.
const S1 = 'The first sentence is long enough to stand on its own.';
const S2 = 'The second sentence is also long enough to stand alone.';
const S3 = 'The third sentence closes the reply and stands alone too.';
const THREE = `${S1} ${S2} ${S3}`;

beforeEach(() => {
  calls = [];
  audios = [];
  events = [];
  window.addEventListener(CLIENT_ERROR_EVENT, onEvent);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.stubGlobal('Audio', FakeAudio);
  URL.createObjectURL = vi.fn(() => 'blob:x');
  URL.revokeObjectURL = vi.fn();
  vi.stubGlobal('fetch', vi.fn((_url: string, init: { body: string; signal: AbortSignal }) => {
    const body = JSON.parse(init.body) as { text: string; provider: string };
    return new Promise<Response>((resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
      calls.push({ text: body.text, provider: body.provider, signal: init.signal, resolve, reject });
    });
  }));
});

afterEach(() => {
  window.removeEventListener(CLIENT_ERROR_EVENT, onEvent);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('order and prefetch', () => {
  it('plays chunk 1 as soon as it arrives, requests chunk 2 while it plays, and never more than one ahead', async () => {
    const { result } = renderHook(() => useTTS(true, 'openai'));
    act(() => { void result.current.speakNow(THREE); });
    expect(calls.map((c) => c.text)).toEqual([S1]); // only the first is requested up front
    expect(calls[0].provider).toBe('openai');
    expect(result.current.speaking).toBe(THREE);

    await answer(0);
    expect(audios).toHaveLength(1);
    expect(audios[0].played).toBe(true);
    expect(calls.map((c) => c.text)).toEqual([S1, S2]); // chunk 2 requested while 1 plays

    await answer(1); // chunk 2 is ready early; it must wait for chunk 1 to end
    expect(audios).toHaveLength(1);
    expect(calls).toHaveLength(2); // no third request until 2 starts

    await endAudio(0);
    expect(audios).toHaveLength(2);
    expect(audios[1].played).toBe(true);
    expect(calls.map((c) => c.text)).toEqual([S1, S2, S3]);

    await answer(2);
    expect(audios).toHaveLength(2); // 3 waits for 2
    expect(result.current.speaking).toBe(THREE);
    await endAudio(1);
    expect(audios).toHaveLength(3);
    expect(result.current.speaking).toBe(THREE);
    await endAudio(2);
    expect(result.current.speaking).toBeNull();
    expect(calls).toHaveLength(3);
  });

  it('plays in order even when a later response resolves first', async () => {
    const { result } = renderHook(() => useTTS(true, 'openai'));
    act(() => { void result.current.speakNow(THREE); });
    await answer(0);
    await endAudio(0); // chunk 2 not here yet
    expect(audios).toHaveLength(1);
    expect(calls).toHaveLength(2);
    // chunk 3 is requested only once chunk 2 plays, so it cannot overtake it
    await answer(1);
    expect(audios).toHaveLength(2);
    expect(calls).toHaveLength(3);
    await answer(2);
    expect(audios).toHaveLength(2);
    await endAudio(1);
    expect(audios).toHaveLength(3);
  });

  it('speakNow resolves once the first chunk is playing, not when the reply ends', async () => {
    const { result } = renderHook(() => useTTS(true, 'openai'));
    let done = false;
    act(() => { void result.current.speakNow(THREE).then(() => { done = true; }); });
    await settle();
    expect(done).toBe(false);
    await answer(0);
    expect(done).toBe(true);
    expect(result.current.speaking).toBe(THREE);
  });
});

describe('a slow chunk', () => {
  it('waits for it, skips nothing, and keeps speaking true through the gap', async () => {
    const { result } = renderHook(() => useTTS(true, 'openai'));
    act(() => { void result.current.speakNow(THREE); });
    await answer(0);
    await endAudio(0);
    // chunk 1 is over, chunk 2 still synthesising
    expect(audios).toHaveLength(1);
    expect(result.current.speaking).toBe(THREE);
    await settle();
    expect(result.current.speaking).toBe(THREE);
    await answer(1);
    expect(audios).toHaveLength(2);
    expect(result.current.speaking).toBe(THREE);
    await endAudio(1);
    await answer(2);
    await endAudio(2);
    expect(audios).toHaveLength(3);
    expect(result.current.speaking).toBeNull();
  });
});

describe('cancelling', () => {
  it('stop() aborts the in-flight fetch, plays nothing further and ignores a late response', async () => {
    const { result } = renderHook(() => useTTS(true, 'openai'));
    act(() => { void result.current.speakNow(THREE); });
    await answer(0);
    expect(calls[1].signal.aborted).toBe(false);
    act(() => result.current.stop());
    expect(calls[1].signal.aborted).toBe(true);
    expect(audios[0].paused).toBe(true);
    expect(result.current.speaking).toBeNull();
    calls[1].resolve(mp3()); // too late
    await settle();
    audios[0].fire('ended');
    await settle();
    expect(audios).toHaveLength(1);
    expect(calls).toHaveLength(2);
    expect(events).toHaveLength(0); // an abort is not a failure
  });

  it('stop() before any chunk arrives aborts the first fetch', async () => {
    const { result } = renderHook(() => useTTS(true, 'openai'));
    act(() => { void result.current.speakNow(THREE); });
    act(() => result.current.stop());
    expect(calls[0].signal.aborted).toBe(true);
    await settle();
    expect(audios).toHaveLength(0);
    expect(events).toHaveLength(0);
  });

  it('a new speakNow replaces the old utterance entirely', async () => {
    const { result } = renderHook(() => useTTS(true, 'openai'));
    act(() => { void result.current.speakNow(THREE); });
    await answer(0);
    const next = 'A different reply that is long enough to be one chunk.';
    act(() => { void result.current.speakNow(next); });
    expect(calls[1].signal.aborted).toBe(true); // the old prefetch
    expect(audios[0].paused).toBe(true);
    expect(result.current.speaking).toBe(next);
    expect(calls[2].text).toBe(next);
    await answer(2);
    expect(audios).toHaveLength(2);
    calls[1].resolve(mp3()); // the old prefetch resolving late changes nothing
    await settle();
    audios[0].fire('ended');
    await settle();
    expect(audios).toHaveLength(2);
    await endAudio(1);
    expect(result.current.speaking).toBeNull();
    expect(calls.map((c) => c.text)).toEqual([S1, S2, next]);
  });

  it('speak() replaces a current utterance like speakNow does', async () => {
    const { result } = renderHook(() => useTTS(true, 'openai'));
    act(() => { void result.current.speakNow(THREE); });
    await answer(0);
    const next = 'A newer announcement that is long enough to be one chunk.';
    act(() => { void result.current.speak(next); });
    expect(audios[0].paused).toBe(true);
    expect(result.current.speaking).toBe(next);
  });

  it('unmounting aborts fetches and stops the audio', async () => {
    const { result, unmount } = renderHook(() => useTTS(true, 'openai'));
    act(() => { void result.current.speakNow(THREE); });
    await answer(0);
    unmount();
    expect(calls[1].signal.aborted).toBe(true);
    expect(audios[0].paused).toBe(true);
    calls[1].resolve(mp3());
    await settle();
    expect(audios).toHaveLength(1);
  });

  it('turning the sound setting off stops an auto-spoken utterance', async () => {
    const { result, rerender } = renderHook(({ on }) => useTTS(on, 'openai'), { initialProps: { on: true } });
    act(() => { void result.current.speak(THREE); });
    await answer(0);
    rerender({ on: false });
    expect(calls[1].signal.aborted).toBe(true);
    expect(audios[0].paused).toBe(true);
    expect(result.current.speaking).toBeNull();
  });

  it('turning the sound setting off leaves an utterance the operator started with the button', async () => {
    const { result, rerender } = renderHook(({ on }) => useTTS(on, 'openai'), { initialProps: { on: true } });
    act(() => { void result.current.speakNow(THREE); });
    await answer(0);
    rerender({ on: false });
    expect(calls[1].signal.aborted).toBe(false);
    expect(audios[0].paused).toBe(false);
    expect(result.current.speaking).toBe(THREE);
  });
});

describe('the total length cap', () => {
  it('speaks only up to the cap, ends on a chunk boundary and says so once', async () => {
    const sentence = 'This is one sentence of about sixty characters, give or take.';
    const text = Array.from({ length: 98 }, () => sentence).join(' '); // about 6,000 characters
    expect(text.length).toBeGreaterThan(5900);
    const { result } = renderHook(() => useTTS(true, 'openai'));
    act(() => { void result.current.speakNow(text); });

    // Play the utterance through, answering each request as it is made.
    for (let i = 0; i < 100; i++) {
      if (calls.length <= i) break;
      await answer(i);
      await endAudio(i);
    }
    await settle();

    const spoken = calls.map((c) => c.text);
    const spokenChars = spoken.reduce((n, t) => n + t.length, 0);
    expect(spokenChars).toBeLessThanOrEqual(SPEECH_TOTAL_MAX_CHARS);
    expect(spokenChars).toBeGreaterThan(SPEECH_TOTAL_MAX_CHARS - 300);
    expect(spoken.every((t) => t.endsWith('.'))).toBe(true); // the cut is on a chunk boundary
    expect(result.current.speaking).toBeNull();

    expect(events).toHaveLength(1);
    expect(events[0].id).toBe('read-aloud-truncated');
    expect(events[0].body).toContain(`first ${spokenChars.toLocaleString('en-US')} characters`);
  });

  it('says nothing about a cap for a text under it', async () => {
    const { result } = renderHook(() => useTTS(true, 'openai'));
    act(() => { void result.current.speakNow(S1); });
    await answer(0);
    await endAudio(0);
    expect(events).toHaveLength(0);
  });
});

describe('a chunk that fails', () => {
  it('stops the utterance after the chunks before it, with one notice carrying the reason', async () => {
    const { result } = renderHook(() => useTTS(true, 'openai'));
    act(() => { void result.current.speakNow(THREE); });
    await answer(0);
    await answer(1, refused); // the prefetch fails while chunk 1 still plays
    expect(audios[0].paused).toBe(false); // chunk 1 is not cut off
    expect(events).toHaveLength(0);
    await endAudio(0);
    expect(events).toHaveLength(1);
    expect(events[0].title).toBe('Read-aloud failed');
    expect(events[0].body).toContain('503');
    expect(events[0].body).toContain('speech engine busy');
    expect(result.current.speaking).toBeNull();
    expect(audios).toHaveLength(1); // chunk 3 is never requested or played
    expect(calls).toHaveLength(2);
  });

  it('a failed first chunk stops the utterance and raises the notice', async () => {
    const { result } = renderHook(() => useTTS(true, 'openai'));
    await act(async () => { const p = result.current.speakNow(THREE); await new Promise((r) => setTimeout(r, 0)); calls[0].resolve(refused()); await p; });
    expect(events).toHaveLength(1);
    expect(result.current.speaking).toBeNull();
    expect(calls).toHaveLength(1);
  });

  it('keeps one notice per failure streak and a new one after audio has played', async () => {
    const { result } = renderHook(() => useTTS(true, 'openai'));
    for (const outcome of [refused, refused]) {
      act(() => { void result.current.speakNow(S1); });
      await answer(calls.length - 1, outcome);
    }
    expect(events).toHaveLength(1);

    act(() => { void result.current.speakNow(S2); });
    await answer(calls.length - 1);
    await endAudio(audios.length - 1);
    act(() => { void result.current.speakNow(S3); });
    await answer(calls.length - 1, refused);
    expect(events).toHaveLength(2);
  });

  it('a playback error in the middle of an utterance stops it with a notice', async () => {
    const { result } = renderHook(() => useTTS(true, 'openai'));
    act(() => { void result.current.speakNow(THREE); });
    await answer(0);
    audios[0].fire('error');
    await settle();
    expect(events).toHaveLength(1);
    expect(result.current.speaking).toBeNull();
    expect(calls).toHaveLength(2); // 2 was already requested; nothing further
  });
});
