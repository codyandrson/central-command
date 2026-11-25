"""CPU on the compute node: no CPU limit on the speech pod, the graph ahead of it.

DL-220 (2026-11-18). ONNX Runtime ignores the container's CPU quota and spins
one thread per PHYSICAL core, and Speaches has no thread setting, so a CPU
limit on the speech pod made synthesis slower than playback (the pod was
throttled in 97% of scheduler periods). The limit is gone; the memory limit
stays. Without a limit a contended CPU is divided in proportion to REQUESTS,
so Neo4j (which shares the compute node) must request at least twice what the
speech pod does, and the requests of everything the manifests place on the
compute node must still fit a 2-CPU node with room for kube-system.
"""

from pathlib import Path

import yaml

K3S = Path(__file__).resolve().parents[1] / "deploy" / "k3s"
# The README states no node sizing, so the smallest supported compute node is
# assumed to be 2 CPU; 1.5 leaves half a CPU for kube-system and the kubelet.
COMPUTE_NODE_REQUEST_CEILING_M = 1500
WORKLOADS = ("Deployment", "DaemonSet", "StatefulSet")


def _docs():
    for path in sorted(K3S.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if doc:
                yield path.name, doc


def _workload(name):
    for _, doc in _docs():
        if doc.get("kind") in WORKLOADS and doc["metadata"]["name"] == name:
            return doc
    raise AssertionError(f"no workload {name} under deploy/k3s")


def _container(name, container):
    for c in _workload(name)["spec"]["template"]["spec"]["containers"]:
        if c["name"] == container:
            return c
    raise AssertionError(f"{name} has no container {container}")


def _milli(q) -> int:
    s = str(q)
    return int(s[:-1]) if s.endswith("m") else int(float(s) * 1000)


def test_the_speech_container_has_a_memory_limit_and_no_cpu_limit():
    res = _container("cc-speech", "speech")["resources"]
    assert res["limits"].get("memory") == "4Gi", "memory is not compressible: the limit stays"
    assert "cpu" not in res["limits"], (
        "ONNX Runtime ignores a CPU quota and spins one thread per core"
    )
    assert _milli(res["requests"]["cpu"]) > 0, "no limit means the CPU REQUEST is its whole claim"


def test_neo4j_requests_at_least_twice_the_speech_pods_cpu():
    neo = _milli(_container("cc-neo4j", "neo4j")["resources"]["requests"]["cpu"])
    speech = _milli(_container("cc-speech", "speech")["resources"]["requests"]["cpu"])
    assert neo >= 2 * speech, (
        f"contended CPU is shared by request: the graph ({neo}m) must hold twice speech's ({speech}m)"
    )


def _places_on_compute(pod_spec) -> bool:
    """Required to, or merely preferring, the compute node (a floating pod may
    land there, so it counts against it)."""
    if (pod_spec.get("nodeSelector") or {}).get("cc-role/compute") == "true":
        return True
    aff = (pod_spec.get("affinity") or {}).get("nodeAffinity") or {}
    required = (aff.get("requiredDuringSchedulingIgnoredDuringExecution") or {}).get(
        "nodeSelectorTerms"
    ) or []
    preferred = [
        p["preference"] for p in aff.get("preferredDuringSchedulingIgnoredDuringExecution") or []
    ]
    return any(
        e["key"] == "cc-role/compute"
        for term in [*required, *preferred]
        for e in term.get("matchExpressions") or []
    )


def _compute_node_requests() -> dict[str, int]:
    """workload/container -> CPU request in millicores, for every static
    workload that can run on the compute node (a DaemonSet runs on both)."""
    out = {}
    for _, doc in _docs():
        if doc.get("kind") not in WORKLOADS:
            continue
        spec = doc["spec"]["template"]["spec"]
        if doc["kind"] == "DaemonSet" or _places_on_compute(spec):
            for c in spec["containers"]:
                out[f"{doc['metadata']['name']}/{c['name']}"] = _milli(
                    ((c.get("resources") or {}).get("requests") or {}).get("cpu", 0)
                )
    return out


def test_what_the_manifests_place_on_the_compute_node_still_fits_a_two_cpu_node():
    requests = _compute_node_requests()
    # the pods this reasoning is about must be in the sum, or the sum is vacuous
    for expected in ("cc-neo4j/neo4j", "cc-speech/speech", "cc-crawler/crawler", "cc-vlogs/vlogs"):
        assert any(k.startswith(expected.split("/")[0] + "/") for k in requests), expected
    total = sum(requests.values())
    assert total <= COMPUTE_NODE_REQUEST_CEILING_M, (total, requests)
