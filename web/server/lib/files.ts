/**
 * Async text-file helper.
 *
 * Swallows `ENOENT` errors and returns a caller-supplied fallback, making it
 * safe to use before the file exists.
 * @module
 */

import fs from 'node:fs/promises';

/**
 * Read a text file. Returns `fallback` on any error.
 */
export async function readText(filePath: string, fallback: string = ''): Promise<string> {
  try {
    return await fs.readFile(filePath, 'utf8');
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== 'ENOENT') {
      console.warn(`[files] readText failed for ${filePath}:`, (err as Error).message);
    }
    return fallback;
  }
}
