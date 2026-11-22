"""Embeddings must never be cacheable through LiteLLM — in ANY config source.

Bite mark (2026-09-01): with LiteLLM's redis cache covering /v1/embeddings, a
batch mixing cache hits and misses came back MISALIGNED — miss rows received a
neighboring cached input's vector. Graphiti embeds each episode's entity names
as one batch, so any batch containing an already-seen name silently corrupted
graph `name_embedding`s (16 identical-vector clusters across different names
before the repair). The fix is `cache_params.supported_call_types` listing
only chat-completion call types.

The proxy config exists in TWO tracked places (the k3s configmap source and
the single-node file compose.yaml bind-mounts), so this walks both files
rather than trusting anyone to remember.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]

CONFIG_SOURCES = [
    REPO / "deploy" / "shared" / "litellm" / "config.yaml",
    REPO / "deploy" / "single" / "litellm-config.yaml",
]

FORBIDDEN = {"embedding", "aembedding"}


def _litellm_settings(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")).get("litellm_settings") or {}


def test_no_config_source_caches_embeddings():
    for path in CONFIG_SOURCES:
        settings = _litellm_settings(path)
        if not settings.get("cache"):
            continue
        call_types = (settings.get("cache_params") or {}).get(
            "supported_call_types"
        )
        assert call_types is not None, (
            f"{path}: cache is on with no supported_call_types — LiteLLM then "
            "caches EVERY call type, embeddings included (the misaligned-batch "
            "corruption). List the cached call types explicitly."
        )
        leaked = FORBIDDEN.intersection(call_types)
        assert not leaked, (
            f"{path}: {sorted(leaked)} in supported_call_types — cached "
            "embeddings misalign mixed hit/miss batches and corrupt graph "
            "name_embeddings. Remove them."
        )


def test_the_response_cache_is_in_the_proxys_memory():
    """DL-225: both profiles cache in the proxy's own memory (`type: local`),
    and no Redis is wired in — no host or port parameter in the cache block,
    no Redis service in either deployment. Moving back to a shared cache (more
    than one proxy worker or replica) is a decision, made by changing this."""
    for path in CONFIG_SOURCES:
        params = _litellm_settings(path).get("cache_params") or {}
        assert params.get("type") == "local", f"{path}: cache_params.type is {params.get('type')!r}"
        assert not {"host", "port", "password"} & set(params), f"{path}: {sorted(params)}"
    compose = yaml.safe_load((REPO / "deploy" / "single" / "compose.yaml").read_text(encoding="utf-8"))
    assert not [s for s in compose["services"] if "redis" in s], compose["services"].keys()
    assert not [v for v in (compose.get("volumes") or {}) if "redis" in v]
    docs = list(yaml.safe_load_all((REPO / "deploy" / "k3s" / "30-litellm.yaml").read_text(encoding="utf-8")))
    names = [d["metadata"]["name"] for d in docs if d]
    assert not [n for n in names if "redis" in n], names
