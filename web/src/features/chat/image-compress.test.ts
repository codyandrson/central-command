import { describe, expect, it } from 'vitest';
import { buildQualityLadder, computeDimensionRungs, computeScaledSize } from './image-compress';

describe('image-compress policy helpers', () => {
  it('builds the expected quality ladder without duplicates', () => {
    expect(buildQualityLadder(82)).toEqual([82, 74, 66]);
    expect(buildQualityLadder(74)).toEqual([74, 66]);
    expect(buildQualityLadder(120)).toEqual([100, 74, 66]);
  });

  it('reduces dimensions by about 15% per rung until the configured minimum', () => {
    expect(computeDimensionRungs(2048, 512)).toEqual([2048, 1741, 1480, 1258, 1069, 909, 773, 657, 558, 512]);
  });

  it('preserves aspect ratio while clamping to a max dimension', () => {
    expect(computeScaledSize(4000, 3000, 2048)).toEqual({ width: 2048, height: 1536 });
    expect(computeScaledSize(1000, 500, 2048)).toEqual({ width: 1000, height: 500 });
  });
});
