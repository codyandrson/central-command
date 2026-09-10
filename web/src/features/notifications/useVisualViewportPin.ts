import { useEffect, type RefObject } from 'react';

/**
 * Keep a `position: fixed` element inside the VISUAL viewport.
 *
 * `fixed` anchors to the layout viewport. Under pinch-zoom on a phone or
 * tablet the operator pans a smaller visual viewport across it, and a
 * bottom-right toast sits outside whatever slice is on screen — a
 * notification nobody can see (2026-09-09). This translates the element by
 * the offset between the two viewports so it follows the visible slice.
 * No-op where `visualViewport` is absent (jsdom, old browsers) or the two
 * viewports coincide (every unzoomed desktop).
 */
export function useVisualViewportPin(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const vv = window.visualViewport;
    const el = ref.current;
    if (!vv || !el) return;
    const place = () => {
      const dx = vv.offsetLeft + vv.width - window.innerWidth;
      const dy = vv.offsetTop + vv.height - window.innerHeight;
      el.style.transform = dx || dy ? `translate(${dx}px, ${dy}px)` : '';
    };
    place();
    vv.addEventListener('resize', place);
    vv.addEventListener('scroll', place);
    return () => {
      vv.removeEventListener('resize', place);
      vv.removeEventListener('scroll', place);
    };
  }, [ref]);
}
