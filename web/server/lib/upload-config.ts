export interface UploadFeatureConfig {
  twoModeEnabled: boolean;
  inlineEnabled: boolean;
  fileReferenceEnabled: boolean;
  modeChooserEnabled: boolean;
  inlineAttachmentMaxMb: number;
  inlineImageContextMaxBytes: number;
  inlineImageAutoDowngradeToFileReference: boolean;
  inlineImageShrinkMinDimension: number;
  inlineImageMaxDimension: number;
  inlineImageWebpQuality: number;
  exposeInlineBase64ToAgent: boolean;
}

const DEFAULT_UPLOAD_FEATURE_CONFIG: UploadFeatureConfig = {
  twoModeEnabled: false,
  inlineEnabled: true,
  fileReferenceEnabled: false,
  modeChooserEnabled: false,
  inlineAttachmentMaxMb: 50,
  inlineImageContextMaxBytes: 32_768,
  inlineImageAutoDowngradeToFileReference: true,
  inlineImageShrinkMinDimension: 512,
  inlineImageMaxDimension: 2048,
  inlineImageWebpQuality: 82,
  exposeInlineBase64ToAgent: false,
};

function readBooleanEnv(name: string, fallback: boolean): boolean {
  const value = process.env[name];
  if (value == null) return fallback;

  const normalized = value.trim().toLowerCase();
  if (['1', 'true', 'yes', 'on'].includes(normalized)) return true;
  if (['0', 'false', 'no', 'off'].includes(normalized)) return false;

  return fallback;
}

function readNumberEnv(name: string, fallback: number): number {
  const value = process.env[name];
  if (value == null) return fallback;

  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function getUploadFeatureConfig(): UploadFeatureConfig {
  return {
    twoModeEnabled: readBooleanEnv('NERVE_UPLOAD_TWO_MODE_ENABLED', DEFAULT_UPLOAD_FEATURE_CONFIG.twoModeEnabled),
    inlineEnabled: readBooleanEnv('NERVE_UPLOAD_INLINE_ENABLED', DEFAULT_UPLOAD_FEATURE_CONFIG.inlineEnabled),
    fileReferenceEnabled: readBooleanEnv('NERVE_UPLOAD_FILE_REFERENCE_ENABLED', DEFAULT_UPLOAD_FEATURE_CONFIG.fileReferenceEnabled),
    modeChooserEnabled: readBooleanEnv('NERVE_UPLOAD_MODE_CHOOSER_ENABLED', DEFAULT_UPLOAD_FEATURE_CONFIG.modeChooserEnabled),
    inlineAttachmentMaxMb: readNumberEnv('NERVE_UPLOAD_INLINE_ATTACHMENT_MAX_MB', DEFAULT_UPLOAD_FEATURE_CONFIG.inlineAttachmentMaxMb),
    inlineImageContextMaxBytes: readNumberEnv('NERVE_UPLOAD_INLINE_IMAGE_CONTEXT_MAX_BYTES', DEFAULT_UPLOAD_FEATURE_CONFIG.inlineImageContextMaxBytes),
    inlineImageAutoDowngradeToFileReference: readBooleanEnv(
      'NERVE_UPLOAD_INLINE_IMAGE_AUTO_DOWNGRADE_TO_FILE_REFERENCE',
      DEFAULT_UPLOAD_FEATURE_CONFIG.inlineImageAutoDowngradeToFileReference,
    ),
    inlineImageShrinkMinDimension: readNumberEnv(
      'NERVE_UPLOAD_INLINE_IMAGE_SHRINK_MIN_DIMENSION',
      DEFAULT_UPLOAD_FEATURE_CONFIG.inlineImageShrinkMinDimension,
    ),
    inlineImageMaxDimension: readNumberEnv(
      'NERVE_UPLOAD_IMAGE_OPTIMIZATION_MAX_DIMENSION',
      DEFAULT_UPLOAD_FEATURE_CONFIG.inlineImageMaxDimension,
    ),
    inlineImageWebpQuality: readNumberEnv(
      'NERVE_UPLOAD_IMAGE_OPTIMIZATION_WEBP_QUALITY',
      DEFAULT_UPLOAD_FEATURE_CONFIG.inlineImageWebpQuality,
    ),
    exposeInlineBase64ToAgent: readBooleanEnv(
      'NERVE_UPLOAD_EXPOSE_INLINE_BASE64_TO_AGENT',
      DEFAULT_UPLOAD_FEATURE_CONFIG.exposeInlineBase64ToAgent,
    ),
  };
}
