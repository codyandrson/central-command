/**
 * A toast goes away three ways: its own countdown, the X, and a SWIPE — with a
 * mouse or a finger, either direction, errors included. The countdown pauses
 * for a mouse hovering, never for a finger (a tap "enters" and never leaves).
 *
 * The context is mocked at its seam: what is under test is the card's pointer
 * handling and timers, not the diff that produces the toasts.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Toast } from './NotificationsContext';

const dismiss = vi.fn();
let toasts: Toast[] = [];

vi.mock('./NotificationsContext', () => ({
  useNotifications: () => ({ toasts, dismiss }),
}));
vi.mock('./useVisualViewportPin', () => ({ useVisualViewportPin: () => {} }));

import { AUTO_DISMISS_MS, SWIPE_DISMISS_PX, SWIPE_OUT_MS, ToastHost } from './ToastHost';

const attention: Toast = {
  id: 'item:1', title: 'inbox-triage proposed a change', body: 'Record a membership',
  severity: 'attention', target: { kind: 'decision', itemId: 'wi_1' }, at: 0,
};
const error: Toast = {
  id: 'err:1', title: 'proposal failed', body: 'boom', severity: 'error', at: 0,
};

function mount(list: Toast[]) {
  toasts = list;
  const onOpenDecision = vi.fn();
  render(<ToastHost onOpenDecision={onOpenDecision} onOpenSession={vi.fn()} />);
  return { card: screen.getByTestId('cc-toast'), onOpenDecision };
}

/** A pointer drag from x=100 to x=100+dx, released. */
function swipe(card: HTMLElement, dx: number, pointerType = 'touch') {
  const base = { pointerId: 7, pointerType, button: 0, isPrimary: true };
  fireEvent.pointerDown(card, { ...base, clientX: 100 });
  fireEvent.pointerMove(card, { ...base, clientX: 100 + dx / 2 });
  fireEvent.pointerMove(card, { ...base, clientX: 100 + dx });
  fireEvent.pointerUp(card, { ...base, clientX: 100 + dx });
}

beforeEach(() => { vi.useFakeTimers(); dismiss.mockReset(); });
afterEach(() => { vi.useRealTimers(); });

describe('swipe to dismiss', () => {
  it('a swipe past the threshold slides the card out and dismisses it', () => {
    const { card } = mount([attention]);
    swipe(card, SWIPE_DISMISS_PX + 20);
    expect(card.dataset.leaving).toBe('true');
    expect(dismiss).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(SWIPE_OUT_MS); });
    expect(dismiss).toHaveBeenCalledWith('item:1');
  });

  it('either direction, and with a mouse as well as a finger', () => {
    const { card } = mount([attention]);
    swipe(card, -(SWIPE_DISMISS_PX + 20), 'mouse');
    act(() => { vi.advanceTimersByTime(SWIPE_OUT_MS); });
    expect(dismiss).toHaveBeenCalledWith('item:1');
  });

  it('a swipe is an explicit act: it dismisses an error toast too', () => {
    const { card } = mount([error]);
    swipe(card, SWIPE_DISMISS_PX + 20);
    act(() => { vi.advanceTimersByTime(SWIPE_OUT_MS); });
    expect(dismiss).toHaveBeenCalledWith('err:1');
  });

  it('a short drag springs back, and the click it ends with does not open the record', () => {
    const { card, onOpenDecision } = mount([attention]);
    swipe(card, SWIPE_DISMISS_PX - 30);
    expect(card.dataset.leaving).toBeUndefined();
    fireEvent.click(screen.getByText('inbox-triage proposed a change'));
    expect(onOpenDecision).not.toHaveBeenCalled();
    expect(dismiss).not.toHaveBeenCalled();
    // The next, undragged click opens as before.
    fireEvent.click(screen.getByText('inbox-triage proposed a change'));
    expect(onOpenDecision).toHaveBeenCalledWith('wi_1');
  });

  it('a wobble under the slop is a tap: it opens the record', () => {
    const { card, onOpenDecision } = mount([attention]);
    swipe(card, 4);
    fireEvent.click(screen.getByText('inbox-triage proposed a change'));
    expect(onOpenDecision).toHaveBeenCalledWith('wi_1');
  });
});

describe('the countdown', () => {
  it('runs out on its own for an attention toast', () => {
    mount([attention]);
    act(() => { vi.advanceTimersByTime(AUTO_DISMISS_MS); });
    expect(dismiss).toHaveBeenCalledWith('item:1');
  });

  it('pauses while a mouse hovers, and resumes when it leaves', () => {
    const { card } = mount([attention]);
    fireEvent.pointerEnter(card, { pointerType: 'mouse' });
    act(() => { vi.advanceTimersByTime(AUTO_DISMISS_MS * 2); });
    expect(dismiss).not.toHaveBeenCalled();
    fireEvent.pointerLeave(card, { pointerType: 'mouse' });
    act(() => { vi.advanceTimersByTime(AUTO_DISMISS_MS); });
    expect(dismiss).toHaveBeenCalledWith('item:1');
  });

  it('does not pause for a finger: a tapped toast on a phone still expires', () => {
    const { card } = mount([attention]);
    fireEvent.pointerEnter(card, { pointerType: 'touch' });
    act(() => { vi.advanceTimersByTime(AUTO_DISMISS_MS); });
    expect(dismiss).toHaveBeenCalledWith('item:1');
  });

  it('pauses while a drag is in progress', () => {
    const { card } = mount([attention]);
    const base = { pointerId: 7, pointerType: 'touch', button: 0, isPrimary: true };
    fireEvent.pointerDown(card, { ...base, clientX: 100 });
    fireEvent.pointerMove(card, { ...base, clientX: 120 });
    act(() => { vi.advanceTimersByTime(AUTO_DISMISS_MS * 2); });
    expect(dismiss).not.toHaveBeenCalled();
    fireEvent.pointerUp(card, { ...base, clientX: 120 });
    act(() => { vi.advanceTimersByTime(AUTO_DISMISS_MS); });
    expect(dismiss).toHaveBeenCalledWith('item:1');
  });
});
