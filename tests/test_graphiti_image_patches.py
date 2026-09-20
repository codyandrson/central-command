"""Guards the Graphiti image's entity-type and output-cap carries.

Both failures these patches end were SILENT: MCP 1.1.0 discards the entity-type
descriptions in `deploy/pi/graphiti/config.yaml` without a word, and the
Responses client ignores `llm.max_tokens` the same way. Nothing in the suite
runs the image, so what can be pinned is the wiring: the patch files exist and
say what they must, the Dockerfile applies them and fails loud on drift, and
BOTH deploy profiles set the switch — a profile that forgets it builds a
patched image and then runs upstream behaviour.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPHITI = ROOT / "deploy" / "pi" / "graphiti"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_entity_type_patch_makes_configured_types_win():
    patch = _read(GRAPHITI / "patches" / "cc-entity-type-source.patch")
    assert "GRAPHITI_ENTITY_TYPE_SOURCE" in patch
    assert "+        registered = None if config_wins else ENTITY_TYPES.get(cfg.name)" in patch


def test_the_client_switch_patch_passes_the_configured_output_cap():
    patch = _read(GRAPHITI / "patches" / "cc-openai-client-switch.patch")
    assert "+                    return OpenAIClient(config=llm_config, max_tokens=config.max_tokens)" in patch
    # The reasoning-model branch builds the same client and needs the same cap.
    assert "+                            max_tokens=config.max_tokens," in patch


def test_the_dockerfile_applies_both_and_fails_loud_on_drift():
    dockerfile = _read(GRAPHITI / "Dockerfile")
    assert "patch -p1 -F 5 < /tmp/graphiti-patches/cc-entity-type-source.patch" in dockerfile
    assert "grep -q 'GRAPHITI_ENTITY_TYPE_SOURCE' src/utils/type_config.py" in dockerfile
    assert "grep -q 'OpenAIClient(config=llm_config, max_tokens=config.max_tokens)'" in dockerfile


def test_both_deploy_profiles_set_the_entity_type_switch():
    k3s = _read(ROOT / "deploy" / "k3s" / "40-graph.yaml")
    assert "- name: GRAPHITI_ENTITY_TYPE_SOURCE\n              value: config" in k3s
    single = _read(ROOT / "deploy" / "single" / "compose.yaml")
    assert "GRAPHITI_ENTITY_TYPE_SOURCE: config" in single
