import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { resolve } from 'node:path';

function defaultIdentityPaths(): string[] {
  const home = homedir();
  return [
    resolve(home, '.openclaw', 'workspace', 'IDENTITY.md'),
    resolve(home, '.openclaw', 'workspace', 'projects', 'openclaw-agent', 'IDENTITY.md'),
  ];
}

export function detectAgentDisplayNameDefault(
  existingName: string | undefined,
  fallbackName: string,
  identityPaths: string[] = defaultIdentityPaths(),
): string {
  if (existingName?.trim()) return existingName.trim();

  for (const identityPath of identityPaths) {
    if (!existsSync(identityPath)) continue;

    try {
      const raw = readFileSync(identityPath, 'utf-8');
      const match = raw.match(/^-[ \t]*\*\*Name:\*\*[ \t]*(.+)$/m);
      const detected = match?.[1]?.trim();
      if (detected) return detected;
    } catch {
      // Non-fatal — keep falling back.
    }
  }

  return fallbackName;
}
