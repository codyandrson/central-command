import { describe, expect, it } from 'vitest';
import {
  AGING_HOURS, STALE_HOURS, ageLabel, ageOf, agingLevel, oldestAge,
} from './aging';

const NOW = Date.parse('2026-07-29T12:00:00Z');
const HOUR = 60 * 60 * 1000;
const ago = (hours: number) => new Date(NOW - hours * HOUR).toISOString();

describe('agingLevel', () => {
  it('is fresh below the aging threshold and aging exactly at it', () => {
    expect(agingLevel(ago(AGING_HOURS - 0.001), NOW)).toBe('fresh');
    expect(agingLevel(ago(AGING_HOURS), NOW)).toBe('aging');
  });

  it('is aging below the stale threshold and stale exactly at it', () => {
    expect(agingLevel(ago(STALE_HOURS - 0.001), NOW)).toBe('aging');
    expect(agingLevel(ago(STALE_HOURS), NOW)).toBe('stale');
    expect(agingLevel(ago(120), NOW)).toBe('stale');
  });

  it('treats a missing or unparseable timestamp as fresh, never as stale', () => {
    // An accusation the record cannot support is worse than no chip.
    expect(agingLevel(null, NOW)).toBe('fresh');
    expect(agingLevel(undefined, NOW)).toBe('fresh');
    expect(agingLevel('not a date', NOW)).toBe('fresh');
  });

  it('does not go negative on a clock-skewed future timestamp', () => {
    expect(agingLevel(new Date(NOW + 5 * HOUR).toISOString(), NOW)).toBe('fresh');
  });
});

describe('ageLabel — REPORT the elapsed time, never round it UP', () => {
  it('NEVER claims a day that has not passed', () => {
    // The bug class the session stall chip shipped with: a Math.max(1, …)
    // clamp made every chip claim "1d", defeating the one thing the chip is
    // for — telling five minutes apart from three days. Every one of these is
    // under 24h and none of them may say "1d".
    expect(ageOf(ago(23), NOW)).toBe('23h');
    expect(ageOf(ago(23.99), NOW)).toBe('23h');
    expect(ageLabel(5 * 60 * 1000)).toBe('<1h');
    expect(ageLabel(0)).toBe('<1h');
  });

  it('floors, so a unit only appears once it has fully elapsed', () => {
    expect(ageOf(ago(24), NOW)).toBe('1d');
    expect(ageOf(ago(47), NOW)).toBe('1d');   // not 2d — 47h is one whole day
    expect(ageOf(ago(71), NOW)).toBe('2d');
    expect(ageOf(ago(96), NOW)).toBe('4d');
  });
});

describe('oldestAge', () => {
  it('stays silent for a healthy section', () => {
    expect(oldestAge([ago(1), ago(3)], NOW)).toBeNull();
    expect(oldestAge([], NOW)).toBeNull();
  });

  it('names the worst item in the section', () => {
    expect(oldestAge([ago(1), ago(96), ago(30)], NOW))
      .toEqual({ level: 'stale', label: '4d' });
    expect(oldestAge([ago(1), ago(30)], NOW))
      .toEqual({ level: 'aging', label: '1d' });
  });

  it('ignores rows with no usable timestamp', () => {
    expect(oldestAge([null, undefined, 'nope', ago(96)], NOW))
      .toEqual({ level: 'stale', label: '4d' });
  });
});
