// Font definitions for the cockpit UI.
//
// Every font comes from its @fontsource package (SIL Open Font License; the
// licence ships in the package), imported per weight as the packages document.
// Vite bundles the CSS and the font files into the build, so they are served
// from the cockpit's own origin: a deployment with no internet access renders
// the same as one with it, and nothing is requested from an outside host.

export type FontName = 'instrument-sans' | 'space-grotesk' | 'jetbrains-mono';

export interface Font {
  name: FontName;
  label: string;
  family: string;
  /** Loads this font's @font-face rules: the normal style, the weights the cockpit uses (lazy). */
  load: () => Promise<unknown>;
}

export const fonts: Record<FontName, Font> = {
  'instrument-sans': {
    name: 'instrument-sans',
    label: 'Instrument Sans',
    family: "'Instrument Sans', 'Helvetica Neue', Arial, sans-serif",
    load: () => Promise.all([
      import('@fontsource/instrument-sans/400.css'),
      import('@fontsource/instrument-sans/500.css'),
      import('@fontsource/instrument-sans/600.css'),
      import('@fontsource/instrument-sans/700.css'),
    ]),
  },
  'space-grotesk': {
    name: 'space-grotesk',
    label: 'Space Grotesk',
    family: "'Space Grotesk', 'Instrument Sans', 'Helvetica Neue', Arial, sans-serif",
    load: () => Promise.all([
      import('@fontsource/space-grotesk/400.css'),
      import('@fontsource/space-grotesk/500.css'),
      import('@fontsource/space-grotesk/700.css'),
    ]),
  },
  'jetbrains-mono': {
    name: 'jetbrains-mono',
    label: 'JetBrains Mono',
    family: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace",
    load: () => Promise.all([
      import('@fontsource/jetbrains-mono/400.css'),
      import('@fontsource/jetbrains-mono/600.css'),
      import('@fontsource/jetbrains-mono/700.css'),
    ]),
  },
};

/** All available font names as a typed array. */
export const fontNames = Object.keys(fonts) as FontName[];

/** Monospace font stack for code blocks (always used regardless of UI font setting). */
export const monoFont = "'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace";

// Track which fonts have been loaded
const loadedFonts = new Set<FontName>();

// Lazy-load a bundled font's stylesheets once.
function loadFont(name: FontName): void {
  if (loadedFonts.has(name)) return;
  loadedFonts.add(name);
  // A failed load (a stale chunk after an update) may be retried by the next apply.
  fonts[name].load().catch(() => loadedFonts.delete(name));
}

/** Apply a font to the document root by setting CSS custom properties and lazy-loading its bundled stylesheet. */
export function applyFont(fontName: FontName): void {
  const font = fonts[fontName];
  if (!font) return;
  
  // Lazy-load the font's bundled stylesheets
  loadFont(fontName);
  
  const root = document.documentElement;
  
  // Set the UI font (--font-sans variable)
  root.style.setProperty('--font-sans', font.family);
  
  // Keep monospace font for code blocks unchanged
  root.style.setProperty('--font-mono', monoFont);
}

// Initialize fonts used in the default shell before settings hydrate
loadFont('instrument-sans');
loadFont('jetbrains-mono');
