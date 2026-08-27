/**
 * Self-signed TLS certificate generator for HTTPS support.
 */

import { execSync } from 'node:child_process';
import { mkdirSync, existsSync, chmodSync } from 'node:fs';
import { resolve } from 'node:path';

export interface CertResult {
  ok: boolean;
  message: string;
}

/**
 * Generate a self-signed certificate for localhost HTTPS.
 * Creates certs/cert.pem and certs/key.pem in the project root.
 */
export function generateSelfSignedCert(projectRoot: string): CertResult {
  const certsDir = resolve(projectRoot, 'certs');
  const certPath = resolve(certsDir, 'cert.pem');
  const keyPath = resolve(certsDir, 'key.pem');

  if (existsSync(certPath) && existsSync(keyPath)) {
    return { ok: true, message: 'Certificates already exist at certs/' };
  }

  try {
    mkdirSync(certsDir, { recursive: true });
    execSync(
      `openssl req -x509 -newkey rsa:2048 -keyout "${keyPath}" -out "${certPath}" ` +
        `-days 365 -nodes -subj '/CN=localhost'`,
      { stdio: 'pipe', timeout: 15_000 },
    );
    try { chmodSync(keyPath, 0o600); } catch { /* non-fatal */ }
    return { ok: true, message: 'Self-signed certificate generated at certs/' };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { ok: false, message: `Certificate generation failed: ${msg}` };
  }
}
