"""A changed Dockerfile rebuilds: the local images carry their inputs hash (v2.57.0, P3).

The three locally built images have FIXED tags (`localhost/cc-sandbox:1`,
`cc-crawler:1`, `cc-graphiti:<CC_GRAPHITI_TAG>`), and until this release the
`fetch` phase skipped the build whenever the tag existed and its probes read
"tag present" as done. So a release that changed a Dockerfile or anything in
its build context never rebuilt on update and the OLD image kept running —
against `.claude/rules/deploy-single.md`'s "our own (locally built) images stay
exact — the release is one tested unit".

Now each build writes a deterministic hash of its inputs as the image label
`cc.build-inputs` (`deploy/env-lib.sh`'s `cc_build_inputs_hash`; the build
script answers what the hash SHOULD be with `--inputs-hash`), `fetch_local`
rebuilds when the label is missing or different, and the probes are true only
when it matches.

Two kinds of test here:

* the hash, as a PURE function — sourced out of env-lib.sh and run on files in
  a temp directory;
* the REAL setup.sh functions and the REAL build scripts in a temp copy of the
  tree, against a stub `podman` that keeps an image store in a directory
  (`image inspect` prints the label, `build` records the `--label` it was
  given). A hook appended before `main "$@"` calls one named function — the
  same append-a-tail pattern tests/test_single_driver_ledger.py uses. No real
  podman, no network, nothing in the checkout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENV_LIB = ROOT / "deploy" / "env-lib.sh"
LABEL = "cc.build-inputs"

_DEBRIS = shutil.ignore_patterns(
    "NUL", "nul", "CON", "con", "AUX", "aux", "PRN", "prn",
    ".env", ".env.*", "__pycache__", "*.pyc",
)


def _bash() -> str:
    from central_command.api.update import _bash as resolve

    b = resolve()
    if not b:
        pytest.skip("no usable bash on this host")
    return b


# ── the hash, as a pure function ─────────────────────────────────────────────

def _hash(ca: str, args: str, *sources: Path, cwd: Path) -> subprocess.CompletedProcess:
    script = (f'. "{ENV_LIB.as_posix()}"; '
              'cc_build_inputs_hash "$1" "$2" "${@:3}"')
    return subprocess.run([_bash(), "-c", script, "hash", ca, args,
                           *[s.as_posix() for s in sources]],
                          capture_output=True, text=True, cwd=cwd)


def _h(ca: str, args: str, *sources: Path, cwd: Path) -> str:
    r = _hash(ca, args, *sources, cwd=cwd)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(r.stdout) == 64 and all(c in "0123456789abcdef" for c in r.stdout), r.stdout
    return r.stdout


def _ctx(base: Path, files: dict[str, bytes]) -> Path:
    base.mkdir(parents=True)
    for rel, data in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return base


CTX = {
    "Dockerfile": b"FROM ${CC_IMG_PYTHON}\nRUN echo one\nCOPY service.py /app/\n",
    "service.py": b"print('hi')\n",
    "patches/a.patch": b"--- a\n+++ b\n",
}
ARGS = "CC_IMG_PYTHON=docker.io/library/python:3.12-slim-bookworm\nPIP_INDEX_URL=\n"


def test_same_inputs_give_the_same_hash(tmp_path):
    a = _ctx(tmp_path / "a", CTX)
    b = _ctx(tmp_path / "b", CTX)
    assert _h("", ARGS, a, cwd=tmp_path) == _h("", ARGS, a, cwd=tmp_path)
    # The PATH of the context is not an input: two checkouts, one hash.
    assert _h("", ARGS, a, cwd=tmp_path) == _h("", ARGS, b, cwd=tmp_path)


def test_one_byte_of_the_dockerfile_changes_the_hash(tmp_path):
    a = _ctx(tmp_path / "a", CTX)
    b = _ctx(tmp_path / "b", {**CTX, "Dockerfile": CTX["Dockerfile"].replace(b"one", b"onf")})
    assert _h("", ARGS, a, cwd=tmp_path) != _h("", ARGS, b, cwd=tmp_path)
    # ...and so does a file deeper in the context (a graphiti patch).
    c = _ctx(tmp_path / "c", {**CTX, "patches/a.patch": b"--- a\n+++ c\n"})
    assert _h("", ARGS, a, cwd=tmp_path) != _h("", ARGS, c, cwd=tmp_path)
    # ...and a file that only exists in one of them.
    d = _ctx(tmp_path / "d", {**CTX, "extra.txt": b"x\n"})
    assert _h("", ARGS, a, cwd=tmp_path) != _h("", ARGS, d, cwd=tmp_path)


def test_file_and_source_order_are_irrelevant(tmp_path):
    # Created in reverse order: whatever order `find` walks them in, the
    # listing is sorted before it is hashed.
    a = _ctx(tmp_path / "a", CTX)
    b = _ctx(tmp_path / "b", dict(reversed(list(CTX.items()))))
    assert _h("", ARGS, a, cwd=tmp_path) == _h("", ARGS, b, cwd=tmp_path)
    # A directory plus a file is the staged UNION, whichever comes first.
    d = _ctx(tmp_path / "d", {"Dockerfile": CTX["Dockerfile"]})
    f = tmp_path / "f" / "service.py"
    f.parent.mkdir()
    f.write_bytes(CTX["service.py"])
    assert _h("", ARGS, d, f, cwd=tmp_path) == _h("", ARGS, f, d, cwd=tmp_path)
    # The build arguments are a set, not a sequence.
    swapped = "\n".join(reversed(ARGS.strip().splitlines())) + "\n"
    assert _h("", ARGS, a, cwd=tmp_path) == _h("", swapped, a, cwd=tmp_path)


def test_crlf_and_lf_checkouts_hash_the_same(tmp_path):
    a = _ctx(tmp_path / "a", CTX)
    b = _ctx(tmp_path / "b", {k: v.replace(b"\n", b"\r\n") for k, v in CTX.items()})
    assert (b / "Dockerfile").read_bytes() != (a / "Dockerfile").read_bytes()
    assert _h("", ARGS, a, cwd=tmp_path) == _h("", ARGS, b, cwd=tmp_path)
    # A single Dockerfile source (the sandbox's shape) too.
    assert _h("", ARGS, a / "Dockerfile", cwd=tmp_path) == _h("", ARGS, b / "Dockerfile", cwd=tmp_path)


def test_the_build_args_and_the_ca_are_inputs(tmp_path):
    a = _ctx(tmp_path / "a", CTX)
    base = _h("", ARGS, a, cwd=tmp_path)
    # A different resolved base ref, or a mirror seam, is a different image.
    assert base != _h("", ARGS.replace("3.12-slim", "3.12.8-slim"), a, cwd=tmp_path)
    assert base != _h("", ARGS.replace("PIP_INDEX_URL=", "PIP_INDEX_URL=https://pypi.example.com/simple"),
                      a, cwd=tmp_path)
    assert base != _h("", ARGS + "CC_TLS_INSECURE=1\n", a, cwd=tmp_path)
    # No CA and an EMPTY CA file stage the same empty cc-ca.crt: same image.
    empty = tmp_path / "empty.pem"
    empty.write_bytes(b"")
    assert base == _h(str(empty), ARGS, a, cwd=tmp_path)
    # A CA with content is copied into the image's trust store: a different one.
    ca = tmp_path / "ca.pem"
    ca.write_bytes(b"-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
    with_ca = _h(str(ca), ARGS, a, cwd=tmp_path)
    assert with_ca != base
    # ...keyed by its CONTENT, never its path.
    moved = tmp_path / "moved.pem"
    shutil.copy2(ca, moved)
    assert _h(str(moved), ARGS, a, cwd=tmp_path) == with_ca


def test_python_bytecode_is_not_an_input(tmp_path):
    """A deployment that imports central_command.crawler grows __pycache__ in
    the crawler context; no Dockerfile COPYs it, and it must not rebuild the
    image on every run."""
    a = _ctx(tmp_path / "a", CTX)
    b = _ctx(tmp_path / "b", {**CTX, "__pycache__/service.cpython-312.pyc": b"\x00\x01",
                              "stray.pyc": b"\x02"})
    assert _h("", ARGS, a, cwd=tmp_path) == _h("", ARGS, b, cwd=tmp_path)


def test_an_unreadable_input_is_an_error_not_a_hash(tmp_path):
    a = _ctx(tmp_path / "a", CTX)
    assert _hash("", ARGS, tmp_path / "nope", cwd=tmp_path).returncode == 1
    assert _hash(str(tmp_path / "no-ca.pem"), ARGS, a, cwd=tmp_path).returncode == 1


def test_build_arg_values_come_out_of_the_argv():
    script = (f'. "{ENV_LIB.as_posix()}"; cc_build_arg_values "$@"')
    r = subprocess.run([_bash(), "-c", script, "x", "--build-arg", "A=1", "--tls-verify=false",
                        "--build-arg", "B=", "--build-arg=C=3", "-t", "img"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["A=1", "B=", "C=3"]


# ── the real setup.sh functions and build scripts, against a stub podman ─────

# An image store in a directory: one file per ref, holding the label value
# ("" = an image built without one). `podman images` lists refs, `image
# inspect` fails for an absent ref, `build` records what it was asked for.
_PODMAN = r'''#!/usr/bin/env bash
echo "podman $*" >> "$STUB_LOG"
key() { local k="${1//\//_}"; printf '%s' "${k//:/_}"; }
case "$1" in
  image)
    [[ "${2:-}" == inspect ]] || exit 0
    ref="${@: -1}"; f="$STUB_STORE/$(key "$ref")"
    [[ -f "$f" ]] || { echo "Error: $ref: image not known" >&2; exit 125; }
    cat "$f"; exit 0 ;;
  build)
    label=""; ref=""; shift
    while (( $# )); do
      case "$1" in
        --label) label="${2#*=}"; shift ;;
        -t) ref="$2"; shift ;;
      esac
      shift
    done
    [[ -f "$STUB_STORE/fail-build" ]] && exit 1
    printf '%s\n' "$label" >"$STUB_STORE/$(key "$ref")"
    printf '%s\n' "$ref" >>"$STUB_STORE/refs"
    exit 0 ;;
  images)
    cat "$STUB_STORE/refs" 2>/dev/null; exit 0 ;;
  untag)
    # `podman untag <image> <name>`: the NAME goes, nothing else.
    grep -vxF -- "$3" "$STUB_STORE/refs" > "$STUB_STORE/refs.new" 2>/dev/null
    mv "$STUB_STORE/refs.new" "$STUB_STORE/refs"
    rm -f "$STUB_STORE/$(key "$3")"; exit 0 ;;
esac
exit 0
'''

SANDBOX = "localhost/cc-sandbox:1"


class Tree:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.repo = tmp / "repo"
        self.state = tmp / "state"
        self.store = tmp / "store"
        self.log = tmp / "podman.log"
        self.single = self.repo / "deploy" / "single"
        self.repo.mkdir()
        self.state.mkdir()
        self.store.mkdir()
        (tmp / "home").mkdir()
        shutil.copytree(ROOT / "deploy", self.repo / "deploy", ignore=_DEBRIS)
        shutil.copytree(ROOT / "central_command" / "crawler",
                        self.repo / "central_command" / "crawler", ignore=_DEBRIS)
        (self.repo / "central_command" / "db").mkdir()
        shutil.copy2(ROOT / "central_command" / "db" / "schema.sql",
                     self.repo / "central_command" / "db" / "schema.sql")
        for f in (".env.example", "VERSION", ".gitignore"):
            shutil.copy2(ROOT / f, self.repo / f)
        env = (ROOT / ".env.example").read_text(encoding="utf-8")
        env += (f"\nCC_STATE_DIR={self.state}\nCC_ENABLE_SANDBOX=1\nCC_ENABLE_CRAWLER=1\n"
                "CC_TLS_INSECURE=0\nCC_CA_BUNDLE=\n")
        (self.repo / ".env").write_text(env, encoding="utf-8")
        setup = self.single / "setup.sh"
        text = setup.read_text(encoding="utf-8")
        tail = 'main "$@"'
        assert text.rstrip().endswith(tail)
        hook = 'if [[ -n "${__CALL:-}" ]]; then "$@"; exit $?; fi\n'
        setup.write_text(text.rstrip()[: -len(tail)] + hook + tail + "\n", encoding="utf-8")
        b = tmp / "bin"
        b.mkdir()
        (b / "podman").write_text(_PODMAN, encoding="utf-8")
        (b / "podman").chmod(0o755)

    @property
    def script(self) -> str:
        return str(self.single / "build-sandbox-image.sh")

    def env(self) -> dict[str, str]:
        return {"PATH": os.pathsep.join([str(self.tmp / "bin"), "/usr/bin", "/bin"]),
                "HOME": str(self.tmp / "home"), "XDG_STATE_HOME": str(self.tmp / "home" / "state"),
                "STUB_LOG": str(self.log), "STUB_STORE": str(self.store), "LANG": "C.UTF-8"}

    def call(self, *args: str, staged: bool = False) -> subprocess.CompletedProcess:
        # staged=True is update.sh apply's acquisition: CC_STAGED_FOR names the
        # deployment (here the same temp tree), which setup.sh AND the build
        # script it runs both read.
        extra = {"CC_STAGED_FOR": str(self.repo)} if staged else {}
        return subprocess.run([_bash(), str(self.single / "setup.sh"), *args],
                              cwd=self.single, capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL, env={**self.env(), "__CALL": "1", **extra})

    def inputs_hash(self) -> str:
        r = subprocess.run([_bash(), self.script, "--inputs-hash"], cwd=self.single,
                           capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL, env=self.env())
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout.strip()

    def seed(self, ref: str, label: str) -> None:
        key = ref.replace("/", "_").replace(":", "_")
        (self.store / key).write_text(label + "\n", encoding="utf-8")
        with (self.store / "refs").open("a", encoding="utf-8") as f:
            f.write(ref + "\n")

    def label(self, ref: str) -> str:
        return (self.store / ref.replace("/", "_").replace(":", "_")).read_text().strip()

    def builds(self) -> list[str]:
        if not self.log.exists():
            return []
        return [l for l in self.log.read_text().splitlines() if l.startswith("podman build")]

    def fetch(self, staged: bool = False) -> subprocess.CompletedProcess:
        return self.call("fetch_local", "image-sandbox", SANDBOX, self.script, "SEAMS",
                         staged=staged)

    def refs(self) -> list[str]:
        f = self.store / "refs"
        return f.read_text().split() if f.exists() else []

    def probe(self) -> bool:
        return self.call("p_image_sandbox").returncode == 0


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
    if sys.platform == "win32":
        pytest.skip("Git Bash prepends /usr/bin to PATH; a stub podman cannot shadow the real one")
    return Tree(tmp_path)


def _pass_line(out: str) -> str:
    lines = [l for l in out.splitlines() if l.startswith(("PASS image-sandbox:", "FAIL image-sandbox:"))]
    assert len(lines) == 1, out
    return lines[0]


def test_inputs_hash_mode_writes_nothing_and_calls_no_podman(tree: Tree):
    before = sorted(p.relative_to(tree.state) for p in tree.state.rglob("*"))
    h = tree.inputs_hash()
    assert len(h) == 64
    assert tree.builds() == [] and not tree.log.exists(), "--inputs-hash ran podman"
    assert sorted(p.relative_to(tree.state) for p in tree.state.rglob("*")) == before, (
        "--inputs-hash wrote into the state dir — the probes call it, and a probe mutates nothing"
    )
    assert h == tree.inputs_hash(), "the hash is not deterministic"


def test_absent_image_is_built_with_the_label(tree: Tree):
    assert not tree.probe(), "the probe passed with no image at all"
    r = tree.fetch()
    assert _pass_line(r.stdout) == f"PASS image-sandbox: {SANDBOX} built", r.stdout + r.stderr
    builds = tree.builds()
    assert len(builds) == 1, builds
    assert f"--label {LABEL}={tree.inputs_hash()}" in builds[0], builds[0]
    assert tree.label(SANDBOX) == tree.inputs_hash()
    assert tree.probe(), "the probe is still false once the label matches"


def test_a_matching_label_is_not_rebuilt(tree: Tree):
    tree.seed(SANDBOX, tree.inputs_hash())
    assert tree.probe()
    r = tree.fetch()
    assert _pass_line(r.stdout) == f"PASS image-sandbox: {SANDBOX} present and built from these inputs", r.stdout
    assert tree.builds() == [], "an image built from these inputs was rebuilt"


def test_a_different_label_is_rebuilt_with_the_new_hash(tree: Tree):
    tree.seed(SANDBOX, "0" * 64)
    assert not tree.probe(), "the probe passed on a STALE image — a changed Dockerfile would never re-run the row"
    r = tree.fetch()
    line = _pass_line(r.stdout)
    assert line == (f"PASS image-sandbox: {SANDBOX} rebuilt — build inputs changed since the image "
                    "was made"), r.stdout
    builds = tree.builds()
    assert len(builds) == 1 and f"--label {LABEL}={tree.inputs_hash()}" in builds[0], builds
    assert tree.probe()


def test_an_unlabelled_image_is_rebuilt_once(tree: Tree):
    """Built by a release before v2.57.0: no label counts as different."""
    tree.seed(SANDBOX, "")
    assert not tree.probe()
    first = tree.fetch()
    assert "rebuilt — build inputs changed since the image was made" in _pass_line(first.stdout)
    assert f"no {LABEL} label" in _pass_line(first.stdout)
    assert len(tree.builds()) == 1
    second = tree.fetch()
    assert "present and built from these inputs" in _pass_line(second.stdout)
    assert len(tree.builds()) == 1, "rebuilt twice"


def test_need_image_compares_the_label_and_never_builds(tree: Tree):
    """stack's assertion keeps its meaning — never a build mid-deploy — and
    agrees with the stack rows' probe, which is the same p_image_* function."""
    r = tree.call("need_image", "image-sandbox", SANDBOX, tree.script)
    assert r.returncode == 1 and "is not in local storage — run: ./setup.sh fetch" in r.stdout, r.stdout
    tree.seed(SANDBOX, "f" * 64)
    r = tree.call("need_image", "image-sandbox", SANDBOX, tree.script)
    assert r.returncode == 1, r.stdout
    assert "was not built from this tree's build inputs" in r.stdout and "./setup.sh fetch" in r.stdout
    assert tree.builds() == [], "need_image built an image"
    tree.seed(SANDBOX, tree.inputs_hash())
    r = tree.call("need_image", "image-sandbox", SANDBOX, tree.script)
    assert r.returncode == 0 and r.stdout.startswith("PASS image-sandbox:"), r.stdout


