/** Tests for extractGoalPreviews — parsing [goal-preview:{...}] markers. */
import { describe, it, expect } from 'vitest';
import { extractGoalPreviews } from './extractGoalPreviews';

describe('extractGoalPreviews', () => {
  it('extracts a goal preview with all fields', () => {
    const text = 'Draft:\n[goal-preview:{"name":"Ship v2","cadence":"weekly","questions":["On track?"],"thresholds":"green if >80%"}]\nThoughts?';
    const { cleaned, goalPreviews } = extractGoalPreviews(text);
    expect(goalPreviews).toHaveLength(1);
    expect(goalPreviews[0].name).toBe('Ship v2');
    expect(goalPreviews[0].cadence).toBe('weekly');
    expect(goalPreviews[0].questions).toEqual(['On track?']);
    expect(goalPreviews[0].thresholds).toBe('green if >80%');
    expect(cleaned).not.toContain('[goal-preview:');
    expect(cleaned).toContain('Draft:');
    expect(cleaned).toContain('Thoughts?');
  });

  it('extracts a goal preview with only the required name field', () => {
    const text = '[goal-preview:{"name":"Minimal Goal"}]';
    const { goalPreviews } = extractGoalPreviews(text);
    expect(goalPreviews).toHaveLength(1);
    expect(goalPreviews[0].name).toBe('Minimal Goal');
  });

  it('rejects a marker with an empty name', () => {
    const text = '[goal-preview:{"name":""}]';
    const { goalPreviews, cleaned } = extractGoalPreviews(text);
    expect(goalPreviews).toHaveLength(0);
    expect(cleaned).toBe('');
  });

  it('rejects a marker missing name entirely', () => {
    const text = '[goal-preview:{"cadence":"weekly"}]';
    const { goalPreviews } = extractGoalPreviews(text);
    expect(goalPreviews).toHaveLength(0);
  });

  it('rejects malformed JSON and leaves prefix in text', () => {
    const text = '[goal-preview:{"name":}]';
    const { goalPreviews } = extractGoalPreviews(text);
    expect(goalPreviews).toHaveLength(0);
  });

  it('returns cleaned text unchanged when there is no marker', () => {
    const text = 'just plain text';
    const { cleaned, goalPreviews } = extractGoalPreviews(text);
    expect(cleaned).toBe(text);
    expect(goalPreviews).toHaveLength(0);
  });
});
