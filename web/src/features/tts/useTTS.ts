import { useRef, useState, useCallback, useEffect } from 'react';
import { ensureAudioContext } from '@/features/voice/audio-feedback';
import { reportClientError } from '@/features/notifications/clientToast';
import { planSpeech } from './speechChunks';

// ─── Audio autoplay unlock ─────────────────────────────────────────────────────
// Browsers block audio.play() until the user has interacted with the page.
// We "unlock" audio by resuming the shared AudioContext on the first user gesture,
// which whitelists the origin for subsequent programmatic playback.
if (typeof document !== 'undefined') {
  const events = ['click', 'touchstart', 'keydown'] as const;
  const handler = () => {
    ensureAudioContext();
    events.forEach(e => document.removeEventListener(e, handler, true));
  };
  events.forEach(e => document.addEventListener(e, handler, { capture: true, once: false }));
}

export type TTSProvider = 'openai' | 'replicate' | 'edge' | 'xiaomi';

/**
 * The speech providers this deployment offers. Deploy setup points the
 * 'openai' provider at the deployment's own speech service through the
 * LiteLLM proxy (cc-tts / cc-stt). Replicate, Edge and Xiaomi TTS call
 * third-party internet services, and 'local' STT needs whisper model files
 * nothing installs, so the settings UI and command palette hide them; their
 * server code stays. Internal ids are unchanged.
 */
export const OFFERED_PROVIDERS = {
  tts: ['openai'],
  stt: ['openai'],
} as const satisfies { tts: readonly TTSProvider[]; stt: readonly string[] };

/** Label for the one remaining provider, wherever the UI names it. */
export const OFFERED_PROVIDER_LABEL = 'Deployment speech service (cc-tts / cc-stt)';

/** A stored or default TTS provider that is not offered resolves to 'openai'. */
export function coerceTTSProvider(provider: string): TTSProvider {
  const migrated = migrateTTSProvider(provider);
  return (OFFERED_PROVIDERS.tts as readonly string[]).includes(migrated) ? migrated : 'openai';
}

/** A stored or default STT provider that is not offered resolves to 'openai'. */
export function coerceSTTProvider(provider: string | null | undefined): 'local' | 'openai' {
  return provider !== null && provider !== undefined && (OFFERED_PROVIDERS.stt as readonly string[]).includes(provider)
    ? (provider as 'local' | 'openai')
    : 'openai';
}

/** @deprecated Use 'replicate' instead. Kept for migration. */
export type LegacyTTSProvider = 'qwen';

/** Migrate legacy provider names to current ones. */
export function migrateTTSProvider(provider: string): TTSProvider {
  if (provider === 'qwen') return 'replicate';
  if (provider === 'openai' || provider === 'replicate' || provider === 'edge' || provider === 'xiaomi') return provider;
  return 'openai';
}

async function fetchTTS(text: string, provider: TTSProvider = 'openai', model?: string, signal?: AbortSignal): Promise<Blob> {
  const body: Record<string, string> = { text, provider };
  if (model) body.model = model;
  const resp = await fetch('/api/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) {
    // The server relays the speech service's own refusal text; keep it so the
    // operator can see why (a refused model, an unknown voice) and not just a status.
    const detail = (await resp.text().catch(() => '')).trim().slice(0, 300);
    throw new Error(detail ? `TTS failed: ${resp.status} ${detail}` : `TTS failed: ${resp.status}`);
  }
  // Use the server's content type (audio/mpeg for MP3, audio/wav for WAV)
  const arrayBuffer = await resp.arrayBuffer();
  const ct = resp.headers.get('Content-Type') || 'audio/mpeg';
  const blob = new Blob([arrayBuffer], { type: ct });
  return blob;
}

/**
 * After a chunk ends, the phase turns to `loading` only if the next chunk has not
 * started playing within this long, so a gap-free handover never flickers.
 */
export const TTS_LOADING_DELAY_MS = 250;

/** `loading`: nothing audible yet (or between chunks); `playing`: audio is audible. */
export type SpeakingPhase = 'loading' | 'playing';

/** One utterance: a text cut into chunks that are requested and played one after another. */
interface Utterance {
  /** The whole text as given; this is what `speaking` carries. */
  text: string;
  /** Started by `speak` (the sound toggle governs it) rather than by an explicit press. */
  auto: boolean;
  /** Aborts every fetch of this utterance. */
  controller: AbortController;
  cancelled: boolean;
  /** The chunk being played. */
  audio: { audio: HTMLAudioElement; url: string } | null;
  /** Releases the wait for the playing chunk to end (used on cancel). */
  wake: (() => void) | null;
  /** Pending switch to `loading` after a chunk ended with the next one not yet playing. */
  loadingTimer: ReturnType<typeof setTimeout> | null;
}

function clearLoadingTimer(utt: Utterance): void {
  if (utt.loadingTimer !== null) {
    clearTimeout(utt.loadingTimer);
    utt.loadingTimer = null;
  }
}

function releaseAudio(utt: Utterance): void {
  const current = utt.audio;
  if (!current) return;
  utt.audio = null;
  current.audio.pause();
  current.audio.src = '';
  URL.revokeObjectURL(current.url);
}

/** Cancel an utterance for good: abort its fetches, stop its audio, let nothing start late. */
function cancelUtterance(utt: Utterance): void {
  if (utt.cancelled) return;
  utt.cancelled = true;
  utt.controller.abort();
  clearLoadingTimer(utt);
  releaseAudio(utt);
  utt.wake?.();
}

/**
 * Hook that provides a `speak` function for text-to-speech playback.
 *
 * The text is cut into sentence-sized chunks (see speechChunks.ts). Each chunk
 * is fetched from `/api/tts` and played via an `HTMLAudioElement` as soon as it
 * arrives, strictly in order; while one plays, the next is already requested
 * (one ahead only: the speech service does one job at a time). Successive calls
 * cancel the previous utterance automatically.
 */
