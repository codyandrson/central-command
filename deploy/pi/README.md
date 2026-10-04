# deploy/pi/ — part live, part retired

`deploy/pi/` predates the two-node k3s cluster (`deploy/k3s/`, 2026-07-31) and
several files here have since been superseded — but not the whole directory.
`deploy/k3s/`'s own scripts and runbook still read several files here
directly, and deleting this tree would break a live deployment. Before calling
anything in here "the superseded compose stack," check which file you mean.

## LIVE

| Path | Read by |
|---|---|
| `deploy/pi/.env` | `deploy/k3s/make-secrets.sh`, `mint-keys.sh`, `backup.sh`, `cc-update.sh`, `graph-browse.sh` — holds `LITELLM_SALT_KEY`, `N8N_ENCRYPTION_KEY` and every other secret those scripts source. |
| `deploy/pi/litellm/config.yaml` | Built into the `cc-litellm-config` ConfigMap by `make-secrets.sh` and re-applied by `cc-update.sh` on a release that changes it — the live LiteLLM proxy config. |
| `deploy/pi/litellm/model-preferences.yaml`, `policy.py`, `register-models.py`, `checks.sh` | The live LiteLLM routing policy: `cc-update.sh` runs `policy.py --apply`/`--check`, and `deploy/k3s/README.md` walks `register-models.py` for onboarding a new model. |
| `deploy/pi/cc-nerve.service` | Still the live cockpit-UI unit — `deploy/k3s/README.md`'s systemd section installs it FROM this path (`cc-uvicorn` moved to a k3s-aware unit in `deploy/k3s/`; the cockpit unit itself never needed to). |

## FROZEN for one release — read by nothing in this tree

| Path | Why it is still here |
|---|---|
| `deploy/pi/graphiti/` (`Dockerfile`, `config.yaml`, `patches/`) | The retired Graphiti MCP server's build context (graphiti-core runs inside the app since the 2026-10-04 design record). The update that removed the server is run by the PREVIOUS release's `deploy/k3s/cc-update.sh`, which still carries an image row and a configmap row pointing here: if any byte under this directory (or `deploy/k3s/build-graphiti-image.sh`) changed, that updater would rebuild the image and then rollout-restart the Deployment its tombstone pass just deleted — dying mid-update with no rollback. So it stays BYTE-IDENTICAL (`tests/test_graphiti_server_boundary.py` pins it) and is deleted by the next release, which this release's updater applies. The two core fixes it carried live in `deploy/graphiti-patches/`. Do not edit. |

## RETIRED — kept as rollback path or reference only

| Path | Status |
|---|---|
| `deploy/pi/docker-compose.yml` | The pre-k3s self-contained Pi stack. `deploy/k3s/README.md` documents it as the last-resort fallback if the cluster itself is unusable — never run alongside k3s (it fights for port 5442). It still declares the retired Graphiti server and requires its three keys (`EMBEDDER_API_KEY`, `RERANKER_API_KEY`, …) that `deploy/pi/.env.example` no longer lists; the app no longer uses that service. |
| `deploy/pi/cc-uvicorn.service` | Superseded by `deploy/k3s/cc-uvicorn.service`. The old unit's `ExecStartPre=docker compose up -d` resurrects the Docker stack and fights k3s for 5442 — do not install it while the cluster runs. |
| `deploy/pi/backup.sh`, `cc-backup.service` | Superseded by `deploy/k3s/backup.sh` + `deploy/k3s/cc-backup.service`, which back up all four k3s-hosted stores instead of the Docker Compose ones. |
| `deploy/pi/verify.sh` | Superseded by `deploy/k3s/verify.sh` (ported from this file — see its header). Checked the Docker stack; has no k3s awareness. |
| `deploy/pi/migrate-from-desktop.sh` | One-time cutover script (desktop → Pi, 2026-07-26). Spent; kept for the history and as a template if a similar migration is ever needed again. |
| `deploy/pi/n8n/calendar-facade-writes.md` | A design/journal record of one past n8n workflow edit, not a live reference — nothing reads it. |
