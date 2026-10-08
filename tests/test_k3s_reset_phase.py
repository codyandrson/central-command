"""`deploy/k3s/setup.sh reset` — wipe the instance, keep the system.

The phase deletes two volumes on a live deployment, so its three promises are
pinned by RUNNING it: the real setup.sh in a scratch tree, with `sudo`, `k3s`
and `systemctl` replaced by stubs that log every call and a `backup.sh` that
writes (or refuses to write) a dump set. No cluster, no root.

  * a bare `reset` changes nothing and stops for the operator;
  * nothing is stopped or deleted unless the backup exited 0 AND left the whole
    dump set, which is then kept outside the nightly retention's reach;
  * only the spine and graph volumes are deleted — never LiteLLM's or n8n's,
    never deploy/pi/.env.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "deploy" / "k3s" / "setup.sh"

SUDO = '''#!/usr/bin/env bash
printf 'sudo %s\\n' "$*" >> "$STUB_LOG"
exec "$@"
'''

K3S = '''#!/usr/bin/env bash
printf 'k3s %s\\n' "$*" >> "$STUB_LOG"
args=" $* "
case "$args" in
  *" get pvc "*)
    case "$args" in
      *deletionTimestamp*) printf '%s' "${STUB_PVC_DELETING:-}" ;;
      *volumeName*)        printf 'pv-of-%s' "$5" ;;
      *" -o name "*)       ;;   # wait_gone: the claim is gone
    esac ;;
  *" get deploy/"*)     printf '%s' "${STUB_REPLICAS:-1}" ;;
  *" get pods "*)
    # A dead pod keeps its label: listed without the phase filter, never with it.
    [[ "$args" == *"status.phase!=Failed"* ]] || echo pod/cc-postgres-dead ;;
  *" get pv/"*) ;;
  *"from agent where"*) echo 11 ;;
  *"from work_item"*)   echo "${STUB_LEDGER:-0}" ;;
  *"cypher-shell"*)     echo 0 ;;
esac
exit 0
'''

SYSTEMCTL = '''#!/usr/bin/env bash
printf 'systemctl %s\\n' "$*" >> "$STUB_LOG"
exit 0
'''

BACKUP = '''#!/usr/bin/env bash
printf 'backup REPO=%s OUT=%s\\n' "$REPO" "$CC_BACKUP_DIR" >> "$STUB_LOG"
[[ "${STUB_BACKUP:-ok}" == fail ]] && exit 1
mkdir -p "$CC_BACKUP_DIR"
for f in central_command_S.sql.gz litellm_S.sql.gz n8n_S.sql.gz keys_S.env neo4j_S.dump.gz; do
  [[ "${STUB_BACKUP:-ok}" == short && "$f" == neo4j_* ]] && continue
  echo dump > "$CC_BACKUP_DIR/$f"
done
exit 0
'''


def _git(tree: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    *args], check=True, capture_output=True)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    if os.name == "nt" or not shutil.which("bash") or not shutil.which("git"):
        pytest.skip("POSIX bash and git needed")
    t = tmp_path / "repo"
    k3s = t / "deploy" / "k3s"
    k3s.mkdir(parents=True)
    (t / "deploy" / "pi").mkdir()
    (t / "web").mkdir()
    shutil.copy(SETUP, k3s / "setup.sh")
    (k3s / "backup.sh").write_text(BACKUP)
    (k3s / "backup.sh").chmod(0o755)
    (k3s / "make-secrets.sh").write_text('#!/usr/bin/env bash\necho make-secrets >> "$STUB_LOG"\n')
    (k3s / "make-secrets.sh").chmod(0o755)
    for m in ("20-postgres.yaml", "40-graph.yaml"):
        (k3s / m).write_text("# stub\n")
    (t / "deploy" / "pi" / ".env").write_text("NEO4J_PASSWORD=pw\nLITELLM_SALT_KEY=salt\n")
    (t / ".env").write_text("CC_JIRA_BASE_URL=https://jira.example.com\n")
    (t / "web" / ".env").write_text("PORT=3080\n")
    # One shipped example (tracked) and one folder an agent synced (untracked).
    (t / "servers" / "echo-demo").mkdir(parents=True)
    (t / "servers" / "echo-demo" / "README.md").write_text("example\n")
    _git(t, "init", "-q")
    _git(t, "add", "servers/echo-demo/README.md")
    _git(t, "commit", "-q", "-m", "seed")
    (t / "servers" / "synced-by-agent").mkdir()
    (t / "servers" / "synced-by-agent" / "server.py").write_text("x = 1\n")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("sudo", SUDO), ("k3s", K3S), ("systemctl", SYSTEMCTL)):
        (stubs / name).write_text(body)
        (stubs / name).chmod(0o755)
    return t


def _run(tree: Path, *args: str, **stub_env: str):
    log = tree.parent / "calls.log"
    log.write_text("")
    env = dict(os.environ)
    env.update(PATH=f"{tree.parent / 'stubs'}{os.pathsep}{env['PATH']}", STUB_LOG=str(log),
               CC_BACKUP_DIR=str(tree.parent / "backups"), **stub_env)
    r = subprocess.run(["bash", str(tree / "deploy" / "k3s" / "setup.sh"), "reset", *args],
                       env=env, capture_output=True, text=True, timeout=120)
    return r, [c for c in log.read_text().splitlines() if c]


def _untouched(tree: Path, calls: list[str]) -> None:
    """Nothing was stopped, scaled or deleted, and every file is where it was."""
    for c in calls:
        assert " delete " not in c and " scale " not in c and "systemctl stop" not in c, c
    assert (tree / ".env").is_file() and (tree / "web" / ".env").is_file()
    assert (tree / "servers" / "synced-by-agent").is_dir()


def _keep_dir(tree: Path) -> Path:
    (keep,) = (tree.parent / "backups").glob("keep-pre-reset-*")
    return keep


def test_a_bare_reset_changes_nothing_and_stops_for_the_operator(tree: Path):
    r, calls = _run(tree)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "USERACTION reset-confirm:" in r.stdout and "--confirm-wipe" in r.stdout
    assert calls == [], "a reset without --confirm-wipe must not run a single command"
    _untouched(tree, calls)


def test_a_confirmed_reset_backs_up_then_wipes_exactly_the_spine_and_the_graph(tree: Path):
    r, calls = _run(tree, "--confirm-wipe")
    assert "FAIL " not in r.stdout, r.stdout + r.stderr
    assert r.returncode in (0, 2), r.stdout + r.stderr   # 2: a host drop-in WARN

    backup_at = next(i for i, c in enumerate(calls) if c.startswith("backup "))
    first_change = next(i for i, c in enumerate(calls)
                        if " delete " in c or " scale " in c or "systemctl stop" in c)
    assert backup_at < first_change, "the backup must finish before anything is stopped or deleted"
    assert f"REPO={tree}" in calls[backup_at]

    # The empty spine loads schema.sql from a ConfigMap, once: it is rebuilt
    # from the checkout before the volume goes.
    assert calls.index("make-secrets") < next(
        i for i, c in enumerate(calls) if " delete pvc cc-pgdata" in c)

    deleted = sorted(c.split(" delete pvc ")[1].split()[0] for c in calls
                     if c.startswith("k3s ") and " delete pvc " in c)
    assert deleted == ["cc-neo4j-data", "cc-pgdata"]
    for c in calls:
        if c.startswith("k3s "):
            assert "litellm" not in c and "n8n" not in c, f"a kept store was touched: {c}"
    for unit in ("cc-uvicorn", "cc-sandbox-runner", "cc-nerve"):
        assert any(c == f"systemctl stop {unit}" for c in calls), unit
    for dep in ("cc-postgres", "cc-neo4j"):
        assert any(f"scale deploy/{dep} --replicas=1" in c for c in calls), f"{dep} was left at 0"

    keep = _keep_dir(tree)
    assert sorted(p.name for p in keep.iterdir() if p.is_file()) == [
        "central_command_S.sql.gz", "keys_S.env", "litellm_S.sql.gz", "n8n_S.sql.gz",
        "neo4j_S.dump.gz"]
    # The app's answers moved beside the dumps; the containers' env stayed.
    assert not (tree / ".env").exists() and not (tree / "web" / ".env").exists()
    assert "jira.example.com" in (keep / "env" / "root.env").read_text()
    assert (keep / "env" / "web.env").is_file()
    assert "LITELLM_SALT_KEY=salt" in (tree / "deploy" / "pi" / ".env").read_text()
    # The synced MCP source left with the instance; the shipped example stayed.
    assert (keep / "servers" / "synced-by-agent" / "server.py").is_file()
    assert not (tree / "servers" / "synced-by-agent").exists()
    assert (tree / "servers" / "echo-demo" / "README.md").is_file()


def test_a_store_a_previous_run_left_at_zero_is_started_before_the_backup(tree: Path):
    """The phase scales to 0 before it deletes; a failure between the two left
    cc-postgres stopped, and the re-run's backup then had nothing to dump."""
    r, calls = _run(tree, "--confirm-wipe", STUB_REPLICAS="0")
    assert "FAIL " not in r.stdout, r.stdout + r.stderr
    backup_at = next(i for i, c in enumerate(calls) if c.startswith("backup "))
    resumed = [i for i, c in enumerate(calls) if "scale deploy/cc-postgres --replicas=1" in c]
    assert resumed and resumed[0] < backup_at, "the stopped store must be running before backup.sh dumps it"
    assert "PASS reset-resume-cc-postgres:" in r.stdout


