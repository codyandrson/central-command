"""litellm.apply_config_change — the verified-restart-with-auto-rollback arc.

The highest-blast-radius capability in the registry: it edits LiteLLM's config
FILES and restarts the proxy. So the tests here are about the arc's ORDER —
what has been written by the time each step runs — not about kubectl. Every
side effect is faked at the three seams the handler owns (`_run` for
kubectl/policy.py, `_git`, `_litellm_run_checks`) and the repo root is a
tmp_path, so nothing here touches the real tree, the real cluster or the real
proxy.
"""

from __future__ import annotations

import pytest

from central_command import events
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import executor
from central_command.gateway.capabilities import gated_write_names

CONFIG = "deploy/pi/litellm/config.yaml"
PREFS = "deploy/pi/litellm/model-preferences.yaml"

OLD_CONFIG = "router_settings:\n  allowed_fails: 1\n"
NEW_CONFIG = "router_settings:\n  allowed_fails: 3\n  routing_strategy: least-busy\n"
OLD_PREFS = "models: {}\n"
NEW_PREFS = "models: {}\naliases:\n  - cc-default\n"


class Harness:
    """Every seam the handler reaches, faked and recorded."""

    def __init__(self, root):
        self.root = root
        self.runs: list[list[str]] = []
        self.gits: list[tuple[str, ...]] = []
        self.checks: list[tuple[int, str]] = []   # queued answers, popped in order
        self.check_calls = 0
        self.emitted: list[tuple[str, dict]] = []
        self.items: list[tuple] = []
        self.dirty = ""

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    async def run(self, cmd=None, *, shell_cmd=None, stdin=None, cwd=None, timeout=0.0):
        self.runs.append(list(cmd or []))
        return 0, "rendered: yaml", ""

    async def git(self, *args, cwd=None):
        self.gits.append(args)
        if args[0] == "status":
            return 0, self.dirty, ""
        if args[0] == "rev-parse":
            return 0, "abc1234\n", ""
        return 0, "", ""

    async def check(self):
        self.check_calls += 1
        return self.checks.pop(0) if self.checks else (0, "all checks green")

    async def emit(self, kind, ref_id=None, payload=None, actor=None):
        self.emitted.append((kind, payload or {}))
        return {"kind": kind}

    def kinds(self) -> list[str]:
        return [k for k, _ in self.emitted]


@pytest.fixture
def h(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "demo_mode", True)
    (tmp_path / "deploy/pi/litellm").mkdir(parents=True)
    (tmp_path / CONFIG).write_text(OLD_CONFIG)
    (tmp_path / PREFS).write_text(OLD_PREFS)
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n")
    monkeypatch.setattr(settings, "litellm_kubeconfig", str(kubeconfig))
    monkeypatch.setattr(executor, "_repo_root", lambda: tmp_path)

    harness = Harness(tmp_path)
    monkeypatch.setattr(executor, "_run", harness.run)
    monkeypatch.setattr(executor, "_git", harness.git)
    monkeypatch.setattr(executor, "_litellm_run_checks", harness.check)
    monkeypatch.setattr(events, "emit", harness.emit)
    return harness


async def apply(files: dict, proposal_id: str = "prop_deadbeef1234") -> str:
    token = executor._current_proposal_id.set(proposal_id)
    try:
        return await executor._litellm_apply_config_change(
            {"files": files}, "lee", "litellm-manager")
    finally:
        executor._current_proposal_id.reset(token)


# --- the registry/executor coupling ------------------------------------------


def test_the_capability_is_a_gated_write_with_a_handler():
    assert "litellm.apply_config_change" in gated_write_names()
    assert executor.HANDLERS["litellm.apply_config_change"] is executor._litellm_apply_config_change


# --- validate: refuse before anything is written ------------------------------


