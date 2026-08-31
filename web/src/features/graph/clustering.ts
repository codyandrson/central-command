/**
 * Community detection for the Graph panel's "Cluster" toggle.
 *
 * Pure over (nodes, edges) so it can be tested without a canvas: GraphView
 * hands it what cytoscape currently holds and gets back the compound parents
 * to create. Louvain is fed a fixed `rng` so the same canvas always yields the
 * same grouping — a re-shuffle on every toggle would be worse than no toggle.
 */
import { UndirectedGraph } from 'graphology';
import louvain from 'graphology-communities-louvain';

export type ClusterNode = { id: string; label: string };
export type ClusterEdge = { source: string; target: string };
export type Cluster = { id: string; label: string; members: string[] };

/** Communities of at least `minSize` members, labelled by their highest-degree
 *  member. Communities smaller than that stay loose on the canvas — collapsing
 *  a pair into a supernode hides more than it explains. */
export function communitiesFrom(
  nodes: ClusterNode[],
  edges: ClusterEdge[],
  minSize = 3,
): Cluster[] {
  const g = new UndirectedGraph();
  for (const n of nodes) g.mergeNode(n.id);
  const degree = new Map<string, number>();
  for (const e of edges) {
    if (e.source === e.target || !g.hasNode(e.source) || !g.hasNode(e.target)) continue;
    g.mergeEdge(e.source, e.target);
    degree.set(e.source, (degree.get(e.source) || 0) + 1);
    degree.set(e.target, (degree.get(e.target) || 0) + 1);
  }
  // No edges means no communities, and louvain has nothing to maximise.
  if (g.size === 0) return [];

  const assignment = louvain(g, { rng: () => 0.5 });
  const byCommunity = new Map<number, string[]>();
  for (const n of nodes) {
    const c = assignment[n.id];
    if (c === undefined) continue;
    const members = byCommunity.get(c);
    if (members) members.push(n.id); else byCommunity.set(c, [n.id]);
  }

  const label = new Map(nodes.map((n) => [n.id, n.label]));
  return [...byCommunity.entries()]
    .filter(([, members]) => members.length >= minSize)
    .sort((a, b) => a[0] - b[0])
    .map(([, members], i) => {
      const hub = members.reduce((best, id) =>
        (degree.get(id) || 0) > (degree.get(best) || 0) ? id : best);
      return {
        id: `cluster:${i}`,
        label: `${label.get(hub) || hub} · ${members.length}`,
        members,
      };
    });
}
