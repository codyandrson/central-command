"""Guards the Graphiti image's entity-type carry and the absence of the client pin.

The failure this patch ends was SILENT twice over: MCP 1.1.0 discards nothing
loudly — its built-in entity-type models carry a required `description`
attribute that grows without bound on a hub entity (v2.38.4), and the first
fix, field-less models built from `config.yaml`'s one-line descriptions,
silently dropped the built-ins' docstrings (the per-type extraction guidance)
and short episodes started extracting nothing (v2.39.0). Nothing in the suite
runs the image, so what can be pinned is the wiring: the patch keeps the
built-in docstring and drops only the fields, the Dockerfile applies it and
fails loud on drift, BOTH deploy profiles set the switch, and the retired
Responses-client pin (`cc-openai-client-switch.patch`, `GRAPHITI_OPENAI_CLIENT`)
does not come back — MCP 1.1.0's stock chat-completions client is the client.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPHITI = ROOT / "deploy" / "pi" / "graphiti"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_entity_type_patch_keeps_the_builtin_docstring_and_drops_the_fields():
    patch = _read(GRAPHITI / "patches" / "cc-entity-type-source.patch")
    assert "GRAPHITI_ENTITY_TYPE_FIELDS" in patch
    assert "+            result[cfg.name] = _doc_only_model(cfg.name, registered.__doc__ or cfg.description)" in patch
    # A name upstream has no model for still gets the configured description.
    assert "+            result[cfg.name] = _doc_only_model(cfg.name, cfg.description)" in patch
    # The old shape — configured one-liners replacing the built-in guidance — is gone.
    assert "config_wins" not in patch


def test_the_responses_client_pin_is_retired():
    assert not (GRAPHITI / "patches" / "cc-openai-client-switch.patch").exists()
    dockerfile = _read(GRAPHITI / "Dockerfile")
    assert "cc-openai-client-switch.patch" not in dockerfile.split("RUN apt-get update")[1]
    for profile in (ROOT / "deploy" / "k3s" / "40-graph.yaml", ROOT / "deploy" / "single" / "compose.yaml"):
        assert "GRAPHITI_OPENAI_CLIENT" not in _read(profile), profile


def test_the_dockerfile_applies_the_patch_and_fails_loud_on_drift():
    dockerfile = _read(GRAPHITI / "Dockerfile")
    assert "patch -p1 -F 5 < /tmp/graphiti-patches/cc-entity-type-source.patch" in dockerfile
    assert "grep -q 'GRAPHITI_ENTITY_TYPE_FIELDS' src/utils/type_config.py" in dockerfile


def test_both_deploy_profiles_set_the_entity_type_switch():
    k3s = _read(ROOT / "deploy" / "k3s" / "40-graph.yaml")
    assert "- name: GRAPHITI_ENTITY_TYPE_FIELDS\n              value: none" in k3s
    single = _read(ROOT / "deploy" / "single" / "compose.yaml")
    assert "GRAPHITI_ENTITY_TYPE_FIELDS: none" in single
