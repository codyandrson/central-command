"""The Graphiti server's removal survives the PREVIOUS release's k3s updater.

Design record 2026-10-04, D10. The update across this boundary is run by the
cc-update.sh of the release being replaced (systemd runs the installed copy).
`min_upgrade_from=2.59.1` makes that release the v2.59.1 BRIDGE, whose
updater applies the graphiti-core patches, widens the app key's scope and adds
CC_GRAPH_RERANK_ALIAS by itself (tests/test_k3s_update_reconcile.py) — and
which, because v2.59.1 still runs the server, still carries, in memory:

* an IMAGES row `graphiti | … | build-graphiti-image.sh | cc-graphiti | … |
  deploy/pi/graphiti deploy/k3s/build-graphiti-image.sh` — if ANY file under
  those two paths differs between the installed release and the target, it
  runs the target's build script in its prebuild and then adds `cc-graphiti`
  to IMAGE_DEPLOYS, whose `rollout restart deploy/cc-graphiti` runs AFTER the
  removed.txt deletes. A deleted Deployment there is `die manifests` on a
  MERGED tree, with no rollback;
* a configmap-refresh row reading `deploy/pi/graphiti/config.yaml` when that
  file changed — a missing file is the same `die manifests`.

So for ONE release the server's build inputs stay BYTE-IDENTICAL (unused by
anything in this tree), and the next release — applied by THIS release's
updater, which has neither row — deletes them together with the
configmap/Secret tombstones. These tests pin both halves; when that next
release lands, delete this file with the frozen files.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# sha256 over the file with CRs stripped (a CRLF checkout hashes the same).
FROZEN = {
    "deploy/k3s/build-graphiti-image.sh": "ee225a10498ad78e0e16bdea82bf94219423f9e622d0e94f36956f7581be8400",
    "deploy/pi/graphiti/Dockerfile": "76905597363e00acde7db8c4e5d11c651b85f9e25b6bb02c4f7dcf5055a4001c",
    "deploy/pi/graphiti/config.yaml": "1742632aed1f08b935da9fa7e8e9bdbf979a46c9b9364eff8e785851c0d2713c",
    "deploy/pi/graphiti/patches/1666-reasoning-first-dedupe.patch": "904de032ec94cf1674007636fde53871c63f96790b4ed40e11c98ad3ff330acc",
    "deploy/pi/graphiti/patches/1729-invalidation-scope.patch": "7d76fe1df96f741b4b5f65decd0143c1dafd9ffd941f4164eff7575059c3d8a7",
    "deploy/pi/graphiti/patches/cc-entity-type-source.patch": "d46ea712a746ce117b7892393ffb471d86b22772736b06f9d0876de613d7ead1",
    "deploy/pi/graphiti/patches/cc-rerank-client.patch": "899c45a766e660d4b60cffcefdd2c07c57d0049979513363d8048de1ec5231e0",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r", b"")).hexdigest()


def test_the_previous_updaters_graphiti_inputs_are_byte_identical():
    for rel, want in FROZEN.items():
        assert _sha(ROOT / rel) == want, (
            f"{rel} changed — the previous release's cc-update.sh would rebuild "
            "the retired image and then rollout-restart the tombstoned "
            "cc-graphiti Deployment, dying mid-update with no rollback"
        )


def test_nothing_was_added_under_the_frozen_directory():
    present = {
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "deploy" / "pi" / "graphiti").rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    assert present == {k for k in FROZEN if k.startswith("deploy/pi/graphiti/")}


def test_nothing_in_this_tree_uses_the_frozen_files():
    for rel in ("deploy/k3s/cc-update.sh", "deploy/k3s/setup.sh", "deploy/k3s/make-secrets.sh",
                "deploy/k3s/verify.sh", "deploy/single/setup.sh"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        assert "build-graphiti-image.sh" not in code, rel
        assert "deploy/pi/graphiti" not in code, rel
    assert not (ROOT / "deploy" / "single" / "build-graphiti-image.sh").exists()


def test_this_updater_has_no_graphiti_image_or_configmap_row():
    text = (ROOT / "deploy" / "k3s" / "cc-update.sh").read_text(encoding="utf-8")
    images = text[text.index("IMAGES=("):]
    images = images[:images.index("\n)\n")]
    assert "graphiti" not in images
    cm = text[text.index("<<'CM'"):]
    cm = cm[:cm.index("\nCM\n")]
    assert "graphiti" not in cm


def test_tombstones_take_the_workload_now_and_leave_its_config_for_rollback():
    lines = {
        tuple(l.split()) for l in (ROOT / "deploy" / "k3s" / "removed.txt").read_text().splitlines()
        if l.strip() and not l.lstrip().startswith("#")
    }
    assert ("central-command", "deployment", "cc-graphiti") in lines
    assert ("central-command", "service", "graphiti") in lines
    # The previous release's rollback re-applies its manifests, which recreate
    # the Deployment but not the make-secrets.sh objects it mounts.
    assert ("central-command", "configmap", "cc-graphiti-config") not in lines
    assert ("central-command", "secret", "cc-graphiti") not in lines


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


def test_the_jump_requires_the_bridge_release():
    """v2.59.1's updater is the one that carries the patch step, the reconcile
    phase and the scope widening; anything older would need the operator's
    hands, so the installed updater refuses it at resolve."""
    text = (ROOT / "VERSION").read_text(encoding="utf-8")
    assert re.search(r"^min_upgrade_from=2\.59\.1$", text, re.M), text


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
