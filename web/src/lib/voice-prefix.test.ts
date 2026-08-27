import { describe, it, expect } from 'vitest';

/**
 * Voice prefix detection and handling tests
 * 
 * The "[voice]" prefix is used to indicate messages that were transcribed
 * from voice input. This triggers TTS responses from the assistant.
 */

const VOICE_PREFIX = '[voice] ';

function isVoiceMessage(message: string): boolean {
  return message.startsWith(VOICE_PREFIX);
}

function extractVoiceContent(message: string): string {
  if (isVoiceMessage(message)) {
    return message.slice(VOICE_PREFIX.length);
  }
  return message;
}

function addVoicePrefix(text: string): string {
  return VOICE_PREFIX + text;
}

describe('Voice Prefix Detection', () => {
  describe('isVoiceMessage', () => {
    it('should detect voice-prefixed messages', () => {
      expect(isVoiceMessage('[voice] hello world')).toBe(true);
      expect(isVoiceMessage('[voice] ')).toBe(true);
      expect(isVoiceMessage('[voice] a')).toBe(true);
    });

    it('should reject non-voice messages', () => {
      expect(isVoiceMessage('hello world')).toBe(false);
      expect(isVoiceMessage('[Voice] hello')).toBe(false); // Case sensitive
      expect(isVoiceMessage('[VOICE] hello')).toBe(false);
      expect(isVoiceMessage('voice hello')).toBe(false);
      expect(isVoiceMessage('')).toBe(false);
    });

    it('should reject partial matches', () => {
      expect(isVoiceMessage('[voice]hello')).toBe(false); // Missing space
      expect(isVoiceMessage(' [voice] hello')).toBe(false); // Leading space
      expect(isVoiceMessage('message [voice] inside')).toBe(false);
    });

    it('should handle edge cases', () => {
      expect(isVoiceMessage('[voice] [voice] nested')).toBe(true);
      expect(isVoiceMessage('[voice] \n newline')).toBe(true);
      expect(isVoiceMessage('[voice] 123')).toBe(true);
      expect(isVoiceMessage('[voice] !@#$%')).toBe(true);
    });
  });

  describe('extractVoiceContent', () => {
    it('should extract content from voice messages', () => {
      expect(extractVoiceContent('[voice] hello world')).toBe('hello world');
      expect(extractVoiceContent('[voice] test')).toBe('test');
      expect(extractVoiceContent('[voice] ')).toBe('');
    });

    it('should return unchanged for non-voice messages', () => {
      expect(extractVoiceContent('hello world')).toBe('hello world');
      expect(extractVoiceContent('')).toBe('');
      expect(extractVoiceContent('[Voice] test')).toBe('[Voice] test');
    });

    it('should preserve whitespace in content', () => {
      // Prefix is exactly "[voice] " (with one trailing space)
      // startsWith('[voice] ') will match if string has "[voice] " at the start
      
      // "[voice]   spaces" starts with "[voice] " ✓, so content is "  spaces"
      expect(extractVoiceContent('[voice]   spaces')).toBe('  spaces');
      // "[voice]  leading" starts with "[voice] " ✓, so content is " leading"
      expect(extractVoiceContent('[voice]  leading')).toBe(' leading');
      // Valid prefix with normal content
      expect(extractVoiceContent('[voice] content with spaces')).toBe('content with spaces');
    });

    it('should handle content with special characters', () => {
      expect(extractVoiceContent('[voice] hello\nworld')).toBe('hello\nworld');
      expect(extractVoiceContent('[voice] "quoted"')).toBe('"quoted"');
      expect(extractVoiceContent("[voice] it's a test")).toBe("it's a test");
    });
  });

  describe('addVoicePrefix', () => {
    it('should add voice prefix to text', () => {
      expect(addVoicePrefix('hello')).toBe('[voice] hello');
      expect(addVoicePrefix('')).toBe('[voice] ');
      expect(addVoicePrefix('test message')).toBe('[voice] test message');
    });

    it('should work with already prefixed text (double prefix)', () => {
      // This tests the implementation behavior - it doesn't check for existing prefix
      expect(addVoicePrefix('[voice] already')).toBe('[voice] [voice] already');
    });
  });
});

describe('Voice Input Integration', () => {
  describe('InputBar voice callback', () => {
    it('should prefix transcribed text with [voice]', () => {
      // Simulating InputBar behavior: onSend('[voice] ' + text)
      const transcribedText = 'hello world';
      const sentMessage = addVoicePrefix(transcribedText);
      
      expect(sentMessage).toBe('[voice] hello world');
      expect(isVoiceMessage(sentMessage)).toBe(true);
      expect(extractVoiceContent(sentMessage)).toBe('hello world');
    });

    it('should handle empty transcription', () => {
      const sentMessage = addVoicePrefix('');
      
      expect(sentMessage).toBe('[voice] ');
      expect(isVoiceMessage(sentMessage)).toBe(true);
    });

    it('should handle transcription with trailing/leading spaces', () => {
      // Transcription is typically trimmed before prefixing
      const transcribedText = '  hello world  '.trim();
      const sentMessage = addVoicePrefix(transcribedText);
      
      expect(sentMessage).toBe('[voice] hello world');
    });
  });

  describe('TTS Response Triggering', () => {
    it('voice messages should trigger TTS markers in responses', () => {
      // The server/assistant sees [voice] prefix and includes TTS markers
      const voiceMessage = '[voice] what is the weather?';
      
      expect(isVoiceMessage(voiceMessage)).toBe(true);
      
      // Expected response pattern would include [tts:...] markers
      // This is handled server-side but we verify the detection works
    });

    it('typed messages should not trigger TTS', () => {
      const typedMessage = 'what is the weather?';
      
      expect(isVoiceMessage(typedMessage)).toBe(false);
    });
  });
});

describe('Voice Message Formats', () => {
  describe('Multilingual Support', () => {
    it('should handle non-ASCII characters', () => {
      expect(isVoiceMessage('[voice] こんにちは')).toBe(true);
      expect(extractVoiceContent('[voice] こんにちは')).toBe('こんにちは');
      
      expect(isVoiceMessage('[voice] 你好世界')).toBe(true);
      expect(isVoiceMessage('[voice] مرحبا')).toBe(true);
      expect(isVoiceMessage('[voice] Привет')).toBe(true);
    });

    it('should handle emoji', () => {
      expect(isVoiceMessage('[voice] hello 👋')).toBe(true);
      expect(extractVoiceContent('[voice] 🎉 party')).toBe('🎉 party');
    });
  });

  describe('Long Messages', () => {
    it('should handle very long transcriptions', () => {
      const longText = 'a'.repeat(10000);
      const message = addVoicePrefix(longText);
      
      expect(isVoiceMessage(message)).toBe(true);
      expect(extractVoiceContent(message)).toBe(longText);
      expect(message.length).toBe(10000 + VOICE_PREFIX.length);
    });
  });

  describe('Multiline Messages', () => {
    it('should handle multiline transcriptions', () => {
      const multiline = 'line one\nline two\nline three';
      const message = addVoicePrefix(multiline);
      
      expect(isVoiceMessage(message)).toBe(true);
      expect(extractVoiceContent(message)).toBe(multiline);
    });
  });
});
