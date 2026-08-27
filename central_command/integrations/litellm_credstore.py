"""LiteLLM's OWN credential store — the enrollment surface for autodiscovery
(2026-08-20 redesign). The operator's only enrollment step is adding a
credential in the LiteLLM UI; autodiscovery reads it straight out of LiteLLM's
Postgres (CC_LITELLM_DB_URL, 5443 — see deploy/k3s/30-litellm.yaml) and
decrypts it with LiteLLM's own LITELLM_SALT_KEY (CC_LITELLM_SALT_KEY). No
Central Command-side provider enrollment exists any more.

VERSION-COUPLED to LiteLLM 1.94.1's `litellm/proxy/common_utils/encrypt_decrypt_utils.py`:
a stored string is either legacy nacl SecretBox (key = sha256(salt_key),
urlsafe-b64 encoded) or, prefixed `v2:gcm:`, AES-GCM via `cryptography`
(same key derivation, 12-byte nonce). Re-verify this module against that file
on any proxy upgrade.

This module holds decrypted secrets TRANSIENTLY, in-process — it must never
log, print, or return them anywhere except the `values` dict handed back to
its caller.
"""

from __future__ import annotations

import base64
import hashlib

import asyncpg

from central_command.config import settings


class CredStoreError(Exception):
    pass


def litellm_decrypt(stored_value: str, salt_key: str) -> str:
    """Decrypt one string field as LiteLLM 1.94.1 would. A single field that
    fails to decrypt is treated as stored PLAINTEXT (LiteLLM does this for
    some fields) and returned as-is; the caller decides what "every field
    failed" means (wrong salt key)."""
    if not stored_value:
        return stored_value
    key = hashlib.sha256(salt_key.encode()).digest()
    if stored_value.startswith("v2:gcm:"):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        raw = base64.urlsafe_b64decode(stored_value[len("v2:gcm:"):])
        nonce, blob = raw[:12], raw[12:]
        return AESGCM(key).decrypt(nonce, blob, None).decode()

    import nacl.secret

    try:
        decoded = base64.urlsafe_b64decode(stored_value)
    except Exception:
        decoded = base64.b64decode(stored_value)
    if len(decoded) == 0:
        return ""
    return nacl.secret.SecretBox(key).decrypt(decoded).decode()


async def list_provider_credentials() -> list[dict]:
    """Every credential LiteLLM has stored, decrypted, as
    `[{credential_name, provider, values: {field: decrypted}}]`.
    `provider` is `credential_info.custom_llm_provider` (may be None — the
    operator did not declare one). Fails loud if litellm_db_url or
    litellm_salt_key is unset, or if EVERY string field of a credential fails
    to decrypt (wrong salt key) — a single failed field is treated as
    plaintext, per LiteLLM's own behavior."""
    if not settings.litellm_db_url:
        raise CredStoreError("list_provider_credentials — CC_LITELLM_DB_URL not set")
    if not settings.litellm_salt_key:
        raise CredStoreError("list_provider_credentials — CC_LITELLM_SALT_KEY not set")

    conn = await asyncpg.connect(settings.litellm_db_url)
    try:
        rows = await conn.fetch(
            'SELECT credential_name, credential_values, credential_info '
            'FROM "LiteLLM_CredentialsTable"'
        )
    finally:
        await conn.close()

    import json

    out: list[dict] = []
    for row in rows:
        raw_values = row["credential_values"]
        values = json.loads(raw_values) if isinstance(raw_values, str) else (raw_values or {})
        raw_info = row["credential_info"]
        info = json.loads(raw_info) if isinstance(raw_info, str) else (raw_info or {})

        decrypted: dict = {}
        fail_count = 0
        str_field_count = 0
        for field, value in values.items():
            if not isinstance(value, str):
                decrypted[field] = value
                continue
            str_field_count += 1
            try:
                decrypted[field] = litellm_decrypt(value, settings.litellm_salt_key)
            except Exception:
                fail_count += 1
                decrypted[field] = value  # treat as plaintext
        if str_field_count and fail_count == str_field_count:
            raise CredStoreError(
                f"list_provider_credentials — every field of credential "
                f"{row['credential_name']!r} failed to decrypt (wrong salt key?)"
            )

        # NORMALIZED to lowercase: the UI lets the operator type the provider
        # free-form ("OpenAI"), while every consumer — the fetcher registry,
        # the "provider/" model-string prefix — is lowercase. A case mismatch
        # here made a whole credential undiscoverable (2026-08-21).
        provider = (info.get("custom_llm_provider") or "").strip().lower() or None
        out.append({
            "credential_name": row["credential_name"],
            "provider": provider,
            "values": decrypted,
        })
    return out
