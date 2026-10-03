"""Every Systems-page link the API can show is DERIVED by the installers.

2026-09-26: the reference deployment's clean-slate rebuild recreated the root
.env from .env.example and the Systems page came back with no "Open →" links
at all — the CC_*_UI_URL / CC_*_DOCS_URL / CC_NEO4J_BROWSER_URL values are
display-only, so no phase had ever filled them and nothing noticed. v2.52.0
made both drivers derive them (k3s from the node's tailnet name + its
`tailscale serve` map; single from the loopback ports it already answers).

This walk keeps that true: the list of link settings is read from
central_command/api/systems.py itself, so a new Systems row whose link nobody
taught the installers to derive fails here, not on the next rebuild. Only
llama-swap is exempt on both profiles (it runs on a host the installer cannot
see), plus the two services the single profile does not deploy.
"""

import re
from pathlib import Path

from tests.installer_source import installer_source

ROOT = Path(__file__).resolve().parents[1]
SYSTEMS = ROOT / "central_command/api/systems.py"
K3S = ROOT / "deploy/k3s/setup.sh"
SINGLE = ROOT / "deploy/single/setup.sh"
ENV_EXAMPLE = ROOT / ".env.example"

# Typed by hand everywhere: nothing in either env file says where llama-swap runs.
NOT_DERIVABLE = {"CC_LLAMA_SWAP_UI_URL"}
# Not part of the single-node profile (k3s-only services), so no link there.
NOT_ON_SINGLE = {"CC_VLOGS_UI_URL", "CC_DB_UI_URL"}


def link_settings() -> set[str]:
    """`"url": settings.<name> or None` rows of the static table → CC_ keys."""
    src = SYSTEMS.read_text()
    names = set(re.findall(r'"url":\s*settings\.(\w+) or None', src))
    assert names, "no settings-driven url rows found in systems.py"
    return {"CC_" + n.upper() for n in names}


def test_every_link_setting_has_an_env_example_line():
    example = ENV_EXAMPLE.read_text()
    for key in sorted(link_settings()):
        assert re.search(rf"^{key}=", example, flags=re.M), f"{key} missing from .env.example"


def test_k3s_driver_derives_every_link():
    src = K3S.read_text()
    assert "derive_systems_links" in src
    for key in sorted(link_settings() - NOT_DERIVABLE):
        assert key in src, f"deploy/k3s/setup.sh never derives {key}"


def test_single_driver_derives_every_link_it_deploys():
    src = installer_source()   # setup.sh + phases/*.sh — phase_app is in phases/app.sh
    for key in sorted(link_settings() - NOT_DERIVABLE - NOT_ON_SINGLE):
        assert re.search(rf'set_kv_if_unset\s+"\$ENV_FILE"\s+{key}\b', src), (
            f"deploy/single/setup.sh never derives {key}"
        )


def test_links_never_overwrite_an_operator_value():
    """Every link write goes through set_kv_if_unset — a URL the operator typed
    (a reverse proxy, a different tailnet name) survives every app re-run."""
    pattern = re.compile(r'^\s*set_kv\s+"\$(?:APP_ENV|ENV_FILE)"\s+(CC_\w+_(?:UI|DOCS|BROWSER)_URL)\b', re.M)
    for path, src in ((K3S, K3S.read_text()), (SINGLE, installer_source())):
        hits = pattern.findall(src)
        assert not hits, f"{path.relative_to(ROOT)} overwrites {hits} with a bare set_kv"


def _loadbalancer_ports() -> set[int]:
    """Every port a `type: LoadBalancer` Service in deploy/k3s/ declares —
    the ports ServiceLB binds on every interface of every node."""
    import yaml

    ports: set[int] = set()
    for path in sorted((ROOT / "deploy/k3s").glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if isinstance(doc, dict) and doc.get("kind") == "Service" \
                    and (doc.get("spec") or {}).get("type") == "LoadBalancer":
                ports.update(int(p["port"]) for p in doc["spec"]["ports"])
    assert ports, "no LoadBalancer Service found under deploy/k3s/"
    return ports


def _k3s_link_rows() -> tuple[dict[str, int], dict[str, int]]:
    """(plain-http ServiceLB rows, tailscale-serve-fronted rows) of
    derive_systems_links, each as {key: port}."""
    body = K3S.read_text().split("derive_systems_links() {", 1)[1].split("\n}\n", 1)[0]
    loops = re.findall(r"for row in(.*?); do", body, flags=re.S)
    assert len(loops) == 2, "derive_systems_links no longer has its two row tables"
    tables = [{k: int(p) for k, p in re.findall(r'"(CC_\w+) (\d+) ', rows)} for rows in loops]
    assert all(tables), "a link row table parsed empty"
    return tables[0], tables[1]


def test_k3s_never_fronts_a_loadbalancer_port_with_tailscale_serve():
    """2026-10-02: the crawler and VictoriaLogs links were derived as
    https://<host>:8091 and :9428 through `tailscale serve` entries on the
    very ports their LoadBalancer Services own. ServiceLB's hostPort DNAT
    takes the connection ahead of tailscaled and answers the TLS handshake in
    plain http, so both links were dead from the day they were set. A
    ServiceLB port is linked as plain http, like LiteLLM's; serve fronts only
    what is loopback-only on the host."""
    lb = _loadbalancer_ports()
    plain, served = _k3s_link_rows()
    clash = {k: p for k, p in served.items() if p in lb}
    assert not clash, f"serve-fronted link rows on LoadBalancer ports: {clash}"
    stray = {k: p for k, p in plain.items() if p not in lb}
    assert not stray, f"plain-http link rows whose port no LoadBalancer Service declares: {stray}"
