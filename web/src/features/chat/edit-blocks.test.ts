/** Tests for edit-blocks — parsing edit and write tool blocks. */
import { describe, it, expect } from 'vitest';
import { extractEditBlocks, extractWriteBlocks } from './edit-blocks';

describe('extractEditBlocks', () => {
  it('extracts a single edit block', () => {
    const text = '**tool:** `Edit`\n```json\n{"file_path": "/src/app.ts", "old_string": "foo", "new_string": "bar"}\n```';
    const blocks = extractEditBlocks(text);
    expect(blocks).toHaveLength(1);
    expect(blocks[0].filePath).toBe('/src/app.ts');
    expect(blocks[0].oldText).toBe('foo');
    expect(blocks[0].newText).toBe('bar');
  });

  it('extracts multiple edit blocks', () => {
    const text = [
      '**tool:** `Edit`\n```json\n{"file_path": "a.ts", "old_string": "a", "new_string": "b"}\n```',
      'Some text between',
      '**tool:** `edit`\n```json\n{"path": "c.ts", "oldText": "c", "newText": "d"}\n```',
    ].join('\n');
    const blocks = extractEditBlocks(text);
    expect(blocks).toHaveLength(2);
  });

  it('handles both field naming conventions', () => {
    const text1 = '**tool:** `Edit`\n```json\n{"file_path": "f", "old_string": "a", "new_string": "b"}\n```';
    const text2 = '**tool:** `Edit`\n```json\n{"path": "f", "oldText": "a", "newText": "b"}\n```';

    expect(extractEditBlocks(text1)[0].oldText).toBe('a');
    expect(extractEditBlocks(text2)[0].oldText).toBe('a');
  });

  it('extracts nested edits arrays from real edit tool payloads', () => {
    const text = '**tool:** `edit`\n```json\n{"path":"2026-04-20.md","edits":[{"oldText":"foo","newText":"bar"},{"oldText":"baz","newText":"qux"}]}\n```';

    const blocks = extractEditBlocks(text);
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toEqual({ filePath: '2026-04-20.md', oldText: 'foo', newText: 'bar' });
    expect(blocks[1]).toEqual({ filePath: '2026-04-20.md', oldText: 'baz', newText: 'qux' });
  });

  it('uses per-edit path when present inside nested edits', () => {
    const text = '**tool:** `edit`\n```json\n{"path":"fallback.md","edits":[{"path":"nested.md","oldText":"a","newText":"b"}]}\n```';

    const blocks = extractEditBlocks(text);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toEqual({ filePath: 'nested.md', oldText: 'a', newText: 'b' });
  });

  it('skips malformed JSON', () => {
    const text = '**tool:** `Edit`\n```json\n{not valid json}\n```';
    expect(extractEditBlocks(text)).toHaveLength(0);
  });

  it('skips blocks with no old or new text', () => {
    const text = '**tool:** `Edit`\n```json\n{"file_path": "f"}\n```';
    expect(extractEditBlocks(text)).toHaveLength(0);
  });

  it('returns empty array for text without edit blocks', () => {
    expect(extractEditBlocks('Just regular text')).toHaveLength(0);
    expect(extractEditBlocks('')).toHaveLength(0);
  });

  it('handles edit blocks case-insensitively (Edit vs edit)', () => {
    const text1 = '**tool:** `Edit`\n```json\n{"old_string": "a", "new_string": "b"}\n```';
    const text2 = '**tool:** `edit`\n```json\n{"old_string": "a", "new_string": "b"}\n```';
    expect(extractEditBlocks(text1)).toHaveLength(1);
    expect(extractEditBlocks(text2)).toHaveLength(1);
  });
});

describe('extractWriteBlocks', () => {
  it('extracts a single write block', () => {
    const text = '**tool:** `Write`\n```json\n{"file_path": "/src/new.ts", "content": "const x = 1;"}\n```';
    const blocks = extractWriteBlocks(text);
    expect(blocks).toHaveLength(1);
    expect(blocks[0].filePath).toBe('/src/new.ts');
    expect(blocks[0].content).toBe('const x = 1;');
  });

  it('extracts multiple write blocks', () => {
    const text = [
      '**tool:** `Write`\n```json\n{"path": "a.ts", "content": "a"}\n```',
      '**tool:** `write`\n```json\n{"file_path": "b.ts", "content": "b"}\n```',
    ].join('\n');
    expect(extractWriteBlocks(text)).toHaveLength(2);
  });

  it('skips blocks with no content', () => {
    const text = '**tool:** `Write`\n```json\n{"file_path": "f"}\n```';
    expect(extractWriteBlocks(text)).toHaveLength(0);
  });

  it('skips malformed JSON', () => {
    const text = '**tool:** `Write`\n```json\n{invalid}\n```';
    expect(extractWriteBlocks(text)).toHaveLength(0);
  });

  it('returns empty array for no write blocks', () => {
    expect(extractWriteBlocks('No blocks here')).toHaveLength(0);
    expect(extractWriteBlocks('')).toHaveLength(0);
  });

  it('handles both field naming conventions', () => {
    const text1 = '**tool:** `Write`\n```json\n{"file_path": "f", "content": "x"}\n```';
    const text2 = '**tool:** `Write`\n```json\n{"path": "f", "content": "x"}\n```';
    expect(extractWriteBlocks(text1)[0].filePath).toBe('f');
    expect(extractWriteBlocks(text2)[0].filePath).toBe('f');
  });
});

describe('sandbox_write_file (Central Command agents write files in their sandbox)', () => {
  // The chat's rawText for a tool call, exactly as loadHistory.ts builds it from
  // the gateway's tool_use block: name + pretty-printed arguments. The arguments
  // are the tool's own signature, sandbox_write_file(path: str, content: str)
  // in central_command/runtime/tools.py.
  const toolCall = (name: string, args: Record<string, unknown>) =>
    `**tool:** \`${name}\`\n\`\`\`json\n${JSON.stringify(args, null, 2)}\n\`\`\``;

  it('shows the path and the whole new content as an added-file block', () => {
    const text = toolCall('sandbox_write_file', {
      path: 'report/summary.md',
      content: '# Summary\n\nThree invoices are overdue.\n',
    });
    expect(extractWriteBlocks(text)).toEqual([
      { filePath: 'report/summary.md', content: '# Summary\n\nThree invoices are overdue.\n' },
    ]);
    // Only the new content exists, so no edit (before/after) block is made up.
    expect(extractEditBlocks(text)).toEqual([]);
  });

  it('keeps an absolute workspace path as given', () => {
    const text = toolCall('sandbox_write_file', { path: '/workspace/src/app.py', content: 'print("hi")\n' });
    expect(extractWriteBlocks(text)[0].filePath).toBe('/workspace/src/app.py');
  });

  it('is not cut short by a code fence inside the file content', () => {
    const content = 'Usage:\n\n```bash\npython app.py\n```\n';
    const text = toolCall('sandbox_write_file', { path: 'README.md', content });
    expect(extractWriteBlocks(text)).toEqual([{ filePath: 'README.md', content }]);
  });

  it('does not treat the sandbox read or exec tools as writes', () => {
    const text = [
      toolCall('sandbox_read_file', { path: 'README.md' }),
      toolCall('sandbox_exec', { command: 'echo hi > a.txt' }),
    ].join('\n');
    expect(extractWriteBlocks(text)).toEqual([]);
  });

  it('skips an empty write', () => {
    expect(extractWriteBlocks(toolCall('sandbox_write_file', { path: 'empty.txt', content: '' }))).toEqual([]);
  });
});

