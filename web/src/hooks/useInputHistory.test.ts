/** Tests for useInputHistory hook. */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useInputHistory } from './useInputHistory';

describe('useInputHistory', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('starts with empty history', () => {
    const { result } = renderHook(() => useInputHistory());
    expect(result.current.isNavigating()).toBe(false);
    expect(result.current.navigateUp('draft')).toBeNull();
  });

  it('adds entries to history', () => {
    const { result } = renderHook(() => useInputHistory());

    act(() => result.current.addToHistory('hello'));
    act(() => result.current.addToHistory('world'));

    // Navigate up should return most recent
    let entry: string | null = null;
    act(() => { entry = result.current.navigateUp(''); });
    expect(entry).toBe('world');
  });

  it('suppresses duplicate consecutive entries', () => {
    const { result } = renderHook(() => useInputHistory());

    act(() => result.current.addToHistory('same'));
    act(() => result.current.addToHistory('same'));

    let first: string | null = null;
    act(() => { first = result.current.navigateUp(''); });
    expect(first).toBe('same');

    let second: string | null = null;
    act(() => { second = result.current.navigateUp(''); });
    // Only one entry, so navigateUp should return null
    expect(second).toBeNull();
  });

  it('ignores empty text', () => {
    const { result } = renderHook(() => useInputHistory());

    act(() => result.current.addToHistory(''));
    act(() => result.current.addToHistory('   '));

    expect(result.current.navigateUp('draft')).toBeNull();
  });

  it('navigates up through history', () => {
    const { result } = renderHook(() => useInputHistory());

    act(() => result.current.addToHistory('first'));
    act(() => result.current.addToHistory('second'));
    act(() => result.current.addToHistory('third'));

    let entry: string | null = null;
    act(() => { entry = result.current.navigateUp('current draft'); });
    expect(entry).toBe('third');

    act(() => { entry = result.current.navigateUp(''); });
    expect(entry).toBe('second');

    act(() => { entry = result.current.navigateUp(''); });
    expect(entry).toBe('first');

    // At oldest — should return null
    act(() => { entry = result.current.navigateUp(''); });
    expect(entry).toBeNull();
  });

  it('navigates down back to draft', () => {
    const { result } = renderHook(() => useInputHistory());

    act(() => result.current.addToHistory('one'));
    act(() => result.current.addToHistory('two'));

    let entry: string | null = null;
    act(() => { entry = result.current.navigateUp('my draft'); });
    expect(entry).toBe('two');

    act(() => { entry = result.current.navigateUp(''); });
    expect(entry).toBe('one');

    // Navigate back down
    act(() => { entry = result.current.navigateDown(); });
    expect(entry).toBe('two');

    act(() => { entry = result.current.navigateDown(); });
    expect(entry).toBe('my draft');

    // Already at draft — returns null
    act(() => { entry = result.current.navigateDown(); });
    expect(entry).toBeNull();
  });

  it('reset clears navigation state', () => {
    const { result } = renderHook(() => useInputHistory());

    act(() => result.current.addToHistory('test'));
    act(() => { result.current.navigateUp(''); });
    expect(result.current.isNavigating()).toBe(true);

    act(() => result.current.reset());
    expect(result.current.isNavigating()).toBe(false);
  });

  it('persists history to localStorage', () => {
    const { result } = renderHook(() => useInputHistory());

    act(() => result.current.addToHistory('persisted'));

    const stored = JSON.parse(localStorage.getItem('nerve-input-history') || '[]');
    expect(stored).toContain('persisted');
  });

  it('loads history from localStorage on mount', () => {
    localStorage.setItem('nerve-input-history', JSON.stringify(['loaded']));

    const { result } = renderHook(() => useInputHistory());

    let entry: string | null = null;
    act(() => { entry = result.current.navigateUp(''); });
    expect(entry).toBe('loaded');
  });
});
