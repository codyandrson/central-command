import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type SyntheticEvent } from 'react';
import { AlertTriangle, Bell, X } from 'lucide-react';
import { useNotifications, type Toast } from './NotificationsContext';
import { useVisualViewportPin } from './useVisualViewportPin';

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
 * Every toast is SWIPEABLE, either way, with a mouse or a finger: one Pointer
 * Events handler covers both. A swipe is an explicit act, so it dismisses an
 * error toast too — the auto-dismiss exemption below is about a toast going
 * away on its own, not about the operator sending it away.
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

/** A drag that ends at least this far from where it started is a swipe. */
export const SWIPE_DISMISS_PX = 72;
/** Closer than this the pointer only wobbled: the tap or click still counts. */
const SWIPE_SLOP_PX = 8;
/** How long the card takes to slide off before it is removed. */
export const SWIPE_OUT_MS = 150;

function ToastCard({ toast, onOpen, onDismiss }: {
  toast: Toast; onOpen: () => void; onDismiss: () => void;
}) {
  const isError = toast.severity === 'error';
  const Icon = isError ? AlertTriangle : Bell;
  // Hovering pauses the countdown — a toast must never vanish mid-read. MOUSE
  // hover only: a finger "enters" on every tap and never leaves, so the mouse
  // events would park a tapped toast on a phone until it was dismissed by hand.
  const [hovered, setHovered] = useState(false);
  // The swipe in progress (`dragX`, px from where it started), and the card on
  // its way out (`leaving`, the direction it slides).
  const [dragX, setDragX] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [leaving, setLeaving] = useState<1 | -1 | null>(null);
  const drag = useRef<{ id: number; startX: number; moved: boolean } | null>(null);
  // Set when a release ended a real drag: the click the browser fires next is
  // the end of that drag, not a request to open the record.
  const swallowClick = useRef(false);

  // Keyed on the toast id, not on the callback: `onDismiss` is a fresh closure
  // every render, and depending on it would restart the countdown each time
  // anything above re-renders — a toast that never expires.
  const dismissRef = useRef(onDismiss);
  useEffect(() => { dismissRef.current = onDismiss; }, [onDismiss]);
  useEffect(() => {
    if (isError || hovered || dragging || leaving !== null) return;
    const t = setTimeout(() => dismissRef.current(), AUTO_DISMISS_MS);
    return () => clearTimeout(t);
  }, [toast.id, isError, hovered, dragging, leaving]);
  // The swipe-out: slide, then remove. Errors included — see the header.
  useEffect(() => {
    if (leaving === null) return;
    const t = setTimeout(() => dismissRef.current(), SWIPE_OUT_MS);
    return () => clearTimeout(t);
  }, [leaving]);

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || leaving !== null) return;
    drag.current = { id: e.pointerId, startX: e.clientX, moved: false };
    // jsdom has no pointer capture; a browser does, and it keeps the move and
    // release events coming when the pointer leaves the card mid-swipe.
    e.currentTarget.setPointerCapture?.(e.pointerId);
    setDragging(true);
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    const dx = e.clientX - d.startX;
    if (!d.moved && Math.abs(dx) < SWIPE_SLOP_PX) return;
    d.moved = true;
    setDragX(dx);
  };
  const onPointerUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    drag.current = null;
    setDragging(false);
    const dx = e.clientX - d.startX;
    swallowClick.current = d.moved;
    if (d.moved && Math.abs(dx) >= SWIPE_DISMISS_PX) {
      setLeaving(dx > 0 ? 1 : -1);
      return;
    }
    setDragX(0);
  };
  const onPointerCancel = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    drag.current = null;
    setDragging(false);
    setDragX(0);
  };
  const onClickCapture = (e: SyntheticEvent) => {
    if (!swallowClick.current) return;
    swallowClick.current = false;
    e.stopPropagation();
    e.preventDefault();
  };

  const x = leaving !== null ? leaving * 480 : dragX;
  return (
    <div
      onPointerEnter={(e) => { if (e.pointerType === 'mouse') setHovered(true); }}
      onPointerLeave={(e) => { if (e.pointerType === 'mouse') setHovered(false); }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
      onClickCapture={onClickCapture}
      data-testid="cc-toast"
      data-toast-id={toast.id}
      data-leaving={leaving !== null ? 'true' : undefined}
      style={{
        transform: x ? `translateX(${x}px)` : undefined,
        opacity: leaving !== null ? 0 : undefined,
        transition: dragging ? 'none' : `transform ${SWIPE_OUT_MS}ms ease-out, opacity ${SWIPE_OUT_MS}ms ease-out`,
      }}
      className={`pointer-events-auto flex w-[22rem] max-w-[calc(100vw-2rem)] touch-pan-y items-start gap-2 rounded-xl border px-3 py-2.5 shadow-lg ${
        dragging ? 'select-none ' : ''
      }${
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
  const hostRef = useRef<HTMLDivElement>(null);
  // Always mounted, even empty: the pin hook needs the element on its first
  // run, and an empty pointer-events-none flex box costs nothing.
  useVisualViewportPin(hostRef);

  return (
    <div
      ref={hostRef}
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
