"""Client for the n8n email tool façade (cc-email-facade → lib-email-provider).

Gmail credentials live only inside n8n — Central Command never sees them. This
boundary is READ-ONLY by construction: the façade refuses attachment downloads,
and the provider's list mode returns references only. Untrusted email content
passes through here as data; the callers (ledger/agent prompts) own the
data-not-commands discipline.
"""

from __future__ import annotations

import httpx

from central_command.config import settings


class EmailFacadeError(Exception):
    pass


async def _call(payload: dict, timeout: float = 60.0) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            settings.email_facade_url,
            headers={"x-cc-token": settings.email_facade_token},
            json=payload,
        )
    if resp.status_code != 200:
        raise EmailFacadeError(
            f"email façade {payload.get('mode')} failed: HTTP {resp.status_code} "
            f"{resp.text[:200]}"
        )
    return resp.json()


async def list_refs(scope_query: str) -> list[dict]:
    """References only ({uuid, conversation_id}) matching a Gmail search."""
    out = await _call({"mode": "list", "scope_query": scope_query})
    return out.get("messages", [])


async def get_message(uuid: str) -> dict:
    """One normalized message (from/subject/date/body_text/body_html/snippet)."""
    out = await _call({"mode": "message", "source_ref": {"uuid": uuid}})
    messages = out.get("messages", [])
    if not messages:
        raise EmailFacadeError(f"message {uuid}: empty envelope from provider")
    return messages[0]
