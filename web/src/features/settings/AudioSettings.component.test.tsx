import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi, Mock } from 'vitest';
import { AudioSettings } from './AudioSettings';
import * as wakeWordSupport from '@/features/voice/wakeWordSupport';

vi.mock('@/features/voice/wakeWordSupport', () => ({
  getWakeWordSupport: vi.fn(() => ({ supported: true, reason: null })),
  isWakeWordSupportedEnvironment: vi.fn(() => true),
}));

const updateField = vi.fn();

// What GET /api/tts/config says about deployment speech mode (null = not in the mode).
const ttsState = vi.hoisted(() => ({
  deploymentSpeech: null as null | { model: string; voice: string; voiceSource: 'env' | 'saved' | 'default' },
}));

vi.mock('@/features/tts/useTTSConfig', () => ({
  useTTSConfig: () => ({
    config: {
      deploymentSpeech: ttsState.deploymentSpeech,
      edge: { voice: 'en-US-AriaNeural' },
      openai: { model: 'tts-1', voice: 'alloy', instructions: '' },
      qwen: { mode: 'voice_design', language: 'English', speaker: 'Serena', voiceDescription: '', styleInstruction: '' },
      xiaomi: { model: 'mimo-v2-tts', voice: 'mimo_default', style: '' },
    },
    saved: true,
    updateField,
  }),
}));

vi.mock('./VoicePhrasesModal', () => ({
  VoicePhrasesModal: () => null,
}));

type InlineSelectOption = { value: string; label: string };
type InlineSelectMockProps = {
  ariaLabel: string;
  options: InlineSelectOption[];
  value: string;
  onChange: (value: string) => void;
};

vi.mock('@/components/ui/InlineSelect', () => ({
  InlineSelect: ({ ariaLabel, options, value, onChange }: InlineSelectMockProps) => (
    <select aria-label={ariaLabel} value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>{opt.label}</option>
      ))}
    </select>
  ),
}));

const baseProps = {
  soundEnabled: true,
  onToggleSound: vi.fn(),
  ttsProvider: 'edge' as const,
  ttsModel: 'tts-1',
  onTtsProviderChange: vi.fn(),
  onTtsModelChange: vi.fn(),
  sttProvider: 'local' as const,
  sttInputMode: 'hybrid' as const,
  sttModel: 'base',
  onSttProviderChange: vi.fn(),
  onSttInputModeChange: vi.fn(),
  onSttModelChange: vi.fn(),
  wakeWordEnabled: true,
  onToggleWakeWord: vi.fn(),
  liveTranscriptionPreview: true,
  onToggleLiveTranscriptionPreview: vi.fn(),
  agentName: 'Kim',
  section: 'input' as const,
};

function mockWakeWordSupport(result: { supported: boolean; reason: 'mobile-web' | null }) {
  (wakeWordSupport.getWakeWordSupport as Mock).mockReturnValue(result);
  (wakeWordSupport.isWakeWordSupportedEnvironment as Mock).mockReturnValue(result.supported);
}

type ApiKeyState = { openaiKeySet: boolean; replicateKeySet: boolean; xiaomiKeySet: boolean; hasGpu: boolean };
let apiKeyState: ApiKeyState = {
  openaiKeySet: true,
  replicateKeySet: true,
  xiaomiKeySet: false,
  hasGpu: false,
};

