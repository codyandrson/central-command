import { describe, expect, it } from 'vitest';
import {
  SPEECH_CHUNK_MAX_CHARS,
  SPEECH_CHUNK_MIN_CHARS,
  SPEECH_TOTAL_MAX_CHARS,
  planSpeech,
  splitForSpeech,
} from './speechChunks';

describe('splitForSpeech', () => {
  it('splits ordinary prose at sentence ends', () => {
    const text =
      'The deployment finished without any errors this morning. Two services restarted exactly as we had planned! Did the nightly backup run afterwards, as it should? It did, and it completed in under a single minute again.';
    expect(splitForSpeech(text)).toEqual([
      'The deployment finished without any errors this morning.',
      'Two services restarted exactly as we had planned!',
      'Did the nightly backup run afterwards, as it should?',
      'It did, and it completed in under a single minute again.',
    ]);
  });

  it('keeps a short text as one chunk, trimmed', () => {
    expect(splitForSpeech('  Hello there.  ')).toEqual(['Hello there.']);
  });

  it('returns nothing for an empty or whitespace-only text', () => {
    expect(splitForSpeech('')).toEqual([]);
    expect(splitForSpeech(' \n\n \t ')).toEqual([]);
  });

  it('splits paragraphs apart', () => {
    const text = 'The first paragraph has a single long sentence in it\n\nThe second paragraph is also a single long sentence';
    expect(splitForSpeech(text)).toEqual([
      'The first paragraph has a single long sentence in it',
      'The second paragraph is also a single long sentence',
    ]);
  });

  it('speaks each list item, merging items that are short', () => {
    const text = [
      '- Check the backup job and confirm that it completed',
      '- Restart the worker',
      '- Read the log',
      '- Close the ticket once the owner has replied to us',
    ].join('\n');
    expect(splitForSpeech(text)).toEqual([
      '- Check the backup job and confirm that it completed',
      '- Restart the worker - Read the log - Close the ticket once the owner has replied to us',
    ]);
  });

  it('does not split inside a decimal number or a version number', () => {
    const text = 'Pi is about 3.14 and the release is v2.94.0 today, which is newer than v2.93.3 by one step.';
    expect(splitForSpeech(text)).toEqual([text]);
  });

  it('does not split inside a URL, but does after one that ends a sentence', () => {
    const text = 'See https://example.com/docs/page.html for the full details of it. Then come back here and tell us what you found.';
    expect(splitForSpeech(text)).toEqual([
      'See https://example.com/docs/page.html for the full details of it.',
      'Then come back here and tell us what you found.',
    ]);
  });

  it('does not split when the next word is lowercase or a digit', () => {
    const text = 'Use a tool such as curl, e.g. the one in the shell, for 2. 3 more things matter here.';
    expect(splitForSpeech(text)).toEqual([text]);
  });

  it('keeps a closing quote with its sentence', () => {
    const text = 'She said "we are done with the migration." Everyone in the room agreed with her about that.';
    expect(splitForSpeech(text)).toEqual([
      'She said "we are done with the migration."',
      'Everyone in the room agreed with her about that.',
    ]);
  });

  it('treats an ellipsis and a run of marks as one boundary', () => {
    const text = 'Well, that was not what we expected at all... Really?! Yes, really, and it took a long time.';
    expect(splitForSpeech(text)).toEqual([
      'Well, that was not what we expected at all...',
      'Really?! Yes, really, and it took a long time.',
    ]);
  });

  it('merges a short fragment into its neighbour', () => {
    const chunks = splitForSpeech('Dr. Smith confirmed the schedule for the quarterly review meeting.');
    expect(chunks).toEqual(['Dr. Smith confirmed the schedule for the quarterly review meeting.']);
  });

  it('merges a short trailing fragment into the chunk before it', () => {
    const text = 'This sentence is long enough to stand alone as one chunk. Ok.';
    expect(splitForSpeech(text)).toEqual(['This sentence is long enough to stand alone as one chunk. Ok.']);
  });

  it('cuts one very long sentence at commas, each chunk within the maximum', () => {
    const clause = 'the engine handles one request at a time';
    const text = `${Array.from({ length: 20 }, () => clause).join(', ')}.`;
    const chunks = splitForSpeech(text);
    expect(chunks.length).toBeGreaterThan(2);
    for (const c of chunks) {
      expect(c.length).toBeLessThanOrEqual(SPEECH_CHUNK_MAX_CHARS);
      expect(c.length).toBeGreaterThanOrEqual(SPEECH_CHUNK_MIN_CHARS);
    }
    for (const c of chunks.slice(0, -1)) expect(c.endsWith(',')).toBe(true);
    expect(chunks.join(' ')).toBe(text);
  });

  it('does not cut inside a number written with thousands separators', () => {
    const filler = 'word '.repeat(54).trim(); // 269 characters
    const text = `${filler} 1,000,000 ${filler}`;
    const chunks = splitForSpeech(text);
    for (const c of chunks) expect(c.length).toBeLessThanOrEqual(SPEECH_CHUNK_MAX_CHARS);
    expect(chunks.some((c) => c.includes('1,000,000'))).toBe(true);
  });

  it('cuts text with no punctuation at spaces', () => {
    const text = Array.from({ length: 120 }, (_, i) => `w${i}`).join(' ');
    const chunks = splitForSpeech(text);
    expect(chunks.length).toBeGreaterThan(1);
    for (const c of chunks) {
      expect(c.length).toBeLessThanOrEqual(SPEECH_CHUNK_MAX_CHARS);
      expect(c).toBe(c.trim());
    }
    expect(chunks.join(' ')).toBe(text);
  });

  it('cuts an unbroken string at the maximum', () => {
    const chunks = splitForSpeech('a'.repeat(700));
    expect(chunks.map((c) => c.length)).toEqual([300, 300, 100]);
  });

  it('drops markdown remnants that have nothing to say', () => {
    const text = '---\n**\n# Heading for the section about deployments\n* * *\nThe body text follows right after the heading.\n```\n';
    expect(splitForSpeech(text)).toEqual([
      '# Heading for the section about deployments',
      'The body text follows right after the heading.',
    ]);
  });

  it('is deterministic', () => {
    const text = 'One sentence here, and another there. A third? Yes! '.repeat(30);
    expect(splitForSpeech(text)).toEqual(splitForSpeech(text));
  });

  it('never returns an empty or untrimmed chunk and never loses a word', () => {
    const text = 'Alpha beta gamma delta epsilon zeta eta theta iota kappa. '.repeat(40);
    const chunks = splitForSpeech(text);
    for (const c of chunks) {
      expect(c).toBe(c.trim());
      expect(c.length).toBeGreaterThan(0);
    }
    expect(chunks.join(' ').split(/\s+/)).toEqual(text.trim().split(/\s+/));
  });
});

describe('planSpeech', () => {
  it('speaks a short text whole and does not truncate', () => {
    const plan = planSpeech('A short reply that is spoken whole.');
    expect(plan.chunks).toEqual(['A short reply that is spoken whole.']);
    expect(plan.truncated).toBe(false);
    expect(plan.spokenChars).toBe(plan.chunks[0].length);
  });

  it('cuts a long text on a chunk boundary at the total cap', () => {
    const sentence = 'This is one sentence of about sixty characters, give or take.';
    const text = Array.from({ length: 120 }, () => sentence).join(' '); // about 7,400 characters
    const plan = planSpeech(text);
    expect(plan.truncated).toBe(true);
    expect(plan.spokenChars).toBeLessThanOrEqual(SPEECH_TOTAL_MAX_CHARS);
    expect(plan.spokenChars).toBeGreaterThan(SPEECH_TOTAL_MAX_CHARS - SPEECH_CHUNK_MAX_CHARS);
    for (const c of plan.chunks) expect(text).toContain(c);
    expect(plan.chunks.every((c) => c.endsWith('.'))).toBe(true);
  });
});
