/**
 * Tests for the unicode-aware speakability check used in buildVoiceFallbackText.
 * The regex /\p{L}{3,}/u must match 3+ consecutive letters in any script.
 */
import { describe, it, expect } from 'vitest';

/** Must match the regex in ChatContext.tsx buildVoiceFallbackText */
const SPEAKABLE_RE = /\p{L}{3,}/u;

describe('voice fallback speakability check', () => {
  it('matches English text', () => {
    expect(SPEAKABLE_RE.test('Hello world')).toBe(true);
  });

  it('matches Chinese text', () => {
    expect(SPEAKABLE_RE.test('你好世界')).toBe(true);
  });

  it('matches Arabic text', () => {
    expect(SPEAKABLE_RE.test('مرحبا بالعالم')).toBe(true);
  });

  it('matches Turkish text', () => {
    expect(SPEAKABLE_RE.test('Merhaba dünya')).toBe(true);
  });

  it('matches Japanese hiragana', () => {
    expect(SPEAKABLE_RE.test('こんにちは')).toBe(true);
  });

  it('matches Cyrillic text', () => {
    expect(SPEAKABLE_RE.test('Привет мир')).toBe(true);
  });

  it('rejects pure numbers', () => {
    expect(SPEAKABLE_RE.test('12345')).toBe(false);
  });

  it('rejects pure symbols', () => {
    expect(SPEAKABLE_RE.test('---')).toBe(false);
  });

  it('rejects fewer than 3 letters', () => {
    expect(SPEAKABLE_RE.test('hi')).toBe(false);
    expect(SPEAKABLE_RE.test('好')).toBe(false);
  });

  it('matches mixed script with 3+ letters', () => {
    expect(SPEAKABLE_RE.test('123 abc 456')).toBe(true);
  });
});
