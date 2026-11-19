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
 * The first chunk of an utterance is at most this long. The engine synthesises a
 * request as a whole and cannot deliver the start of a chunk early, and its time
 * is about a second or two plus 0.023 s per character (100 characters 2.9 s,
 * 300 characters about 9 s), so a short first chunk means about 3 s to first
 * sound instead of about 9 s. Later chunks keep {@link SPEECH_CHUNK_MAX_CHARS}.
 */
export const SPEECH_FIRST_CHUNK_MAX_CHARS = 100;

/*
 * POSSIBLE FUTURE ENHANCEMENT (not built; the operator judged read-aloud good
 * enough as it is, 2026-11-18): ramp the maximum over the first few chunks
 * instead of jumping from SPEECH_FIRST_CHUNK_MAX_CHARS straight to
 * SPEECH_CHUNK_MAX_CHARS, for example 100, then about 150, then about 220,
 * then 300.
 *
 * Why it would help: read-aloud fetches ahead of playback (useTTS.ts,
 * TTS_MAX_BUFFERED_CHUNKS), but at the START of a reply no buffer exists yet.
 * Measured on a live install after v2.96.3, on a reply of about 2,600
 * characters: every gap between chunks was 23 to 76 ms except one of 1.8 s,
 * between the second and third chunks, where a full 300-character chunk
 * (8.9 s to synthesise) followed two short ones (38 and 90 characters) and
 * playback caught up with the engine. Smaller second and third chunks are
 * synthesised faster than they play, so the buffer would build before the
 * first full-size chunk is needed.
 *
 * What it would touch: only this file (the per-chunk maximum becomes a
 * function of the chunk's position) and speechChunks.test.ts. The numbers
 * above are for one engine on one machine; re-measure before choosing the
 * steps (the rate is in the comment at the top of this file).
 */

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

/** Where to cut a first piece longer than the first-chunk maximum: after a comma, semicolon, colon or dash followed by whitespace, else at a space, else hard. */
function firstCutPoint(s: string): number {
  for (let i = SPEECH_FIRST_CHUNK_MAX_CHARS - 1; i >= SPEECH_CHUNK_MIN_CHARS - 1; i--) {
    if (',;:-–—'.includes(s[i]) && isSpace(s[i + 1])) return i + 1;
  }
  for (let i = SPEECH_FIRST_CHUNK_MAX_CHARS; i >= SPEECH_CHUNK_MIN_CHARS; i--) {
    if (isSpace(s[i])) return i;
  }
  return SPEECH_FIRST_CHUNK_MAX_CHARS;
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
 * merged into a neighbour while the result still fits. The first chunk is held to
 * {@link SPEECH_FIRST_CHUNK_MAX_CHARS}: a longer first sentence is cut at a
 * comma, semicolon, colon, dash or space and its remainder flows into the chunks after it.
 */
export function splitForSpeech(text: string): string[] {
  const sentences: string[] = [];
  for (const line of text.replace(/\r\n?/g, '\n').split('\n')) sentencesOfLine(line, sentences);

  const pieces: string[] = [];
  for (const sentence of sentences) {
    const trimmed = sentence.trim();
    if (!trimmed || !isSpeakable(trimmed)) continue;
    if (pieces.length === 0 && trimmed.length > SPEECH_FIRST_CHUNK_MAX_CHARS) {
      // The first chunk is short so the first sound comes sooner; the rest flows on.
      const cut = firstCutPoint(trimmed);
      pieces.push(trimmed.slice(0, cut).trim());
      const rest = trimmed.slice(cut).trim();
      if (rest) hardSplit(rest, pieces);
    } else {
      hardSplit(trimmed, pieces);
    }
  }
  const maxFor = (index: number): number => (index === 0 ? SPEECH_FIRST_CHUNK_MAX_CHARS : SPEECH_CHUNK_MAX_CHARS);

  const chunks: string[] = [];
  let current = '';
  for (const piece of pieces) {
    if (!current) current = piece;
    else if (current.length < SPEECH_CHUNK_MIN_CHARS && current.length + 1 + piece.length <= maxFor(chunks.length)) {
      current += ` ${piece}`;
    } else {
      chunks.push(current);
      current = piece;
    }
  }
  if (current) {
    const last = chunks.length - 1;
    if (current.length < SPEECH_CHUNK_MIN_CHARS && last >= 0 && chunks[last].length + 1 + current.length <= maxFor(last)) {
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
