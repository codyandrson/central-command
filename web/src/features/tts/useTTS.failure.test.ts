/**
 * Read-aloud failures reach the operator: the speech service's own refusal is
 * kept, and ONE notice is raised per failure streak (not one per sentence).
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CLIENT_ERROR_EVENT, type ClientErrorDetail } from '@/features/notifications/clientToast';
import { useTTS } from './useTTS';

const REFUSAL = '{"error":{"message":"key not allowed to access model gpt-4o-mini-tts"}}';

let responses: Array<() => Response>;
let events: ClientErrorDetail[];
const onEvent = (e: Event) => events.push((e as CustomEvent<ClientErrorDetail>).detail);

class FakeAudio {
  src = '';
  addEventListener() {}
  pause() {}
  play() { return Promise.resolve(); }
}

beforeEach(() => {
  events = [];
  responses = [];
  window.addEventListener(CLIENT_ERROR_EVENT, onEvent);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.stubGlobal('Audio', FakeAudio);
  URL.createObjectURL = vi.fn(() => 'blob:x');
  URL.revokeObjectURL = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async () => (responses.shift() ?? (() => new Response('', { status: 500 })))()));
});

afterEach(() => {
  window.removeEventListener(CLIENT_ERROR_EVENT, onEvent);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const refused = () => new Response(REFUSAL, { status: 403 });
const audio = () => new Response(new Uint8Array([1, 2, 3]), { status: 200, headers: { 'Content-Type': 'audio/mpeg' } });

describe('useTTS failures', () => {
  it('shows why a request was refused, using the server text', async () => {
    responses = [refused];
    const { result } = renderHook(() => useTTS(true, 'openai'));
    await act(async () => { await result.current.speakNow('Hello'); });

    expect(events).toHaveLength(1);
    expect(events[0].title).toBe('Read-aloud failed');
    expect(events[0].body).toContain('403');
    expect(events[0].body).toContain('key not allowed to access model');
  });

  it('raises one notice for a streak of failures, not one per sentence', async () => {
    responses = [refused, refused, refused];
    const { result } = renderHook(() => useTTS(true, 'openai'));
    await act(async () => { await result.current.speakNow('One.'); });
    await act(async () => { await result.current.speakNow('Two.'); });
    await act(async () => { await result.current.speakNow('Three.'); });

    expect(events).toHaveLength(1);
  });

  it('raises a new notice once audio has played in between', async () => {
    responses = [refused, audio, refused];
    const { result } = renderHook(() => useTTS(true, 'openai'));
    await act(async () => { await result.current.speakNow('One.'); });
    await act(async () => { await result.current.speakNow('Two.'); });
    await act(async () => { await result.current.speakNow('Three.'); });

    expect(events).toHaveLength(2);
  });
});
