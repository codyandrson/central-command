# deploy/pi/ — duplicates and a retired stack, both gone in v2.67.0

Since v2.66.0 nothing here is read by name. The live set moved:

| Was | Now |
|---|---|
| `deploy/pi/.env` (untracked) | `deploy/k3s/.env` — `./deploy/k3s/init-env.sh` moves it once and leaves a symlink here; `deploy/k3s/env-path.sh` is the one resolver every k3s script uses |
| `deploy/pi/.env.example` | `deploy/k3s/.env.example` |
| `deploy/pi/litellm/*` | `deploy/shared/litellm/*` (read by both deployment substrates) |
| `deploy/pi/cc-nerve.service` | `deploy/k3s/cc-nerve.service` |

The copies below stay for exactly one release, byte-identical, because the
update INTO v2.66.0 is run by the installed v2.65.x `cc-update.sh`, whose
configmap row, units glob and policy-apply still name these paths against the
merged tree (see `.claude/rules/deploy-k3s.md`, "the update ACROSS a removal").
Do not edit them: a changed `litellm/config.yaml` here would restart
`cc-litellm` during that update for nothing.

## Duplicates (unread since v2.66.0, deleted in v2.67.0)

`.env.example`, `litellm/{config.yaml, model-preferences.yaml, policy.py,
register-models.py, checks.sh}`, `cc-nerve.service`.

## Retired — the pre-k3s Compose stack (deleted in v2.67.0)

`docker-compose.yml`, `cc-uvicorn.service` (its `ExecStartPre=docker compose
up -d` fights k3s for port 5442 — never install it beside the cluster),
`backup.sh`, `cc-backup.service`, `cc-backup.timer`, `verify.sh`,
`migrate-from-desktop.sh`, `n8n/calendar-facade-writes.md`. The Compose file
had already drifted from the deployment (no Graphiti, crawler, speech or
reranker). After v2.67.0 it is reachable as
`git show v2.65.3:deploy/pi/docker-compose.yml`.