describe('AudioSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    ttsState.deploymentSpeech = null;
    mockWakeWordSupport({ supported: true, reason: null });
    apiKeyState = {
      openaiKeySet: true,
      replicateKeySet: true,
      xiaomiKeySet: false,
      hasGpu: false,
    };

    globalThis.fetch = vi.fn((input: string | URL) => {
      const url = String(input);

      if (url === '/api/keys') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(apiKeyState),
        } as Response);
      }

      if (url === '/api/transcribe/config') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ hasGpu: apiKeyState.hasGpu }),
        } as Response);
      }

      if (url === '/api/language') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            language: 'en',
            supported: [{ code: 'en', name: 'English', nativeName: 'English' }],
            providers: { edge: true, qwen3: true, openai: true },
          }),
        } as Response);
      }

      if (url === '/api/language/support') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ languages: [], isMultilingual: false }),
        } as Response);
      }

      if (url === '/api/voice-phrases/status') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({}),
        } as Response);
      }

      if (url.startsWith('/api/voice-phrases?lang=')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ wakePhrases: [] }),
        } as Response);
      }

      if (url === '/api/tts/config') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({}),
        } as Response);
      }

      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
    }) as typeof fetch;
  });

  describe('mobile wake-word gating', () => {
    it('renders the wake word toggle as disabled and visually off on mobile web', async () => {
      mockWakeWordSupport({ supported: false, reason: 'mobile-web' });

      render(<AudioSettings {...baseProps} wakeWordEnabled={true} />);

      const toggle = await screen.findByRole('switch', { name: /toggle wake word detection/i });
      expect(toggle).toBeDisabled();
      expect(toggle).toHaveAttribute('aria-checked', 'false');
    });

    it('shows helper copy pointing users to the manual mic trigger', async () => {
      mockWakeWordSupport({ supported: false, reason: 'mobile-web' });

      render(<AudioSettings {...baseProps} wakeWordEnabled={false} />);

      expect(await screen.findByText(/wake word isn't supported on mobile web/i)).toBeInTheDocument();
      expect(screen.getByText(/use the manual mic trigger instead/i)).toBeInTheDocument();
    });
  });

  describe('output provider settings', () => {
    it('hides the providers this deployment cannot serve and shows the one remaining as a label', async () => {
      render(<AudioSettings {...baseProps} section="output" ttsProvider="openai" ttsModel="" />);

      expect(await screen.findByText('Deployment speech service (cc-tts / cc-stt)')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Replicate' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Edge (Free)' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Xiaomi Mimo' })).not.toBeInTheDocument();
    });

    it('outside deployment speech mode keeps the OpenAI model list, voice list and instructions', async () => {
      render(<AudioSettings {...baseProps} section="output" ttsProvider="openai" ttsModel="" />);

      expect(await screen.findByLabelText('TTS Model')).toBeInTheDocument();
      expect(screen.getByRole('option', { name: 'gpt-4o-mini-tts (default)' })).toBeInTheDocument();
      expect(screen.getByLabelText('OpenAI Voice')).toHaveValue('alloy');
      expect(screen.getByText('Voice Instructions')).toBeInTheDocument();
      expect(screen.queryByLabelText('Deployment Voice')).not.toBeInTheDocument();
      expect(screen.queryByTestId('deployment-tts-model')).not.toBeInTheDocument();
    });

    describe('in deployment speech mode', () => {
      it('shows the alias as a label and replaces the OpenAI controls with one voice field', async () => {
        ttsState.deploymentSpeech = { model: 'cc-tts', voice: 'af_heart', voiceSource: 'default' };
        // A stale model saved in the browser must not show up as a choice.
        render(<AudioSettings {...baseProps} section="output" ttsProvider="openai" ttsModel="tts-1" />);

        expect(await screen.findByTestId('deployment-tts-model')).toHaveTextContent('cc-tts');
        expect(screen.queryByLabelText('TTS Model')).not.toBeInTheDocument();
        expect(screen.queryByRole('option', { name: 'gpt-4o-mini-tts (default)' })).not.toBeInTheDocument();
        expect(screen.queryByLabelText('OpenAI Voice')).not.toBeInTheDocument();
        expect(screen.queryByText('Voice Instructions')).not.toBeInTheDocument();
        expect(screen.getByLabelText('Deployment Voice')).toHaveValue('af_heart');
        expect(screen.getByText(/a wrong name makes read-aloud fail/i)).toBeInTheDocument();
      });

      it('saves an edited voice on blur, under the deployment voice key', async () => {
        ttsState.deploymentSpeech = { model: 'cc-tts', voice: 'af_heart', voiceSource: 'default' };
        render(<AudioSettings {...baseProps} section="output" ttsProvider="openai" ttsModel="" />);

        const input = await screen.findByLabelText('Deployment Voice');
        fireEvent.change(input, { target: { value: 'bf_emma' } });
        expect(updateField).not.toHaveBeenCalled();
        fireEvent.blur(input);
        expect(updateField).toHaveBeenCalledWith('openai', 'deploymentVoice', 'bf_emma');
      });

      it('does not save a voice that is not a valid token', async () => {
        ttsState.deploymentSpeech = { model: 'cc-tts', voice: 'af_heart', voiceSource: 'saved' };
        render(<AudioSettings {...baseProps} section="output" ttsProvider="openai" ttsModel="" />);

        const input = await screen.findByLabelText('Deployment Voice');
        fireEvent.change(input, { target: { value: 'not a voice' } });
        fireEvent.blur(input);
        expect(updateField).not.toHaveBeenCalled();
        expect(screen.getByText(/letters, digits, underscore or hyphen/i)).toBeInTheDocument();
      });

      it('shows a voice set by the environment read-only, with the reason', async () => {
        ttsState.deploymentSpeech = { model: 'cc-tts', voice: 'am_adam', voiceSource: 'env' };
        render(<AudioSettings {...baseProps} section="output" ttsProvider="openai" ttsModel="" />);

        const input = await screen.findByLabelText('Deployment Voice');
        expect(input).toHaveValue('am_adam');
        expect(input).toHaveAttribute('readonly');
        expect(screen.getByText(/OPENAI_TTS_VOICE/)).toBeInTheDocument();
        fireEvent.change(input, { target: { value: 'bf_emma' } });
        fireEvent.blur(input);
        expect(updateField).not.toHaveBeenCalled();
      });
    });

    it('renders Xiaomi model and voice selectors', async () => {
      render(<AudioSettings {...baseProps} section="output" ttsProvider="xiaomi" ttsModel="mimo-v2-tts" />);

      expect(await screen.findByLabelText('TTS Model')).toHaveValue('mimo-v2-tts');
      expect(screen.getByLabelText('Xiaomi Voice')).toHaveValue('mimo_default');
      expect(screen.getByRole('option', { name: 'mimo-v2-tts' })).toBeInTheDocument();
      expect(screen.getByRole('option', { name: 'mimo_default' })).toBeInTheDocument();
      expect(screen.getByRole('option', { name: 'default_zh' })).toBeInTheDocument();
      expect(screen.getByRole('option', { name: 'default_en' })).toBeInTheDocument();
    });

    it('updates Xiaomi voice when the voice selector changes', async () => {
      render(<AudioSettings {...baseProps} section="output" ttsProvider="xiaomi" ttsModel="mimo-v2-tts" />);

      fireEvent.change(await screen.findByLabelText('Xiaomi Voice'), { target: { value: 'default_en' } });
      expect(updateField).toHaveBeenCalledWith('xiaomi', 'voice', 'default_en');
    });

    it('updates Xiaomi style when the Style field changes', async () => {
      render(<AudioSettings {...baseProps} section="output" ttsProvider="xiaomi" ttsModel="mimo-v2-tts" />);

      fireEvent.click(await screen.findByText('Happy, whisper, calm, dramatic...'));
      fireEvent.change(screen.getByPlaceholderText('Happy, whisper, calm, dramatic...'), { target: { value: 'Happy' } });

      expect(updateField).toHaveBeenCalledWith('xiaomi', 'style', 'Happy');
    });
  });
});
