"""What the suite can prove about the sandbox's reach to the API (R17, G-07;
ruled 2026-10-29, Decision 12, epic-small-fixes story 11.13).

The sandbox is CONTAINMENT, not restriction: its egress is open on purpose
(deploy/k3s/60-sandbox.yaml), so NOTHING here asserts an egress deny — the
gate is the capability, never reachability. What stops a sandbox reaching the
API's or the cockpit's port is that those listeners are loopback-only on the
anchor node and nothing publishes them. This file pins the SHIPPED text that
makes that true:

  * every API, sandbox-runner and cockpit command binds loopback, and no unit
    or driver binds from `settings.api_host` (display only, `api/systems.py`);
  * the runner's Job manifest gives a sandbox pod no host network, no service
    account token, no environment and no secret volume, under gVisor, in
    `cc-sandbox`;
  * 60-sandbox.yaml denies ingress and carries no hostNetwork or hostPort;
  * no manifest under deploy/k3s exposes the API's port 8080.

What a static test cannot prove — that a pod in `cc-sandbox` really cannot
connect to the API's or the cockpit's port, nor to the cockpit's
tailnet-published URL (`tailscale serve` proxies /api to the loopback API
without authentication) — is a LIVE DRILL written into deploy/k3s/README.md
and referenced from the cc-test-sweep skill. pytest never runs it; the
release story runs it once after apply with the operator's OK.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
K3S = ROOT / "deploy" / "k3s"

# A trailing flag (story 5 added --ws-max-size) must not break these.
HOST_RE = re.compile(r"--host[ =](\S+)")


def _bind_hosts(text: str) -> list[str]:
    """The value of every `--host` on an `uvicorn`-or-`app:app` command line."""
    out: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if "uvicorn" in line or ":app" in line:
            out += HOST_RE.findall(line)
    return [h.strip("\"'") for h in out]


def _shipped_files(*suffixes: str) -> list[Path]:
    files: list[Path] = []
    for base in (ROOT / "deploy" / "k3s", ROOT / "deploy" / "single"):
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in suffixes and "node_modules" not in p.parts:
                files.append(p)
    return files


# ── the API, the sandbox runner and the cockpit bind loopback ───────────────

@pytest.mark.parametrize(("path", "module"), [
    ("deploy/k3s/cc-uvicorn.service", "central_command.api.app:app"),
    ("deploy/k3s/cc-sandbox-runner.service", "central_command.sandbox.runner:app"),
])
def test_the_k3s_units_bind_loopback(path, module):
    exec_lines = [ln for ln in (ROOT / path).read_text().splitlines() if ln.startswith("ExecStart=")]
    assert len(exec_lines) == 1 and module in exec_lines[0], path
    assert _bind_hosts(exec_lines[0]) == ["127.0.0.1"], path


def test_the_single_node_boot_phase_binds_loopback_for_the_api_and_the_runner():
    text = (ROOT / "deploy/single/phases/boot.sh").read_text()
    by_module = {}
    for line in text.splitlines():
        for module in ("central_command.api.app:app", "central_command.sandbox.runner:app"):
            if module in line and "PROC_CMD=" in line:
                by_module[module] = _bind_hosts(line)
    assert by_module == {"central_command.api.app:app": ["127.0.0.1"],
                         "central_command.sandbox.runner:app": ["127.0.0.1"]}


def test_no_shipped_uvicorn_command_binds_anything_but_loopback():
    for p in _shipped_files(".service", ".sh", ".yaml", ".yml"):
        for host in _bind_hosts(p.read_text(errors="replace")):
            assert host == "127.0.0.1", f"{p.relative_to(ROOT)} binds {host}"


def test_the_bind_check_catches_a_unit_edited_to_listen_on_every_interface():
    text = (K3S / "cc-uvicorn.service").read_text()
    edited = text.replace("--host 127.0.0.1", "--host 0.0.0.0")
    assert edited != text
    assert _bind_hosts(edited) == ["0.0.0.0"]


def test_nothing_binds_from_settings_api_host():
    """`settings.api_host` defaults to 0.0.0.0 and is DISPLAY only (the Systems
    page): a unit or driver that fed it to a listener would open the API."""
    allowed = {ROOT / "central_command" / "config.py", ROOT / "central_command" / "api" / "systems.py"}
    for p in (ROOT / "central_command").rglob("*.py"):
        if p not in allowed:
            assert "api_host" not in p.read_text(), f"{p.relative_to(ROOT)} reads api_host"
    for p in _shipped_files(".service", ".sh", ".yaml", ".yml", ".conf"):
        # CC_API_HOST is the env spelling of the setting (resolve-images.sh has an
        # unrelated registry helper that happens to be called api_host).
        assert not re.search(r"CC_API_HOST|settings\.api_host", p.read_text(errors="replace")), p.relative_to(ROOT)


def test_the_cockpit_defaults_to_loopback_and_refuses_every_interface_without_auth():
    constants = (ROOT / "web/server/lib/constants.ts").read_text()
    assert re.search(r"DEFAULT_HOST\s*=\s*'127\.0\.0\.1'", constants)
    config = (ROOT / "web/server/lib/config.ts").read_text()
    assert re.search(r"host:\s*process\.env\.HOST\s*\|\|\s*DEFAULT_HOST", config)
    refusal = re.search(r"if \(config\.host === '0\.0\.0\.0' && !config\.auth\) \{.*?process\.exit\(1\)",
                        config, re.S)
    assert refusal, "the cockpit no longer refuses HOST=0.0.0.0 without auth"
    assert "NERVE_ALLOW_INSECURE" in refusal.group(0)  # the one explicit override


def test_no_unit_or_driver_sets_the_cockpits_host_to_every_interface():
    for p in _shipped_files(".service", ".sh", ".yaml", ".yml", ".conf"):
        for line in p.read_text(errors="replace").splitlines():
            if line.lstrip().startswith("#"):
                continue
            assert not re.search(r"\bHOST\s*=\s*['\"]?0\.0\.0\.0", line), p.relative_to(ROOT)
    for p in (K3S / "cc-nerve.service", ROOT / "deploy/single/phases/boot.sh"):
        assert not re.search(r"(?m)^[^#\n]*\bHOST=", p.read_text()), (
            f"{p.name} sets HOST: the cockpit's loopback default must stand")


# ── a sandbox pod holds nothing and shares nothing with the host ────────────

def _walk(node, key):
    """Every value stored under `key`, at any depth."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            yield from _walk(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, key)


