import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useServerEvents, type ServerEvent } from './useServerEvents';

// Mock EventSource
class MockEventSource {
  static instances: MockEventSource[] = [];
  
  url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  readyState = 0; // CONNECTING
  
  private eventListeners: Map<string, ((e: MessageEvent) => void)[]> = new Map();
  
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
    
    // Simulate async connection
    setTimeout(() => {
      this.readyState = 1; // OPEN
      this.onopen?.();
    }, 10);
  }
  
  addEventListener(type: string, handler: (e: MessageEvent) => void) {
    const handlers = this.eventListeners.get(type) || [];
    handlers.push(handler);
    this.eventListeners.set(type, handlers);
  }
  
  removeEventListener(type: string, handler: (e: MessageEvent) => void) {
    const handlers = this.eventListeners.get(type) || [];
    const idx = handlers.indexOf(handler);
    if (idx >= 0) handlers.splice(idx, 1);
  }
  
  dispatchEvent(type: string, data: unknown) {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    const handlers = this.eventListeners.get(type) || [];
    for (const h of handlers) h(event);
    this.onmessage?.(event);
  }
  
  simulateError() {
    this.readyState = 2; // CLOSED
    this.onerror?.();
  }
  
  close() {
    this.readyState = 2; // CLOSED
    const idx = MockEventSource.instances.indexOf(this);
    if (idx >= 0) MockEventSource.instances.splice(idx, 1);
  }
  
  static clear() {
    MockEventSource.instances = [];
  }
  
  static getLatest(): MockEventSource | undefined {
    return MockEventSource.instances[MockEventSource.instances.length - 1];
  }
}

describe('useServerEvents', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    MockEventSource.clear();
    // @ts-expect-error - mocking global
    global.EventSource = MockEventSource;
  });
  
  afterEach(() => {
    vi.useRealTimers();
    MockEventSource.clear();
  });
  
  it('should connect to SSE endpoint', async () => {
    const onEvent = vi.fn();
    
    renderHook(() => useServerEvents(onEvent));
    
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    
    expect(MockEventSource.instances.length).toBe(1);
    expect(MockEventSource.instances[0].url).toBe('/api/events');
  });
  
  it('should report connected state after connection', async () => {
    const onEvent = vi.fn();
    
    const { result } = renderHook(() => useServerEvents(onEvent));
    
    expect(result.current.connected).toBe(false);
    
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    
    expect(result.current.connected).toBe(true);
  });
  
  it('should call onEvent for memory.changed events', async () => {
    const onEvent = vi.fn();
    
    renderHook(() => useServerEvents(onEvent));
    
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    
    const es = MockEventSource.getLatest()!;
    const event: ServerEvent = {
      event: 'memory.changed',
      data: { source: 'file' },
      ts: Date.now(),
    };
    
    act(() => {
      es.dispatchEvent('memory.changed', event);
    });
    
    expect(onEvent).toHaveBeenCalledWith(event);
  });
  
  it('should not call onEvent for ping events', async () => {
    const onEvent = vi.fn();
    
    renderHook(() => useServerEvents(onEvent));
    
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    
    const es = MockEventSource.getLatest()!;
    const pingEvent: ServerEvent = {
      event: 'ping',
      data: {},
      ts: Date.now(),
    };
    
    act(() => {
      es.dispatchEvent('ping', pingEvent);
    });
    
    expect(onEvent).not.toHaveBeenCalled();
  });
  
  it('should not connect when disabled', async () => {
    const onEvent = vi.fn();
    
    renderHook(() => useServerEvents(onEvent, { enabled: false }));
    
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    
    expect(MockEventSource.instances.length).toBe(0);
  });
  
  it('should reconnect on error with exponential backoff', async () => {
    const onEvent = vi.fn();
    
    const { result } = renderHook(() => 
      useServerEvents(onEvent, { reconnectBaseDelay: 100 })
    );
    
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    
    expect(result.current.reconnectAttempts).toBe(0);
    
    // Simulate error
    const es = MockEventSource.getLatest()!;
    act(() => {
      es.simulateError();
    });
    
    expect(result.current.connected).toBe(false);
    expect(result.current.reconnectAttempts).toBe(1);
    
    // Wait for reconnect
    await act(async () => {
      await vi.advanceTimersByTimeAsync(150);
    });
    
    // Should have new connection
    expect(MockEventSource.instances.length).toBe(1);
  });
  
  it('should cleanup on unmount', async () => {
    const onEvent = vi.fn();
    
    const { unmount } = renderHook(() => useServerEvents(onEvent));
    
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    
    expect(MockEventSource.instances.length).toBe(1);
    
    unmount();
    
    expect(MockEventSource.instances.length).toBe(0);
  });
  
  it('should track lastEvent', async () => {
    const onEvent = vi.fn();
    
    const { result } = renderHook(() => useServerEvents(onEvent));
    
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    
    expect(result.current.lastEvent).toBe(null);
    
    const es = MockEventSource.getLatest()!;
    const event: ServerEvent = {
      event: 'memory.changed',
      data: { test: true },
      ts: 123456,
    };
    
    act(() => {
      es.dispatchEvent('memory.changed', event);
    });
    
    expect(result.current.lastEvent).toEqual(event);
  });
});
