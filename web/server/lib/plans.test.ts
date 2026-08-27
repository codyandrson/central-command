import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { findRepoPlanByBeadId, listRepoPlans } from './plans.js';

const tempDirs: string[] = [];

async function createTempRepo(): Promise<string> {
  const repoRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'nerve-plans-'));
  tempDirs.push(repoRoot);
  await fs.mkdir(path.join(repoRoot, '.plans'), { recursive: true });
  return repoRoot;
}

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => fs.rm(dir, { recursive: true, force: true })));
});

describe('listRepoPlans', () => {
  it('parses CRLF frontmatter and closing delimiters at EOF', async () => {
    const repoRoot = await createTempRepo();
    await fs.writeFile(
      path.join(repoRoot, '.plans', 'crlf-plan.md'),
      [
        '---',
        'plan_id: plan-crlf',
        'plan_title: CRLF Plan',
        'status: In Progress',
        'bead_ids:',
        '  - nerve-4gpd',
        '---',
      ].join('\r\n'),
      'utf8',
    );

    const plans = await listRepoPlans(repoRoot);

    expect(plans).toHaveLength(1);
    expect(plans[0]).toMatchObject({
      path: '.plans/crlf-plan.md',
      title: 'CRLF Plan',
      planId: 'plan-crlf',
      status: 'In Progress',
      beadIds: ['nerve-4gpd'],
      archived: false,
    });
  });

  it('finds bead ids from frontmatter when the closing delimiter is the final line', async () => {
    const repoRoot = await createTempRepo();
    await fs.writeFile(
      path.join(repoRoot, '.plans', 'final-delimiter.md'),
      '---\nplan_id: plan-final\nbead_ids: [nerve-4gpd]\n---',
      'utf8',
    );

    await expect(findRepoPlanByBeadId('nerve-4gpd', repoRoot)).resolves.toMatchObject({
      path: '.plans/final-delimiter.md',
      planId: 'plan-final',
      beadIds: ['nerve-4gpd'],
    });
  });

  it('parses BOM-prefixed frontmatter', async () => {
    const repoRoot = await createTempRepo();
    await fs.writeFile(
      path.join(repoRoot, '.plans', 'bom-plan.md'),
      '\uFEFF---\nplan_id: plan-bom\nplan_title: BOM Plan\nbead_ids:\n  - nerve-bom1\n---\n# ignored title',
      'utf8',
    );

    await expect(findRepoPlanByBeadId('nerve-bom1', repoRoot)).resolves.toMatchObject({
      path: '.plans/bom-plan.md',
      planId: 'plan-bom',
      title: 'BOM Plan',
      beadIds: ['nerve-bom1'],
    });
  });
});
