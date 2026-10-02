"""`update.sh apply` ACQUIRES before it merges, and the rest of D5 (v2.57.0, P3).

The 2026-10-01 design record's D5, and its acceptance scenario verbatim:
"`update.sh apply` with the mirror missing one tag, confirm the tree is
untouched". Until this release `cmd_apply` fast-forwarded `local` and THEN ran
`./setup.sh fetch`, so a mirror lacking one image left a merged tree over the
old venv and the old containers. Now the NEW release's own `setup.sh acquire`
runs from a sparse git worktree of `upstream` in the state dir before anything
moves, and the post-merge sequence stops at the first phase that stops.

These run the REAL `update.sh`, the REAL `update-run.sh` and the REAL
`setup.sh` — its real fetch phase, the real `resolve-images.sh` and the real
catalog probe — in a temp git repo with `local`/`upstream` branches, against
stubs on PATH: a `curl` that plays a container registry (and the proxy's
`/v1/models`), a `podman`, a `uv`, an `npm`, a `node`. The deploy phases that
need a real stack (llm, app, verify) are stub functions appended to the copy's
setup.sh before `main "$@"` — `tests/test_single_driver_ledger.py`'s pattern —
so everything that DECIDES (main, run_phase, the ledger gate, the staged mode)
is the shipped code. Nothing here touches the checkout, the network or a
real container.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = [
    pytest.mark.skipif(shutil.which("git") is None, reason="needs git"),
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="Git Bash prepends /mingw64/bin:/usr/bin to PATH; stub binaries cannot shadow "
               "curl/podman (tests/test_update_runner.py's reason)",
    ),
]

OLD, NEW = "2.56.0", "2.57.0"

_DEBRIS = shutil.ignore_patterns("NUL", "nul", ".env", ".env.*", "__pycache__", "*.pyc")


def _bash() -> str:
    from central_command.api.update import _bash as resolve

    b = resolve()
    if not b:
        pytest.skip("no usable bash on this host")
    return b


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _set(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    seen = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0] if "=" in line else None
        if key in values:
            lines[i] = f"{key}={values[key]}"
            seen.add(key)
    lines += [f"{k}={v}" for k, v in values.items() if k not in seen]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _exe(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


# ── the stubs ───────────────────────────────────────────────────────────────

# A container registry, the proxy's catalog and a stopped API, in one `curl`.
# The registry answers the two calls resolve-images.sh makes: a manifest HEAD
# for the locked tag (200 + the locked digest, so the lock verifies) and a
# tags list. A repository named in `missing` has neither — the mirror lacks it.
_CURL = r'''#!/usr/bin/env python3
import json, os, re, sys
cfg = json.load(open(os.environ["STUB_CFG"]))
args = sys.argv[1:]
takes = {"-D", "-H", "-m", "-o", "-d", "-K", "-X", "--connect-timeout", "--max-time", "--data"}
hdr = url = None; head = False; i = 0
while i < len(args):
    a = args[i]
    if a in takes:
        if a == "-D": hdr = args[i + 1]
        i += 2; continue
    if a == "-I": head = True
    elif a.startswith("http"): url = a
    i += 1
with open(cfg["log"], "a") as f: f.write("curl %s\n" % url)
if url is None: sys.exit(2)
if url.startswith("http://127.0.0.1"):
    if url.endswith("/v1/models"):
        sys.stdout.write(json.dumps({"data": [{"id": m} for m in cfg["models"]]})); sys.exit(0)
    sys.exit(7)                              # nothing else answers: the API is stopped
m = re.match(r"https://[^/]+/v2/(.+)/(manifests|tags)/(.+)$", url)
if not m: sys.exit(7)
repo, kind, ref = m.groups()
gone = repo in cfg["missing"]
def headers(lines):
    if hdr: open(hdr, "w").write("\r\n".join(lines) + "\r\n\r\n")
if kind == "manifests":
    if gone: headers(["HTTP/1.1 404 Not Found"])
    else: headers(["HTTP/1.1 200 OK", "Docker-Content-Digest: " + cfg["digests"].get(repo + ":" + ref, "sha256:" + "0" * 64)])
    sys.exit(0)
headers(["HTTP/1.1 200 OK"])
sys.stdout.write(json.dumps({"tags": ["0.0.1-elsewhere"] if gone else [cfg["locks"].get(repo, "x")]}))
'''


def _stub_bin(tmp: Path, log: Path, calls: Path) -> Path:
    b = tmp / "bin"
    b.mkdir()
    _exe(b / "curl", _CURL)
    # podman: no machine, every container exists, pulls succeed unless the
    # flag file names the ref. The three local images are already there, built
    # by an earlier release (present, no build-inputs label) unless the test
    # seeds a label for one in labels/. A build records its `-t` ref and its
    # `--label` value there, so the stage's ASIDE tag and the live one can be
    # told apart afterwards; `untag` drops a built ref again.
    (tmp / "labels").mkdir()
    _exe(b / "podman", f'''#!/usr/bin/env bash
echo "podman $*" >> "{log}"
key() {{ local k="${{1//\\//_}}"; printf '%s' "${{k//:/_}}"; }}
case "$1" in
  images) printf 'localhost/cc-graphiti:1.0.2-anthropic\\nlocalhost/cc-sandbox:1\\nlocalhost/cc-crawler:1\\n'
          cat "{tmp}/built" 2>/dev/null ;;
  image)  [[ "${{2:-}}" == inspect ]] || exit 0
          ref="${{@: -1}}"
          [[ -f "{tmp}/labels/$(key "$ref")" ]] && {{ cat "{tmp}/labels/$(key "$ref")"; exit 0; }}
          case "$ref" in localhost/cc-graphiti:1.0.2-anthropic|localhost/cc-sandbox:1|localhost/cc-crawler:1) exit 0 ;; esac
          grep -qxF -- "$ref" "{tmp}/built" 2>/dev/null && exit 0
          exit 125 ;;
  build)  label=""; ref=""; shift
          while (( $# )); do
            case "$1" in --label) label="${{2#*=}}"; shift ;; -t) ref="$2"; shift ;; esac
            shift
          done
          printf '%s\\n' "$label" > "{tmp}/labels/$(key "$ref")"
          grep -qxF -- "$ref" "{tmp}/built" 2>/dev/null || printf '%s\\n' "$ref" >> "{tmp}/built" ;;
  untag)  grep -vxF -- "$3" "{tmp}/built" > "{tmp}/built.new" 2>/dev/null; mv "{tmp}/built.new" "{tmp}/built"
          rm -f "{tmp}/labels/$(key "$3")" ;;
  pull)   for a in "$@"; do grep -qxF -- "$a" "{tmp}/flags/pull-fails" 2>/dev/null && exit 125; done ;;
  exec)   case "$*" in
            *pg_dump*) echo "-- a spine dump" ;;
            *psql*)    cat >/dev/null; echo schema >> "{calls}" ;;
          esac ;;
esac
exit 0
''')
    # uv: `uv venv <path>` makes a venv whose python exits 0; `uv pip` resolves.
    _exe(b / "uv", f'''#!/usr/bin/env bash
echo "uv $*" >> "{log}"
if [[ "$1" == venv ]]; then
  p="${{@: -1}}"; mkdir -p "$p/bin"
  printf '#!/usr/bin/env bash\\nexit 0\\n' > "$p/bin/python"; chmod +x "$p/bin/python"
fi
exit 0
''')
    _exe(b / "npm", f'''#!/usr/bin/env bash
echo "npm $* (in $PWD)" >> "{log}"
[[ "$1" == ci ]] && mkdir -p node_modules
exit 0
''')
    _exe(b / "node", "#!/usr/bin/env bash\necho v22.11.0\n")
    return b


# Appended to the copy's setup.sh before `main "$@"`: the phases that need a
# running stack become stubs that record their name; `fetch` stays REAL and is
# only wrapped, so the calls file says whether the staged run or the post-merge
# one ran it. Probes read true — except the two the catalog probe IS.
def _stub_tail(calls: Path, flags: Path) -> str:
    phases = ["check", "machine", "stack", "llm", "app", "verify", "test", "boot", "demo"]
    out = [
        f'__CALLS="{calls}"; __FLAGS="{flags}"',
        'for __f in $(compgen -A function p_); do',
        '  case "$__f" in p_models_json|p_catalog_aliases) continue ;; esac',
        '  eval "$__f() { return 0; }"',
        'done',
        'eval "$(declare -f phase_fetch | sed \'1s/^phase_fetch/__real_phase_fetch/\')"',
        'phase_fetch() { if (( STAGED )); then echo staged-fetch; else echo fetch; fi >>"$__CALLS"; '
        '__real_phase_fetch "$@"; }',
    ]
    for p in phases:
        out.append(
            f'phase_{p}() {{ echo {p} >>"$__CALLS"; '
            f'if [[ -f "$__FLAGS/ua-{p}" ]]; then useraction "$(cat "$__FLAGS/ua-{p}")" "stub: waiting on you"; return 3; fi; '
            f'if [[ -f "$__FLAGS/fail-{p}" ]]; then fail "$(cat "$__FLAGS/fail-{p}")" "stub: told to fail"; return 1; fi; '
            f'pass "{p}-stub" "ran"; }}'
        )
    return "\n".join(out) + "\n"


class Dep:
    """A deployment: the repo, its state dir, and what the stubs record."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.repo = tmp / "dep"
        self.state = tmp / "state"
        self.calls = tmp / "calls.txt"
        self.log = tmp / "stub.log"
        self.flags = tmp / "flags"
        self.cfg = tmp / "stub.json"

    # ── reading ──
    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True,
                              text=True, check=True).stdout.strip()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def called(self) -> list[str]:
        return self.calls.read_text().split() if self.calls.exists() else []

    @property
    def ledger(self) -> Path:
        return self.state / "ledger.tsv"

    def ledger_rows(self) -> dict[str, list[str]]:
        return {l.split("\t")[0]: l.split("\t") for l in self.ledger.read_text().splitlines()
                if l and not l.startswith("#")}

    def stage_gone(self) -> bool:
        worktrees = [l for l in self.git("worktree", "list", "--porcelain").splitlines()
                     if l.startswith("worktree ")]
        return not (self.state / "stage").exists() and len(worktrees) == 1

    # ── acting ──
    def configure(self, *, missing: tuple[str, ...] = (), models: tuple[str, ...] | None = None):
        locks, digests = {}, {}
        for line in (self.repo / "deploy" / "single" / "images.txt").read_text().splitlines():
            parts = line.split("#", 1)[0].split()
            if len(parts) == 6:
                _key, path, _cons, lock, dig, _comp = parts
                locks[path] = lock
                if dig != "-":
                    digests[f"{path}:{lock}"] = dig
        if models is None:
            models = ("cc-default", "graphiti-llm", "cc-embedding", "gpt-4.1-nano")
        self.cfg.write_text(json.dumps({"log": str(self.log), "missing": list(missing),
                                        "models": list(models), "locks": locks,
                                        "digests": digests}))

    def env(self, **extra: str) -> dict[str, str]:
        e = {"PATH": os.pathsep.join([str(self.tmp / "bin"), "/usr/bin", "/bin"]),
             "HOME": str(self.tmp / "home"), "XDG_STATE_HOME": str(self.tmp / "home" / "state"),
             "STUB_CFG": str(self.cfg), "CC_BACKUP_DIR": str(self.tmp / "backups"),
             "LANG": "C.UTF-8"}
        e.update(extra)
        return e

    def update(self, *args: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([_bash(), str(self.repo / "deploy" / "single" / "update.sh"), *args],
                              cwd=self.repo / "deploy" / "single", capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=300, env=self.env(**extra))

    def fill_ledger(self, version: str = OLD) -> None:
        """Every manifest row `done` at the installed release: an install that
        completed under the ledger."""
        rows = ["# ledger.tsv — prepared by the test"]
        for line in (self.repo / "deploy" / "single" / "steps.tsv").read_text().splitlines():
            if line and not line.startswith("#"):
                phase, step = line.split("\t")[:2]
                rows.append(f"{phase}/{step}\tdone\t{version}\t2026-10-01T00:00:00Z\tnone\t")
        self.ledger.write_text("\n".join(rows) + "\n")


@pytest.fixture
def dep(tmp_path: Path) -> Dep:
    d = Dep(tmp_path)
    repo = d.repo
    repo.mkdir()
    d.state.mkdir()
    d.flags.mkdir()
    (tmp_path / "home").mkdir()
    shutil.copytree(ROOT / "deploy", repo / "deploy", ignore=_DEBRIS)
    for f in (".env.example", ".gitignore", "pyproject.toml", "requirements.lock"):
        shutil.copy2(ROOT / f, repo / f)
    (repo / "central_command" / "db").mkdir(parents=True)
    (repo / "central_command" / "__init__.py").write_text("")
    shutil.copy2(ROOT / "central_command" / "db" / "schema.sql",
                 repo / "central_command" / "db" / "schema.sql")
    (repo / "web").mkdir()
    (repo / "web" / "package.json").write_text('{"name": "web", "version": "0.0.0"}\n')
    (repo / "web" / "package-lock.json").write_text('{"name": "web", "lockfileVersion": 3}\n')
    (repo / "VERSION").write_text(f"version={OLD}\nmin_upgrade_from=2.0.0\n")
    # A tracked file the sparse stage must NOT check out: docs/vendor stands in
    # for the 47k files the list exists to keep off a Windows disk.
    (repo / "docs" / "vendor").mkdir(parents=True)
    (repo / "docs" / "vendor" / "big.txt").write_text("vendored\n")
    setup = repo / "deploy" / "single" / "setup.sh"
    text = setup.read_text(encoding="utf-8")
    tail = 'main "$@"'
    assert text.rstrip().endswith(tail)
    setup.write_text(text.rstrip()[: -len(tail)] + _stub_tail(d.calls, d.flags) + tail + "\n",
                     encoding="utf-8")
    shutil.copy2(ROOT / ".env.example", repo / ".env")
    _set(repo / ".env", {
        "CC_STATE_DIR": str(d.state), "CC_API_PORT": str(_free_port()),
        "CC_ENABLE_SANDBOX": "0", "CC_ENABLE_CRAWLER": "0", "CC_ENABLE_SPEECH": "0",
        "CC_ENABLE_N8N": "0", "CC_LLM_PROXY_ADMIN_KEY": "sk-test-admin-0000000000",
    })
    _stub_bin(tmp_path, d.log, d.calls)
    d.configure()

    def git(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)

    git("init", "-q", ".")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("checkout", "-qb", "local")
    git("add", "-A")
    git("commit", "-qm", "baseline")
    git("branch", "upstream")
    git("checkout", "-q", "upstream")
    (repo / "VERSION").write_text(f"version={NEW}\nmin_upgrade_from=2.0.0\n")
    git("commit", "-qam", f"import v{NEW}")
    git("checkout", "-q", "local")
    return d


def _untouched(d: Dep, head: str, env_bytes: bytes, ledger_bytes: bytes | None) -> None:
    assert d.head() == head, "local moved"
    assert d.git("status", "--porcelain", "--untracked-files=no") == "", "the tree changed"
    assert (d.repo / ".env").read_bytes() == env_bytes, ".env changed"
    if ledger_bytes is None:
        assert not d.ledger.exists(), "the staged run created a ledger"
    else:
        assert d.ledger.read_bytes() == ledger_bytes, "the ledger changed"
    assert not d.git("tag", "-l", "pre-update-*"), "a checkpoint was taken"
    assert not (d.state / "installed.manifest").exists(), "the real manifest was written"
    assert "schema" not in d.called(), "the schema ran"
    assert d.stage_gone(), "the staged worktree was left behind"


# ── before the merge ────────────────────────────────────────────────────────


def test_a_mirror_missing_one_tag_stops_before_the_merge_with_nothing_changed(dep: Dep):
    """The record's acceptance scenario, verbatim."""
    dep.fill_ledger()
    dep.configure(missing=("library/neo4j",))
    head, env, led = dep.head(), (dep.repo / ".env").read_bytes(), dep.ledger.read_bytes()

    r = dep.update("apply")

    assert r.returncode == 3, r.stdout + r.stderr
    # The seam is named — by the resolver, for the one image, and by the phase.
    assert "FAIL image-neo4j:" in r.stdout and "CC_REGISTRY_DOCKERIO" in r.stdout, r.stdout
    assert "USERACTION resolve-images:" in r.stdout, r.stdout
    summary = [l for l in r.stdout.splitlines() if l.startswith("USERACTION acquire:")]
    assert summary and "NOTHING was changed" in summary[0], r.stdout
    assert dep.called() == ["staged-fetch"], dep.called()
    _untouched(dep, head, env, led)
    # The staged run never even looked at the API, the tree's docs/vendor, or a
    # second ledger: it ran from the sparse worktree, nested under the lock.
    assert not (dep.state / "run.lock").exists()


def test_a_failed_staged_artifact_is_a_fail_and_still_changes_nothing(dep: Dep):
    dep.fill_ledger()
    (dep.flags / "pull-fails").write_text("docker.io/library/redis:7-alpine\n")
    head, env, led = dep.head(), (dep.repo / ".env").read_bytes(), dep.ledger.read_bytes()

    r = dep.update("apply")

    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL image-redis:" in r.stdout, r.stdout
    assert any(l.startswith("FAIL acquire:") and "NOTHING was changed" in l
               for l in r.stdout.splitlines()), r.stdout
    _untouched(dep, head, env, led)


def test_a_missing_required_alias_is_a_useraction_with_the_tree_untouched(dep: Dep):
    dep.fill_ledger()
    dep.configure(models=("cc-default", "graphiti-llm", "cc-embedding"))
    head, env, led = dep.head(), (dep.repo / ".env").read_bytes(), dep.ledger.read_bytes()

    r = dep.update("apply")

    assert r.returncode == 3, r.stdout + r.stderr
    probe = [l for l in r.stdout.splitlines() if l.startswith("USERACTION catalog-probe:")]
    assert probe, r.stdout
    assert "gpt-4.1-nano" in probe[0] and "LiteLLM UI" in probe[0], probe[0]
    assert "CC_LLM_UPSTREAM_MODEL_GPT_4_1_NANO" in probe[0], probe[0]
    # The fetch half passed: both halves always run, so one pass names every seam.
    assert "PASS resolve-images:" in r.stdout
    _untouched(dep, head, env, led)


def test_a_declared_missing_alias_does_not_stop_the_update(dep: Dep):
    """A release that ADDS an alias must stay appliable by a declared-catalog
    install: its row only appears when the post-merge llm phase registers it."""
    dep.fill_ledger()
    dep.configure(models=("cc-default", "graphiti-llm", "cc-embedding"))
    _set(dep.repo / ".env", {"CC_LLM_UPSTREAM_BASE_URL": "http://llm.example.com/v1",
                             "CC_LLM_UPSTREAM_API_KEY": "upstream-test-key",
                             "CC_LLM_UPSTREAM_MODEL_GPT_4_1_NANO": "some-small-model"})
    r = dep.update("apply", CC_UPDATE_DRIVEN="1")
    assert any(l.startswith("PASS catalog-probe:") and "gpt-4.1-nano" in l
               for l in r.stdout.splitlines()), r.stdout
    assert "PASS acquire:" in r.stdout, r.stdout
    # It merged. (What happens after is the stub llm's business: it registers
    # nothing, so the real llm/catalog probe then reads the alias absent.)
    assert dep.head() == dep.git("rev-parse", "upstream"), r.stdout


# ── the merge and after ─────────────────────────────────────────────────────


def test_after_a_clean_acquisition_it_merges_and_runs_the_post_merge_phases_in_order(dep: Dep):
    dep.fill_ledger()
    r = dep.update("apply", CC_UPDATE_DRIVEN="1")

    assert r.returncode in (0, 2), r.stdout + r.stderr
    assert dep.head() == dep.git("rev-parse", "upstream"), "local was not fast-forwarded"
    assert dep.called() == ["staged-fetch", "schema", "fetch", "llm", "stack", "app", "verify"], \
        dep.called()
    assert "PASS restart:" in r.stdout
    assert dep.stage_gone()
    # The post-merge fetch wrote the REAL refs and manifest; the rows the
    # update ran are recorded at the new release, the rest left where they were.
    assert (dep.state / "installed.manifest").exists()
    assert "CC_IMG_NEO4J=docker.io/library/neo4j:5.26.2" in (dep.repo / ".env").read_text()
    rows = dep.ledger_rows()
    # stack among them since v2.57.0: the update DEPLOYS what it acquired.
    for step in ("fetch/resolve-images", "llm/catalog", "stack/up-stack", "app/mint-key",
                 "verify/selfcheck"):
        assert rows[step][1:3] == ["done", NEW], rows[step]
    assert rows["boot/boot-api"][2] == OLD
    # The stage was sparse: what the acquisition reads, never docs/vendor.
    stage_cmd = [l for l in dep.log.read_text().splitlines() if l.startswith("npm ci")]
    assert any("/stage/tree/web" in l for l in stage_cmd), stage_cmd


GRAPHITI = "localhost/cc-graphiti:1.0.2-anthropic"
ASIDE = GRAPHITI + "-staged"


def _label_file(dep: Dep, ref: str) -> Path:
    return dep.tmp / "labels" / ref.replace("/", "_").replace(":", "_")


def _builds(dep: Dep) -> list[tuple[str, str]]:
    """(tag, label) per `podman build`, in the order they ran."""
    out = []
    for line in dep.log.read_text().splitlines():
        if line.startswith("podman build"):
            tag = line.split(" -t ", 1)[1].split()[0]
            label = line.split(" --label cc.build-inputs=", 1)[1].split()[0]
            out.append((tag, label))
    return out


def test_a_stop_after_the_staged_build_leaves_the_live_image_tag_alone(dep: Dep):
    """D5's promise, for the image store: the running deployment's graphiti tag
    stays on the image its containers use when apply stops before the merge —
    here at the catalog probe, AFTER the staged fetch built the new image."""
    dep.fill_ledger()
    old = "0" * 64
    _label_file(dep, GRAPHITI).write_text(old + "\n")      # the old release's build
    dep.configure(models=("cc-default", "graphiti-llm", "cc-embedding"))
    head, env, led = dep.head(), (dep.repo / ".env").read_bytes(), dep.ledger.read_bytes()

    r = dep.update("apply")

    assert r.returncode == 3, r.stdout + r.stderr
    assert any(l.startswith(f"PASS image-graphiti: {ASIDE} built") for l in r.stdout.splitlines()), r.stdout
    builds = _builds(dep)
    assert [t for t, _ in builds] == [ASIDE], builds
    assert _label_file(dep, GRAPHITI).read_text().strip() == old, (
        "the staged build moved the LIVE tag: a stop before the merge would leave new "
        "containers and sandbox sessions on the NEW image under the OLD code"
    )
    assert _label_file(dep, ASIDE).read_text().strip() == builds[0][1]
    assert "podman untag" not in dep.log.read_text()
    _untouched(dep, head, env, led)


def test_the_post_merge_fetch_builds_the_live_tag_from_the_same_inputs_and_drops_the_aside(dep: Dep):
    dep.fill_ledger()
    _label_file(dep, GRAPHITI).write_text("0" * 64 + "\n")

    r = dep.update("apply", CC_UPDATE_DRIVEN="1")

    assert r.returncode in (0, 2), r.stdout + r.stderr
    builds = _builds(dep)
    # Staged aside first, then the live tag after the merge — with the SAME
    # inputs hash (the tag is not an input), which is what makes the second
    # build a cache hit on a real engine.
    assert [t for t, _ in builds] == [ASIDE, GRAPHITI], builds
    assert builds[0][1] == builds[1][1]
    assert _label_file(dep, GRAPHITI).read_text().strip() == builds[1][1]
    log = dep.log.read_text().splitlines()
    live_build = next(i for i, l in enumerate(log) if l.startswith("podman build") and f"-t {GRAPHITI} " in l)
    untag = [i for i, l in enumerate(log) if l == f"podman untag {ASIDE} {ASIDE}"]
    assert untag and untag[0] > live_build, "the aside tag was not dropped after the live build"
    assert ASIDE not in (dep.tmp / "built").read_text().split()


def test_the_staged_checkout_is_sparse(dep: Dep, tmp_path: Path):
    """Observed from inside the staged run: a probe in the stub tail records
    what the worktree holds while it exists."""
    dep.fill_ledger()
    setup = dep.repo / "deploy" / "single" / "setup.sh"
    seen = tmp_path / "seen.txt"
    text = setup.read_text().replace(
        'phase_fetch() { if (( STAGED ));',
        f'phase_fetch() {{ (( STAGED )) && ls -A "$REPO_ROOT" "$REPO_ROOT/docs" > "{seen}" 2>&1; if (( STAGED ));')
    setup.write_text(text)
    subprocess.run(["git", "-C", str(dep.repo), "commit", "-qam", "probe"], check=True)
    subprocess.run(["git", "-C", str(dep.repo), "branch", "-qf", "upstream", "local"], check=True)
    subprocess.run(["git", "-C", str(dep.repo), "checkout", "-q", "upstream"], check=True)
    (dep.repo / "VERSION").write_text(f"version={NEW}\n")
    subprocess.run(["git", "-C", str(dep.repo), "commit", "-qam", "bump"], check=True)
    subprocess.run(["git", "-C", str(dep.repo), "checkout", "-q", "local"], check=True)
    dep.configure(missing=("library/neo4j",))       # stop after the stage: quicker

    dep.update("apply")

    listing = seen.read_text()
    assert "deploy" in listing and "central_command" in listing and "VERSION" in listing, listing
    assert "No such file" in listing, f"docs/ was checked out into the stage:\n{listing}"


def test_a_pause_after_the_merge_stops_there_and_app_and_verify_do_not_run(dep: Dep):
    dep.fill_ledger()
    (dep.flags / "ua-llm").write_text("catalog-filled")

    r = dep.update("apply", CC_UPDATE_DRIVEN="1")

    assert r.returncode == 3, r.stdout + r.stderr
    assert dep.called() == ["staged-fetch", "schema", "fetch", "llm"], dep.called()
    ua = [l for l in r.stdout.splitlines() if l.startswith("USERACTION llm:")]
    assert ua and "./update.sh apply" in ua[0], r.stdout
    assert "PASS restart:" not in r.stdout
    # ...and the ledger setup.sh printed shows where.
    assert dep.ledger_rows()["llm/catalog-filled"][1] == "gate"
    assert "LEDGER " in r.stdout


def test_a_failed_phase_after_the_merge_is_a_fail(dep: Dep):
    dep.fill_ledger()
    (dep.flags / "fail-app").write_text("install")
    r = dep.update("apply", CC_UPDATE_DRIVEN="1")
    assert r.returncode == 1, r.stdout + r.stderr
    assert dep.called()[-1] == "app"
    assert any(l.startswith("FAIL app:") for l in r.stdout.splitlines()), r.stdout


# ── the pre-ledger install ──────────────────────────────────────────────────


def test_a_pre_ledger_install_is_asked_to_run_setup_once(dep: Dep):
    """No ledger at all: a deployment installed before v2.55.0. After the merge
    and the schema, ONE USERACTION — never the ledger gate's exit 1."""
    r = dep.update("apply", CC_UPDATE_DRIVEN="1")

    assert r.returncode == 3, r.stdout + r.stderr
    ua = [l for l in r.stdout.splitlines() if l.startswith("USERACTION ")]
    assert len(ua) == 1 and ua[0].startswith("USERACTION ledger-adopt:"), ua
    assert "predates the install ledger" in ua[0] and "./setup.sh once" in ua[0], ua[0]
    assert "FAIL" not in r.stdout, r.stdout
    assert dep.head() == dep.git("rev-parse", "upstream"), "the update is merged"
    assert dep.called() == ["staged-fetch", "schema"], dep.called()
    # Neither the staged run nor update.sh created a ledger: ./setup.sh will.
    assert not dep.ledger.exists()


def _run_runner(dep: Dep) -> tuple[subprocess.CompletedProcess, dict]:
    upd = dep.state / "update"
    upd.mkdir(exist_ok=True)
    runner = upd / "run.sh"
    shutil.copyfile(ROOT / "deploy" / "single" / "update-run.sh", runner)
    # The runner's own health waits are a minute and a half; nothing here may
    # wait on them (the API never answers in this rig).
    runner.write_text(runner.read_text().replace("seq 1 90", "seq 1 1").replace("seq 1 30", "seq 1 1"))
    r = subprocess.run([_bash(), str(runner), NEW, str(dep.repo / "deploy" / "single")],
                       capture_output=True, text=True, timeout=300,
                       env=dep.env(CC_UPDATE_DIR=str(upd)))
    return r, json.loads((upd / "status.json").read_text())


def test_update_run_does_not_roll_back_the_adoption_pause(dep: Dep):
    r, status = _run_runner(dep)
    assert r.returncode == 3, r.stdout + r.stderr
    assert dep.head() == dep.git("rev-parse", "upstream"), "rolled back"
    assert status["state"] == "failed" and status["phase"] == "operator-action", status
    assert "predates the install ledger" in status["error"], status
    assert "./setup.sh" in status["error"], status
    log = (dep.state / "update" / "apply.log").read_text()
    assert "update.sh rollback" not in log and "PASS reset:" not in log


def test_update_run_does_not_roll_back_a_stop_before_the_merge(dep: Dep):
    """Before v2.57.0 any apply exit 1 ran `update.sh rollback`, which resets
    to the NEWEST pre-update-* tag — an EARLIER update's. A stop before the
    merge changed nothing, so that rollback was a downgrade."""
    dep.fill_ledger()
    subprocess.run(["git", "-C", str(dep.repo), "tag", "pre-update-20200101T000000Z",
                    "local"], check=True)
    old_tags = dep.git("tag", "-l")
    (dep.flags / "pull-fails").write_text("docker.io/library/redis:7-alpine\n")
    head = dep.head()

    r, status = _run_runner(dep)

    assert r.returncode == 1, r.stdout + r.stderr
    assert dep.head() == head
    assert dep.git("tag", "-l") == old_tags
    assert status["state"] == "failed" and status["phase"] == "apply", status
    assert "NOTHING was changed" in status["error"] and "image-redis" in status["error"], status


def test_update_run_reports_a_pre_merge_pause_as_needing_the_operator(dep: Dep):
    dep.fill_ledger()
    dep.configure(models=("cc-default",))
    r, status = _run_runner(dep)
    assert r.returncode == 3, r.stdout + r.stderr
    assert status["phase"] == "operator-action", status
    # The seam line itself, not update.sh's "the line(s) above" summary.
    assert status["error"].startswith("USERACTION catalog-probe:"), status
    assert "NOTHING was changed" in status["error"], status


# ── setup.sh: `acquire` is staged-only, and a staged run does nothing else ──


def test_acquire_is_refused_on_an_install_and_a_staged_run_refuses_everything_else(dep: Dep):
    single = dep.repo / "deploy" / "single"
    a = subprocess.run([_bash(), "setup.sh", "acquire"], cwd=single, capture_output=True,
                       text=True, timeout=120, env=dep.env())
    assert a.returncode == 1 and "FAIL acquire:" in a.stdout, a.stdout + a.stderr
    s = subprocess.run([_bash(), "setup.sh", "llm"], cwd=single, capture_output=True, text=True,
                       timeout=120, env=dep.env(CC_STAGED_FOR=str(dep.repo)))
    assert s.returncode == 1 and "FAIL staged:" in s.stdout, s.stdout + s.stderr
    assert "llm" not in dep.called()


# ── phase_test: the ledger, not a healthy API, makes it once per release ────


class _Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — the stdlib's name
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, *a):  # silence
        pass


