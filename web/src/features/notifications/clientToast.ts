/**
 * A way for code that runs OUTSIDE the notification tree (SettingsProvider
 * wraps it, so a hook like useTTS cannot reach its context) to ask for an error
 * toast. The provider listens for the event and pushes through the same path as
 * a failure push, so the toast-mode setting and the stack cap apply.
 */
export const CLIENT_ERROR_EVENT = 'cc:client-error';

export interface ClientErrorDetail {
  /** Stable id: the same id is not stacked twice while its toast is showing. */
  id: string;
  title: string;
  body?: string;
}

export function reportClientError(detail: ClientErrorDetail): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<ClientErrorDetail>(CLIENT_ERROR_EVENT, { detail }));
}
