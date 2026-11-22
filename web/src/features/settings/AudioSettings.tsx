import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { Volume2, VolumeX, Mic, MicOff, Globe } from 'lucide-react';
import { Switch } from '@/components/ui/switch';
import { InlineSelect } from '@/components/ui/InlineSelect';
import { OFFERED_PROVIDER_LABEL, type TTSProvider } from '@/features/tts/useTTS';
import { useTTSConfig, type DeploymentSpeech } from '@/features/tts/useTTSConfig';
import { VoicePhrasesModal } from './VoicePhrasesModal';
import { buildPrimaryWakePhrase } from '@/lib/constants';
import { getWakeWordSupport } from '@/features/voice/wakeWordSupport';

// ─── Language types ──────────────────────────────────────────────────────────

interface LanguageInfo {
  code: string;
  name: string;
  nativeName: string;
}

interface LanguageState {
  language: string;
  supported: LanguageInfo[];
}

const INLINE_SELECT_TRIGGER_CLASS =
  'min-h-11 w-full justify-between rounded-2xl border-border/80 bg-background/65 px-3 py-2 text-left text-sm font-sans text-foreground sm:w-auto';
const INLINE_SELECT_MENU_CLASS =
  'rounded-2xl border-border/80 bg-card/98 p-1 shadow-[0_20px_48px_rgba(0,0,0,0.28)]';

/** Hook to manage language preference via the /api/language endpoints. */
function useLanguage() {
  const [state, setState] = useState<LanguageState | null>(null);

  // Fetch current language on mount
  useEffect(() => {
    const langController = new AbortController();

    fetch('/api/language', { signal: langController.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((langData) => {
        if (!langController.signal.aborted && langData) {
          setState(langData);
        }
      })
      .catch((err) => {
        if ((err as DOMException)?.name !== 'AbortError') {
          console.warn('[settings] failed to fetch /api/language');
        }
      });

    return () => {
      langController.abort();
    };
  }, []);

  const setLanguage = useCallback(async (language: string): Promise<boolean> => {
    try {
      const res = await fetch('/api/language', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language }),
      });
      if (!res.ok) return false;

      const data = await res.json();
      setState((prev) => prev ? { ...prev, language: data.language } : prev);
      return true;
    } catch {
      return false;
    }
  }, []);

  return { state, setLanguage };
}

