/* @vitest-environment node */
import { afterEach, describe, expect, it, vi } from 'vitest';

async function importRoute() {
  vi.resetModules();
  return import('./upload-config.js');
}

const ENV_KEYS = [
  'NERVE_UPLOAD_TWO_MODE_ENABLED',
  'NERVE_UPLOAD_INLINE_ENABLED',
  'NERVE_UPLOAD_FILE_REFERENCE_ENABLED',
  'NERVE_UPLOAD_MODE_CHOOSER_ENABLED',
  'NERVE_UPLOAD_INLINE_ATTACHMENT_MAX_MB',
  'NERVE_UPLOAD_INLINE_IMAGE_CONTEXT_MAX_BYTES',
  'NERVE_UPLOAD_INLINE_IMAGE_AUTO_DOWNGRADE_TO_FILE_REFERENCE',
  'NERVE_UPLOAD_INLINE_IMAGE_SHRINK_MIN_DIMENSION',
  'NERVE_UPLOAD_IMAGE_OPTIMIZATION_MAX_DIMENSION',
  'NERVE_UPLOAD_IMAGE_OPTIMIZATION_WEBP_QUALITY',
  'NERVE_UPLOAD_EXPOSE_INLINE_BASE64_TO_AGENT',
] as const;

const originalEnv = Object.fromEntries(ENV_KEYS.map((key) => [key, process.env[key]]));

afterEach(() => {
  for (const key of ENV_KEYS) {
    const value = originalEnv[key];
    if (value == null) {
      delete process.env[key];
    } else {
      process.env[key] = value;
    }
  }
});

describe('GET /api/upload-config', () => {
  it('returns upload capability defaults when env overrides are absent', async () => {
    for (const key of ENV_KEYS) delete process.env[key];

    const { default: app } = await importRoute();
    const res = await app.request('/api/upload-config');

    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toEqual({
      twoModeEnabled: false,
      inlineEnabled: true,
      fileReferenceEnabled: false,
      modeChooserEnabled: false,
      inlineAttachmentMaxMb: 4,
      inlineImageContextMaxBytes: 32768,
      inlineImageAutoDowngradeToFileReference: true,
      inlineImageShrinkMinDimension: 512,
      inlineImageMaxDimension: 2048,
      inlineImageWebpQuality: 82,
      exposeInlineBase64ToAgent: false,
    });
  });

  it('returns env-backed capability flags for the existing client contract', async () => {
    process.env.NERVE_UPLOAD_TWO_MODE_ENABLED = 'true';
    process.env.NERVE_UPLOAD_INLINE_ENABLED = 'true';
    process.env.NERVE_UPLOAD_FILE_REFERENCE_ENABLED = 'true';
    process.env.NERVE_UPLOAD_MODE_CHOOSER_ENABLED = 'true';
    process.env.NERVE_UPLOAD_INLINE_ATTACHMENT_MAX_MB = '8';
    process.env.NERVE_UPLOAD_INLINE_IMAGE_CONTEXT_MAX_BYTES = '65536';
    process.env.NERVE_UPLOAD_INLINE_IMAGE_AUTO_DOWNGRADE_TO_FILE_REFERENCE = 'false';
    process.env.NERVE_UPLOAD_INLINE_IMAGE_SHRINK_MIN_DIMENSION = '640';
    process.env.NERVE_UPLOAD_IMAGE_OPTIMIZATION_MAX_DIMENSION = '1536';
    process.env.NERVE_UPLOAD_IMAGE_OPTIMIZATION_WEBP_QUALITY = '76';
    process.env.NERVE_UPLOAD_EXPOSE_INLINE_BASE64_TO_AGENT = 'true';

    const { default: app } = await importRoute();
    const res = await app.request('/api/upload-config');

    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toEqual({
      twoModeEnabled: true,
      inlineEnabled: true,
      fileReferenceEnabled: true,
      modeChooserEnabled: true,
      inlineAttachmentMaxMb: 8,
      inlineImageContextMaxBytes: 65536,
      inlineImageAutoDowngradeToFileReference: false,
      inlineImageShrinkMinDimension: 640,
      inlineImageMaxDimension: 1536,
      inlineImageWebpQuality: 76,
      exposeInlineBase64ToAgent: true,
    });
  });
});