@pytest.fixture
def api_up():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Health)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()


def _plain_tree(tmp_path: Path) -> Path:
    """test_single_driver_ledger.py's tree: a temp copy of what the driver reads."""
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copytree(ROOT / "deploy", repo / "deploy", ignore=_DEBRIS)
    for f in (".env.example", "VERSION", ".gitignore"):
        shutil.copy2(ROOT / f, repo / f)
    shutil.copy2(ROOT / ".env.example", repo / ".env")
    (repo / "central_command" / "db").mkdir(parents=True)
    shutil.copy2(ROOT / "central_command" / "db" / "schema.sql",
                 repo / "central_command" / "db" / "schema.sql")
    (tmp_path / "home").mkdir()
    (tmp_path / "state").mkdir()
    _set(repo / ".env", {"CC_STATE_DIR": str(tmp_path / "state")})
    return repo


def _version() -> str:
    for line in (ROOT / "VERSION").read_text().splitlines():
        if line.startswith("version="):
            return line.split("=", 1)[1].strip()
    raise AssertionError("no version")


def _ledger(tmp_path: Path, steps: list[str]) -> None:
    rows = ["# prepared"] + [f"{s}\tdone\t{_version()}\t2026-10-01T00:00:00Z\tnone\t" for s in steps]
    (tmp_path / "state" / "ledger.tsv").write_text("\n".join(rows) + "\n")