def test_dead_pods_are_removed_before_the_claim_is_deleted(tree: Path):
    """A pod in a terminal phase still counts as a user of the claim for the
    pvc-protection controller, so the claim's deletion hangs behind it."""
    r, calls = _run(tree, "--confirm-wipe")
    assert "FAIL " not in r.stdout, r.stdout + r.stderr
    for dep, pvc in (("cc-postgres", "cc-pgdata"), ("cc-neo4j", "cc-neo4j-data")):
        drained = next(i for i, c in enumerate(calls) if f"scale deploy/{dep} --replicas=0" in c)
        dead = next(i for i, c in enumerate(calls) if f"delete pods -l app={dep}" in c)
        wiped = next(i for i, c in enumerate(calls) if f" delete pvc {pvc}" in c)
        assert drained < dead < wiped, (dep, calls)


def test_a_claim_a_previous_run_left_half_deleted_is_finished_and_recreated_before_the_backup(tree: Path):
    r, calls = _run(tree, "--confirm-wipe", STUB_PVC_DELETING="2026-10-07T19:20:00Z")
    assert "FAIL " not in r.stdout, r.stdout + r.stderr
    backup_at = next(i for i, c in enumerate(calls) if c.startswith("backup "))
    dead = next(i for i, c in enumerate(calls) if "delete pods -l app=cc-postgres" in c)
    recreated = next(i for i, c in enumerate(calls) if "apply -f" in c and "20-postgres.yaml" in c)
    assert dead < recreated < backup_at, calls
    assert "WARN reset-resume-cc-postgres:" in r.stdout and "keep-pre-reset-" in r.stdout
    assert "PASS reset-keep:" in r.stdout, "the run still goes on to the wipe proper"


