// Font definitions for the cockpit UI.
//
// Every font is bundled with the cockpit (web/public/fonts/, from the @fontsource
// packages, SIL Open Font License — each folder carries its OFL.txt) and served
// from the cockpit's own origin: a deployment with no internet access renders
// the same as one with it, and nothing is requested from an outside host.

export type FontName = 'instrument-sans' | 'space-grotesk' | 'jetbrains-mono';

export interface Font {
  name: FontName;
  label: string;
  family: string;
  /** Same-origin stylesheet holding this font's @font-face rules (lazy-loaded). */
  stylesheetUrl?: string;
}

export const fonts: Record<FontName, Font> = {
  'instrument-sans': {
    name: 'instrument-sans',
    label: 'Instrument Sans',
    family: "'Instrument Sans', 'Helvetica Neue', Arial, sans-serif",
    stylesheetUrl: '/fonts/instrument-sans/instrument-sans.css',
  },
  'space-grotesk': {
    name: 'space-grotesk',
    label: 'Space Grotesk',
    family: "'Space Grotesk', 'Instrument Sans', 'Helvetica Neue', Arial, sans-serif",
    stylesheetUrl: '/fonts/space-grotesk/space-grotesk.css',
  },
  'jetbrains-mono': {
    name: 'jetbrains-mono',
    label: 'JetBrains Mono',
    family: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace",
    stylesheetUrl: '/fonts/jetbrains-mono/jetbrains-mono.css',
  },
};

/** All available font names as a typed array. */
export const fontNames = Object.keys(fonts) as FontName[];

/** Monospace font stack for code blocks (always used regardless of UI font setting). */
export const monoFont = "'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace";

// Track which fonts have been loaded
const loadedFonts = new Set<string>();

// Lazy-load a bundled font's stylesheet (same origin).
function loadFontStylesheet(url: string): void {
  if (loadedFonts.has(url)) return;
  
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = url;
  document.head.appendChild(link);
  loadedFonts.add(url);
}

/** Apply a font to the document root by setting CSS custom properties and lazy-loading its bundled stylesheet. */
export function applyFont(fontName: FontName): void {
  const font = fonts[fontName];
  if (!font) return;
  
  // Lazy-load the font's bundled stylesheet
  if (font.stylesheetUrl) {
    loadFontStylesheet(font.stylesheetUrl);
  }
  
  const root = document.documentElement;
  
  // Set the UI font (--font-sans variable)
  root.style.setProperty('--font-sans', font.family);
  
  // Keep monospace font for code blocks unchanged
  root.style.setProperty('--font-mono', monoFont);
}

// Initialize fonts used in the default shell before settings hydrate
loadFontStylesheet(fonts['instrument-sans'].stylesheetUrl!);
loadFontStylesheet(fonts['jetbrains-mono'].stylesheetUrl!);
