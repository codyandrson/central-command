"""backup.sh keeps the three environment files (R19, FR-51; ruled 2026-10-29,
Decision 5, epic-small-fixes story 11.13).

The real backup.sh runs in a scratch tree with a `k3s` stub that fails every
store, so it runs to its end and exits non-zero for the stores — what these
tests read is the env copies and the line it prints about them. They pin:

  * each of deploy/k3s/.env, the root .env and web/.env is copied byte for
    byte to env_/env-app_/env-web_<stamp>.env, mode 0600, in a 0700 directory;
  * a missing or empty source fails the run naming the copy and the file's
    basename — and no value ever reaches the output;
  * none of the copies is named keys_* (the reset phase's `keys_*` keep label
    would otherwise pick up the wrong file).

Values are fake and never appear in an assertion message.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
K3S_DIR = ROOT / "deploy" / "k3s"

K3S_STUB = "#!/usr/bin/env bash\nexit 1\n"

CONTENT = {
    "env": b"N8N_ENCRYPTION_KEY=fake-n8n-0001\nLITELLM_SALT_KEY=fake-salt-0002\nNEO4J_PASSWORD=fake-neo-0003\n",
    "env-app": b"CC_LLM_API_KEY=fake-llm-0004\r\nexotic=\xff\xfe bytes # kept as is\n",
    "env-web": b"PORT=3080\nNERVE_AUTH_TOKEN=fake-web-0005\n",
}
SOURCE = {"env": "deploy/k3s/.env", "env-app": ".env", "env-web": "web/.env"}


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    if os.name == "nt" or not shutil.which("bash") or not shutil.which("flock"):
        pytest.skip("POSIX bash and flock needed")
    t = tmp_path / "repo"
    (t / "deploy" / "k3s").mkdir(parents=True)
    (t / "web").mkdir()
    shutil.copy(K3S_DIR / "backup.sh", t / "deploy" / "k3s" / "backup.sh")
    shutil.copy(K3S_DIR / "env-path.sh", t / "deploy" / "k3s" / "env-path.sh")
    shutil.copy(K3S_DIR / "instance-lib.sh", t / "deploy" / "k3s" / "instance-lib.sh")
    for label, rel in SOURCE.items():
        (t / rel).write_bytes(CONTENT[label])
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "k3s").write_text(K3S_STUB)
    (stubs / "k3s").chmod(0o755)
    return t


def _run(tree: Path):
    out = tree.parent / "backups"
    env = dict(os.environ)
    env.update(PATH=f"{tree.parent / 'stubs'}{os.pathsep}{env['PATH']}",
               REPO=str(tree), CC_BACKUP_DIR=str(out))
    # The scratch tree's own deploy/k3s/.env must be the only source.
    for k in ("N8N_ENCRYPTION_KEY", "LITELLM_SALT_KEY", "NEO4J_PASSWORD"):
        env.pop(k, None)
    r = subprocess.run(["bash", str(tree / "deploy" / "k3s" / "backup.sh")],
                       env=env, capture_output=True, text=True, timeout=120)
    return r, out


def _copies(out: Path, label: str) -> list[Path]:
    return sorted(out.glob(f"{label}_*.env"))


def _no_values(text: str) -> bool:
    return "fake-" not in text


def test_each_env_file_is_copied_byte_for_byte_at_0600_in_a_0700_directory(tree: Path):
    r, out = _run(tree)
    assert r.returncode != 0  # the k3s stub fails every store
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    for label in CONTENT:
        (copy,) = _copies(out, label)
        assert copy.read_bytes() == CONTENT[label], f"{label}: copy differs from its source"
        assert stat.S_IMODE(copy.stat().st_mode) == 0o600, f"{label}: copy is not 0600"
    assert _no_values(r.stdout) and _no_values(r.stderr), "backup.sh printed an env value"


def test_the_prefixes_do_not_collide_with_the_key_file_or_each_other(tree: Path):
    _run(tree)
    out = tree.parent / "backups"
    names = {p.name for p in out.glob("env*")}
    assert len(names) == 3
    for label in CONTENT:
        # the reset keep loop globs `${label}_*`: each must match exactly one file
        assert len(list(out.glob(f"{label}_*"))) == 1, label
    assert not any(n.startswith("keys_") and "env" in n.split("_")[0] for n in names)


def test_a_symlinked_source_is_followed_not_copied_as_a_link(tree: Path):
    real = tree / "real.env"
    real.write_bytes(CONTENT["env"])
    (tree / "deploy" / "k3s" / ".env").unlink()
    (tree / "deploy" / "k3s" / ".env").symlink_to(real)
    _run(tree)
    (copy,) = _copies(tree.parent / "backups", "env")
    assert not copy.is_symlink() and copy.read_bytes() == CONTENT["env"]


@pytest.mark.parametrize("label", list(CONTENT))
@pytest.mark.parametrize("state", ["missing", "empty"])
def test_a_missing_or_empty_env_file_fails_the_run_naming_it_and_no_value(tree: Path, label, state):
    src = tree / SOURCE[label]
    if state == "missing":
        src.unlink()
    else:
        src.write_bytes(b"")
    r, out = _run(tree)
    assert r.returncode != 0
    combined = r.stdout + r.stderr
    assert f"ERROR: {label}:" in combined, "the failure does not name the copy"
    assert _copies(out, label) == [], "a failed copy must leave no file behind"
    assert "FAILED:" in combined and label in combined.split("FAILED:")[1]
    assert _no_values(combined), "backup.sh printed an env value"
    for other in CONTENT:
        if other != label:
            assert len(_copies(out, other)) == 1, f"{other} was lost because {label} failed"