def test_keep_env_leaves_the_apps_answers_in_place(tree: Path):
    r, calls = _run(tree, "--confirm-wipe", "--keep-env")
    assert "FAIL " not in r.stdout, r.stdout + r.stderr
    assert (tree / ".env").is_file() and (tree / "web" / ".env").is_file()
    assert not (_keep_dir(tree) / "env").exists()
    assert any(" delete pvc cc-pgdata" in c for c in calls)


@pytest.mark.parametrize("mode", ["fail", "short"])
def test_no_complete_backup_no_wipe(tree: Path, mode: str):
    """`fail`: backup.sh exits non-zero. `short`: it exits 0 without the graph dump."""
    r, calls = _run(tree, "--confirm-wipe", STUB_BACKUP=mode)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL reset-" in r.stdout
    _untouched(tree, calls)


def test_a_store_that_does_not_come_back_empty_is_a_failure(tree: Path):
    r, _ = _run(tree, "--confirm-wipe", STUB_LEDGER="7")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL reset-spine-fresh:" in r.stdout
    assert (tree / ".env").is_file(), "the env files move only after both stores are proven empty"


def test_reset_is_never_part_of_the_full_run():
    text = SETUP.read_text(encoding="utf-8")
    assert "for p in validate preflight llm stack app verify; do" in text
