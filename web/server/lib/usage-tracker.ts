/**
 * Persistent token usage tracker.
 *
 * Stores a high-water mark of token input/output counts and cost that
 * survives session compaction. Values only ever increase — a session
 * reset won't lose accumulated usage data. Uses async `fs/promises`
 * for non-blocking I/O and a mutex to prevent concurrent write races.
 * @module
 */

import fs from 'node:fs/promises';
import path from 'node:path';
import { config } from './config.js';
import { createMutex } from './mutex.js';

const USAGE_FILE = config.usageFile;

interface UsageData {
  totalInput: number;
  totalOutput: number;
  totalCost: number;
  lastUpdated: string;
}

const DEFAULT_USAGE: UsageData = {
  totalInput: 0,
  totalOutput: 0,
  totalCost: 0,
  lastUpdated: '',
};

const withLock = createMutex();

async function loadUsage(): Promise<UsageData> {
  try {
    const data = await fs.readFile(USAGE_FILE, 'utf8');
    return JSON.parse(data) as UsageData;
  } catch {
    // File doesn't exist or is invalid — return defaults
    return { ...DEFAULT_USAGE };
  }
}

async function saveUsage(data: UsageData): Promise<void> {
  data.lastUpdated = new Date().toISOString();
  // Ensure directory exists
  await fs.mkdir(path.dirname(USAGE_FILE), { recursive: true });
  await fs.writeFile(USAGE_FILE, JSON.stringify(data, null, 2));
}

/**
 * Update with current tokscale totals.
 * Only increases - never decreases (survives compaction).
 * Serialized via mutex to prevent read-modify-write races.
 */
export async function updateUsage(
  input: number,
  output: number,
  cost: number,
): Promise<UsageData> {
  return withLock(async () => {
    const data = await loadUsage();

    // High water mark: only go up, never down
    if (input > data.totalInput) data.totalInput = input;
    if (output > data.totalOutput) data.totalOutput = output;
    if (cost > data.totalCost) data.totalCost = cost;

    await saveUsage(data);
    return data;
  });
}
