"""The single-node profile's dependency seams stay consistent across five files.

What 2026-08-30's air-gap redesign made true, and what would silently rot
without a walk:

* every image the profile pulls is in ``deploy/single/images.txt`` with a
  digest — the templates and the Dockerfiles reference it by ``name:tag``,
  and ``setup.sh fetch`` is what turns that into a pull-by-digest. A ref in a
  template that is not in the manifest is a floating pull at ``kube play``
  time, exactly the drift the manifest exists to end;
* every ``CC_*`` seam the scripts read is declared (commented or not) in
  ``env.example`` — the operator's only map of what can be set;
* ``deploy/airgap.env.example`` does not re-teach the deprecated
  ``UV_INDEX_URL`` as a live variable (uv reads ``UV_DEFAULT_INDEX``; the
  old name is still documented there ONLY as the thing not to use).

No podman here; this is a source walk plus ``bash -n``.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SINGLE = ROOT / "deploy" / "single"
IMAGES_TXT = SINGLE / "images.txt"
ENV_EXAMPLE = SINGLE / "env.example"
DOCKERFILES = [
    ROOT / "deploy" / "pi" / "graphiti" / "Dockerfile",
    ROOT / "deploy" / "k3s" / "sandbox.Dockerfile",
    ROOT / "central_command" / "crawler" / "Dockerfile",
]
SCRIPTS = [
    SINGLE / "setup.sh",
    SINGLE / "update.sh",
    SINGLE / "bundle.sh",
    SINGLE / "build-graphiti-image.sh",
    SINGLE / "build-sandbox-image.sh",
    SINGLE / "build-crawler-image.sh",
    ROOT / "deploy" / "airgap-image-tarballs.sh",
]
REGISTRY_KEYS = {"dockerio", "ghcr", "mcr"}
COMPONENTS = {"core", "n8n", "graphiti-base", "sandbox-base", "crawler-base"}
REGISTRY_VAR = {"CC_REGISTRY_DOCKERIO": "dockerio", "CC_REGISTRY_GHCR": "ghcr", "CC_REGISTRY_MCR": "mcr"}


def _manifest() -> dict[tuple[str, str], tuple[str, str]]:
    rows = {}
    for line in IMAGES_TXT.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        assert len(parts) == 4, f"images.txt: expected 4 columns: {line!r}"
        key, path, digest, comp = parts
        assert key in REGISTRY_KEYS, f"images.txt: unknown registry key {key!r}"
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest), f"images.txt: bad digest for {path}"
        assert comp in COMPONENTS, f"images.txt: unknown component {comp!r}"
        assert ":" in path and "@" not in path, f"images.txt: path must be name:tag without digest: {path!r}"
        rows[(key, path)] = (digest, comp)
    return rows


def test_images_txt_is_well_formed():
    rows = _manifest()
    assert rows, "images.txt is empty"
    assert len({p for _, p in rows}) == len(rows), "duplicate image path in images.txt"


def _template_refs() -> set[tuple[str, str]]:
    refs = set()
    for tmpl in SINGLE.glob("*.yaml.tmpl"):
        for m in re.finditer(r"image:\s*(\S+)", tmpl.read_text(encoding="utf-8")):
            ref = m.group(1)
            if ref.startswith("localhost/"):
                continue  # the three locally-built images
            m2 = re.fullmatch(r"\$\{(CC_REGISTRY_[A-Z]+)\}/(.+)", ref)
            assert m2, f"{tmpl.name}: image ref {ref!r} must be ${{CC_REGISTRY_*}}/path:tag (fetch pulls it by digest from images.txt)"
            refs.add((REGISTRY_VAR[m2.group(1)], m2.group(2)))
    return refs


def _dockerfile_bases() -> set[tuple[str, str]]:
    bases = set()
    for df in DOCKERFILES:
        text = df.read_text(encoding="utf-8")
        froms = re.findall(r"^FROM\s+(\S+)", text, flags=re.M)
        assert froms, f"{df}: no FROM"
        for ref in froms:
            m = re.fullmatch(r"\$\{(CC_REGISTRY_[A-Z]+)\}/(.+)", ref)
            assert m, f"{df}: FROM {ref!r} must go through a ${{CC_REGISTRY_*}} build-arg"
            assert re.search(rf"^ARG\s+{m.group(1)}=", text, flags=re.M), f"{df}: ARG {m.group(1)} must be declared before FROM"
            bases.add((REGISTRY_VAR[m.group(1)], m.group(2)))
    return bases


def test_every_pulled_image_is_pinned_in_images_txt():
    rows = _manifest()
    for ref in _template_refs() | _dockerfile_bases():
        assert ref in rows, f"{ref[0]}/{ref[1]} is referenced but has no digest line in images.txt"


def test_images_txt_has_no_orphans():
    used = _template_refs() | _dockerfile_bases()
    for ref in _manifest():
        assert ref in used, f"images.txt pins {ref[1]} but nothing references it"


SEAM_RE = re.compile(r"\bCC_(?:SOURCE_[A-Z]+|REGISTRY_[A-Z]+|APT_(?:SECURITY_)?MIRROR|PYPI_INDEX_URL|PYTHON_MIRROR|NPM_REGISTRY|BUNDLE_DIR)\b")


def test_every_seam_the_scripts_read_is_declared_in_env_example():
    declared = set(re.findall(r"^#?\s*(CC_[A-Z_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), flags=re.M))
    for path in SCRIPTS + DOCKERFILES:
        for seam in set(SEAM_RE.findall(path.read_text(encoding="utf-8"))):
            if seam.endswith("_") or "${" in seam:
                continue
            assert seam in declared, f"{path.relative_to(ROOT)} reads {seam}, which env.example does not declare"


def test_fetch_phase_runs_before_anything_deploys():
    text = (SINGLE / "setup.sh").read_text(encoding="utf-8")
    m = re.search(r"for p in (validate preflight fetch llm stack app verify test boot demo); do", text)
    assert m, "setup.sh's full run must go validate -> preflight -> fetch -> llm -> ..."
    assert "build_if_missing" not in text, "stack must ASSERT images (need_image), never build them mid-deploy"


def test_airgap_env_example_teaches_the_live_uv_variable():
    text = (ROOT / "deploy" / "airgap.env.example").read_text(encoding="utf-8")
    assert "UV_DEFAULT_INDEX=" in text
    assert not re.search(r"^#?\s*UV_INDEX_URL=", text, flags=re.M), "UV_INDEX_URL is deprecated — only UV_DEFAULT_INDEX may be offered as a seam"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_parse(script: pathlib.Path):
    subprocess.run(["bash", "-n", str(script)], check=True)
