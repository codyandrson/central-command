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


PYTHON_RESOLVER_MIRRORS = (SHARED / "policy.py", SHARED / "register-models.py",
                           ROOT / "scripts" / "graph_inspect.py")


def test_operator_tooling_resolves_the_env_like_the_resolver():
    """The three Python mirrors of cc_k3s_env_file build the path from
    segments, which no literal-string guard sees; each must prefer
    deploy/k3s/.env — taken when it exists OR is a symlink, like the shell
    resolver — and fall back to the old path only after. The Executor runs
    policy.py and register-models.py without LITELLM_MASTER_KEY in the
    environment, so the file fallback is load-bearing: release B's first
    story deleted the test that pinned the two shared files, and flipping
    their order or dropping is_symlink() passed the suite until this."""
    fallback_kept = ("the resolver's deploy/pi fallback is kept by decision until a release drops it "
                     "from all three mirrors and this test together")
    for path in PYTHON_RESOLVER_MIRRORS:
        src = path.read_text(encoding="utf-8")
        name = str(path.relative_to(ROOT))
        # `new` IS the k3s path — not merely "the k3s literal appears first"
        assert re.search(r'^\s+new = \w+ / "deploy" / "k3s" / "\.env"$', src, re.M), name
        assert '"deploy" / "pi" / ".env"' in src, f"{name}: {fallback_kept}"
        assert src.index('"deploy" / "k3s" / ".env"') < src.index('"deploy" / "pi" / ".env"'), name
        assert "if new.exists() or new.is_symlink():" in src, name


def test_the_moved_litellm_directory_still_finds_its_own_siblings():
    for name in ("policy.py", "register-models.py"):
        src = (SHARED / name).read_text(encoding="utf-8")
        assert 'HERE / "model-preferences.yaml"' in src, name
    assert '"$HERE/policy.py"' in (SHARED / "checks.sh").read_text(encoding="utf-8")


def test_the_rules_glob_follows_the_files():
    rules = (ROOT / ".claude" / "rules" / "models.md").read_text(encoding="utf-8")
    assert "deploy/shared/litellm/**" in rules and "deploy/pi/litellm/**" not in rules


def test_the_floor_is_release_a():
    """The update INTO a release is run by the installed updater. Only
    v2.66.x's names the new paths; a v2.65.x updater's configmap row would die
    on deploy/pi/litellm/config.yaml against the merged tree, after the merge
    and without a rollback. So B's floor is A: an install on v2.65.x takes
    v2.66.0 first. Only the floor is pinned here: the version line belongs to
    tests/test_version_file.py (VERSION against the newest CHANGELOG
    heading), and an earlier form that also pinned `version=2.67.x` would
    have failed the first 2.68.0 bump at the gate for no reason. The floor
    stays 2.66.0 until a release deliberately raises it."""
    text = (ROOT / "VERSION").read_text(encoding="utf-8")
    assert re.search(r"^min_upgrade_from=2\.66\.0$", text, re.M), text


# ── nothing in the tree names deploy/pi outside history and the kept seam ───


def _block(start: str):
    """Lines from the one starting with `start` up to (not including) the next
    column-0 non-blank line — the next heading or bullet in Markdown, the next
    top-level statement in Python."""
    def pred(lines: list[str], i: int) -> bool:
        for j in range(i, -1, -1):
            if lines[j].startswith(start):
                return True
            if lines[j] and not lines[j][0].isspace():
                return False
        return False
    return pred


def _heading_range(first: str, stop: str | None = None):
    """Lines from the heading `first` up to the heading `stop` (to the end of
    the file when there is none). Both headings must exist, or a renumbering
    would silently widen the range."""
    def pred(lines: list[str], i: int) -> bool:
        assert any(l.startswith(first) for l in lines), f"heading {first!r} not found"
        assert stop is None or any(l.startswith(stop) for l in lines), f"heading {stop!r} not found"
        seen = False
        for j in range(i + 1):
            if stop is not None and lines[j].startswith(stop):
                return False
            if lines[j].startswith(first):
                seen = True
        return seen
    return pred


