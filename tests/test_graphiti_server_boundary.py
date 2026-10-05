"""The Graphiti server's removal, and what is left of the boundary it crossed.

Design record 2026-10-04, D10. The update across the removal was run by the
cc-update.sh of the release being replaced (systemd runs the installed copy),
which for v2.59.1 still carried, in memory, an IMAGES row for the server's
locally built image and a configmap-refresh row for its config file. For ONE
release (v2.60.0) the old image's build inputs therefore stayed byte-identical
and unreferenced, so that updater found nothing changed. v2.60.0's own updater
has neither row, so v2.61.0 — applied by it — deleted the inputs
(`deploy/pi/graphiti/`, `deploy/k3s/build-graphiti-image.sh`) and tombstoned
the configmap and Secret the Deployment mounted. These tests pin the finished
state, and the steps the bridge introduced that the NEXT boundary's update
still depends on.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _code(rel: str) -> str:
    text = (ROOT / rel).read_text(encoding="utf-8")
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def test_the_retired_servers_build_inputs_are_gone_and_nothing_names_them():
    assert not (ROOT / "deploy" / "pi" / "graphiti").exists()
    assert not (ROOT / "deploy" / "k3s" / "build-graphiti-image.sh").exists()
    assert not (ROOT / "deploy" / "single" / "build-graphiti-image.sh").exists()
    for rel in ("deploy/k3s/cc-update.sh", "deploy/k3s/setup.sh", "deploy/k3s/make-secrets.sh",
                "deploy/k3s/verify.sh", "deploy/k3s/backup.sh", "deploy/k3s/migrate-to-cc.sh",
                "deploy/single/setup.sh", "deploy/single/update.sh", "deploy/pi/backup.sh",
                "deploy/pi/docker-compose.yml"):
        code = _code(rel)
        assert "build-graphiti-image.sh" not in code, rel
        assert "deploy/pi/graphiti" not in code, rel


def test_nothing_creates_the_tombstoned_configmap_or_secret():
    for rel in ("deploy/k3s/make-secrets.sh", "deploy/k3s/setup.sh", "deploy/k3s/migrate-to-cc.sh",
                "deploy/k3s/cc-update.sh"):
        code = _code(rel)
        assert "cc-graphiti-config" not in code, rel
        assert not re.search(r"apply_secret\s+cc-graphiti\b", code), rel
    for manifest in (ROOT / "deploy" / "k3s").glob("*.yaml"):
        assert not re.search(r"^\s*name:\s*(cc-graphiti-config|cc-graphiti)\s*$",
                             manifest.read_text(encoding="utf-8"), re.M), manifest.name


def test_this_updater_has_no_graphiti_image_or_configmap_row():
    text = (ROOT / "deploy" / "k3s" / "cc-update.sh").read_text(encoding="utf-8")
    images = text[text.index("IMAGES=("):]
    images = images[:images.index("\n)\n")]
    assert "graphiti" not in images
    cm = text[text.index("<<'CM'"):]
    cm = cm[:cm.index("\nCM\n")]
    assert "graphiti" not in cm


def test_tombstones_take_the_workload_and_what_it_mounted():
    lines = {
        tuple(l.split()) for l in (ROOT / "deploy" / "k3s" / "removed.txt").read_text().splitlines()
        if l.strip() and not l.lstrip().startswith("#")
    }
    assert ("central-command", "deployment", "cc-graphiti") in lines
    assert ("central-command", "service", "graphiti") in lines
    # One release after the Deployment: the previous release's rollback
    # re-applied manifests that recreate the Deployment but not these, so they
    # could not go earlier.
    assert ("central-command", "configmap", "cc-graphiti-config") in lines
    assert ("central-command", "secret", "cc-graphiti") in lines


def test_the_patch_step_follows_every_install_and_tolerates_an_old_tree():
    upd = (ROOT / "deploy" / "k3s" / "cc-update.sh").read_text(encoding="utf-8")
    rebuild = upd[upd.index("rebuild() {"):]
    rebuild = rebuild[:rebuild.index("\n}\n")]
    assert re.search(r"uv pip install[^\n]*\n\s*patch_graphiti\b", rebuild)
    fn = upd[upd.index("patch_graphiti() {"):]
    fn = fn[:fn.index("\n}\n")]
    # A rollback reinstalls a release that may have no script at all.
    assert '[[ -f "$REPO/scripts/apply_graphiti_patches.py" ]] || return 0' in fn
    setup = (ROOT / "deploy" / "k3s" / "setup.sh").read_text(encoding="utf-8")
    app = setup[setup.index("phase_app() {"):]
    assert app.index("uv pip install") < app.index("scripts/apply_graphiti_patches.py")


def test_no_install_can_jump_across_the_server_removal():
    """An installed v2.59.1 updater still has the server's IMAGES row and would
    prebuild with a build script this tree no longer has (`die prebuild`), and
    one older than that lacks the bridge's patch step and key widening. So the
    floor is v2.60.0, whose own updater has neither row — refused at resolve,
    before anything changes."""
    text = (ROOT / "VERSION").read_text(encoding="utf-8")
    m = re.search(r"^min_upgrade_from=(\d+)\.(\d+)\.(\d+)$", text, re.M)
    assert m, text
    assert tuple(int(x) for x in m.groups()) >= (2, 60, 0), text


def test_this_updater_keeps_the_bridges_steps():
    """The next boundary's update is run by THIS release's updater: the steps
    the bridge introduced stay, keyed on what the merged tree contains."""
    upd = (ROOT / "deploy" / "k3s" / "cc-update.sh").read_text(encoding="utf-8")
    for name in ("patch_graphiti() {", "ensure_app_env_defaults() {", "reconcile_app_config() {"):
        assert name in upd, name
    main = upd[upd.index("\nmain() {"):]
    assert main.index("reconcile_app_config") < main.index('phase "starting services"')
    assert "grep -q -- '--scope-only'" in upd
    mint = (ROOT / "deploy" / "k3s" / "mint-keys.sh").read_text(encoding="utf-8")
    assert "--scope-only)" in mint
    assert re.search(r"^#CC_GRAPH_RERANK_ALIAS=", (ROOT / ".env.example").read_text(), re.M)
