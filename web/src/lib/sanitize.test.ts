import { describe, it, expect } from 'vitest';
import { sanitizeHtml, isSafeUrl } from './sanitize';

describe('sanitizeHtml', () => {
  describe('XSS Prevention', () => {
    it('should remove script tags', () => {
      const malicious = '<p>Hello</p><script>alert("XSS")</script>';
      const result = sanitizeHtml(malicious);
      expect(result).toBe('<p>Hello</p>');
      expect(result).not.toContain('script');
      expect(result).not.toContain('alert');
    });

    it('should remove nested script tags', () => {
      const malicious = '<div><p><script>alert("nested")</script>Text</p></div>';
      const result = sanitizeHtml(malicious);
      expect(result).toBe('<div><p>Text</p></div>');
      expect(result).not.toContain('script');
    });

    it('should remove onclick handlers', () => {
      const malicious = '<a href="#" onclick="alert(\'XSS\')">Click me</a>';
      const result = sanitizeHtml(malicious);
      expect(result).not.toContain('onclick');
      expect(result).not.toContain('alert');
      // Link should remain but without onclick
      expect(result).toContain('<a href="#">');
    });

    it('should remove onerror handlers', () => {
      const malicious = '<img src="x" onerror="alert(\'XSS\')">';
      const result = sanitizeHtml(malicious);
      expect(result).not.toContain('onerror');
      expect(result).not.toContain('alert');
      // img is not in allowlist, so entire tag should be removed
      expect(result).toBe('');
    });

    it('should remove onload handlers', () => {
      const malicious = '<body onload="alert(\'XSS\')">Content</body>';
      const result = sanitizeHtml(malicious);
      expect(result).not.toContain('onload');
      expect(result).not.toContain('alert');
      // body is not in allowlist
      expect(result).not.toContain('body');
    });

    it('should remove javascript: URLs', () => {
      const malicious = '<a href="javascript:alert(\'XSS\')">Click</a>';
      const result = sanitizeHtml(malicious);
      expect(result).not.toContain('javascript:');
      expect(result).not.toContain('alert');
    });

    it('should remove data: URLs in href', () => {
      const malicious = '<a href="data:text/html,<script>alert(\'XSS\')</script>">Click</a>';
      const result = sanitizeHtml(malicious);
      expect(result).not.toContain('data:');
      expect(result).not.toContain('script');
    });

    it('should remove vbscript: URLs', () => {
      const malicious = '<a href="vbscript:msgbox(\'XSS\')">Click</a>';
      const result = sanitizeHtml(malicious);
      expect(result).not.toContain('vbscript:');
    });

    it('should remove style tags', () => {
      const malicious = '<style>body { background: url("javascript:alert(1)") }</style><p>Text</p>';
      const result = sanitizeHtml(malicious);
      expect(result).toBe('<p>Text</p>');
      expect(result).not.toContain('style');
    });

    it('should remove iframe tags', () => {
      const malicious = '<iframe src="https://evil.com"></iframe><p>Text</p>';
      const result = sanitizeHtml(malicious);
      expect(result).toBe('<p>Text</p>');
      expect(result).not.toContain('iframe');
    });

    it('should remove object and embed tags', () => {
      const malicious = '<object data="evil.swf"></object><embed src="evil.swf"><p>Text</p>';
      const result = sanitizeHtml(malicious);
      expect(result).toBe('<p>Text</p>');
      expect(result).not.toContain('object');
      expect(result).not.toContain('embed');
    });

    it('should remove form and input tags', () => {
      const malicious = '<form action="https://evil.com"><input name="data"></form><p>Text</p>';
      const result = sanitizeHtml(malicious);
      expect(result).toBe('<p>Text</p>');
      expect(result).not.toContain('form');
      expect(result).not.toContain('input');
    });

    it('should remove SVG tags (XSS vector)', () => {
      const malicious = '<svg><script>alert("XSS")</script></svg><p>Text</p>';
      const result = sanitizeHtml(malicious);
      expect(result).toBe('<p>Text</p>');
      expect(result).not.toContain('svg');
      expect(result).not.toContain('script');
    });
  });

  describe('Allowed Elements', () => {
    it('should preserve safe block elements', () => {
      const safe = '<p>Paragraph</p><div>Division</div><br><blockquote>Quote</blockquote>';
      const result = sanitizeHtml(safe);
      expect(result).toContain('<p>Paragraph</p>');
      expect(result).toContain('<div>Division</div>');
      expect(result).toContain('<br>');
      expect(result).toContain('<blockquote>Quote</blockquote>');
    });

    it('should preserve headings', () => {
      const safe = '<h1>H1</h1><h2>H2</h2><h3>H3</h3><h4>H4</h4><h5>H5</h5><h6>H6</h6>';
      const result = sanitizeHtml(safe);
      expect(result).toBe(safe);
    });

    it('should preserve code and pre tags', () => {
      const safe = '<pre><code>const x = 1;</code></pre>';
      const result = sanitizeHtml(safe);
      expect(result).toBe(safe);
    });

    it('should preserve inline formatting', () => {
      const safe = '<strong>Bold</strong> <b>Bold2</b> <em>Italic</em> <i>Italic2</i> <mark>Marked</mark> <span>Span</span>';
      const result = sanitizeHtml(safe);
      expect(result).toBe(safe);
    });

    it('should preserve safe links with allowed attributes', () => {
      const safe = '<a href="https://example.com" target="_blank" rel="noopener">Link</a>';
      const result = sanitizeHtml(safe);
      expect(result).toBe(safe);
    });

    it('should preserve lists', () => {
      const safe = '<ul><li>Item 1</li><li>Item 2</li></ul><ol><li>First</li><li>Second</li></ol>';
      const result = sanitizeHtml(safe);
      expect(result).toBe(safe);
    });

    it('should preserve tables', () => {
      const safe = '<table><thead><tr><th>Header</th></tr></thead><tbody><tr><td>Data</td></tr></tbody></table>';
      const result = sanitizeHtml(safe);
      expect(result).toBe(safe);
    });

    it('should preserve details/summary', () => {
      const safe = '<details open><summary>Summary</summary>Content</details>';
      const result = sanitizeHtml(safe);
      // DOMPurify may normalize boolean attributes to open=""
      expect(result).toMatch(/<details open(="")?>.*<\/details>/);
      expect(result).toContain('<summary>Summary</summary>');
      expect(result).toContain('Content');
    });

    it('should preserve class attributes for styling', () => {
      const safe = '<p class="text-red-500">Styled text</p>';
      const result = sanitizeHtml(safe);
      expect(result).toBe(safe);
    });
  });

  describe('Edge Cases', () => {
    it('should handle deeply nested scripts', () => {
      const malicious = '<div><p><span><strong><script>alert("deep")</script>Text</strong></span></p></div>';
      const result = sanitizeHtml(malicious);
      expect(result).toBe('<div><p><span><strong>Text</strong></span></p></div>');
      expect(result).not.toContain('script');
    });

    it('should handle encoded script tags', () => {
      const malicious = '<p>&lt;script&gt;alert("XSS")&lt;/script&gt;</p>';
      const result = sanitizeHtml(malicious);
      // Encoded entities should remain as text (already escaped)
      expect(result).toContain('&lt;script&gt;');
      expect(result).not.toContain('<script>');
    });

    it('should handle case variations of dangerous tags', () => {
      const malicious = '<p>Text</p><SCRIPT>alert(1)</SCRIPT><ScRiPt>alert(2)</ScRiPt>';
      const result = sanitizeHtml(malicious);
      expect(result).toBe('<p>Text</p>');
      expect(result).not.toContain('SCRIPT');
      expect(result).not.toContain('ScRiPt');
    });

    it('should handle empty strings', () => {
      const result = sanitizeHtml('');
      expect(result).toBe('');
    });

    it('should handle plain text without HTML', () => {
      const plain = 'Just plain text with no tags';
      const result = sanitizeHtml(plain);
      expect(result).toBe(plain);
    });

    it('should handle malformed HTML gracefully', () => {
      const malformed = '<p>Unclosed paragraph<div>Nested</p></div>';
      const result = sanitizeHtml(malformed);
      // DOMPurify should fix and sanitize
      expect(result).not.toContain('script');
      expect(result).toContain('Unclosed paragraph');
      expect(result).toContain('Nested');
    });

    it('should remove event handlers with unusual spacing', () => {
      const malicious = '<a href="#" on click="alert(1)">Link</a>';
      const result = sanitizeHtml(malicious);
      expect(result).not.toContain('on click');
      expect(result).not.toContain('alert');
    });

    it('should handle javascript: with unusual encoding', () => {
      const malicious = '<a href="jav&#x09;ascript:alert(1)">Link</a>';
      const result = sanitizeHtml(malicious);
      expect(result).not.toContain('javascript');
      expect(result).not.toContain('alert');
    });

    it('should handle multiple XSS vectors in one string', () => {
      const malicious = `
        <script>alert(1)</script>
        <img src=x onerror="alert(2)">
        <a href="javascript:alert(3)">Click</a>
        <iframe src="evil.com"></iframe>
        <p onclick="alert(4)">Text</p>
      `;
      const result = sanitizeHtml(malicious);
      expect(result).not.toContain('script');
      expect(result).not.toContain('onerror');
      expect(result).not.toContain('javascript:');
      expect(result).not.toContain('iframe');
      expect(result).not.toContain('onclick');
      expect(result).not.toContain('alert');
    });
  });

  describe('Real-world Markdown Use Cases', () => {
    it('should handle typical markdown-generated HTML', () => {
      const markdown = `
        <h1>Title</h1>
        <p>This is a paragraph with <strong>bold</strong> and <em>italic</em> text.</p>
        <pre><code class="language-javascript">const x = 1;</code></pre>
        <ul>
          <li>Item 1</li>
          <li>Item 2</li>
        </ul>
        <a href="https://example.com" target="_blank" rel="noopener">External link</a>
      `;
      const result = sanitizeHtml(markdown);
      expect(result).toContain('<h1>Title</h1>');
      expect(result).toContain('<strong>bold</strong>');
      expect(result).toContain('<em>italic</em>');
      expect(result).toContain('<code');
      expect(result).toContain('<ul>');
      expect(result).toContain('<a href="https://example.com"');
    });

    it('should allow safe relative URLs', () => {
      const safe = '<a href="/docs/api">API Docs</a>';
      const result = sanitizeHtml(safe);
      expect(result).toContain('href="/docs/api"');
    });

    it('should allow mailto: links', () => {
      const safe = '<a href="mailto:test@example.com">Email</a>';
      const result = sanitizeHtml(safe);
      expect(result).toContain('mailto:test@example.com');
    });
  });
});

