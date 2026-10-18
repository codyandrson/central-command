"""The deploy/pi split, release B (v2.67.0): deploy/pi/ is gone from the
tracked tree. Release A (v2.66.0) moved the live set to deploy/shared/litellm/,
deploy/k3s/.env and deploy/k3s/cc-nerve.service and repointed every reader,
but kept a byte-identical duplicate under deploy/pi/ for ONE release — because
the update INTO A was run by the installed v2.65.x cc-update.sh, whose
configmap row, units glob and policy-apply still named deploy/pi/... against
the merged tree (the same shape as the Graphiti removal,
tests/test_graphiti_server_boundary.py). A's own updater names only the new
paths, so B — applied by it — deletes the duplicate live set, the retired
Compose stack and the README whole; `git show v2.65.3:deploy/pi/<file>` is the
way to reach them now.

These pin that state, and that every reader still names the new paths.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PI = ROOT / "deploy" / "pi"
SHARED = ROOT / "deploy" / "shared" / "litellm"
K3S = ROOT / "deploy" / "k3s"

# What B deleted: the duplicate live set A kept for one release, the retired
# Compose stack, and the directory's README.
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


# ── release B deleted deploy/pi/ whole ──────────────────────────────────────


def test_nothing_under_deploy_pi_is_tracked():
    out = subprocess.run(["git", "ls-files", "deploy/pi"], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout
    assert out.strip() == "", out


def test_the_duplicate_live_set_the_retired_stack_and_the_readme_are_gone():
    """File by file, not the directory: a deployment keeps an untracked
    deploy/pi/.env symlink (the resolver's fallback) until the operator's
    post-apply checklist for this release removes it and the empty directory;
    nothing under deploy/pi/ is tracked or ignored any more."""
    for rel in LIVE_DUPLICATES + RETIRED + ("README.md",):
        assert not (PI / rel).exists(), rel


# ── every reader names the new paths ────────────────────────────────────────


def test_every_k3s_env_reader_goes_through_the_resolver():
    for name in ENV_READERS:
        code = _code(K3S / name)
        assert "env-path.sh" in code, name
        # init-env.sh MOVES the file first and then owns the new path; the rest resolve it
        assert ("cc_k3s_env_migrate" if name == "init-env.sh" else "cc_k3s_env_file") in code, name
    # The readers outside deploy/k3s/: the LiteLLM checks script (moved in
    # release A) and the n8n workflow installer's --k3s arm (release A's move
    # table missed it; it read deploy/pi/.env through the symlink).
    checks = _code(SHARED / "checks.sh")
    assert "env-path.sh" in checks and "cc_k3s_env_file" in checks and '"$HERE/../.env"' not in checks
    n8n = _code(ROOT / "deploy" / "n8n" / "apply-workflows.sh")
    assert "env-path.sh" in n8n and 'DB_ENV="$(cc_k3s_env_file "$REPO")"' in n8n


def _tracked_shell_scripts() -> list[Path]:
    """Every tracked shell script, plus a root setup.sh/update.sh if one ever
    appears — not a directory glob, so the next reader added anywhere under
    the tree is scanned too (deploy/n8n/apply-workflows.sh slipped past the
    deploy/k3s-only scan in release A)."""
    out = subprocess.run(["git", "ls-files", "--", "*.sh", "setup.sh", "update.sh"], cwd=ROOT,
                         check=True, capture_output=True, text=True).stdout
    return [ROOT / rel for rel in out.splitlines() if rel]


def test_no_script_reads_the_old_env_path_directly():
    """The literal old path survives only inside the resolver's fallback and
    in operator-facing messages — never as a path a script opens."""
    opens = re.compile(r'(\$\{?\w+\}?/deploy/pi/\.env|(?<![\w/])deploy/pi/\.env\s*\]\]|\.\s+deploy/pi/\.env)')
    scripts = _tracked_shell_scripts()
    assert ROOT / "deploy" / "n8n" / "apply-workflows.sh" in scripts
    assert ROOT / "deploy" / "shared" / "litellm" / "checks.sh" in scripts
    for path in scripts:
        if path == K3S / "env-path.sh":
            continue
        for line in _non_comment(path):
            assert not opens.search(line), f"{path.relative_to(ROOT)}: {line.strip()}"


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