@pytest.mark.parametrize(
    "files, expect",
    [
        ({CONFIG: "router_settings:\n  bad: [unclosed\n"}, "not valid YAML"),
        ({CONFIG: "general_settings:\n  master_key: sk-ant-abc123\n"}, "secret shape"),
        ({CONFIG: "keys:\n  - ATATT-jira-token\n"}, "secret shape"),
        ({"deploy/pi/.env": "X=1\n"}, "is not one of"),
        ({}, "non-empty"),
        (
            {CONFIG: "model_list:\n  - model_name: cc-default\n  - model_name: graphiti-llm\n"},
            "cc-embedding",
        ),
    ],
)
async def test_validation_refuses_and_writes_nothing(h, files, expect):
    with pytest.raises(executor.ExecutorError) as exc:
        await apply(files)
    assert expect in str(exc.value)
    assert h.read(CONFIG) == OLD_CONFIG
    assert h.check_calls == 0 and h.gits == [] and h.runs == []


async def test_unprovisioned_kubeconfig_refuses_before_writing_anything(h, monkeypatch):
    monkeypatch.setattr(settings, "litellm_kubeconfig", "")
    with pytest.raises(executor.ExecutorError) as exc:
        await apply({CONFIG: NEW_CONFIG})
    assert "kubeconfig not provisioned" in str(exc.value)
    assert h.read(CONFIG) == OLD_CONFIG
    assert h.check_calls == 0 and h.gits == [] and h.runs == []


async def test_a_missing_kubeconfig_file_is_the_same_refusal(h, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "litellm_kubeconfig", str(tmp_path / "gone.kubeconfig"))
    with pytest.raises(executor.ExecutorError, match="kubeconfig not provisioned"):
        await apply({CONFIG: NEW_CONFIG})
    assert h.read(CONFIG) == OLD_CONFIG


async def test_uncommitted_changes_in_those_files_refuse(h):
    h.dirty = f" M {CONFIG}\n"
    with pytest.raises(executor.ExecutorError, match="uncommitted changes"):
        await apply({CONFIG: NEW_CONFIG})
    assert h.read(CONFIG) == OLD_CONFIG
    assert h.check_calls == 0


# --- pre-check: never build on a broken baseline ------------------------------


async def test_failing_pre_check_aborts_before_the_write(h):
    h.checks = [(1, "CHECK FAILED: liveliness: HTTP 000")]
    with pytest.raises(executor.ExecutorError) as exc:
        await apply({CONFIG: NEW_CONFIG})
    assert "PRE-CHECK failed" in str(exc.value)
    assert "liveliness" in str(exc.value)
    assert h.read(CONFIG) == OLD_CONFIG          # nothing written
    assert h.check_calls == 1                     # and nothing restarted
    assert h.runs == []
    assert [g[0] for g in h.gits] == ["status"]   # no commit
    assert h.kinds() == []


# --- green path ---------------------------------------------------------------


async def test_green_path_writes_commits_restarts_and_records_both_checks(h):
    h.checks = [(0, "PRE green"), (0, "POST green")]
    out = await apply({CONFIG: NEW_CONFIG, PREFS: NEW_PREFS})

    assert h.read(CONFIG) == NEW_CONFIG and h.read(PREFS) == NEW_PREFS
    assert h.check_calls == 2
    assert "PRE green" in out and "POST green" in out

    verbs = [g[0] for g in h.gits]
    assert verbs == ["status", "add", "commit", "rev-parse", "push"]
    commit_msg = next(g for g in h.gits if g[0] == "commit")[2]
    assert "prop_deadbeef1234" in commit_msg and "litellm-manager" in commit_msg and "lee" in commit_msg

    joined = [" ".join(c) for c in h.runs]
    assert any("create configmap cc-litellm-config" in c for c in joined)
    assert any(c.endswith("apply -f -") for c in joined)
    # model-preferences.yaml is NOT mounted anywhere: policy.py --apply is its
    # render step, and without it the post-check's --check would fail.
    assert any("policy.py --apply" in c for c in joined)
    assert any("rollout restart deployment/cc-litellm" in c for c in joined)
    assert any("rollout status deployment/cc-litellm" in c for c in joined)

    kind, payload = h.emitted[0]
    assert kind == "litellm.config_applied"
    assert payload["pre_check"] == "PRE green" and payload["post_check"] == "POST green"
    assert payload["files"] == [CONFIG, PREFS]