def test_the_runner_job_manifest_gives_the_pod_no_host_reach_no_token_no_env_no_secret():
    from central_command.sandbox import runner

    manifest = runner._job_manifest("cc-sbx-test", "some-agent")
    assert manifest["metadata"]["namespace"] == "cc-sandbox" == runner.NAMESPACE
    spec = manifest["spec"]["template"]["spec"]
    for flag in ("hostNetwork", "hostPID", "hostIPC"):
        assert not list(v for v in _walk(manifest, flag) if v), flag
    assert spec["automountServiceAccountToken"] is False
    assert spec["runtimeClassName"] == "gvisor"
    assert "serviceAccountName" not in spec
    for c in spec["containers"]:
        assert "env" not in c and "envFrom" not in c, "a sandbox container must hold no environment"
        assert not (c.get("securityContext") or {}).get("privileged")
        assert not list(_walk(c, "hostPort"))
    assert spec["volumes"], "the workspace volume is expected"
    for v in spec["volumes"]:
        kinds = set(v) - {"name"}
        assert kinds == {"emptyDir"}, f"volume {v['name']} is {sorted(kinds)}: only emptyDir is allowed"
    assert not list(_walk(manifest, "secretKeyRef")) and not list(_walk(manifest, "secretRef"))


# ── 60-sandbox.yaml and the rest of the manifests ───────────────────────────

def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def test_the_sandbox_manifest_denies_ingress_and_publishes_nothing():
    docs = _docs(K3S / "60-sandbox.yaml")
    deny = [d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "default-deny-ingress"]
    assert len(deny) == 1
    (policy,) = deny
    assert policy["metadata"]["namespace"] == "cc-sandbox"
    assert policy["spec"]["podSelector"] == {}
    assert "Ingress" in policy["spec"]["policyTypes"]
    assert not policy["spec"].get("ingress"), "an ingress rule would let something reach INTO a sandbox"
    for d in docs:
        assert not [v for v in _walk(d, "hostNetwork") if v], d["metadata"]["name"]
        assert not list(_walk(d, "hostPort")), d["metadata"]["name"]
    # No second policy re-opens ingress in this namespace.
    for d in docs:
        if d["kind"] == "NetworkPolicy" and "Ingress" in d["spec"].get("policyTypes", []):
            assert not d["spec"].get("ingress"), d["metadata"]["name"]


def test_no_manifest_exposes_the_apis_port():
    api_port = 8080
    for path in sorted(K3S.glob("*.yaml")):
        for d in _docs(path):
            where = f"{path.name} {d.get('kind')}/{d.get('metadata', {}).get('name')}"
            assert api_port not in list(_walk(d, "hostPort")), where
            if d.get("kind") == "Service":
                for p in d["spec"].get("ports", []):
                    for k in ("port", "targetPort", "nodePort"):
                        assert p.get(k) != api_port, f"{where} publishes {k} {api_port}"
            if d.get("kind") in ("Ingress", "IngressRoute"):
                pytest.fail(f"{where}: the deployment uses no Ingress")


# ── the live drill is written down, with no deployment fact ─────────────────

def _drill() -> str:
    readme = (K3S / "README.md").read_text()
    m = re.search(r"^### Sandbox reach drill\b.*?(?=^#{1,3} )", readme, re.M | re.S)
    assert m, "deploy/k3s/README.md has no 'Sandbox reach drill' section"
    return m.group(0)


def test_the_readme_carries_the_drill_for_all_three_targets():
    drill = _drill()
    assert "cc-sandbox" in drill and "curl" in drill
    assert "<anchor-address>:8080" in drill          # the API's port
    assert "<anchor-address>:3080" in drill          # the cockpit's port
    assert "<cockpit-tailnet-url>" in drill          # the tailscale serve front
    assert "tailscale serve" in drill
    assert re.search(r"delete pod", drill), "the drill must delete its pod"
    assert "never run by pytest" in drill.lower()


def test_the_drill_names_no_deployment_fact():
    drill = _drill()
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", drill.replace("127.0.0.1", ""))
    assert ".ts.net" not in drill and "@" not in drill


def test_the_test_sweep_skill_points_at_the_drill():
    text = (ROOT / ".claude/skills/cc-test-sweep/SKILL.md").read_text()
    assert "Sandbox reach drill" in text and "deploy/k3s/README.md" in text

