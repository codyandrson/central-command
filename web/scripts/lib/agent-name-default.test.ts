import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { describe, expect, it } from 'vitest';

import { detectAgentDisplayNameDefault } from './agent-name-default.js';

describe('detectAgentDisplayNameDefault', () => {
  it('prefers existing AGENT_NAME when present', () => {
    const result = detectAgentDisplayNameDefault('  Existing Agent  ', 'Agent', []);
    expect(result).toBe('Existing Agent');
  });

  it('detects Name from IDENTITY.md metadata', () => {
    const tempDir = mkdtempSync(join(tmpdir(), 'nerve-agent-name-'));
    const identityPath = join(tempDir, 'IDENTITY.md');
    writeFileSync(identityPath, '# IDENTITY\n- **Name:** Chip\n', 'utf8');

    const result = detectAgentDisplayNameDefault(undefined, 'Agent', [identityPath]);
    expect(result).toBe('Chip');
  });

  it('falls back to second identity candidate if first is missing/invalid', () => {
    const tempDir = mkdtempSync(join(tmpdir(), 'nerve-agent-name-'));
    const firstPath = join(tempDir, 'missing.md');
    const secondPath = join(tempDir, 'nested', 'IDENTITY.md');
    mkdirSync(join(tempDir, 'nested'), { recursive: true });
    writeFileSync(secondPath, 'Notes\n- **Name:** Cookie\n', 'utf8');

    const result = detectAgentDisplayNameDefault(undefined, 'Agent', [firstPath, secondPath]);
    expect(result).toBe('Cookie');
  });

  it('falls back to literal default when metadata is unavailable or malformed', () => {
    const tempDir = mkdtempSync(join(tmpdir(), 'nerve-agent-name-'));
    const identityPath = join(tempDir, 'IDENTITY.md');
    writeFileSync(identityPath, '# IDENTITY\nNo name field here\n', 'utf8');

    const result = detectAgentDisplayNameDefault(undefined, 'Agent', [identityPath]);
    expect(result).toBe('Agent');
  });
});
