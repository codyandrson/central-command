import { describe, it, expect } from 'vitest';
import { extractTTSMarkers, OFFERED_PROVIDERS } from './useTTS';

describe('extractTTSMarkers', () => {
  it('should extract a single TTS marker', () => {
    const result = extractTTSMarkers('Hello [tts:world] there');
    expect(result.cleaned).toBe('Hello  there');
    expect(result.ttsText).toBe('world');
  });

  it('should extract only the first TTS marker text', () => {
    const result = extractTTSMarkers('[tts:first] and [tts:second]');
    expect(result.ttsText).toBe('first');
  });

  it('should remove all TTS markers from cleaned text', () => {
    const result = extractTTSMarkers('[tts:one] some [tts:two] text');
    expect(result.cleaned).toBe('some  text');
  });

  it('should return null ttsText when no markers present', () => {
    const result = extractTTSMarkers('No markers here');
    expect(result.cleaned).toBe('No markers here');
    expect(result.ttsText).toBeNull();
  });

  it('should handle empty string', () => {
    const result = extractTTSMarkers('');
    expect(result.cleaned).toBe('');
    expect(result.ttsText).toBeNull();
  });

  it('should handle marker at start of string', () => {
    const result = extractTTSMarkers('[tts:hello] world');
    expect(result.cleaned).toBe('world');
    expect(result.ttsText).toBe('hello');
  });

  it('should handle marker at end of string', () => {
    const result = extractTTSMarkers('hello [tts:world]');
    expect(result.cleaned).toBe('hello');
    expect(result.ttsText).toBe('world');
  });

  it('should handle marker with spaces in content', () => {
    const result = extractTTSMarkers('[tts:hello world] text');
    expect(result.ttsText).toBe('hello world');
  });

  it('should handle marker with special characters', () => {
    const result = extractTTSMarkers('[tts:say "hello!"] text');
    expect(result.ttsText).toBe('say "hello!"');
  });

  it('should not match incomplete brackets', () => {
    const result = extractTTSMarkers('[tts:unclosed text');
    expect(result.cleaned).toBe('[tts:unclosed text');
    expect(result.ttsText).toBeNull();
  });

  it('should handle string that is only a marker', () => {
    const result = extractTTSMarkers('[tts:entire string]');
    expect(result.cleaned).toBe('');
    expect(result.ttsText).toBe('entire string');
  });
});

describe('offered providers', () => {
  it('offers the one deployment speech provider for read-aloud and dictation', () => {
    expect(OFFERED_PROVIDERS).toEqual({ tts: ['openai'], stt: ['openai'] });
  });
});
