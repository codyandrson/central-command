/** Tests for useKeyboardShortcuts hook. */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useKeyboardShortcuts } from './useKeyboardShortcuts';

function fireKey(opts: {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  shiftKey?: boolean;
  altKey?: boolean;
  target?: HTMLElement;
}) {
  const event = new KeyboardEvent('keydown', {
    key: opts.key,
    metaKey: opts.metaKey || false,
    ctrlKey: opts.ctrlKey || false,
    shiftKey: opts.shiftKey || false,
    altKey: opts.altKey || false,
    bubbles: true,
    cancelable: true,
  });
  // Override target for testing focus context
  if (opts.target) {
    Object.defineProperty(event, 'target', { value: opts.target });
  }
  window.dispatchEvent(event);
}

describe('useKeyboardShortcuts', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('fires handler on matching key press', () => {
    const handler = vi.fn();
    renderHook(() => useKeyboardShortcuts([
      { key: 'k', meta: true, handler },
    ]));

    fireKey({ key: 'k', metaKey: true });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('does not fire on non-matching key', () => {
    const handler = vi.fn();
    renderHook(() => useKeyboardShortcuts([
      { key: 'k', meta: true, handler },
    ]));

    fireKey({ key: 'j', metaKey: true });
    expect(handler).not.toHaveBeenCalled();
  });

  it('supports Ctrl as meta on non-Mac', () => {
    const handler = vi.fn();
    renderHook(() => useKeyboardShortcuts([
      { key: 'k', meta: true, handler },
    ]));

    fireKey({ key: 'k', ctrlKey: true });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('matches shift modifier', () => {
    const handler = vi.fn();
    renderHook(() => useKeyboardShortcuts([
      { key: 'p', meta: true, shift: true, handler },
    ]));

    // Without shift — should not fire
    fireKey({ key: 'p', metaKey: true });
    expect(handler).not.toHaveBeenCalled();

    // With shift — should fire
    fireKey({ key: 'p', metaKey: true, shiftKey: true });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('matches alt modifier', () => {
    const handler = vi.fn();
    renderHook(() => useKeyboardShortcuts([
      { key: 'n', alt: true, handler },
    ]));

    fireKey({ key: 'n', altKey: true });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('handles multiple shortcuts', () => {
    const handler1 = vi.fn();
    const handler2 = vi.fn();
    renderHook(() => useKeyboardShortcuts([
      { key: 'k', meta: true, handler: handler1 },
      { key: 'j', meta: true, handler: handler2 },
    ]));

    fireKey({ key: 'k', metaKey: true });
    expect(handler1).toHaveBeenCalledTimes(1);
    expect(handler2).not.toHaveBeenCalled();

    fireKey({ key: 'j', metaKey: true });
    expect(handler2).toHaveBeenCalledTimes(1);
  });

  it('is case insensitive for key matching', () => {
    const handler = vi.fn();
    renderHook(() => useKeyboardShortcuts([
      { key: 'K', meta: true, handler },
    ]));

    fireKey({ key: 'k', metaKey: true });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('skips global shortcuts when input is focused', () => {
    const handler = vi.fn();
    renderHook(() => useKeyboardShortcuts([
      { key: 'k', meta: true, handler, global: true },
    ]));

    const input = document.createElement('input');
    document.body.appendChild(input);
    try {
      fireKey({ key: 'k', metaKey: true, target: input });
      expect(handler).not.toHaveBeenCalled();
    } finally {
      document.body.removeChild(input);
    }
  });

  it('cleans up event listener on unmount', () => {
    const handler = vi.fn();
    const { unmount } = renderHook(() => useKeyboardShortcuts([
      { key: 'k', meta: true, handler },
    ]));

    unmount();
    fireKey({ key: 'k', metaKey: true });
    expect(handler).not.toHaveBeenCalled();
  });
});
