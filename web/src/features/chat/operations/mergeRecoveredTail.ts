import type { ChatMsg } from '@/features/chat/types';

function hashString(input: string): number {
  let hash = 0;
  for (let i = 0; i < input.length; i++) {
    hash = ((hash << 5) - hash + input.charCodeAt(i)) | 0;
  }
  return hash;
}

function messageSignature(msg: ChatMsg): string {
  const normalizedText = (msg.rawText || '')
    .trim()
    .replace(/\s+/g, ' ')
    .slice(0, 4000);
  const textHash = hashString(normalizedText).toString(16);
  const tsBucket = Math.floor(msg.timestamp.getTime() / 30_000);
  const flags = [
    msg.isThinking ? 'thinking' : '',
    msg.intermediate ? 'intermediate' : '',
    msg.toolGroup ? `toolGroup:${msg.toolGroup.length}` : '',
    msg.images?.length ? `images:${msg.images.length}` : '',
  ].filter(Boolean).join(',');

  return `${msg.role}|${textHash}|${tsBucket}|${flags}`;
}

function findSuffixPrefixOverlap(existingSigs: string[], recoveredSigs: string[]): number {
  const max = Math.min(existingSigs.length, recoveredSigs.length, 120);
  for (let len = max; len >= 1; len--) {
    let match = true;
    for (let i = 0; i < len; i++) {
      if (existingSigs[existingSigs.length - len + i] !== recoveredSigs[i]) {
        match = false;
        break;
      }
    }
    if (match) return len;
  }
  return 0;
}

/**
 * Find a single-message anchor between existing tail and recovered messages.
 * Searches from the END of the existing array to find the latest match,
 * reducing the risk of hash collisions on short/common messages anchoring
 * at the wrong position.
 */
function findTailAnchor(existingSigs: string[], recoveredSigs: string[]) {
  const tailStart = Math.max(0, existingSigs.length - 160);

  for (let existingIdx = existingSigs.length - 1; existingIdx >= tailStart; existingIdx--) {
    const sig = existingSigs[existingIdx];
    for (let recoveredIdx = 0; recoveredIdx < recoveredSigs.length; recoveredIdx++) {
      if (recoveredSigs[recoveredIdx] === sig) {
        return { existingIdx, recoveredIdx };
      }
    }
  }

  return null;
}

/**
 * Merge a recovered history tail into the current transcript without replacing
 * unaffected prefix messages.
 */
/**
 * Messages the operator typed that the server transcript cannot know about yet.
 *
 * Central Command persists a turn's transcript when the turn ENDS, so a mid-turn
 * recovery read (the `agent_tool_result` refresh) returns a history that stops
 * before the message currently being answered. The merge would then "correct"
 * the transcript by deleting it, and it would reappear seconds later when the
 * turn landed — the operator's own words blinking out while the agent worked.
 *
 * The operator's input is the one thing the client is authoritative about.
 * Nothing recovered from the server may remove it (The operator, 2026-07-25: "my
 * inputs should never disappear").
 */
function unconfirmedLocalTail(existing: ChatMsg[], recovered: ChatMsg[]): ChatMsg[] {
  const recoveredSigs = new Set(recovered.map(messageSignature));
  const tail: ChatMsg[] = [];
  for (let i = existing.length - 1; i >= 0; i--) {
    const msg = existing[i];
    if (msg.role !== 'user') break;          // only a trailing run of user turns
    if (recoveredSigs.has(messageSignature(msg))) break;  // server already has it
    tail.unshift(msg);
  }
  return tail;
}

function withLocalTail(merged: ChatMsg[], localTail: ChatMsg[]): ChatMsg[] {
  if (localTail.length === 0) return merged;
  const mergedSigs = new Set(merged.map(messageSignature));
  const missing = localTail.filter((m) => !mergedSigs.has(messageSignature(m)));
  return missing.length ? [...merged, ...missing] : merged;
}

export function mergeRecoveredTail(existing: ChatMsg[], recovered: ChatMsg[]): ChatMsg[] {
  if (recovered.length === 0) return existing;
  if (existing.length === 0) return recovered;

  const localTail = unconfirmedLocalTail(existing, recovered);
  const existingSigs = existing.map(messageSignature);
  const recoveredSigs = recovered.map(messageSignature);

  // Fast path: recovered starts where existing tail ends.
  const overlap = findSuffixPrefixOverlap(existingSigs, recoveredSigs);
  if (overlap > 0) {
    return withLocalTail([...existing, ...recovered.slice(overlap)], localTail);
  }

  // Anchor path: find a matching point in the existing tail and replace only suffix.
  const anchor = findTailAnchor(existingSigs, recoveredSigs);
  if (anchor) {
    const preservedPrefix = existing.slice(0, anchor.existingIdx);
    const patchedTail = recovered.slice(anchor.recoveredIdx);
    return withLocalTail([...preservedPrefix, ...patchedTail], localTail);
  }

  // Last resort: no overlap/anchor detected, prefer authoritative recovered tail
  // — but still never at the cost of what the operator just typed.
  return withLocalTail(recovered, localTail);
}
