/**
 * Cutting a text into speakable chunks, so read-aloud can start on the first
 * sentence instead of waiting for the whole reply.
 *
 * Why the sizes below: measured on a live install, the speech service
 * synthesises at about 0.45 of playback time (100 characters in 2.9 s, 400 in
 * 9.9 s, 1,200 in 31.6 s: linear in the length) and takes ONE job at a time.
 * One request per reply therefore meant 54 s of silence for 1,500 characters,
 * and a long reply would run into the service's request timeout. Short chunks
 * bound the wait for the first sound and the length of any one request.
 */

/** A fragment shorter than this is merged into a neighbour (about 1.2 s of synthesis). */
export const SPEECH_CHUNK_MIN_CHARS = 40;

/** No chunk is longer than this (about 9 s of synthesis, one request). */
export const SPEECH_CHUNK_MAX_CHARS = 300;

/**
 * The most text one utterance speaks. At the measured rate this is about 3 min
 * of synthesis and several minutes of listening: it bounds both the read-aloud
 * time and the work queued on an engine that does one job at a time. A longer
 * text is cut on a chunk boundary and the operator is told.
 */
export const SPEECH_TOTAL_MAX_CHARS = 4000;

const TERMINATORS = '.!?…';
/** Closing quotes and brackets that stay with the sentence they close. */
const CLOSERS = '"\'”’)]»';

const isSpace = (ch: string | undefined): boolean => ch !== undefined && /\s/.test(ch);
const isSpeakable = (s: string): boolean => /[\p{L}\p{N}]/u.test(s);

/**
 * Sentences of one line. A boundary is a run of . ! ? … (plus closing quotes)
 * followed by whitespace or the end of the line, and not by a lowercase letter
 * or a digit: "3.14", "v2.94.0" and "example.com/a" have no whitespace after the
 * dot, and "e.g. the" continues in lowercase, so none of them split.
 */
function sentencesOfLine(line: string, out: string[]): void {
  let start = 0;
  for (let i = 0; i < line.length; i++) {
    if (!TERMINATORS.includes(line[i])) continue;
    let end = i + 1;
    while (end < line.length && TERMINATORS.includes(line[end])) end++;
    while (end < line.length && CLOSERS.includes(line[end])) end++;
    i = end - 1;
    if (end < line.length) {
      if (!isSpace(line[end])) continue;
      let next = end;
      while (next < line.length && isSpace(line[next])) next++;
      if (next < line.length && /[\p{Ll}\p{Nd}]/u.test(line[next])) continue;
    }
    out.push(line.slice(start, end));
    start = end;
  }
  out.push(line.slice(start));
}

/** Where to cut a piece longer than the maximum: after a comma or semicolon, else at a space, else hard. */
function cutPoint(s: string): number {
  for (let i = SPEECH_CHUNK_MAX_CHARS - 1; i >= SPEECH_CHUNK_MIN_CHARS - 1; i--) {
    if ((s[i] === ',' || s[i] === ';') && isSpace(s[i + 1])) return i + 1; // "1,000" is not a cut
  }
  for (let i = SPEECH_CHUNK_MAX_CHARS; i >= SPEECH_CHUNK_MIN_CHARS; i--) {
    if (isSpace(s[i])) return i;
  }
  return SPEECH_CHUNK_MAX_CHARS;
}

function hardSplit(piece: string, out: string[]): void {
  let rest = piece;
  while (rest.length > SPEECH_CHUNK_MAX_CHARS) {
    const cut = cutPoint(rest);
    const head = rest.slice(0, cut).trim();
    if (head) out.push(head);
    rest = rest.slice(cut).trim();
  }
  if (rest) out.push(rest);
}

/**
 * Split a text into chunks to speak, in order. Deterministic. Line breaks and
 * sentence ends separate pieces; pieces with nothing to say (a bare "---" or
 * "**") are dropped; a piece over {@link SPEECH_CHUNK_MAX_CHARS} is cut at a
 * comma, semicolon or space; a piece under {@link SPEECH_CHUNK_MIN_CHARS} is
 * merged into a neighbour while the result still fits.
 */
export function splitForSpeech(text: string): string[] {
  const sentences: string[] = [];
  for (const line of text.replace(/\r\n?/g, '\n').split('\n')) sentencesOfLine(line, sentences);

  const pieces: string[] = [];
  for (const sentence of sentences) {
    const trimmed = sentence.trim();
    if (trimmed && isSpeakable(trimmed)) hardSplit(trimmed, pieces);
  }

  const chunks: string[] = [];
  let current = '';
  for (const piece of pieces) {
    if (!current) current = piece;
    else if (current.length < SPEECH_CHUNK_MIN_CHARS && current.length + 1 + piece.length <= SPEECH_CHUNK_MAX_CHARS) {
      current += ` ${piece}`;
    } else {
      chunks.push(current);
      current = piece;
    }
  }
  if (current) {
    const last = chunks.length - 1;
    if (current.length < SPEECH_CHUNK_MIN_CHARS && last >= 0 && chunks[last].length + 1 + current.length <= SPEECH_CHUNK_MAX_CHARS) {
      chunks[last] += ` ${current}`;
    } else {
      chunks.push(current);
    }
  }
  return chunks;
}

export interface SpeechPlan {
  /** What to speak, in order; never more than the total cap in characters. */
  chunks: string[];
  /** True when chunks beyond the cap were left out. */
  truncated: boolean;
  /** Characters in `chunks`. */
  spokenChars: number;
}

/** Split `text` and apply the total cap; the cut falls on a chunk boundary. */
export function planSpeech(text: string, maxTotal: number = SPEECH_TOTAL_MAX_CHARS): SpeechPlan {
  const all = splitForSpeech(text);
  const chunks: string[] = [];
  let spokenChars = 0;
  for (const chunk of all) {
    if (spokenChars + chunk.length > maxTotal) break;
    chunks.push(chunk);
    spokenChars += chunk.length;
  }
  return { chunks, truncated: chunks.length < all.length, spokenChars };
}
