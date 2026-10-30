"""No tracked file names this deployment's hosts, addresses, accounts or paths (R12, INSTANCE-001).

The public tree carries no deployment specifics (AGENTS.md, Policy). A guard that
needs the operator's identifier list cannot run on a clone, so this test refuses
instance-fact SHAPES -- the forms a value takes, none of which needs a value to
be written down here:

  a  an IPv4 address in the tailnet range 100.64.0.0/10 outside the example
     block 100.64.0.0/24
  b  a /home/<name>/ path whose <name> is not in EXAMPLE_HOME_NAMES
  c  an ssh-target default with a literal: ${CC_..._SSH:-literal} (or :=)
  d  kubernetes.io/hostname followed by a literal node value, in central_command/
     or deploy/k3s/ (a bare mention of the key, as in a comment, passes)
  e  in a deploy/k3s/*.in unit template, a User=, Group=, WorkingDirectory=,
     ExecStart=, EnvironmentFile= or Environment= value that holds an absolute
     /home path, or (User=/Group=) is not a token (@NAME@, ${NAME}, {{NAME}} or
     <name>) -- no template exists yet; the rule is armed for the unit templates

Scope: `git ls-files` (the tracked tree, not the working tree), minus docs/vendor/;
under web/ only the paths web/VENDORED.md names as ours (server/routes/cc-*.ts,
server/lib/ws-proxy.ts and the src/features folders decisions, agents, workspace,
skills, attention), so upstream fixtures and docs are out by construction. NAMES
(hostnames, accounts, addresses, the private repo's name) are not checked here:
`scripts/prepush-scan.sh --tree` checks them against the operator's private list
(tests/test_prepush_scan.py), and this test never reads that list.

While sweeps are open, tests/instance_facts_baseline.txt lists `<path>\\t<shape>`
for every hit that still stands (paths and shape letters only, never the matched
text). The test fails on a hit not in the baseline AND on a baseline line with no
hit (stale), so each story deletes the lines of the files it cleans. The file and
the code reading it are deleted when it is empty (epic story 12).

Every planted value below is built at runtime from parts: a literal one would be
a hit in this very file.
"""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).with_name("instance_facts_baseline.txt")

EXAMPLE_NETWORK = ipaddress.ip_network("100.64.0.0/24")
TAILNET = ipaddress.ip_network("100.64.0.0/10")
EXAMPLE_HOME_NAMES = frozenset({"user", "username", "node", "ubuntu", "jane", "doe", "rivers"})

SHAPES = "abcde"

_IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d]|\.\d)")
_HOME = re.compile(r"/home/([A-Za-z0-9_][A-Za-z0-9_.-]*)")
_SSH_DEFAULT = re.compile(r"\$\{CC_[A-Z_]*SSH:[-=][^}]+\}")
_HOSTNAME_KEY = "kubernetes" + r"\.io/hostname"
# The key and, on the same line or the next few, a literal value: a quoted/bare
# scalar after the key, a `values: [lit]`, or a `values:` block list.
_HOSTNAME_VALUE = re.compile(
    _HOSTNAME_KEY + r"""["']?\s*[:=]\s*["']?[A-Za-z0-9]"""
    r"""|""" + _HOSTNAME_KEY + r"""(?:[^\n]*\n){0,4}?[^\n]*values["']?\s*:\s*(?:\[\s*["']?|\n\s*-\s*["']?)[A-Za-z0-9]"""
)
_UNIT_KEY = re.compile(r"^(User|Group|WorkingDirectory|ExecStart|EnvironmentFile|Environment)=(.*)$", re.M)
_TOKEN = re.compile(r"@[A-Za-z_][A-Za-z0-9_]*@|\$\{[^}]+\}|\{\{[^}]+\}\}|<[^>]+>")

_WEB_OURS = re.compile(
    r"^web/(server/routes/cc-[^/]+\.ts"
    r"|server/lib/ws-proxy\.ts"
    r"|src/features/(?:decisions|agents|workspace|skills|attention)/.+)$"
)


def in_scope(path: str) -> bool:
    if path.startswith("docs/vendor/"):
        return False
    if path.startswith("web/"):
        return bool(_WEB_OURS.match(path))
    return True


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p and in_scope(p)]


def _shape_a(text: str) -> bool:
    for m in _IPV4.finditer(text):
        try:
            ip = ipaddress.ip_address(m.group(1))
        except ValueError:
            continue
        if ip in TAILNET and ip not in EXAMPLE_NETWORK:
            return True
    return False