def test_a_changed_dockerfile_rebuilds_and_a_rollback_rebuilds_the_old_one(tree: Tree):
    """The release case and the rollback case are one mechanism: the label is
    compared with the hash of the tree checked out NOW."""
    dockerfile = tree.repo / "deploy" / "k3s" / "sandbox.Dockerfile"   # the temp COPY
    old_text = dockerfile.read_text(encoding="utf-8")
    tree.fetch()
    old_hash = tree.label(SANDBOX)
    assert tree.probe()

    # The update: the release changes the Dockerfile.
    dockerfile.write_text(old_text + "# a release changed this\n", encoding="utf-8")
    assert not tree.probe(), "a changed Dockerfile left the fetch row reading done"
    r = tree.fetch()
    assert "rebuilt — build inputs changed" in _pass_line(r.stdout)
    new_hash = tree.label(SANDBOX)
    assert new_hash != old_hash and tree.probe()

    # The rollback: `update.sh rollback` restores the old tree and re-runs fetch.
    dockerfile.write_text(old_text, encoding="utf-8")
    assert not tree.probe(), "after a rollback the NEW image read as current"
    r = tree.fetch()
    assert "rebuilt — build inputs changed" in _pass_line(r.stdout)
    assert tree.label(SANDBOX) == old_hash, "the rollback did not rebuild from the old inputs"
    assert len(tree.builds()) == 3


