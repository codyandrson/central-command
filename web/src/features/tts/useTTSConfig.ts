import { useState, useEffect, useRef, useCallback } from 'react';

export interface TTSVoiceConfig {
  openai: {
    model: string;
    voice: string;
    instructions: string;
    /** Voice saved for the deployment's speech service (deployment speech mode). */
    deploymentVoice?: string;
  };
  /**
   * Set by the server when OPENAI_TTS_MODEL is configured: the deployment owns
   * the TTS model and the voice sent when a request names none. null otherwise.
   */
  deploymentSpeech?: DeploymentSpeech | null;
}

export interface DeploymentSpeech {
  model: string;
  voice: string;
  voiceSource: 'env' | 'saved' | 'default';
}

/** The sections a field can be saved in (everything but the server-computed deploymentSpeech). */
type ConfigSection = Exclude<keyof TTSVoiceConfig, 'deploymentSpeech'>;

interface UseTTSConfigReturn {
  config: TTSVoiceConfig | null;
  loading: boolean;
  error: string | null;
  saved: boolean;
  updateField: (provider: ConfigSection, field: string, value: string) => void;
}

/**
 * Hook that loads and auto-saves TTS voice configuration from `/api/tts/config`.
 *
 * The text field (voice instructions) is debounced before saving;
 * all other fields save immediately on change.
 */
export function useTTSConfig(): UseTTSConfigReturn {
  const [config, setConfig] = useState<TTSVoiceConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const debounceTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const savedTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    fetch('/api/tts/config')
      .then((r) => {
        if (!r.ok) throw new Error(`TTS config request failed: ${r.status}`);
        return r.json();
      })
      .then((data) => { setConfig(data); setLoading(false); })
      .catch((e) => { setError(e.message); setLoading(false); });
  }, []);

  const saveConfig = useCallback((patch: Record<string, unknown>) => {
    fetch('/api/tts/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
      .then((r) => {
        if (!r.ok) throw new Error(`TTS config request failed: ${r.status}`);
        return r.json();
      })
      .then((updated) => {
        setConfig(updated);
        setSaved(true);
        clearTimeout(savedTimer.current);
        savedTimer.current = setTimeout(() => setSaved(false), 1500);
      })
      .catch((e) => setError(e.message));
  }, []);

  const updateField = useCallback(
    (provider: ConfigSection, field: string, value: string) => {
      // Optimistic update
      setConfig((prev) => {
        if (!prev) return prev;
        return { ...prev, [provider]: { ...prev[provider], [field]: value } };
      });

      const patch = { [provider]: { [field]: value } };
      const key = `${provider}.${field}`;
      const isTextField = field === 'instructions';

      if (isTextField) {
        clearTimeout(debounceTimers.current[key]);
        debounceTimers.current[key] = setTimeout(() => saveConfig(patch), 500);
      } else {
        saveConfig(patch);
      }
    },
    [saveConfig],
  );

  // Cleanup timers
  useEffect(() => {
    const timers = debounceTimers.current;
    return () => {
      Object.values(timers).forEach(clearTimeout);
      clearTimeout(savedTimer.current);
    };
  }, []);

  return { config, loading, error, saved, updateField };
}
