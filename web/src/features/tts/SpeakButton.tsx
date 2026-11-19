import { Volume2, Square, Loader2 } from 'lucide-react';
import { useOptionalSettings } from '@/contexts/SettingsContext';
import { buildVoiceFallbackText } from '@/hooks/useChatTTS';

/** The TTS route caps text at 5000 chars (server/routes/tts.ts). */
const MAX_SPEECH_CHARS = 4800;

/** Turn on-screen markdown/prose into one speakable string, or null if there is nothing to say. */
function speechText(raw: string | null | undefined): string | null {
  return raw ? buildVoiceFallbackText(raw, MAX_SPEECH_CHARS) : null;
}

/**
 * Speaker button: press to read `text` aloud through the configured TTS
 * provider; press again to stop. Ignores the auto-speak sound toggle — a
 * press is explicit intent. Renders nothing when there is nothing speakable.
 */
export function SpeakButton({ text, className = '' }: { text: string | null | undefined; className?: string }) {
  const settings = useOptionalSettings();
  const spoken = speechText(text);
  if (!settings || !spoken) return null;
  const { speakNow, stopSpeaking, speaking, speakingPhase } = settings;
  const active = speaking === spoken;
  const loading = active && speakingPhase === 'loading';
  return (
    <button
      type="button"
      className={`cockpit-toolbar-button min-h-7 px-2 text-[0.667rem] ${className}`}
      aria-label={loading ? 'Preparing audio — press to stop' : active ? 'Stop reading aloud' : 'Read aloud'}
      title={loading ? 'Preparing audio — press to stop' : active ? 'Stop' : 'Read aloud'}
      aria-pressed={active}
      aria-busy={loading ? true : undefined}
      onClick={(e) => { e.stopPropagation(); if (active) stopSpeaking(); else void speakNow(spoken); }}
    >
      {loading ? <Loader2 size={12} className="animate-spin motion-reduce:animate-none" data-testid="speak-spinner" /> : active ? <Square size={12} /> : <Volume2 size={12} />}
    </button>
  );
}
