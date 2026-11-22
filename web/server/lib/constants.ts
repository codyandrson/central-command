/**
 * Server-side constants — no hardcoded values in service files.
 * External API URLs, paths, and defaults all live here.
 * Override via env vars where noted.
 */

// ─── External API base URLs ───────────────────────────────────────────────────
// Override for proxies, self-hosted endpoints, or API-compatible alternatives.

export const OPENAI_BASE_URL = process.env.OPENAI_BASE_URL || 'https://api.openai.com/v1';

// ─── API endpoints (derived from base URLs) ──────────────────────────────────

export const OPENAI_TTS_URL = `${OPENAI_BASE_URL}/audio/speech`;
export const OPENAI_WHISPER_URL = `${OPENAI_BASE_URL}/audio/transcriptions`;
/** Model name sent to the transcription endpoint — an alias when OPENAI_BASE_URL is a proxy (e.g. LiteLLM `cc-stt`). */
export const OPENAI_STT_MODEL = process.env.OPENAI_STT_MODEL || 'whisper-1';

// ─── Default connection ──────────────────────────────────────────────────────

export const DEFAULT_GATEWAY_URL = 'http://127.0.0.1:18789';
export const DEFAULT_GATEWAY_WS = 'ws://127.0.0.1:18789';
export const DEFAULT_PORT = 3080;
export const DEFAULT_SSL_PORT = 3443;
export const DEFAULT_HOST = '127.0.0.1';

// ─── Language registry ───────────────────────────────────────────────────────

export interface LanguageConfig {
  code: string;         // ISO 639-1
  name: string;         // Display name
  nativeName: string;   // Name in own language
}

export const SUPPORTED_LANGUAGES: LanguageConfig[] = [
  { code: 'en', name: 'English', nativeName: 'English' },
  { code: 'zh', name: 'Chinese', nativeName: '中文' },
  { code: 'hi', name: 'Hindi', nativeName: 'हिन्दी' },
  { code: 'es', name: 'Spanish', nativeName: 'Español' },
  { code: 'fr', name: 'French', nativeName: 'Français' },
  { code: 'ar', name: 'Arabic', nativeName: 'العربية' },
  { code: 'bn', name: 'Bengali', nativeName: 'বাংলা' },
  { code: 'pt', name: 'Portuguese', nativeName: 'Português' },
  { code: 'ru', name: 'Russian', nativeName: 'Русский' },
  { code: 'ja', name: 'Japanese', nativeName: '日本語' },
  { code: 'de', name: 'German', nativeName: 'Deutsch' },
  { code: 'tr', name: 'Turkish', nativeName: 'Türkçe' },
];

export const DEFAULT_LANGUAGE = 'en';

// ─── Default voice phrases per language ──────────────────────────────────────
// Used as suggestions when user first selects a non-English language.
// English defaults are the canonical set; others are translations.

export interface LanguageVoicePhrases {
  stopPhrases: string[];
  cancelPhrases: string[];
  wakePhrases?: string[];  // Optional — overrides default "hey <agent>" for this language
}

export const DEFAULT_VOICE_PHRASES: Record<string, LanguageVoicePhrases> = {
  en: {
    stopPhrases: ["boom", "i'm done", "im done", "all right i'm done", "alright i'm done", "that's it", "thats it", "send it", "done"],
    cancelPhrases: ['cancel', 'never mind', 'nevermind'],
  },
  zh: {
    stopPhrases: ['发送', '完了', '好了', '搞定', '就这样'],
    cancelPhrases: ['取消', '算了'],
  },
  hi: {
    stopPhrases: ['भेजो', 'हो गया', 'बस', 'भेज दो', 'बस इतना'],
    cancelPhrases: ['रद्द', 'छोड़ो', 'रहने दो'],
  },
  es: {
    stopPhrases: ['enviar', 'listo', 'ya está', 'terminé', 'envíalo'],
    cancelPhrases: ['cancelar', 'déjalo', 'olvídalo'],
  },
  fr: {
    stopPhrases: ['envoie', 'terminé', "c'est tout", 'fini', 'envoyer'],
    cancelPhrases: ['annuler', 'laisse tomber', "tant pis"],
  },
  ar: {
    stopPhrases: ['أرسل', 'خلاص', 'تم', 'انتهيت', 'كفاية'],
    cancelPhrases: ['إلغاء', 'خلاص ما أبي', 'لا تهتم'],
  },
  bn: {
    stopPhrases: ['পাঠাও', 'হয়ে গেছে', 'এটাই', 'শেষ'],
    cancelPhrases: ['বাতিল', 'থাক', 'দরকার নেই'],
  },
  pt: {
    stopPhrases: ['enviar', 'pronto', 'é isso', 'terminei', 'manda'],
    cancelPhrases: ['cancelar', 'deixa', 'esquece'],
  },
  ru: {
    stopPhrases: ['отправь', 'готово', 'всё', 'отправить', 'хватит'],
    cancelPhrases: ['отмена', 'забудь', 'не надо'],
  },
  ja: {
    stopPhrases: ['送って', '送信', '以上', '終わり', 'おわり'],
    cancelPhrases: ['キャンセル', 'やめて', 'いいや'],
  },
  de: {
    stopPhrases: ['sende', 'fertig', "das war's", 'erledigt', 'abschicken'],
    cancelPhrases: ['abbrechen', 'vergiss es', 'lass es'],
  },
  tr: {
    stopPhrases: ['gönder', 'tamam', 'bitti', 'bu kadar', 'yolla'],
    cancelPhrases: ['iptal', 'boşver', 'vazgeç'],
  },
};
