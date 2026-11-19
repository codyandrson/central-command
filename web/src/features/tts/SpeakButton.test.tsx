import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, render, screen, fireEvent } from '@testing-library/react';
import { useTTS } from './useTTS';
import { SpeakButton } from './SpeakButton';
import { useOptionalSettings } from '@/contexts/SettingsContext';

vi.mock('@/contexts/SettingsContext', () => ({ useOptionalSettings: vi.fn() }));

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

describe('SpeakButton phases', () => {
  const TEXT = 'Read this sentence aloud please.';
  const stopSpeaking = vi.fn();
  const speakNow = vi.fn();
  const setSettings = (speaking: string | null, speakingPhase: 'loading' | 'playing' | null | undefined) => {
    vi.mocked(useOptionalSettings).mockReturnValue({ speakNow, stopSpeaking, speaking, speakingPhase } as never);
  };
  beforeEach(() => { stopSpeaking.mockClear(); speakNow.mockClear(); });

  it('shows the spinner, busy state and preparing label while loading, and a press cancels', () => {
    setSettings(TEXT, 'loading');
    render(<SpeakButton text={TEXT} />);
    const button = screen.getByRole('button', { name: 'Preparing audio — press to stop' });
    expect(button.getAttribute('aria-busy')).toBe('true');
    expect(button.getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByTestId('speak-spinner')).toBeTruthy();
    fireEvent.click(button);
    expect(stopSpeaking).toHaveBeenCalledTimes(1);
    expect(speakNow).not.toHaveBeenCalled();
  });

  it('shows the stop state while playing, and when the phase is unknown but the text matches', () => {
    for (const phase of ['playing', null, undefined] as const) {
      setSettings(TEXT, phase);
      const { unmount } = render(<SpeakButton text={TEXT} />);
      const button = screen.getByRole('button', { name: 'Stop reading aloud' });
      expect(button.getAttribute('aria-busy')).toBeNull();
      expect(screen.queryByTestId('speak-spinner')).toBeNull();
      unmount();
    }
  });

  it('shows the play state when another text is being spoken or nothing is', () => {
    setSettings('Some other text entirely.', 'loading');
    render(<SpeakButton text={TEXT} />);
    const button = screen.getByRole('button', { name: 'Read aloud' });
    expect(button.getAttribute('aria-pressed')).toBe('false');
    expect(button.getAttribute('aria-busy')).toBeNull();
    fireEvent.click(button);
    expect(speakNow).toHaveBeenCalledTimes(1);
  });
});
