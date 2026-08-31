import { describe, it, expect } from 'vitest';
import { communitiesFrom } from './clustering';

/** Two 4-cliques joined by a single bridge edge — the textbook case Louvain
 *  must split, and the fixture that fails if the graph is ever built directed,
 *  unweighted-by-accident, or the min-size filter drifts. */
const NODES = ['a1', 'a2', 'a3', 'a4', 'b1', 'b2', 'b3', 'b4']
  .map((id) => ({ id, label: id.toUpperCase() }));
const EDGES = [
  ['a1', 'a2'], ['a1', 'a3'], ['a1', 'a4'], ['a2', 'a3'], ['a2', 'a4'], ['a3', 'a4'],
  ['b1', 'b2'], ['b1', 'b3'], ['b1', 'b4'], ['b2', 'b3'], ['b2', 'b4'], ['b3', 'b4'],
  ['a1', 'b1'],
].map(([source, target]) => ({ source, target }));

describe('communitiesFrom', () => {
  it('splits two cliques joined by a bridge, and labels each by its hub', () => {
    const clusters = communitiesFrom(NODES, EDGES);
    expect(clusters.map((c) => c.id)).toEqual(['cluster:0', 'cluster:1']);
    expect(clusters.map((c) => [...c.members].sort())).toEqual([
      ['a1', 'a2', 'a3', 'a4'],
      ['b1', 'b2', 'b3', 'b4'],
    ]);
    // a1/b1 carry the bridge, so they are the highest-degree members.
    expect(clusters.map((c) => c.label)).toEqual(['A1 · 4', 'B1 · 4']);
  });

  it('is stable across runs', () => {
    expect(communitiesFrom(NODES, EDGES)).toEqual(communitiesFrom(NODES, EDGES));
  });

  it('drops communities below the minimum and handles an edgeless canvas', () => {
    expect(communitiesFrom(NODES, EDGES, 5)).toEqual([]);
    expect(communitiesFrom(NODES, [])).toEqual([]);
  });
});
