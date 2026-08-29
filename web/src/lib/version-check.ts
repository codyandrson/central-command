/**
 * One version-check state for the whole cockpit — the status-bar badge, the
 * Settings › Updates section and the version labels all read it, so a manual
 * "Check for updates" moves every surface at once.
 *
 * Automatic: on first subscribe, hourly after that, and whenever the tab
 * becomes visible again (the question the operator actually has on return
 * is "is this current?"). Manual: `checkVersion(true)` bypasses the server's
 * hourly cache. Neither applies anything — apply stays behind its own button
 * and the root updater.
 */
import { useSyncExternalStore } from 'react';

export interface VersionCheck {
  current: string;
  latest: string | null;
  updateAvailable: boolean;
  projectDir?: string | null;
  checkedAt?: number;
  error?: string;
}

export interface VersionState {
  info: VersionCheck | null;
  checking: boolean;
  /** Client-side failure to reach the cockpit server (not the server's own `info.error`). */
  failure: string | null;
}

const CHECK_INTERVAL_MS = 60 * 60 * 1000; // 1 hour

let state: VersionState = { info: null, checking: false, failure: null };
const listeners = new Set<() => void>();
let started = false;

function set(next: Partial<VersionState>) {
  state = { ...state, ...next };
  listeners.forEach(l => l());
}

export async function checkVersion(force = false): Promise<void> {
  if (state.checking) return;
  set({ checking: true, failure: null });
  try {
    const res = await fetch(force ? '/api/version/check?force=1' : '/api/version/check');
    if (!res.ok) {
      set({ checking: false, failure: `HTTP ${res.status}` });
      return;
    }
    set({ info: (await res.json()) as VersionCheck, checking: false });
  } catch (err) {
    set({ checking: false, failure: err instanceof Error ? err.message : String(err) });
  }
}

function start() {
  if (started) return;
  started = true;
  void checkVersion();
  setInterval(() => void checkVersion(), CHECK_INTERVAL_MS);
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') void checkVersion();
    });
  }
}

function subscribe(l: () => void) {
  listeners.add(l);
  start();
  return () => { listeners.delete(l); };
}

export function useVersionCheck(): VersionState {
  return useSyncExternalStore(subscribe, () => state, () => state);
}

/** Test seam: forget everything, including the started flag. */
export function _resetVersionCheck() {
  state = { info: null, checking: false, failure: null };
  started = false;
}