def _shape_b(text: str) -> bool:
    return any(m.group(1) not in EXAMPLE_HOME_NAMES for m in _HOME.finditer(text))


def _shape_e(text: str) -> bool:
    for m in _UNIT_KEY.finditer(text):
        key, value = m.group(1), m.group(2).strip()
        if "/home/" in value:
            return True
        if key in ("User", "Group") and value and not _TOKEN.fullmatch(value):
            return True
    return False


def shapes_of(path: str, text: str) -> set[str]:
    """The shape letters `text`, the content of tracked file `path`, shows."""
    hit = set()
    if _shape_a(text):
        hit.add("a")
    if _shape_b(text):
        hit.add("b")
    if _SSH_DEFAULT.search(text):
        hit.add("c")
    if path.startswith(("central_command/", "deploy/k3s/")) and _HOSTNAME_VALUE.search(text):
        hit.add("d")
    if re.fullmatch(r"deploy/k3s/[^/]+\.in", path) and _shape_e(text):
        hit.add("e")
    return hit


def find_hits(root: Path) -> set[tuple[str, str]]:
    hits = set()
    for path in tracked_files(root):
        f = root / path
        if not f.is_file() or f.is_symlink():
            continue
        data = f.read_bytes()
        if b"\0" in data:
            continue
        for shape in shapes_of(path, data.decode("utf-8", "replace")):
            hits.add((path, shape))
    return hits


def read_baseline(path: Path) -> set[tuple[str, str]]:
    rows = set()
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        p, _, shape = line.partition("\t")
        assert shape in tuple(SHAPES), f"{path.name}:{n}: want '<path><TAB><shape a-e>'"
        rows.add((p, shape))
    return rows