def _setup(repo: Path, *args: str, path_prefix: str | None = None):
    env = dict(os.environ)
    home = repo.parent / "home"
    env.update(HOME=str(home), XDG_STATE_HOME=str(home / "state"), CC_VERIFY_MAX_WAIT="1")
    for stale in ("CC_STATE_DIR", "CC_ENABLE_SPEECH", "CC_SETUP_UNLEDGERED", "CC_RUN_LOCK_PID",
                  "CC_LLM_PROXY_ADMIN_KEY", "CC_LLM_API_KEY", "CC_EXECUTOR_MODE", "CC_STAGED_FOR"):
        env.pop(stale, None)
    if path_prefix:
        env["PATH"] = path_prefix + os.pathsep + env.get("PATH", "")
    return subprocess.run([_bash(), "setup.sh", *args], cwd=repo / "deploy" / "single",
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300,
                          env=env)


def _rows(tmp_path: Path) -> dict[str, list[str]]:
    led = (tmp_path / "state" / "ledger.tsv").read_text()
    return {l.split("\t")[0]: l.split("\t") for l in led.splitlines() if l and not l.startswith("#")}


def test_the_suite_runs_even_while_the_api_answers(tmp_path: Path, api_up: int):
    repo = _plain_tree(tmp_path)
    _set(repo / ".env", {"CC_API_PORT": str(api_up)})
    _ledger(tmp_path, ["check/tree-pristine", "app/venv", "app/install"])
    ran = tmp_path / "suite-ran"
    py = repo / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    _exe(py, f'#!/usr/bin/env bash\necho "$*" >> "{ran}"\nexit "$(cat "{tmp_path}/suite-rc" 2>/dev/null || echo 0)"\n')

    r = _setup(repo, "test")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "-m pytest -q" in ran.read_text(), "the suite did not run under a healthy API"
    assert "PASS test: the offline suite is green" in r.stdout, r.stdout
    assert "skipped" not in r.stdout
    assert _rows(tmp_path)["test/test"][1] == "done"

    (tmp_path / "suite-rc").write_text("1")
    red = _setup(repo, "test")
    assert red.returncode == 1, red.stdout + red.stderr
    assert "FAIL test:" in red.stdout
    assert _rows(tmp_path)["test/test"][1] == "failed"


