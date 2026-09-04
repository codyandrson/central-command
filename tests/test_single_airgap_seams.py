"""The single-node profile's dependency seams stay consistent across five files.

What 2026-08-30's air-gap redesign made true, and what would silently rot
without a walk:

* every image the profile pulls is in ``deploy/single/images.txt`` with a
  constraint, a locked tag and a locked digest (2026-09-03, design record
  ``2026-09-03-deploy-refactor-design.md`` D2). ``compose.yaml`` references
  each one as ``${CC_IMG_*:-<host>/<path>:<locked-tag>}`` and the Dockerfiles
  as ``${CC_REGISTRY_*}/<path>:<tag>``; ``resolve-images.sh`` is what turns
  the manifest into the refs the registry can serve. A ref that is not in the
  manifest is a floating pull at deploy time, exactly the drift the manifest
  exists to end — and a compose default whose tag is not the LOCKED tag would
  make a bare ``compose up`` deploy something the release never tested;
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
COMPOSE = SINGLE / "compose.yaml"
ENV_EXAMPLE = SINGLE / "env.example"
DOCKERFILES = [
    ROOT / "deploy" / "pi" / "graphiti" / "Dockerfile",
    ROOT / "deploy" / "k3s" / "sandbox.Dockerfile",
    ROOT / "central_command" / "crawler" / "Dockerfile",
]
SCRIPTS = [
    SINGLE / "setup.sh",
    SINGLE / "resolve-images.sh",
    SINGLE / "make-secrets.sh",
    SINGLE / "verify.sh",
    SINGLE / "update.sh",
    SINGLE / "build-graphiti-image.sh",
    SINGLE / "build-sandbox-image.sh",
    SINGLE / "build-crawler-image.sh",
]
REGISTRY_KEYS = {"dockerio", "ghcr", "mcr"}
COMPONENTS = {"core", "n8n", "graphiti-base", "sandbox-base", "crawler-base", "speech"}
REGISTRY_VAR = {"CC_REGISTRY_DOCKERIO": "dockerio", "CC_REGISTRY_GHCR": "ghcr", "CC_REGISTRY_MCR": "mcr"}
REGISTRY_HOST = {"docker.io": "dockerio", "ghcr.io": "ghcr", "mcr.microsoft.com": "mcr"}


def _manifest() -> dict[tuple[str, str], tuple[str, str, str, str]]:
    """(registry-key, path) -> (constraint, locked tag, locked digest, component)."""
    rows = {}
    for line in IMAGES_TXT.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        assert len(parts) == 6, f"images.txt: expected 6 columns: {line!r}"
        key, path, constraint, tag, digest, comp = parts
        assert key in REGISTRY_KEYS, f"images.txt: unknown registry key {key!r}"
        # `-` declares the locked tag ROLLING (a series or channel like 16 /
        # main-stable): those move on every upstream rebuild, so the tag is the
        # pin and a digest there would fail weekly on a healthy registry.
        assert digest == "-" or re.fullmatch(r"sha256:[0-9a-f]{64}", digest), (
            f"images.txt: bad digest for {path}"
        )
        assert comp in COMPONENTS, f"images.txt: unknown component {comp!r}"
        assert ":" not in path and "@" not in path, f"images.txt: path must carry no tag: {path!r}"
        # The constraint must actually admit the tag this release locked —
        # otherwise resolution would reject the very artifact it was tested on.
        # A constraint is a version SERIES: the tag equals it or continues it
        # at a non-digit (16 admits 16.10, not 160). Flavour lives in the tag.
        assert re.fullmatch(re.escape(constraint) + r"(\D.*)?", tag), (
            f"images.txt: locked tag {tag!r} does not satisfy its own constraint {constraint!r}"
        )
        rows[(key, path)] = (constraint, tag, digest, comp)
    return rows


def test_resolver_tag_matcher_self_test():
    """The substitution rule is the one piece of resolve-images.sh with real
    logic; its own --self-test pins it (same flavour, series boundary)."""
    r = subprocess.run(["bash", str(SINGLE / "resolve-images.sh"), "--self-test"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_images_txt_is_well_formed():
    rows = _manifest()
    assert rows, "images.txt is empty"
    assert len({p for _, p in rows}) == len(rows), "duplicate image path in images.txt"


def _compose_refs() -> set[tuple[str, str]]:
    """Every third-party image compose.yaml deploys, as (registry-key, path).

    The fallback in ``${CC_IMG_X:-<ref>}`` is what a bare ``compose config``
    (and therefore a hand-run ``compose up``) deploys, so it is checked as
    strictly as the manifest itself: right registry, right path, LOCKED tag.
    """
    rows = _manifest()
    refs = set()
    for m in re.finditer(r"^\s*image:\s*(\S+)\s*$", COMPOSE.read_text(encoding="utf-8"), flags=re.M):
        ref = m.group(1)
        if ref.startswith("localhost/"):
            continue  # the three locally-built images: exact, never resolved
        m2 = re.fullmatch(r"\$\{(CC_IMG_[A-Z0-9_]+):-([^/]+)/(.+):([^:]+)\}", ref)
        assert m2, (
            f"compose.yaml: image ref {ref!r} must be "
            "${CC_IMG_<NAME>:-<registry>/<path>:<locked-tag>} — resolve-images.sh owns the variable"
        )
        var, host, path, tag = m2.groups()
        assert host in REGISTRY_HOST, f"compose.yaml: unknown registry host {host!r} in {ref!r}"
        key = REGISTRY_HOST[host]
        assert (key, path) in rows, f"compose.yaml deploys {host}/{path} with no line in images.txt"
        assert var == _img_var(path), f"compose.yaml: {ref!r} should use {_img_var(path)}"
        assert tag == rows[(key, path)][1], (
            f"compose.yaml: {path} defaults to tag {tag!r}, but images.txt locks {rows[(key, path)][1]!r} — "
            "a bare `compose up` would deploy something this release never tested"
        )
        refs.add((key, path))
    return refs


def _img_var(path: str) -> str:
    """resolve-images.sh's name derivation, mirrored (its `img_var`)."""
    name = path.upper().replace("/", "_").replace("-", "_")
    return "CC_IMG_" + name.removeprefix("LIBRARY_")


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
            path, _, tag = m.group(2).rpartition(":")
            bases.add((REGISTRY_VAR[m.group(1)], path, tag))
    return bases


