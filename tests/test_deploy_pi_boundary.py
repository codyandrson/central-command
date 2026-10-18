"""The deploy/pi split, release A (v2.66.0): the live set has moved and every
reader names the new paths, while deploy/pi/ keeps a byte-identical duplicate
of that set for ONE release — because the update INTO this release is run by
the installed v2.65.x cc-update.sh, whose configmap row, units glob and
policy-apply still name deploy/pi/... against the merged tree (the same shape
as the Graphiti removal, tests/test_graphiti_server_boundary.py). Release A
deletes nothing: the retired Compose stack is still here too, and both sets
leave in v2.67.0 once this release is validated.

These pin that state. Release B flips them: deploy/pi/ gone, no mention left.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PI = ROOT / "deploy" / "pi"
SHARED = ROOT / "deploy" / "shared" / "litellm"
K3S = ROOT / "deploy" / "k3s"

LIVE_DUPLICATES = ("litellm/config.yaml", "litellm/model-preferences.yaml", "litellm/policy.py",
                   "litellm/register-models.py", "litellm/checks.sh", "cc-nerve.service", ".env.example")
RETIRED = ("docker-compose.yml", "cc-uvicorn.service", "backup.sh", "cc-backup.service", "cc-backup.timer",
           "verify.sh", "migrate-from-desktop.sh", "n8n/calendar-facade-writes.md")
ENV_READERS = ("init-env.sh", "make-secrets.sh", "mint-keys.sh", "backup.sh", "cc-update.sh",
               "verify.sh", "setup.sh")


def _code(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def _non_comment(path: Path) -> list[str]:
    return [l for l in path.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#")]


# ── release A deletes nothing ───────────────────────────────────────────────


def test_the_duplicate_live_set_and_the_retired_stack_are_still_here():
    for rel in LIVE_DUPLICATES + RETIRED:
        assert (PI / rel).is_file(), rel
    assert (PI / "README.md").is_file()


V2_65_4_SHA256 = {
    "litellm/config.yaml": "51a22c5469a5cec67a550ed14f7801ace31d002af8687b406476485fd9db7fcd",
    "litellm/model-preferences.yaml": "bd1133533b3341a349796efe6d0fa727bffed58611a00775e760b00fc0380e5e",
    "litellm/policy.py": "77559803263bdf3fa57eed988c291af7f0d12e61cc0d586bfabccfb976bda0ce",
    "litellm/register-models.py": "c0a54f9c4d0eb4306fc3aa386449e2512789730fd9d4a2c458745061a81cf176",
    "litellm/checks.sh": "dfb3749b2accff7aa0871297a9742eea8b6a06dae8fffc94950dcac757716fd3",
    "cc-nerve.service": "909a87e604cba54a95da0771a8865c719fbb806f006273b1bf239cfaff0abb31",
    ".env.example": "a7f950c2f7efd458937773161cd05a785b8ae9135518e6a20a539768a2babd83",
}


def test_the_duplicates_are_byte_identical_to_v2_65_4():
    """The v2.65.x updater's configmap row diffs deploy/pi/litellm/config.yaml
    against the checkpoint and its policy-apply runs deploy/pi/litellm/policy.py
    on the merged tree — so the OLD copies must not change by one byte until
    v2.67.0 deletes them. Pinned by hash, not by comparison with the moved
    copies: those legitimately differ (paths in comments and messages, and the
    env resolver below)."""
    for rel, digest in V2_65_4_SHA256.items():
        assert hashlib.sha256((PI / rel).read_bytes()).hexdigest() == digest, rel


def test_the_moved_copies_differ_from_the_old_only_where_they_must():
    for name in ("config.yaml", "model-preferences.yaml"):
        assert _non_comment(PI / "litellm" / name) == _non_comment(SHARED / name), name
    assert _non_comment(PI / "cc-nerve.service") == _non_comment(K3S / "cc-nerve.service")
    assert _non_comment(PI / ".env.example") == _non_comment(K3S / ".env.example")
    for name in ("policy.py", "register-models.py"):
        old = (PI / "litellm" / name).read_text(encoding="utf-8")
        new = (SHARED / name).read_text(encoding="utf-8")
        # HERE.parent/.env was deploy/pi/.env; from deploy/shared/litellm/ it is
        # nothing — the Executor runs these with no LITELLM_MASTER_KEY in the
        # environment, so the file fallback is load-bearing and must resolve
        # like env-path.sh: the new path when it exists, else the old.
        assert 'ENV_PATH = HERE.parent / ".env"' in old, name
        assert 'ENV_PATH = _env_path()' in new and '"deploy" / "k3s" / ".env"' in new \
            and '"deploy" / "pi" / ".env"' in new, name
        strip = lambda s: "\n".join(l for l in s.splitlines() if "deploy/pi" not in l and "deploy/shared" not in l
                                     and "deploy/k3s" not in l and "ENV_PATH" not in l and "REPO_ROOT" not in l
                                     and "_env_path" not in l and l.strip() not in ("new = REPO_ROOT / \"deploy\" / \"k3s\" / \".env\"",
                                     "if new.exists() or new.is_symlink():", "return new", '"""'))
        # everything else is the same program
        old_lines = set(strip(old).splitlines())
        for line in strip(new).splitlines():
            if line.strip() and line not in old_lines:
                assert "env" in line.lower() or "path before v2.66.0" in line or "single-node" in line \
                    or "Only a fallback" in line, (name, line)