async def test_a_config_only_change_does_not_run_policy_apply(h):
    await apply({CONFIG: NEW_CONFIG})
    assert not any("policy.py" in " ".join(c) for c in h.runs)


# --- rollback -----------------------------------------------------------------


async def test_post_check_failure_restores_the_files_and_fails_the_proposal(h):
    h.checks = [(0, "PRE green"), (1, "CHECK FAILED: completion HTTP 500"), (0, "ROLLBACK green")]
    with pytest.raises(executor.ExecutorError) as exc:
        await apply({CONFIG: NEW_CONFIG})

    assert h.read(CONFIG) == OLD_CONFIG           # the snapshotted bytes are back
    assert h.check_calls == 3
    msg = str(exc.value)
    assert "ROLLED BACK" in msg
    assert "completion HTTP 500" in msg and "ROLLBACK green" in msg   # both outputs

    assert h.kinds() == ["litellm.config_rolled_back"]
    payload = h.emitted[0][1]
    assert payload["post_check"].endswith("completion HTTP 500")
    assert payload["rollback_check"] == "ROLLBACK green"
    # the revert is committed too — the repo never stays ahead of the cluster
    assert [g[0] for g in h.gits].count("commit") == 2
    assert "revert" in next(g for g in h.gits[::-1] if g[0] == "commit")[2]


async def test_a_failed_rollout_is_treated_as_a_post_check_failure(h, monkeypatch):
    restarts = []

    async def failing_run(cmd=None, **kw):
        h.runs.append(list(cmd or []))
        if "restart" in (cmd or []):
            restarts.append(cmd)
            if len(restarts) == 1:      # the APPLY restart fails; the rollback's works
                return 1, "", "deployment not found"
        return 0, "rendered", ""

    h.checks = [(0, "PRE green"), (0, "ROLLBACK green")]
    monkeypatch.setattr(executor, "_run", failing_run)
    with pytest.raises(executor.ExecutorError) as exc:
        await apply({CONFIG: NEW_CONFIG})
    assert "ROLLED BACK" in str(exc.value)
    assert "rollout restart failed" in str(exc.value)
    assert h.read(CONFIG) == OLD_CONFIG


async def test_red_rollback_promotes_an_operator_item_and_stops(h, monkeypatch):
    loaded = {"id": "prop_deadbeef1234", "session_id": "sess_1", "agent_id": "litellm-manager"}

    async def fake_load(pid):
        assert pid == "prop_deadbeef1234"
        return loaded

    async def fake_item(*args, **kw):
        h.items.append(args)

    monkeypatch.setattr(repo, "load_proposal", fake_load)
    monkeypatch.setattr(repo, "create_operator_item", fake_item)

    h.checks = [(0, "PRE green"), (1, "POST red"), (1, "ROLLBACK red")]
    with pytest.raises(executor.ExecutorError) as exc:
        await apply({CONFIG: NEW_CONFIG})

    msg = str(exc.value)
    assert "ROLLBACK FAILED" in msg and "POST red" in msg and "ROLLBACK red" in msg
    assert h.kinds() == ["litellm.config_rollback_failed"]
    payload = h.emitted[0][1]
    assert payload["operator_item_id"] == h.items[0][0]
    assert h.items[0][2:4] == ("sess_1", "litellm-manager")
    assert "rollback" in h.items[0][4].lower()


async def test_a_red_rollback_without_a_session_is_still_loud(h, monkeypatch):
    async def no_row(pid):
        return None

    monkeypatch.setattr(repo, "load_proposal", no_row)
    h.checks = [(0, "PRE green"), (1, "POST red"), (1, "ROLLBACK red")]
    with pytest.raises(executor.ExecutorError, match="ROLLBACK FAILED"):
        await apply({CONFIG: NEW_CONFIG})
    assert h.kinds() == ["litellm.config_rollback_failed"]
    assert h.emitted[0][1]["operator_item_id"] is None
