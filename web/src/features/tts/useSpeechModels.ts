import { useState, useEffect, useCallback } from 'react';

/**
 * One side of `GET /api/speech/models` (read-aloud or dictation). Served by the
 * cockpit server on k3s (`server/routes/cc-speech-models.ts`) and by the API on
 * the single-node profile (`central_command/api/speech.py`); the wire shape is
 * pinned by the backend test `tests/test_speech_models.py`.
 */
export interface SpeechSide {
  /** The model in use now. */
  selected: string;
  /** The deployment's alias (`cc-tts` / `cc-stt`): what an unset choice uses. */
  default: string;
  /** The models LiteLLM serves for this mode; empty when the list is unavailable. */
  models: string[];
}

export interface SpeechModels {
  /** null: this deployment does not route read-aloud through LiteLLM. */
  tts: SpeechSide | null;
  /** null: this deployment does not route dictation through LiteLLM. */
  stt: SpeechSide | null;
  /** Why the lists are empty, when they could not be read. */
  error: string | null;
}

export type SpeechKind = 'tts' | 'stt';

interface UseSpeechModelsReturn {
  /** null until loaded, and when the route could not be reached at all. */
  models: SpeechModels | null;
  loading: boolean;
  /** The route itself failed (not the LiteLLM list, which is `models.error`). */
  loadError: string | null;
  /** The last save the server refused, by side. */
  saveError: Partial<Record<SpeechKind, string>>;
  choose: (kind: SpeechKind, model: string) => Promise<void>;
}

function side(value: unknown): SpeechSide | null {
  if (!value || typeof value !== 'object') return null;
  const v = value as Record<string, unknown>;
  if (typeof v.selected !== 'string' || !v.selected) return null;
  return {
    selected: v.selected,
    default: typeof v.default === 'string' ? v.default : '',
    models: Array.isArray(v.models) ? v.models.filter((m): m is string => typeof m === 'string') : [],
  };
}

function parse(data: unknown): SpeechModels {
  const d = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>;
  return {
    tts: side(d.tts),
    stt: side(d.stt),
    error: typeof d.error === 'string' && d.error ? d.error : null,
  };
}

/** Loads the speech model lists and the current choice; `choose` saves one side. */
export function useSpeechModels(): UseSpeechModelsReturn {
  const [models, setModels] = useState<SpeechModels | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<Partial<Record<SpeechKind, string>>>({});

  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/speech/models', { signal: controller.signal })
      .then(async (r) => {
        if (!r.ok) throw new Error(`speech model list request failed: ${r.status}`);
        return r.json();
      })
      .then((data) => { setModels(parse(data)); setLoading(false); })
      .catch((e) => {
        if ((e as DOMException)?.name === 'AbortError') return;
        setLoadError((e as Error).message);
        setLoading(false);
      });
    return () => controller.abort();
  }, []);

  const choose = useCallback(async (kind: SpeechKind, model: string) => {
    setSaveError((prev) => ({ ...prev, [kind]: undefined }));
    try {
      const r = await fetch('/api/speech/models', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [kind]: model }),
      });
      if (!r.ok) {
        const text = await r.text().catch(() => '');
        let message = text;
        try { message = (JSON.parse(text) as { detail?: string }).detail || text; } catch { /* plain text */ }
        throw new Error(message || `HTTP ${r.status}`);
      }
      setModels(parse(await r.json()));
    } catch (e) {
      setSaveError((prev) => ({ ...prev, [kind]: (e as Error).message }));
    }
  }, []);

  return { models, loading, loadError, saveError, choose };
}