describe('isSafeUrl', () => {
  it('should allow http URLs', () => {
    expect(isSafeUrl('http://example.com')).toBe(true);
  });

  it('should allow https URLs', () => {
    expect(isSafeUrl('https://example.com')).toBe(true);
  });

  it('should allow mailto URLs', () => {
    expect(isSafeUrl('mailto:test@example.com')).toBe(true);
  });

  it('should allow relative URLs', () => {
    expect(isSafeUrl('/path/to/page')).toBe(true);
    expect(isSafeUrl('./relative')).toBe(true);
    expect(isSafeUrl('../parent')).toBe(true);
  });

  it('should block javascript: URLs', () => {
    expect(isSafeUrl('javascript:alert(1)')).toBe(false);
    expect(isSafeUrl('JavaScript:alert(1)')).toBe(false);
    expect(isSafeUrl('JAVASCRIPT:alert(1)')).toBe(false);
  });

  it('should block data: URLs', () => {
    expect(isSafeUrl('data:text/html,<script>alert(1)</script>')).toBe(false);
    expect(isSafeUrl('DATA:text/html,test')).toBe(false);
  });

  it('should block vbscript: URLs', () => {
    expect(isSafeUrl('vbscript:msgbox(1)')).toBe(false);
    expect(isSafeUrl('VBScript:msgbox(1)')).toBe(false);
  });
});
