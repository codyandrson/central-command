import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Bell, X } from 'lucide-react';
import { useNotifications, type Toast } from './NotificationsContext';

/**
 * Where interrupt-tier notifications appear.
 *
 * Bottom-right, fixed, and NEVER in the document flow — the no-displacement
 * rule: new content may arrive on its own, but it must not move what the operator is
 * currently reading. A toast that pushed the transcript down would be a worse
 * bug than no toast at all.
 *
 * Every toast is CLICKABLE and lands on the thing it is about (the Decisions
 * Inbox with the ask selected, or the discussion lane in Chat). A notification
 * the operator then has to go and find is a notification that wasted his time.
 *
 * No sound, by design — see NotificationsContext.
 */
export interface ToastHostProps {
  /** Go to the Decisions Inbox, selecting an operator item when we know it. */
  onOpenDecision: (itemId?: string) => void;
  /** Open a conversation lane in Chat. */
  onOpenSession: (sessionKey: string) => void;
}

/**
 * How long an attention toast stays before it fades itself out. The item it
 * points at is already counted by the Decisions badge and listed in the inbox,
 * so the toast is the "look at this next" nudge, not the record — nothing is
 * lost when it goes. ERRORS DO NOT AUTO-DISMISS: a failure push has no
 * projection row behind it (see NotificationsContext), so the toast is the
 * only place it surfaces until the operator opens Activity.
 */
export const AUTO_DISMISS_MS = 10_000;

function ToastCard({ toast, onOpen, onDismiss }: {
  toast: Toast; onOpen: () => void; onDismiss: () => void;
}) {
  const isError = toast.severity === 'error';
  const Icon = isError ? AlertTriangle : Bell;
  // Hovering pauses the countdown — a toast must never vanish mid-read.
  const [hovered, setHovered] = useState(false);

  // Keyed on the toast id, not on the callback: `onDismiss` is a fresh closure
  // every render, and depending on it would restart the countdown each time
  // anything above re-renders — a toast that never expires.
  const dismissRef = useRef(onDismiss);
  useEffect(() => { dismissRef.current = onDismiss; }, [onDismiss]);
  useEffect(() => {
    if (isError || hovered) return;
    const t = setTimeout(() => dismissRef.current(), AUTO_DISMISS_MS);
    return () => clearTimeout(t);
  }, [toast.id, isError, hovered]);

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      data-testid="cc-toast"
      data-toast-id={toast.id}
      className={`pointer-events-auto flex w-[22rem] max-w-[calc(100vw-2rem)] items-start gap-2 rounded-xl border px-3 py-2.5 shadow-lg ${
        isError
          ? 'border-destructive/30 bg-card/95 text-foreground'
          : 'border-primary/30 bg-card/95 text-foreground'
      }`}
    >
      <span className={`mt-0.5 shrink-0 ${isError ? 'text-destructive' : 'text-primary'}`}>
        <Icon size={13} aria-hidden="true" />
      </span>
      <button
        type="button"
        onClick={onOpen}
        disabled={!toast.target}
        className="min-w-0 flex-1 text-left disabled:cursor-default"
      >
        <p className="truncate text-xs font-semibold text-foreground">{toast.title}</p>
        {toast.body && (
          <p className="mt-0.5 line-clamp-2 text-[0.7rem] leading-snug text-muted-foreground">
            {toast.body}
          </p>
        )}
      </button>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss notification"
        className="-m-1.5 shrink-0 p-1.5 text-muted-foreground transition-colors hover:text-foreground"
      >
        <X size={12} />
      </button>
    </div>
  );
}

export function ToastHost({ onOpenDecision, onOpenSession }: ToastHostProps) {
  const { toasts, dismiss } = useNotifications();
  if (toasts.length === 0) return null;

  return (
    <div
      data-testid="cc-toast-host"
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2"
      role="status"
      aria-live="polite"
    >
      {toasts.map((t) => (
        <ToastCard
          key={t.id}
          toast={t}
          onDismiss={() => dismiss(t.id)}
          onOpen={() => {
            if (!t.target) return;
            if (t.target.kind === 'session' && t.target.sessionKey) {
              onOpenSession(t.target.sessionKey);
            } else if (t.target.kind === 'decision') {
              onOpenDecision(t.target.itemId);
            }
            // Dismiss on arrival: the operator is now looking at the record
            // itself, and a toast still floating over it is just clutter.
            dismiss(t.id);
          }}
        />
      ))}
    </div>
  );
}
