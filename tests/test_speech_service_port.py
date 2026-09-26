"""The speech aliases dial the cc-speech SERVICE port, and every place that
tells the operator what to enter agrees with the manifest.

Found 2026-09-26 on the clean re-deploy: setup.sh's catalog pause, the
declaration's comment and the manifest's own header all said
`http://cc-speech:8000/v1` — the CONTAINER port — while the Service maps 8093
to it. LiteLLM cannot reach `cc-speech:8000`, the TTS probe timed out for
300 s twice, and the gate read like a model-download race. The manifest is
the one truth; the prose is checked against it here."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "deploy" / "k3s" / "90-speech.yaml"
PROSE = (
    ROOT / "deploy" / "k3s" / "setup.sh",
    ROOT / "deploy" / "pi" / "litellm" / "model-preferences.yaml",
    MANIFEST,
)


def _service_port() -> int:
    for doc in yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8")):
        if doc and doc.get("kind") == "Service" and doc["metadata"]["name"] == "cc-speech":
            ports = doc["spec"]["ports"]
            assert len(ports) == 1
            return int(ports[0]["port"])
    raise AssertionError("no cc-speech Service in 90-speech.yaml")


def test_every_cc_speech_url_in_prose_uses_the_service_port():
    port = _service_port()
    assert port != 8000, "the Service port is meant to differ from the container's 8000"
    for path in PROSE:
        mentions = re.findall(r"cc-speech:(\d+)", path.read_text(encoding="utf-8"))
        assert mentions, f"{path.name} never names cc-speech:<port>"
        wrong = sorted({m for m in mentions if int(m) != port})
        assert not wrong, f"{path.relative_to(ROOT)} says cc-speech:{wrong} — the Service port is {port}"