# ── phase_app: a failed `npm ci` is the cockpit step's FAIL ─────────────────


def test_a_failing_npm_ci_fails_the_cockpit_step_even_when_the_build_would_succeed(tmp_path: Path):
    repo = _plain_tree(tmp_path)
    _set(repo / ".env", {"CC_LLM_API_KEY": "sk-already-minted-0000000000", "CC_LLM_PROXY_ADMIN_KEY": "",
                         "CC_EXECUTOR_MODE": "dry_run"})
    _ledger(tmp_path, ["check/tree-pristine", "fetch/venv", "fetch/cockpit", "llm/secrets",
                       "llm/litellm-live"])
    (repo / "web").mkdir()
    py = repo / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    _exe(py, "#!/usr/bin/env bash\nexit 0\n")
    calls = tmp_path / "npm.log"
    b = tmp_path / "bin"
    b.mkdir()
    _exe(b / "uv", "#!/usr/bin/env bash\nexit 0\n")
    _exe(b / "node", "#!/usr/bin/env bash\necho v22.11.0\n")
    # `npm ci` fails; `npm run build` would succeed and leave a complete build.
    _exe(b / "npm", f'''#!/usr/bin/env bash
echo "npm $*" >> "{calls}"
[[ "$1" == ci ]] && exit 1
mkdir -p dist server-dist && touch server-dist/index.js
exit 0
''')

    r = _setup(repo, "app", path_prefix=str(b))

    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL cockpit:" in r.stdout, r.stdout
    assert "PASS cockpit: cockpit built" not in r.stdout
    assert calls.read_text().split("\n")[0] == "npm ci"
    assert "npm run build" not in calls.read_text(), "the build ran after a failed npm ci"
    assert _rows(tmp_path)["app/cockpit"][1] == "failed"


