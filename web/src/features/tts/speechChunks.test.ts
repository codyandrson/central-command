import { describe, expect, it } from 'vitest';
import {
  SPEECH_CHUNK_MAX_CHARS,
  SPEECH_FIRST_CHUNK_MAX_CHARS,
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

  it('cuts an unbroken string at the maxima', () => {
    const chunks = splitForSpeech('a'.repeat(700));
    expect(chunks.map((c) => c.length)).toEqual([100, 300, 300]); // the first chunk is short
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

describe('the short first chunk', () => {
  const first = (text: string) => splitForSpeech(text)[0];

  it('cuts a long first sentence at the last comma that fits and carries the rest on', () => {
    const text = `${'The engine handles exactly one request at a time, '.repeat(3).trim()} and the rest of it goes on for a while longer. Then a second sentence follows here.`;
    const chunks = splitForSpeech(text);
    expect(chunks[0].length).toBeLessThanOrEqual(SPEECH_FIRST_CHUNK_MAX_CHARS);
    expect(chunks[0].endsWith(',')).toBe(true);
    expect(chunks.join(' ')).toBe(text);
  });

  it('cuts at a colon or dash followed by whitespace', () => {
    const colon = `Here is what happened this morning when the backup ran: ${'the job finished late and the report was sent after it, '.repeat(3)}`.trim();
    expect(first(colon).endsWith(':')).toBe(true);
    const dash = `Here is what happened this morning when the backup ran - ${'the job finished late and the report was sent after it, '.repeat(3)}`.trim();
    expect(first(dash).endsWith(' -')).toBe(true);
  });

  it('keeps a short first sentence as the first chunk and a long second one apart', () => {
    const second = `${'Another clause that keeps going and going, '.repeat(5).trim()}.`;
    const text = `Yes. ${second}`;
    const chunks = splitForSpeech(text);
    expect(chunks[0]).toBe('Yes.'); // merging would pass the first-chunk maximum
    expect(chunks[1].length).toBeGreaterThan(SPEECH_FIRST_CHUNK_MAX_CHARS); // later chunks keep the normal maximum
    expect(chunks[1].length).toBeLessThanOrEqual(SPEECH_CHUNK_MAX_CHARS);
    expect(chunks.join(' ')).toBe(text);
  });

  it('merges a short first sentence with the next only while within the first maximum', () => {
    const text = 'Yes. This second sentence is short too, fine. And a third one that is long enough to stand all by itself later on.';
    const chunks = splitForSpeech(text);
    expect(chunks[0]).toBe('Yes. This second sentence is short too, fine.');
    expect(chunks[0].length).toBeLessThanOrEqual(SPEECH_FIRST_CHUNK_MAX_CHARS);
  });

  it('speaks a text shorter than the first maximum as one chunk', () => {
    const text = 'A short reply with two sentences. It is spoken whole.';
    expect(splitForSpeech(text)).toEqual([text]);
  });

  it('does not cut inside a decimal or a URL before any comma', () => {
    const text = 'Version 3.14 is documented at https://example.com/docs/a.b/c.html and the details follow here, so read them with care today.';
    const chunks = splitForSpeech(text);
    expect(chunks[0].length).toBeLessThanOrEqual(SPEECH_FIRST_CHUNK_MAX_CHARS);
    expect(chunks[0]).toContain('https://example.com/docs/a.b/c.html');
    expect(chunks[0]).toContain('3.14');
    expect(chunks.join(' ')).toBe(text);
  });

  it('cuts text with no punctuation at a space within the first maximum', () => {
    const text = Array.from({ length: 80 }, (_, i) => `w${i}`).join(' ');
    const chunks = splitForSpeech(text);
    expect(chunks[0].length).toBeLessThanOrEqual(SPEECH_FIRST_CHUNK_MAX_CHARS);
    expect(chunks[0].length).toBeGreaterThan(SPEECH_FIRST_CHUNK_MAX_CHARS - 8);
    expect(chunks.join(' ')).toBe(text);
    for (const c of chunks.slice(1)) expect(c.length).toBeLessThanOrEqual(SPEECH_CHUNK_MAX_CHARS);
  });

  it('plans the same short first chunk', () => {
    const text = `${'word '.repeat(60).trim()}.`;
    const plan = planSpeech(text);
    expect(plan.chunks[0].length).toBeLessThanOrEqual(SPEECH_FIRST_CHUNK_MAX_CHARS);
    expect(plan.truncated).toBe(false);
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
