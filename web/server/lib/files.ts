/**
 * Async file helpers for reading/writing JSON and text files.
 *
 * All functions swallow `ENOENT` errors and return a caller-supplied fallback,
 * making them safe to use before files exist.
 * @module
 */

import fs from 'node:fs/promises';

/**
 * Read and parse a JSON file. Returns `fallback` on any error.
 */
export async function readJSON<T>(filePath: string, fallback: T): Promise<T> {
  try {
    const raw = await fs.readFile(filePath, 'utf8');
    return JSON.parse(raw) as T;
  } catch (err) {
    // Log non-ENOENT errors (corruption, permissions, etc.)
    if ((err as NodeJS.ErrnoException).code !== 'ENOENT') {
      console.warn(`[files] readJSON failed for ${filePath}:`, (err as Error).message);
    }
    return fallback;
  }
}

/**
 * Write JSON to a file (pretty-printed).
 */
export async function writeJSON(filePath: string, data: unknown): Promise<void> {
  await fs.writeFile(filePath, JSON.stringify(data, null, 2));
}

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
