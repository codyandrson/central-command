import { describe, it, expect } from 'vitest';
import { lightTone } from './lightTone';

describe('lightTone', () => {
  it('maps known lights to their tone', () => {
    expect(lightTone('green').dot).toBe('bg-green');
    expect(lightTone('yellow').dot).toBe('bg-orange');
    expect(lightTone('red').dot).toBe('bg-red');
  });

  it('renders neutrally for no check-in and for unrecognized lights', () => {
    expect(lightTone(null)).toEqual(lightTone(undefined));
    expect(lightTone('bogus')).toEqual(lightTone(null));
  });
});