def test_a_crlf_checkout_does_not_rebuild(tree: Tree):
    tree.fetch()
    dockerfile = tree.repo / "deploy" / "k3s" / "sandbox.Dockerfile"
    dockerfile.write_bytes(dockerfile.read_bytes().replace(b"\n", b"\r\n"))
    assert tree.probe(), "a CRLF checkout of the same release read as changed inputs"


def test_an_env_seam_change_rebuilds(tree: Tree):
    """The resolved base ref is a build input: a re-resolved CC_IMG_PYTHON (a
    new lock, a substitution, an operator pin) rebuilds the sandbox."""
    tree.fetch()
    assert tree.probe()
    with (tree.repo / ".env").open("a", encoding="utf-8") as f:
        f.write("CC_IMG_PYTHON=registry.example.com/library/python:3.12.9-slim-bookworm\n")
    assert not tree.probe()


def test_a_failed_rebuild_is_a_fail_and_the_probe_stays_false(tree: Tree):
    tree.seed(SANDBOX, "0" * 64)
    (tree.store / "fail-build").write_text("")
    r = tree.fetch()
    assert _pass_line(r.stdout).startswith("FAIL image-sandbox:"), r.stdout
    assert "SEAMS" in r.stdout
    assert not tree.probe()


@pytest.mark.parametrize("script,ref", [
    ("build-graphiti-image.sh", "localhost/cc-graphiti:1.0.2-anthropic"),
    ("build-crawler-image.sh", "localhost/cc-crawler:1"),
])
def test_the_other_two_builds_carry_the_label_too(tree: Tree, script, ref):
    path = str(tree.single / script)
    r = tree.call("fetch_local", "image-x", ref, path, "SEAMS")
    assert f"PASS image-x: {ref} built" in r.stdout, r.stdout + r.stderr
    want = subprocess.run([_bash(), path, "--inputs-hash"], cwd=tree.single, capture_output=True,
                          text=True, env=tree.env(), stdin=subprocess.DEVNULL).stdout.strip()
    assert len(want) == 64 and tree.label(ref) == want
    probe = "p_image_graphiti" if "graphiti" in script else "p_image_crawler"
    assert tree.call(probe).returncode == 0