def test_the_old_checks_script_is_untouched_and_the_new_one_uses_the_resolver():
    old = _code(PI / "litellm" / "checks.sh")
    assert '"$HERE/../.env"' in old                      # its v2.65.3 shape
    new = _code(SHARED / "checks.sh")
    assert "cc_k3s_env_file" in new and '"$HERE/../.env"' not in new
    # everything but the key lookup is the same script
    strip = lambda s: "\n".join(l for l in s.splitlines() if "KEY" not in l and "ENV_FILE" not in l and "env-path" not in l)
    assert strip(old) == strip(new)


# ── every reader names the new paths ────────────────────────────────────────


def test_every_k3s_env_reader_goes_through_the_resolver():
    for name in ENV_READERS:
        code = _code(K3S / name)
        assert "env-path.sh" in code, name
        # init-env.sh MOVES the file first and then owns the new path; the rest resolve it
        assert ("cc_k3s_env_migrate" if name == "init-env.sh" else "cc_k3s_env_file") in code, name


def test_no_script_reads_the_old_env_path_directly():
    """The literal old path survives only inside the resolver's fallback and
    in operator-facing messages — never as a path a script opens."""
    opens = re.compile(r'(\$\{?\w+\}?/deploy/pi/\.env|(?<![\w/])deploy/pi/\.env\s*\]\]|\.\s+deploy/pi/\.env)')
    for path in list(K3S.glob("*.sh")) + [SHARED / "checks.sh"]:
        if path.name == "env-path.sh":
            continue
        for line in _non_comment(path):
            assert not opens.search(line), f"{path.name}: {line.strip()}"


def test_the_updater_names_the_new_paths_and_carries_the_move():
    upd = (K3S / "cc-update.sh").read_text(encoding="utf-8")
    cm = upd[upd.index("<<'CM'"):]
    cm = cm[:cm.index("\nCM\n")]
    assert "deploy/shared/litellm/config.yaml|cc-litellm-config" in cm and "deploy/pi" not in cm
    units = upd[upd.index("changed_units=\"$(changed_between"):]
    units = units[:units.index(')"')]
    assert "deploy/pi" not in units            # cc-nerve.service is matched by deploy/k3s/*.service now
    assert 'python3 "$REPO/deploy/shared/litellm/policy.py" --apply' in upd
    assert "\nmigrate_k3s_env() {" in upd
    main = upd[upd.index("\nmain() {"):]
    assert main.index('phase "merging') < main.index("migrate_k3s_env") < main.index('phase "applying schema')


def test_the_other_k3s_scripts_name_the_shared_litellm_files_and_the_moved_unit():
    setup = _code(K3S / "setup.sh")
    assert "deploy/shared/litellm/register-models.py" in setup and "deploy/shared/litellm/policy.py" in setup
    assert '"$HERE/cc-nerve.service"' in setup and "deploy/pi/cc-nerve.service" not in setup
    assert "deploy/shared/litellm/config.yaml" in _code(K3S / "make-secrets.sh")
    assert "deploy/shared/litellm/policy.py --check" in _code(K3S / "verify.sh")
    assert "deploy/pi/cc-nerve.service" not in _code(K3S / "migrate-to-cc.sh")
    migrate = _code(K3S / "migrate-to-cc.sh")
    assert '"$REPO/deploy/k3s/.env"' in migrate and '"$REPO/deploy/pi/.env"' not in migrate


def test_the_app_and_the_single_profile_name_the_shared_files():
    executor = (ROOT / "central_command" / "gateway" / "executor.py").read_text(encoding="utf-8")
    for const in ("_LITELLM_CONFIG_FILE", "_LITELLM_PREFS_FILE", "_LITELLM_CHECKS_SCRIPT", "_LITELLM_POLICY_SCRIPT"):
        m = re.search(rf'^{const} = "([^"]+)"', executor, re.M)
        assert m and m.group(1).startswith("deploy/shared/litellm/"), const
    tools = (ROOT / "central_command" / "runtime" / "tools.py").read_text(encoding="utf-8")
    assert "deploy/pi/litellm" not in tools
    llm = _code(ROOT / "deploy" / "single" / "phases" / "llm.sh")
    assert "deploy/shared/litellm/register-models.py" in llm and "deploy/pi" not in llm
    for rel in ("central_command/gateway/capabilities.py", "central_command/runtime/packs.py",
                "central_command/runtime/litellm_manager_profile.py", "central_command/api/systems.py"):
        assert "deploy/pi/" not in (ROOT / rel).read_text(encoding="utf-8"), rel


def test_the_moved_litellm_directory_still_finds_its_own_siblings():
    for name in ("policy.py", "register-models.py"):
        src = (SHARED / name).read_text(encoding="utf-8")
        assert 'HERE / "model-preferences.yaml"' in src, name
    assert '"$HERE/policy.py"' in (SHARED / "checks.sh").read_text(encoding="utf-8")


def test_the_rules_glob_follows_the_files():
    rules = (ROOT / ".claude" / "rules" / "models.md").read_text(encoding="utf-8")
    assert "deploy/shared/litellm/**" in rules and "deploy/pi/litellm/**" not in rules


def test_the_floor_is_unchanged_until_the_deletion_release():
    """Release B (the deletions) raises min_upgrade_from to this release; this
    one leaves the floor where the Graphiti removal put it."""
    text = (ROOT / "VERSION").read_text(encoding="utf-8")
    assert re.search(r"^version=2\.66\.\d+$", text, re.M), text
    assert re.search(r"^min_upgrade_from=2\.60\.0$", text, re.M), text
