import { describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';
import { useRef } from 'react';
import { useVisualViewportPin } from './useVisualViewportPin';

function Pinned() {
  const ref = useRef<HTMLDivElement>(null);
  useVisualViewportPin(ref);
  return <div ref={ref} data-testid="pinned" />;
}

describe('useVisualViewportPin', () => {
  it('translates the element by the visual/layout viewport offset and tracks pans', () => {
    const listeners: Record<string, () => void> = {};
    const vv = {
      offsetLeft: 100, offsetTop: 50, width: 400, height: 300,
      addEventListener: vi.fn((k: string, fn: () => void) => { listeners[k] = fn; }),
      removeEventListener: vi.fn(),
    };
    vi.stubGlobal('visualViewport', vv);
    Object.defineProperty(window, 'innerWidth', { value: 1000, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 800, configurable: true });

    const { getByTestId, unmount } = render(<Pinned />);
    expect(getByTestId('pinned').style.transform).toBe('translate(-500px, -450px)');

    vv.offsetLeft = 600; vv.offsetTop = 500;
    listeners.scroll();
    expect(getByTestId('pinned').style.transform).toBe('');

    unmount();
    expect(vv.removeEventListener).toHaveBeenCalledTimes(2);
    vi.unstubAllGlobals();
  });
});