def test_every_pulled_image_is_pinned_in_images_txt():
    rows = _manifest()
    for ref in _compose_refs():
        assert ref in rows, f"{ref[0]}/{ref[1]} is referenced but has no line in images.txt"
    # A Dockerfile's FROM tag is hardcoded, so it must be the LOCKED tag: a
    # substituted base is not what the build would use.
    for key, path, tag in _dockerfile_bases():
        assert (key, path) in rows, f"{key}/{path} is a build base with no line in images.txt"
        assert tag == rows[(key, path)][1], (
            f"{key}/{path}: the Dockerfile builds FROM :{tag} but images.txt locks "
            f":{rows[(key, path)][1]} — the build would not use the tested base"
        )


def test_images_txt_has_no_orphans():
    used = _compose_refs() | {(k, p) for k, p, _ in _dockerfile_bases()}
    for ref in _manifest():
        assert ref in used, f"images.txt pins {ref[1]} but nothing references it"


SEAM_RE = re.compile(r"\bCC_(?:REGISTRY_[A-Z]+|APT_(?:SECURITY_)?MIRROR|PYPI_INDEX_URL|PYTHON_MIRROR|NPM_REGISTRY)\b")


def test_every_seam_the_scripts_read_is_declared_in_env_example():
    declared = set(re.findall(r"^#?\s*(CC_[A-Z_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), flags=re.M))
    for path in SCRIPTS + DOCKERFILES:
        for seam in set(SEAM_RE.findall(path.read_text(encoding="utf-8"))):
            if seam.endswith("_") or "${" in seam:
                continue
            assert seam in declared, f"{path.relative_to(ROOT)} reads {seam}, which env.example does not declare"


def test_no_kube_play_path_survives():
    """The old substrate is DELETED, not kept as a second maintained path.

    (The removed air-gap bundle mechanism is the cautionary tale.)
    """
    assert not list(SINGLE.glob("*.yaml.tmpl")), "the kube-play templates are gone"
    assert not (SINGLE / "render.sh").exists(), "render.sh is gone — compose reads .env natively"
    for script in SCRIPTS:
        text = script.read_text(encoding="utf-8")
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            assert "kube play" not in code, f"{script.name}: still calls podman kube play"


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