def _contains(*needles: str):
    return lambda lines, i: all(n in lines[i] for n in needles)


def _message(lines: list[str], i: int) -> bool:
    """An operator-facing message, not a path a script opens: the name sits
    after the message call's opening quote."""
    line = lines[i]
    at = line.find("deploy/pi")
    return any(0 <= line.find(tok) < at for tok in ('note "', 'die "', 'step "', 'echo "'))


# path or directory prefix → (reason, line predicate or None for the whole file)
ALLOWED = {
    "CHANGELOG.md": ("release history names the paths each release changed", None),
    "docs/superpowers/": ("design records are dated history; their As-built headers point at live files", None),
    "docs/audit/": ("audit ledgers are dated history", None),
    "docs/decisions/process.md": ("the decision log recounts the v2.61.0 Graphiti deletion", None),
    "deploy/desktop-superseded/": ("the pre-move desktop units, kept as history", None),
    "deploy/k3s/env-path.sh": ("the resolver's fallback and the one-time move (kept through B by decision)", None),
    "deploy/shared/litellm/policy.py": ("the resolver's Python mirror", _block("def _env_path(")),
    "deploy/shared/litellm/register-models.py": ("the resolver's Python mirror", _block("def _env_path(")),
    "tests/test_k3s_env_path.py": ("exercises the resolver and the move in every layout", None),
    "deploy/k3s/cc-update.sh": ("operator-facing fallback message", _message),
    "deploy/k3s/init-env.sh": ("operator-facing fallback message", _message),
    "deploy/k3s/setup.sh": ("operator-facing fallback message", _message),
    "deploy/k3s/migrate-to-cc.sh": ("operator-facing fallback message", _message),
    "deploy/k3s/README.md": ("sections 0a/0b are the dated history of the two boundary updates; "
                             "section 10 reaches the retired stack through git and says which "
                             "env to copy to the old path before the Compose fallback can start",
                             lambda lines, i: _heading_range("## 0a.", "## 1.")(lines, i)
                             or (_heading_range("## 10.")(lines, i)
                                 and (_contains("git show v2.65.3:deploy/pi/")(lines, i)
                                      or _contains("deploy/k3s/.env", "deploy/pi/.env")(lines, i)))),
    "docs/ARCHITECTURE.md": ("the D9 / Story 2.1 provenance row names the retired image's files",
                             _contains("D9 / Story 2.1")),
    ".claude/rules/deploy-k3s.md": ("the dated v2.66.0 bite mark on the env move",
                                    _block("- **The containers' env is")),
    "web/VENDORED.md": ("past tense: where the live set lived until v2.66.0", _contains("lived in", "v2.66.0")),
    "deploy/shared/README.md": ("past tense: where these files lived until v2.66.0", _contains("lived in", "v2.66.0")),
    "tests/test_deploy_pi_boundary.py": ("this guard: its docstrings, regexes and negative assertions name the path", None),
    "tests/test_graphiti_server_boundary.py": ("the Graphiti removal's guard asserts deploy/pi/graphiti is never named", None),
}


def _allowed(path: str, lines: list[str], i: int) -> bool:
    # A `#`-led comment line in a non-Markdown file (.sh, .py, .yaml, .service,
    # .Dockerfile, .env.example alike) may recount where things were.
    if not path.endswith(".md") and lines[i].lstrip().startswith("#"):
        return True
    for key, (_reason, pred) in ALLOWED.items():
        if path == key or (key.endswith("/") and path.startswith(key)):
            return pred is None or pred(lines, i)
    return False


