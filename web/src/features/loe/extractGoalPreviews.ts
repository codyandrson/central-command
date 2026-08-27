/**
 * extractGoalPreviews — [goal-preview:{...}] marker parsing.
 * Clone of extractCharts.ts's bracket-balanced scan; see that file for why
 * regex can't handle nested JSON here.
 */

export interface GoalPreviewData {
  name: string;
  cadence?: string;
  questions?: string[];
  thresholds?: string;
}

function isValidGoalPreviewData(obj: unknown): obj is GoalPreviewData {
  if (!obj || typeof obj !== 'object') return false;
  const o = obj as Record<string, unknown>;
  if (typeof o.name !== 'string' || o.name.length === 0) return false;
  if (o.cadence !== undefined && typeof o.cadence !== 'string') return false;
  if (o.questions !== undefined && (!Array.isArray(o.questions) || !o.questions.every((q) => typeof q === 'string'))) return false;
  if (o.thresholds !== undefined && typeof o.thresholds !== 'string') return false;
  return true;
}

const GOAL_PREVIEW_PREFIX = '[goal-preview:';

/**
 * Extract [goal-preview:{...}] markers using bracket-balanced parsing.
 * Regex can't handle nested brackets in JSON, so we find the opening
 * `[goal-preview:{` and then scan for the matching closing `}]`.
 */
export function extractGoalPreviews(text: string): { cleaned: string; goalPreviews: GoalPreviewData[] } {
  const goalPreviews: GoalPreviewData[] = [];
  let cleaned = '';
  let cursor = 0;

  while (cursor < text.length) {
    const start = text.indexOf(GOAL_PREVIEW_PREFIX, cursor);
    if (start === -1) {
      cleaned += text.slice(cursor);
      break;
    }

    // Add text before the marker
    cleaned += text.slice(cursor, start);

    // Find the JSON start (the `{` after `[goal-preview:`)
    const jsonStart = start + GOAL_PREVIEW_PREFIX.length;
    if (text[jsonStart] !== '{') {
      // Not a valid goal-preview marker, keep the text
      cleaned += GOAL_PREVIEW_PREFIX;
      cursor = jsonStart;
      continue;
    }

    // Scan for balanced closing `}]`
    let depth = 0;
    let inString = false;
    let escaped = false;
    let jsonEnd = -1;

    for (let i = jsonStart; i < text.length; i++) {
      const ch = text[i];
      if (escaped) { escaped = false; continue; }
      if (ch === '\\' && inString) { escaped = true; continue; }
      if (ch === '"') { inString = !inString; continue; }
      if (inString) continue;
      if (ch === '{' || ch === '[') depth++;
      if (ch === '}' || ch === ']') depth--;
      if (depth === 0 && ch === '}') {
        // Check if followed by `]`
        if (i + 1 < text.length && text[i + 1] === ']') {
          jsonEnd = i + 1; // points to the `]`
          break;
        }
      }
    }

    if (jsonEnd === -1) {
      // No balanced close found, keep the text as-is
      cleaned += GOAL_PREVIEW_PREFIX;
      cursor = jsonStart;
      continue;
    }

    const jsonStr = text.slice(jsonStart, jsonEnd);
    cursor = jsonEnd + 1; // skip past the `]`

    try {
      const parsed = JSON.parse(jsonStr);
      if (isValidGoalPreviewData(parsed)) {
        goalPreviews.push(parsed);
      }
    } catch { /* skip malformed */ }
  }

  return { cleaned: cleaned.trim(), goalPreviews };
}