def compare(root: Path, baseline: Path) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(hits the baseline does not list, baseline lines with no hit), both sorted."""
    hits, listed = find_hits(root), read_baseline(baseline)
    return sorted(hits - listed), sorted(listed - hits)


def _need_checkout() -> None:
    if not shutil.which("git") or not (ROOT / ".git").exists():
        pytest.skip("git and a checkout are needed (a source archive has neither)")


# -- the tree ----------------------------------------------------------------

def test_tracked_tree_matches_the_baseline():
    _need_checkout()
    new, stale = compare(ROOT, BASELINE)
    assert not new, (
        "instance-fact shapes in tracked files (R12): "
        + "; ".join(f"{p} [{s}]" for p, s in new)
        + " -- name the role or an example value instead, never this deployment's"
    )
    assert not stale, (
        "baseline lines with no hit (delete them): " + "; ".join(f"{p} [{s}]" for p, s in stale)
    )


def test_baseline_is_sorted_and_free_of_matched_text():
    lines = [ln for ln in BASELINE.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    assert lines == sorted(lines), "keep the baseline sorted by path (adjacent deletions merge cleanly)"
    for ln in lines:
        assert re.fullmatch(r"[^\t]+\t[a-e]", ln), f"baseline line is not '<path><TAB><letter>': {ln[:60]!r}"


# -- the shapes, each planted in a temp tracked file -------------------------

def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    if not shutil.which("git"):
        pytest.skip("git needed")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel, text in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    subprocess.run(["git", "-C", str(tmp_path), "add", "--", *files], check=True)
    return tmp_path


@pytest.fixture
def empty_baseline(tmp_path_factory):
    f = tmp_path_factory.mktemp("baseline") / "baseline.txt"
    f.write_text("# empty\n")
    return f


def _planted(shape: str) -> tuple[str, str]:
    """(path, text) planting `shape`, its values built at runtime."""
    ip = ".".join(["100", "64", "1", "9"])                      # in 100.64.0.0/10, outside the /24
    name = "zq" + "7acct"                                       # not in the example set
    if shape == "a":
        return "deploy/x.sh", f"ssh {ip}\n"
    if shape == "b":
        return "scripts/x.sh", f"cd /home/{name}/checkout\n"
    if shape == "c":
        return "deploy/k3s/x.sh", "T=${CC_COMPUTE" + "_SSH:-" + name + "@host}\n"
    if shape == "d":
        return "central_command/x.py", 'sel = {"key": "kubernetes.io/' + 'hostname", "values": ["' + name + '"]}\n'
    if shape == "e":
        return "deploy/k3s/cc-x.service.in", f"[Service]\nUser={name}\n"
    raise AssertionError(shape)


@pytest.mark.parametrize("shape", list(SHAPES))
def test_planted_shape_fails(tmp_path, empty_baseline, shape):
    path, text = _planted(shape)
    root = _repo(tmp_path / "r", {path: text})
    new, stale = compare(root, empty_baseline)
    assert (path, shape) in new and not stale


@pytest.mark.parametrize("shape", list(SHAPES))
def test_planted_shape_passes_when_baselined(tmp_path, shape):
    path, text = _planted(shape)
    root = _repo(tmp_path / "r", {path: text})
    base = tmp_path / "baseline.txt"
    base.write_text(f"{path}\t{shape}\n")
    assert compare(root, base) == ([], [])


def test_a_unit_template_home_path_is_shape_e(tmp_path, empty_baseline):
    # Also shape b (a /home/<name>/ path); the point is that e fires on a path in a directive.
    path = "deploy/k3s/cc-x.service.in"
    root = _repo(tmp_path / "r", {path: "[Service]\nUser=@CC_OWNER@\nWorkingDirectory=/home/" + "zq7acct/repo\n"})
    assert (path, "e") in compare(root, empty_baseline)[0]


def test_a_baseline_line_with_no_hit_is_stale(tmp_path):
    root = _repo(tmp_path / "r", {"README.md": "clean\n"})
    base = tmp_path / "baseline.txt"
    base.write_text("README.md\ta\n")
    assert compare(root, base) == ([], [("README.md", "a")])


def test_examples_and_bare_mentions_pass(tmp_path, empty_baseline):
    key = "kubernetes.io/" + "hostname"
    ok = {
        "deploy/x.sh": "ssh 100.64.0.5 # example block\nping 8.8.8.8 and 100.200.1.1 and v1.2.100.64.3.4\n",
        "docs/x.md": "path /home/<user>/cc-backups, /home/user/x, /home/node/.n8n, /home/ubuntu/.cache\n",
        "deploy/k3s/y.sh": 'T="${CC_COMPUTE' + '_SSH:?set CC_COMPUTE_SSH}" ; U=${CC_X_SSH:-}\n',
        "deploy/k3s/00-ns.yaml": f"# {key} nodeAffinity is added to every PV\n",
        "deploy/k3s/cc-x.service.in": "[Service]\nUser=@CC_OWNER@\nExecStart=@CC_CHECKOUT@/.venv/bin/uvicorn\n",
        "deploy/k3s/note.in": "Environment=HOME=${CC_HOME}\n",
    }
    root = _repo(tmp_path / "r", ok)
    assert compare(root, empty_baseline) == ([], [])


def test_a_yaml_hostname_selector_with_a_literal_value_is_shape_d(tmp_path, empty_baseline):
    key = "kubernetes.io/" + "hostname"
    text = f"- key: {key}\n  operator: In\n  values:\n    - zq7node\n"
    root = _repo(tmp_path / "r", {"deploy/k3s/x.yaml": text})
    assert compare(root, empty_baseline)[0] == [("deploy/k3s/x.yaml", "d")]


def test_scope_skips_vendor_and_upstream_web_and_keeps_our_web(tmp_path, empty_baseline):
    path, text = _planted("b")
    files = {
        "docs/vendor/x.md": text,
        "web/docs/x.md": text,
        "web/src/features/chat/x.ts": text,
        "web/server/routes/cc-x.ts": text,
        "web/server/lib/ws-proxy.ts": text,
        "web/src/features/decisions/x.ts": text,
        "web/server/routes/other.ts": text,
    }
    root = _repo(tmp_path / "r", files)
    new, _ = compare(root, empty_baseline)
    assert sorted(p for p, _s in new) == [
        "web/server/lib/ws-proxy.ts",
        "web/server/routes/cc-x.ts",
        "web/src/features/decisions/x.ts",
    ]


def test_the_working_tree_is_not_scanned_only_tracked_files(tmp_path, empty_baseline):
    path, text = _planted("a")
    root = _repo(tmp_path / "r", {"README.md": "clean\n"})
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(text)                     # untracked
    assert compare(root, empty_baseline) == ([], [])


def test_this_file_and_its_baseline_hold_no_shape():
    _need_checkout()
    for p in ("tests/test_no_instance_facts.py", "tests/instance_facts_baseline.txt"):
        assert shapes_of(p, (ROOT / p).read_text()) == set(), p


@pytest.mark.skipif(os.name == "nt", reason="POSIX paths")
def test_tracked_files_are_listed_from_git_not_the_working_tree():
    _need_checkout()
    files = tracked_files(ROOT)
    assert "AGENTS.md" in files and not any(f.startswith("docs/vendor/") for f in files)