# ── a STAGED build moves no live tag (v2.57.0, D5) ──────────────────────────

ASIDE = SANDBOX + "-staged"


def _tags(builds: list[str]) -> list[str]:
    return [b.split(" -t ", 1)[1].split()[0] for b in builds]


def test_a_staged_build_is_tagged_aside_and_the_live_image_is_untouched(tree: Tree):
    """update.sh apply promises that a stop before the merge leaves the
    containers as they were. The staged fetch now BUILDS the new release's local
    images, so under the fixed live tag it would have moved the image every new
    sandbox session and every recreated container starts from. It builds aside."""
    old = "0" * 64
    tree.seed(SANDBOX, old)

    r = tree.fetch(staged=True)

    line = _pass_line(r.stdout)
    assert line.startswith(f"PASS image-sandbox: {ASIDE} built"), r.stdout + r.stderr
    assert "does not move before the merge" in line, line
    assert _tags(tree.builds()) == [ASIDE], tree.builds()
    assert tree.label(SANDBOX) == old, "the staged build moved the LIVE tag's image"
    # The aside image carries THIS release's inputs hash — the hash has no tag in it.
    assert tree.label(ASIDE) == tree.inputs_hash()
    assert not tree.probe(), "the live row read done on the strength of an aside build"
    assert "podman untag" not in tree.log.read_text(), "a staged run untagged something"