# ── machine_sh: its stderr reaches the log, a proxy value never does ────────


def _lift(name: str) -> str:
    """One function's source, out of setup.sh (the compose-floor test's way)."""
    src = (ROOT / "deploy" / "single" / "setup.sh").read_text(encoding="utf-8")
    start = src.index(f"\n{name}() {{") + 1
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def test_machine_sh_stderr_is_logged_with_the_proxy_value_removed(tmp_path: Path):
    """D5: a failed write into the podman machine used to leave no trace (its
    stderr went to /dev/null). It goes to the log now — and the one proxy
    VALUE that could appear there (a curl error names the proxy host; a tool
    may echo its environment) is stripped, credentials and host alike."""
    log = tmp_path / "log.txt"
    script = (
        f'logline() {{ printf "%s\\n" "$*" >> "{log}"; }}\n'
        + _lift("machine_sh_log")
        + '\nCC_PROXY="http://agent:s3cret@proxy.corp.example:3128"\n'
        + 'machine_sh_log "sudo tee /etc/x" 1 "curl: (7) Failed to connect to proxy.corp.example '
          'port 3128; via http://agent:s3cret@proxy.corp.example:3128; '
          'other http://bob:pw@else.example/ path"\n'
        + 'machine_sh_log "true" 0 ""\n'
    )
    r = subprocess.run([_bash(), "-c", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    text = log.read_text()
    assert text.count("\n") == 1, "an empty stderr writes nothing"
    assert "machine-ssh: `sudo tee /etc/x` exited 1" in text, text
    for leaked in ("s3cret", "agent", "proxy.corp.example", "bob:pw"):
        assert leaked not in text, f"{leaked!r} reached the log: {text}"
    assert "[REDACTED:CC_PROXY]" in text and "[REDACTED:url-userinfo]" in text, text