def test_no_tracked_file_names_deploy_pi_outside_history_and_the_kept_seam():
    """deploy/pi/ was deleted in v2.67.0. What may still say the name is
    enumerated in ALLOWED with its reason — dated history, the resolver seam
    kept by decision, operator-facing fallback messages, and comment lines —
    so the next reference anywhere else fails here naming its file and line."""
    # The path itself, not a prefix of another (deploy/pipeline). The
    # segment-built form `"deploy" / "pi"` is not seen here; the next test is.
    r = subprocess.run(["git", "grep", "-n", "-I", "-E", "-e", r"deploy/pi([^A-Za-z0-9_-]|$)"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode in (0, 1), r.stderr
    hits: dict[str, list[int]] = {}
    for row in r.stdout.split("\n"):
        if not row:
            continue
        path, line, _ = row.split(":", 2)
        hits.setdefault(path, []).append(int(line))
    offenders = []
    for path, numbers in sorted(hits.items()):
        # split("\n"), not splitlines(): git counts newlines only
        lines = (ROOT / path).read_text(encoding="utf-8", errors="replace").split("\n")
        for n in numbers:
            if not _allowed(path, lines, n - 1):
                offenders.append(f"{path}:{n}: {lines[n - 1].strip()}")
    assert not offenders, "deploy/pi named outside the allowlist:\n  " + "\n  ".join(offenders)


# path → (reason, line predicate or None for the whole file)
SEGMENT_ALLOWED = {
    "deploy/shared/litellm/policy.py": ("the resolver's Python mirror", _block("def _env_path(")),
    "deploy/shared/litellm/register-models.py": ("the resolver's Python mirror", _block("def _env_path(")),
    "scripts/graph_inspect.py": ("the resolver's Python mirror", _block("def _env_path(")),
    "tests/test_k3s_env_path.py": ("builds every env layout under tmp_path", None),
    "tests/test_k3s_mint_key_scope.py": ("builds the pre-move layout under tmp_path", None),
    "tests/test_k3s_reset_phase.py": ("builds the pre-move layout under tmp_path", None),
    "tests/test_deploy_pi_boundary.py": ("this guard: it names the shape it greps for and asserts the path is gone", None),
    "tests/test_graphiti_server_boundary.py": ("asserts deploy/pi/graphiti is gone", None),
}


def test_no_tracked_code_builds_deploy_pi_from_segments_outside_the_allowlist():
    """The literal guard above never saw scripts/graph_inspect.py, which built
    the old path as `"deploy" / "pi" / ".env"` — release A's move table missed
    it for the same reason. This greps every tracked .py and .sh for the forms
    the languages build the path from (`"deploy" / "pi"`, `"deploy", "pi"`,
    either quote style); what may carry them is enumerated in SEGMENT_ALLOWED
    with its reason — the three resolver mirrors' _env_path blocks and the
    tests that lay the old tree out under tmp_path or assert it is gone — so
    the shape planted anywhere else fails here naming its file and line."""
    r = subprocess.run(["git", "grep", "-n", "-I", "-E", "-e",
                        r"""["']deploy["']\s*(/|,)\s*["']pi["']""", "--", "*.py", "*.sh"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode in (0, 1), r.stderr
    hits: dict[str, list[int]] = {}
    for row in r.stdout.split("\n"):
        if not row:
            continue
        path, line, _ = row.split(":", 2)
        hits.setdefault(path, []).append(int(line))
    # the canary: the grep sees what the literal guard could not. It holds as
    # long as the resolver's fallback is kept (by decision); the release that
    # drops it from the three mirrors retires this line with them.
    assert "scripts/graph_inspect.py" in hits, "canary gone: the segment grep no longer matches graph_inspect.py"
    offenders = []
    for path, numbers in sorted(hits.items()):
        lines = (ROOT / path).read_text(encoding="utf-8", errors="replace").split("\n")
        _reason, pred = SEGMENT_ALLOWED.get(path, (None, False))
        for n in numbers:
            if pred is False or (pred is not None and not pred(lines, n - 1)):
                offenders.append(f"{path}:{n}: {lines[n - 1].strip()}")
    assert not offenders, "deploy/pi built from segments outside the allowlist:\n  " + "\n  ".join(offenders)
