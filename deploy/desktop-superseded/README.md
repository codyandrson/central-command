# Superseded DESKTOP artefacts — history, not runnable

Nothing in this directory runs. It is the deployment as it existed on the operator's
desktop before **2026-07-26**, when Central Command moved to the Raspberry Pi and
became self-contained (`deploy/pi/`). Kept because the journal references it and
because it is the rollback record, not because anything should be installed
from here.

They were moved out of `deploy/` on 2026-07-29 for one blunt reason: two of the
filenames — `cc-uvicorn.service` and `cc-nerve.service` — are **identical to
the live Pi units in `deploy/pi/`, with different content**. The desktop pair
are systemd **`--user`** units that assume linger and podman; the Pi pair are
**system** units for Docker CE. Copying the wrong one into
`/etc/systemd/system/` yields a unit that looks right and never starts.

| file | why it is dead |
|---|---|
| `cc-uvicorn.service` | user unit + linger. The Pi runs a system unit — `deploy/pi/cc-uvicorn.service`. |
| `cc-nerve.service` | same. Live version: `deploy/pi/cc-nerve.service`. |
| `homelab-portcheck.sh` | Existed only because **rootless podman's** port forwarders fail to bind at boot. The Pi runs Docker CE as root with `restart: always`, so there is nothing to self-heal. Do not port it. |
| `homelab-portcheck.service` / `.timer` | The 45s-after-boot + 15-minute schedule for the above. |

The live deployment is **`deploy/pi/`** — one compose stack, two system units,
`verify.sh` to assert it, `backup.sh` on a nightly timer.
