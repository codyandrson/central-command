# Superseded DESKTOP artefacts — history, not runnable

Nothing in this directory runs. It is the deployment as it existed on the operator's
desktop before **2026-07-26**, when Central Command moved off it and became
self-contained. Kept because the journal references it and because it is the
rollback record, not because anything should be installed from here.

They were moved out of `deploy/` on 2026-07-29 for one blunt reason: two of the
filenames — `cc-uvicorn.service` and `cc-nerve.service` — are **identical to
the live units in `deploy/k3s/`, with different content**. The desktop pair
are systemd **`--user`** units that assume linger and podman; the live pair are
**system** units for the k3s deployment. Copying the wrong one into
`/etc/systemd/system/` yields a unit that looks right and never starts.

| file | why it is dead |
|---|---|
| `cc-uvicorn.service` | user unit + linger. The live one is a system unit — `deploy/k3s/cc-uvicorn.service`. |
| `cc-nerve.service` | same. Live version: `deploy/k3s/cc-nerve.service`. |
| `homelab-portcheck.sh` | Existed only because **rootless podman's** port forwarders fail to bind at boot. The live deployment publishes its ports through k3s, with no rootless podman involved, so there is nothing to self-heal. Do not port it. |
| `homelab-portcheck.service` / `.timer` | The 45s-after-boot + 15-minute schedule for the above. |

The live deployment is **`deploy/k3s/`** — see its `README.md`.