export function useTTS(enabled: boolean, provider: TTSProvider = 'openai', model?: string) {
  const currentRef = useRef<Utterance | null>(null);
  /** True while read-aloud is failing: one notice per streak, cleared when audio plays. */
  const failingRef = useRef(false);
  /** Text of the utterance currently fetching or playing — null when idle. */
  const [speaking, setSpeaking] = useState<string | null>(null);
  /** Which phase that utterance is in; null when idle. */
  const [speakingPhase, setSpeakingPhase] = useState<SpeakingPhase | null>(null);

  /** Cancel whatever is fetching or playing. */
  const stop = useCallback(() => {
    const utt = currentRef.current;
    currentRef.current = null;
    if (utt) cancelUtterance(utt);
    setSpeaking(null);
    setSpeakingPhase(null);
  }, []);

  // Unmount cancels the utterance: no fetch left running, no chunk started late.
  useEffect(() => {
    return () => {
      const utt = currentRef.current;
      currentRef.current = null;
      if (utt) cancelUtterance(utt);
    };
  }, []);

  // Turning the sound setting off ends an auto-spoken utterance; an utterance the
  // operator started with the speaker button does not depend on that setting.
  useEffect(() => {
    if (enabled) return;
    const utt = currentRef.current;
    if (utt?.auto) stop();
  }, [enabled, stop]);

  /** Speak `text`, replacing any current utterance. Resolves when the first chunk is playing, or the utterance has failed or ended. */
  const begin = useCallback((text: string, auto: boolean): Promise<void> => {
    if (!text) return Promise.resolve();
    const previous = currentRef.current;
    if (previous) cancelUtterance(previous);
    const plan = planSpeech(text);
    if (plan.chunks.length === 0) {
      currentRef.current = null;
      setSpeaking(null);
      setSpeakingPhase(null);
      return Promise.resolve();
    }
    const utt: Utterance = { text, auto, controller: new AbortController(), cancelled: false, audio: null, wake: null, loadingTimer: null };
    currentRef.current = utt;
    setSpeaking(text);
    setSpeakingPhase('loading');

    const request = (i: number): Promise<Blob> => {
      const p = fetchTTS(plan.chunks[i], provider, model, utt.controller.signal);
      p.catch(() => {}); // a failure is raised when playback reaches this chunk
      return p;
    };

    return new Promise<void>((resolveStarted) => {
      void (async () => {
        try {
          let pending: Promise<Blob> | null = request(0);
          for (let i = 0; i < plan.chunks.length; i++) {
            const blob = await pending!;
            // Superseded or stopped while the chunk was being fetched
            if (utt.cancelled) return;
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            utt.audio = { audio, url };
            const ended = new Promise<void>((resolve, reject) => {
              utt.wake = resolve;
              audio.addEventListener('playing', () => {
                if (utt.cancelled) return;
                clearLoadingTimer(utt);
                setSpeakingPhase('playing');
              });
              audio.addEventListener('ended', () => resolve(), { once: true });
              audio.addEventListener('error', () => reject(new Error('Audio playback failed')), { once: true });
            });
            ended.catch(() => {});
            await audio.play();
            if (utt.cancelled) return;
            failingRef.current = false;
            // One request ahead, issued once this chunk is playing.
            pending = i + 1 < plan.chunks.length ? request(i + 1) : null;
            if (i === 0) resolveStarted();
            await ended;
            if (utt.cancelled) return;
            if (i + 1 < plan.chunks.length) {
              // Show `loading` only if the next chunk is not audible shortly.
              clearLoadingTimer(utt);
              utt.loadingTimer = setTimeout(() => {
                utt.loadingTimer = null;
                if (!utt.cancelled && currentRef.current === utt) setSpeakingPhase('loading');
              }, TTS_LOADING_DELAY_MS);
            }
            releaseAudio(utt);
          }
          if (plan.truncated) {
            reportClientError({
              id: 'read-aloud-truncated',
              title: 'Read-aloud shortened',
              body: `Read-aloud stopped after the first ${plan.spokenChars.toLocaleString('en-US')} characters of a longer reply.`,
            });
          }
        } catch (err: unknown) {
          if (utt.cancelled) return;
          const message = err instanceof Error ? err.message : String(err);
          console.error('[TTS] play failed:', message);
          if (!failingRef.current) {
            failingRef.current = true;
            reportClientError({ id: 'read-aloud', title: 'Read-aloud failed', body: message });
          }
        } finally {
          resolveStarted();
          clearLoadingTimer(utt);
          releaseAudio(utt);
          if (currentRef.current === utt) {
            currentRef.current = null;
            setSpeaking(null);
            setSpeakingPhase(null);
          }
        }
      })();
    });
  }, [provider, model]);

  /** Speak regardless of the sound toggle — for an explicit operator press. */
  const speakNow = useCallback((text: string) => begin(text, false), [begin]);

  /** Auto-speak path: honours the sound toggle. */
  const speak = useCallback(async (text: string) => {
    if (!enabled) return;
    await begin(text, true);
  }, [enabled, begin]);

  return { speak, speakNow, stop, speaking, speakingPhase };
}

/** Strip [tts:...] markers from text, returning cleaned text and the first TTS text found */
export function extractTTSMarkers(text: string): { cleaned: string; ttsText: string | null } {
  let ttsText: string | null = null;
  const cleaned = text.replace(/\[tts:([^\]]+)\]/g, (_, t) => {
    if (ttsText === null) ttsText = t;
    return '';
  });
  return { cleaned: cleaned.trim(), ttsText };
}
