/**
 * apiFetch — one seam for the cockpit's `{ ok, error }` REST envelope (and
 * FastAPI's bare `{ detail }` errors on a non-2xx status), replacing the
 * hand-rolled fetch → res.json() → check-ok → throw duplicated at ~35 call
 * sites. Also the natural home for typing a response instead of `any`.
 *
 * `fallback` is used only when the server reports `ok: false` with no
 * `error` string — pass a call-specific message to preserve a site's old
 * wording; a non-ok HTTP status always reports `detail`/`error`/`HTTP <status>`.
 */
export async function apiFetch<T = unknown>(
  url: string,
  init?: RequestInit,
  fallback = 'Request failed',
): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const body = await res.json().catch(() => null) as { error?: string; detail?: string } | null;
    throw new Error(body?.error || body?.detail || `HTTP ${res.status}`);
  }
  const payload = await res.json() as T & { ok?: boolean; error?: string };
  if (payload && typeof payload === 'object' && (payload as { ok?: boolean }).ok === false) {
    throw new Error((payload as { error?: string }).error || fallback);
  }
  return payload as T;
}