/** Single-line input that expands into a textarea on focus, collapses on blur. */
function ExpandableInput({
  value,
  onChange,
  placeholder,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  label: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const collapse = useCallback(() => {
    // Small delay so click inside textarea doesn't trigger collapse
    setTimeout(() => {
      if (containerRef.current && !containerRef.current.contains(document.activeElement)) {
        setExpanded(false);
      }
    }, 100);
  }, []);

  useEffect(() => {
    if (expanded && textareaRef.current) {
      textareaRef.current.focus();
      // Move cursor to end
      const len = textareaRef.current.value.length;
      textareaRef.current.setSelectionRange(len, len);
    }
  }, [expanded]);

  return (
    <div ref={containerRef} className="cockpit-row flex-col items-stretch gap-2">
      <span className="cockpit-field-label">{label}</span>
      {expanded ? (
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={collapse}
          rows={4}
          className="min-h-[96px] w-full resize-none border-none bg-transparent p-0 text-sm text-foreground/82 outline-none transition-all"
          placeholder={placeholder}
        />
      ) : (
        <div
          onClick={() => setExpanded(true)}
          className="w-full cursor-text truncate text-sm text-foreground/72 opacity-80 transition-opacity hover:opacity-100"
          title={value || placeholder}
        >
          {value || <span className="text-muted-foreground">{placeholder}</span>}
        </div>
      )}
    </div>
  );
}

type AudioSettingsSection = 'all' | 'input' | 'output';

interface AudioSettingsProps {
  soundEnabled: boolean;
  onToggleSound: () => void;
  ttsProvider: TTSProvider;
  ttsModel: string;
  onTtsModelChange: (model: string) => void;
  wakeWordEnabled: boolean;
  onToggleWakeWord: () => void;
  liveTranscriptionPreview: boolean;
  onToggleLiveTranscriptionPreview: () => void;
  agentName?: string;
  section?: AudioSettingsSection;
}

/** Same shape the server accepts for a deployment voice (web/server/lib/tts-config.ts). */
const DEPLOYMENT_VOICE_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

/**
 * Voice row for deployment speech mode: one text field holding the voice the
 * deployment's speech engine will be asked for. Read-only when the environment
 * sets it; otherwise saved on blur or Enter (a half-typed name is never sent).
 * The caller keys it by the effective voice, so the draft restarts when that changes.
 */
function DeploymentVoiceField({
  deployment,
  onSave,
  error,
}: {
  deployment: DeploymentSpeech;
  onSave: (voice: string) => void;
  error: string | null;
}) {
  const fromEnv = deployment.voiceSource === 'env';
  const [draft, setDraft] = useState(deployment.voice);
  const trimmed = draft.trim();
  const invalid = !fromEnv && trimmed !== '' && !DEPLOYMENT_VOICE_PATTERN.test(trimmed);

  const commit = () => {
    if (fromEnv || trimmed === deployment.voice) return;
    if (!DEPLOYMENT_VOICE_PATTERN.test(trimmed)) return;
    onSave(trimmed);
  };

  // Stacked, not side by side: the settings drawer is narrow, and a fixed-width
  // field beside this hint squeezed the text to one word per line.
  return (
    <div className="cockpit-row flex-col items-stretch gap-3">
      <div className="min-w-0">
        <span className="text-sm font-medium text-foreground">Voice</span>
        <p className="mt-1 text-xs text-muted-foreground">
          {fromEnv
            ? 'Set by the deployment (OPENAI_TTS_VOICE); it cannot be changed here.'
            : 'Must be a voice the deployment\u2019s speech engine provides; a wrong name makes read-aloud fail.'}
        </p>
        {invalid && (
          <p className="mt-1 text-xs text-destructive">Use letters, digits, underscore or hyphen (up to 64 characters).</p>
        )}
        {!invalid && error && <p className="mt-1 text-xs text-destructive">{error}</p>}
      </div>
      <input
        type="text"
        aria-label="Deployment Voice"
        value={draft}
        readOnly={fromEnv}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') commit(); }}
        className="cockpit-input cockpit-input-mono h-11 w-full"
      />
    </div>
  );
}

/** Models of the OpenAI-shaped endpoint outside deployment speech mode. */
const PROVIDER_MODELS: Record<TTSProvider, { value: string; label: string }[]> = {
  openai: [
    { value: '', label: 'gpt-4o-mini-tts (default)' },
    { value: 'tts-1', label: 'tts-1' },
    { value: 'tts-1-hd', label: 'tts-1-hd' },
  ],
};

/** Settings section for notification sounds, TTS model and voice, and wake-word toggle. */
export function AudioSettings({
  soundEnabled,
  onToggleSound,
  ttsProvider,
  ttsModel,
  onTtsModelChange,
  wakeWordEnabled,
  onToggleWakeWord,
  liveTranscriptionPreview,
  onToggleLiveTranscriptionPreview,
  agentName = 'Agent',
  section = 'all',
}: AudioSettingsProps) {
  const models = PROVIDER_MODELS[ttsProvider] || [];
  const showInput = section === 'all' || section === 'input';
  const showOutput = section === 'all' || section === 'output';
  const wakeWordSupport = useMemo(() => getWakeWordSupport(), []);
  const wakeWordSupported = wakeWordSupport.supported;
  const effectiveWakeWordEnabled = wakeWordSupported ? wakeWordEnabled : false;
  const headingLabel = section === 'input' ? 'Input Capture' : section === 'output' ? 'Voice Output' : 'Audio';
  const headingCopy = section === 'input'
    ? 'Tune language detection, wake phrases, and transcription before speech reaches the agent.'
    : section === 'output'
      ? 'Shape the speaking voice, model, and playback behavior for replies and announcements.'
      : '';
  const { config, loading: ttsConfigLoading, error: ttsConfigError, saved, updateField } = useTTSConfig();
  // Deployment speech mode (server: OPENAI_TTS_MODEL set): the deployment owns
  // the model and voice, so the OpenAI model/voice/instructions controls do not apply.
  const deployment = ttsProvider === 'openai' ? config?.deploymentSpeech ?? null : null;
  // Until the config answers, do not flash the OpenAI model list for a deployment.
  const awaitingDeploymentMode = ttsProvider === 'openai' && ttsConfigLoading === true;
  const { state: langState, setLanguage } = useLanguage();

  // Voice phrases modal — opens when switching to non-English without configured phrases
  const [phrasesModal, setPhrasesModal] = useState<{
    open: boolean;
    code: string;
    name: string;
    nativeName: string;
  }>({ open: false, code: '', name: '', nativeName: '' });

  // Track which languages have custom phrases
  const [phrasesStatus, setPhrasesStatus] = useState<Record<string, { configured: boolean }>>({});
  const [activeWakePhrase, setActiveWakePhrase] = useState('');
  useEffect(() => {
    fetch('/api/voice-phrases/status')
      .then((r) => {
        if (!r.ok) throw new Error('Failed to fetch phrase status');
        return r.json();
      })
      .then(setPhrasesStatus)
      .catch(() => {});
  }, [phrasesModal.open]); // Refetch after modal closes (might have saved)

  useEffect(() => {
    const lang = langState?.language;
    if (!lang) return;

    let cancelled = false;
    fetch(`/api/voice-phrases?lang=${lang}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled) return;
        const customWake = Array.isArray(data?.wakePhrases)
          ? data.wakePhrases.map((phrase: string) => phrase.trim()).find(Boolean) || ''
          : '';
        setActiveWakePhrase(customWake);
      })
      .catch(() => {
        if (!cancelled) setActiveWakePhrase('');
      });

    return () => {
      cancelled = true;
    };
  }, [langState?.language, phrasesModal.open]);

  // Keep language switches lightweight; phrase editing is explicit via the CTA button.
  const handleLanguageChange = useCallback((code: string) => {
    void setLanguage(code).then((saved) => {
      if (!saved) return;
      // Notify InputBar after language update succeeds
      window.dispatchEvent(new CustomEvent('nerve:language-changed'));
    });
  }, [setLanguage]);

  const currentLangInfo = useMemo(() => {
    if (!langState) return null;
    return langState.supported.find((l) => l.code === langState.language) || null;
  }, [langState]);

  // All 13 OpenAI TTS voices. tts-1 and tts-1-hd only support a subset (no ballad, cedar, marin, verse).
  const LEGACY_ONLY_VOICES = new Set(['ballad', 'cedar', 'marin', 'verse']);
  const isLegacyModel = ttsModel === 'tts-1' || ttsModel === 'tts-1-hd';
  const OPENAI_VOICES = [
    { value: 'alloy', label: 'Alloy — Neutral, balanced' },
    { value: 'ash', label: 'Ash — Warm, conversational' },
    { value: 'ballad', label: 'Ballad — Expressive, storytelling' },
    { value: 'cedar', label: 'Cedar — Calm, steady' },
    { value: 'coral', label: 'Coral — Clear, friendly' },
    { value: 'echo', label: 'Echo — Smooth, calm' },
    { value: 'fable', label: 'Fable — British-accented, narrative' },
    { value: 'marin', label: 'Marin — Warm, approachable' },
    { value: 'nova', label: 'Nova — Energetic, young' },
    { value: 'onyx', label: 'Onyx — Deep, authoritative' },
    { value: 'sage', label: 'Sage — Wise, measured' },
    { value: 'shimmer', label: 'Shimmer — Soft, gentle' },
    { value: 'verse', label: 'Verse — Versatile, dynamic' },
  ].filter(v => !isLegacyModel || !LEGACY_ONLY_VOICES.has(v.value));

  const wakePhraseDisplay = useMemo(() => {
    const phrase = buildPrimaryWakePhrase(agentName, langState?.language || 'en', activeWakePhrase ? [activeWakePhrase] : undefined);
    if (!phrase) return `Hey ${agentName}`;
    return phrase.charAt(0).toUpperCase() + phrase.slice(1);
  }, [activeWakePhrase, agentName, langState?.language]);

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <span className="cockpit-kicker">
          <span className="text-primary">◆</span>
          {headingLabel}
        </span>
        {headingCopy && <p className="cockpit-copy max-w-[36ch]">{headingCopy}</p>}
      </div>

      {/* Language Preference */}
      {showInput && langState && (
        <div className="space-y-3">
          <div className="cockpit-row flex-col items-stretch gap-3">
            <div className="flex min-w-0 items-start gap-3">
              <Globe size={14} className="text-primary" aria-hidden="true" />
              <div className="flex min-w-0 flex-col">
                <span className="text-sm font-medium text-foreground">Language</span>
                <span className="text-xs text-muted-foreground">Match wake phrases, STT support, and voice options to the language you actually use.</span>
              </div>
            </div>
            <InlineSelect
              value={langState.language}
              onChange={handleLanguageChange}
              options={langState.supported.map((l) => ({
                value: l.code,
                label: `${l.name} — ${l.nativeName}`,
              }))}
              ariaLabel="Voice Language"
              triggerClassName={`${INLINE_SELECT_TRIGGER_CLASS} w-full justify-between`}
              menuClassName={`${INLINE_SELECT_MENU_CLASS} min-w-[220px] max-w-[calc(100vw-2rem)]`}
            />
          </div>

          {/* Configure Voice Phrases button — available for all languages (including English) */}
          <div className="space-y-2">
            <button
              type="button"
              onClick={() => {
                setPhrasesModal({
                  open: true,
                  code: langState.language,
                  name: currentLangInfo?.name || langState.language,
                  nativeName: currentLangInfo?.nativeName || langState.language,
                });
              }}
              className="cockpit-row group w-full items-start justify-between text-left"
            >
              <div className="flex items-start gap-3">
                <Mic size={14} className="text-primary" aria-hidden="true" />
                <div className="flex flex-col">
                  <span className="text-sm font-medium text-foreground">Voice phrases</span>
                  <span className="text-xs text-muted-foreground">Customize wake, send, and cancel phrases for {currentLangInfo?.name || langState.language}.</span>
                </div>
              </div>
              <span className="cockpit-badge group-hover:text-primary" data-tone={phrasesStatus[langState.language]?.configured ? 'primary' : undefined}>
                {phrasesStatus[langState.language]?.configured ? 'Edit' : 'Configure'}
              </span>
            </button>
            {!phrasesStatus[langState.language]?.configured && (
              <span className="cockpit-field-hint px-1">
                Optional: customize wake/send/cancel phrases for {currentLangInfo?.name || langState.language}.
              </span>
            )}
          </div>

        </div>
      )}

      {/* Sound Effects */}
      {showOutput && (
        <div className="cockpit-row items-start justify-between">
          <div className="flex items-center gap-3">
            {soundEnabled ? (
              <Volume2 size={14} className="text-green" aria-hidden="true" />
            ) : (
              <VolumeX size={14} className="text-muted-foreground" aria-hidden="true" />
            )}
            <div className="flex flex-col">
              <span className="text-sm font-medium text-foreground" id="sound-label">Sound effects</span>
              <span className="text-xs text-muted-foreground">Keep subtle UI cues and audio confirmations enabled.</span>
            </div>
          </div>
          <Switch
            checked={soundEnabled}
            onCheckedChange={onToggleSound}
            aria-label="Toggle sound effects"
          />
        </div>
      )}

      {/* TTS Provider */}
      {showOutput && (
        <div className="space-y-2">
          <span className="cockpit-field-label">TTS Provider</span>
          <p className="text-sm font-medium text-foreground">{OFFERED_PROVIDER_LABEL}</p>
          <p className="cockpit-field-hint px-1">Replies are read aloud by this service.</p>
        </div>
      )}

      {/* TTS Model: the deployment's alias as a plain label in deployment speech mode */}
      {showOutput && deployment && (
        <div className="cockpit-row items-start justify-between">
          <div className="min-w-0 flex-1">
            <span className="text-sm font-medium text-foreground">TTS model</span>
            <p className="mt-1 text-xs text-muted-foreground">The model is set by the deployment.</p>
          </div>
          <span className="font-mono text-sm text-foreground/82" data-testid="deployment-tts-model">{deployment.model}</span>
        </div>
      )}

      {/* TTS Model (shown when provider has multiple models) */}
      {showOutput && !deployment && !awaitingDeploymentMode && models.length > 0 && (
        <div className="cockpit-row items-start justify-between">
          <div className="min-w-0 flex-1">
            <span className="text-sm font-medium text-foreground">TTS model</span>
            <p className="mt-1 text-xs text-muted-foreground">Select the synthesis model exposed by the active provider.</p>
          </div>
          <InlineSelect
            value={ttsModel}
            onChange={onTtsModelChange}
            options={models}
            ariaLabel="TTS Model"
            triggerClassName={`${INLINE_SELECT_TRIGGER_CLASS} min-w-[188px]`}
            menuClassName={`${INLINE_SELECT_MENU_CLASS} min-w-[200px]`}
          />
        </div>
      )}

      {/* Voice Config */}
      {showOutput && config && (
        <div className="space-y-3">
          {saved && (
            <div className="cockpit-note border-green/25 bg-green/8 text-green">
              <span className="font-mono text-[0.733rem] animate-pulse">Saved ✓</span>
            </div>
          )}

          {ttsProvider === 'openai' && deployment && (
            <DeploymentVoiceField
              key={deployment.voice}
              deployment={deployment}
              onSave={(v) => updateField('openai', 'deploymentVoice', v)}
              error={ttsConfigError}
            />
          )}

          {ttsProvider === 'openai' && !deployment && (
            <>
              <div className="cockpit-row items-start justify-between">
                <div className="min-w-0 flex-1">
                  <span className="text-sm font-medium text-foreground">Voice</span>
                  <p className="mt-1 text-xs text-muted-foreground">Pick the OpenAI voice profile used for reply playback.</p>
                </div>
                <InlineSelect
                  value={config.openai.voice}
                  onChange={(v) => updateField('openai', 'voice', v)}
                  options={OPENAI_VOICES}
                  ariaLabel="OpenAI Voice"
                  triggerClassName={`${INLINE_SELECT_TRIGGER_CLASS} min-w-[220px]`}
                  menuClassName={`${INLINE_SELECT_MENU_CLASS} min-w-[260px]`}
                />
              </div>
              <ExpandableInput
                label="Voice Instructions"
                value={config.openai.instructions}
                onChange={(v) => updateField('openai', 'instructions', v)}
                placeholder="Describe how the voice should sound..."
              />
            </>
          )}

        </div>
      )}

      {/* Wake Word */}
      {showInput && (
        <div className="cockpit-row items-start justify-between">
          <div className="flex items-center gap-3">
            {effectiveWakeWordEnabled ? (
              <Mic size={14} className="text-green" aria-hidden="true" />
            ) : (
              <MicOff size={14} className="text-muted-foreground" aria-hidden="true" />
            )}
            <div className="flex flex-col">
              <span className="text-sm font-medium text-foreground" id="wake-word-label">Wake word</span>
              {wakeWordSupported ? (
                <span className="text-xs text-muted-foreground">Say "{wakePhraseDisplay}" to activate.</span>
              ) : (
                <>
                  <span className="text-xs text-muted-foreground">Wake word isn't supported on mobile web.</span>
                  <span className="text-xs text-muted-foreground">Use the manual mic trigger instead.</span>
                </>
              )}
            </div>
          </div>
          <Switch
            checked={effectiveWakeWordEnabled}
            onCheckedChange={onToggleWakeWord}
            disabled={!wakeWordSupported}
            aria-label="Toggle wake word detection"
          />
        </div>
      )}

      {/* Speech-to-Text */}
      {showInput && (
        <div className="space-y-1.5 pt-2">
          <span className="cockpit-kicker">
            <span className="text-primary">◆</span>
            Speech to Text
          </span>
        </div>
      )}

      {showInput && (
        <div className="space-y-2">
          <span className="cockpit-field-label">STT Provider</span>
          <p className="text-sm font-medium text-foreground">{OFFERED_PROVIDER_LABEL}</p>
          <span className="cockpit-field-hint px-1">Using the deployment speech service (cc-stt)</span>
        </div>
      )}

      {showInput && (
        <div className="cockpit-row items-start justify-between">
          <div className="flex items-center gap-3">
            <Mic size={14} className={liveTranscriptionPreview ? 'text-primary' : 'text-muted-foreground'} aria-hidden="true" />
            <div className="flex flex-col">
              <span className="text-sm font-medium text-foreground" id="live-transcription-label">Live transcription preview</span>
              <span className="text-xs text-muted-foreground">Show a browser preview while speaking; the committed transcript may still differ by provider.</span>
            </div>
          </div>
          <Switch
            checked={liveTranscriptionPreview}
            onCheckedChange={onToggleLiveTranscriptionPreview}
            aria-label="Toggle live transcription preview"
          />
        </div>
      )}

      {/* Voice Phrases Modal — shown when switching to non-English language */}
      <VoicePhrasesModal
        open={phrasesModal.open}
        onClose={() => {
          setPhrasesModal(prev => ({ ...prev, open: false }));
          // Phrases may have changed — notify voice input to refetch phrase config.
          window.dispatchEvent(new CustomEvent('nerve:voice-phrases-changed'));
        }}
        languageCode={phrasesModal.code}
        languageName={phrasesModal.name}
        languageNativeName={phrasesModal.nativeName}
      />
    </div>
  );
}
