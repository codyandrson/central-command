import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useTTS } from './useTTS';

// The press-to-play seam: speakNow ignores the sound toggle, stop cancels an
// in-flight fetch, and `speaking` tracks the utterance so the button can
// flip to a stop control.
describe('useTTS speakNow / stop', () => {
  let resolveFetch: (r: Response) => void;
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((res) => { resolveFetch = res; })));
    vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:x', revokeObjectURL: () => {} });
    vi.stubGlobal('Audio', class { play = () => Promise.resolve(); pause() {} addEventListener() {} src = ''; });
  });

  it('speak() honours the toggle; speakNow() does not', async () => {
    const { result } = renderHook(() => useTTS(false, 'edge'));
    await act(() => result.current.speak('hello there'));
    expect(fetch).not.toHaveBeenCalled();
    act(() => { void result.current.speakNow('hello there'); });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(result.current.speaking).toBe('hello there');
  });

  it('stop() during fetch clears speaking and drops the late response', async () => {
    const { result } = renderHook(() => useTTS(true, 'edge'));
    act(() => { void result.current.speakNow('read this'); });
    act(() => result.current.stop());
    expect(result.current.speaking).toBeNull();
    await act(async () => { resolveFetch(new Response(new Blob(['x']), { headers: { 'Content-Type': 'audio/mpeg' } })); });
    expect(result.current.speaking).toBeNull();
  });
});
