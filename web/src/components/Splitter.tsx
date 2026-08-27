import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Group, Panel, Separator, useDefaultLayout, type GroupProps } from 'react-resizable-panels';

export { Panel };

/**
 * A `Group` whose layout persists itself.
 *
 * The library owns the storage — `localStorage['react-resizable-panels:<id>']`
 * (with the panel ids appended when `panelIds` is given, which is how a group
 * with conditionally-rendered panels keeps one layout per shape). There is no
 * second source of truth to keep in sync; that is the whole point of adopting it.
 */
export function SplitGroup({
  id,
  panelIds,
  children,
  ...rest
}: GroupProps & { id: string; panelIds?: string[] }) {
  const { defaultLayout, onLayoutChanged } = useDefaultLayout({
    id,
    panelIds,
    onlySaveAfterUserInteractions: true,
  });

  return (
    <Group id={id} defaultLayout={defaultLayout} onLayoutChanged={onLayoutChanged} {...rest}>
      {children}
    </Group>
  );
}

/**
 * The cockpit's divider look: a hairline that lights up on hover, in a 12px
 * hit target. Double-click resets the neighbouring panel to its default size
 * (the library's own behaviour).
 *
 * `inset` leaves a 12px gap at both ends — right for the rounded shell panels,
 * wrong for a flush list/detail boundary.
 */
export function SplitSeparator({ vertical = false, inset = true }: { vertical?: boolean; inset?: boolean }) {
  return (
    <Separator
      title="Drag to resize. Double click to reset"
      aria-label="Resize panels"
      className={`group relative flex shrink-0 justify-center ${vertical ? 'h-3 flex-col' : 'w-3 items-stretch'}`}
    >
      <div
        className={`pointer-events-none rounded-full bg-border transition-colors group-hover:bg-primary/55 group-hover:shadow-[0_0_16px_rgba(0,0,0,0.22)] group-active:bg-primary/70 ${
          vertical ? `h-px w-full ${inset ? 'mx-3' : ''}` : `w-px ${inset ? 'my-3' : ''}`
        }`}
      />
    </Separator>
  );
}

/** Tailwind's `@3xl` container breakpoint in px — the side-by-side threshold. */
const SIDE_BY_SIDE_PX = 768;

/**
 * The list+detail shape Agents / Decisions / Skills all share.
 *
 * The width test used to be a Tailwind container query (`@3xl:flex-row`), but a
 * draggable boundary cannot be expressed in CSS alone — the panel group has to
 * exist only in the side-by-side state, because in the stacked state its
 * percentages would be dividing HEIGHT. So the breakpoint is measured here and
 * the narrow layout stays exactly the plain stacked flex column it was.
 */
export function ListDetailSplit({ id, aside, children }: { id: string; aside: ReactNode; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [wide, setWide] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;

    const measure = () => setWide(el.getBoundingClientRect().width >= SIDE_BY_SIDE_PX);
    measure();

    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  if (!wide) {
    return (
      <div ref={ref} className="flex h-full min-h-0 flex-col">
        <aside className="flex w-full max-h-[45%] shrink-0 flex-col border-b border-border/40 min-h-0">
          {aside}
        </aside>
        {children}
      </div>
    );
  }

  return (
    <div ref={ref} className="h-full min-h-0">
      <SplitGroup id={id} className="h-full min-h-0">
        {/* Panel styles land on a NESTED div whose `overflow` is set inline, so
            anything but the library's `auto` has to be a style, not a class.
            Both panes are flex columns and each view keeps its OWN scroll
            container as the child — same elements it had before the split. */}
        <Panel
          id="list"
          defaultSize={340}
          minSize={220}
          maxSize="60"
          className="flex min-h-0 flex-col"
          style={{ overflow: 'hidden' }}
        >
          {aside}
        </Panel>
        <SplitSeparator inset={false} />
        <Panel id="detail" className="flex min-h-0 min-w-0 flex-col" style={{ overflow: 'hidden' }}>
          {children}
        </Panel>
      </SplitGroup>
    </div>
  );
}