def test_the_post_merge_fetch_builds_the_live_tag_and_drops_the_aside_one(tree: Tree):
    tree.seed(SANDBOX, "0" * 64)
    tree.fetch(staged=True)
    staged_label = tree.label(ASIDE)

    r = tree.fetch()

    assert "rebuilt — build inputs changed" in _pass_line(r.stdout), r.stdout
    assert _tags(tree.builds()) == [ASIDE, SANDBOX], tree.builds()
    assert tree.label(SANDBOX) == staged_label == tree.inputs_hash()
    assert tree.probe()
    # The aside NAME is gone — by name, never by stripping every name of the
    # image (which would have taken the live tag with it).
    assert f"podman untag {ASIDE} {ASIDE}" in tree.log.read_text()
    assert ASIDE not in tree.refs() and SANDBOX in tree.refs()


def test_a_staged_run_builds_nothing_when_the_live_image_already_matches(tree: Tree):
    """A release that did not touch this image's inputs: the running image IS
    the one it needs, so there is nothing to prove and no aside tag to leave."""
    tree.seed(SANDBOX, tree.inputs_hash())
    r = tree.fetch(staged=True)
    assert _pass_line(r.stdout) == (f"PASS image-sandbox: {SANDBOX} present and built from this "
                                    "release's inputs already"), r.stdout
    assert tree.builds() == []
    assert ASIDE not in tree.refs()


def test_a_rerun_staged_acquisition_reuses_its_aside_build(tree: Tree):
    """An apply that stopped AFTER the staged build and is re-run: the aside
    image is current, so it is not built twice."""
    tree.seed(SANDBOX, "0" * 64)
    tree.fetch(staged=True)
    r = tree.fetch(staged=True)
    assert _pass_line(r.stdout).startswith(f"PASS image-sandbox: {ASIDE} present and built from "
                                           "these inputs"), r.stdout
    assert len(tree.builds()) == 1


@pytest.mark.parametrize("script,ref", [
    ("build-graphiti-image.sh", "localhost/cc-graphiti:1.0.2-anthropic"),
    ("build-crawler-image.sh", "localhost/cc-crawler:1"),
])
def test_the_other_two_builds_tag_aside_when_staged(tree: Tree, script, ref):
    path = str(tree.single / script)
    r = tree.call("fetch_local", "image-x", ref, path, "SEAMS", staged=True)
    assert f"PASS image-x: {ref}-staged built" in r.stdout, r.stdout + r.stderr
    assert _tags(tree.builds()) == [f"{ref}-staged"], tree.builds()
    assert ref not in tree.refs()
