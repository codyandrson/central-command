# deploy/shared/ — assets both deployment substrates read

Until v2.66.0 these lived in `deploy/pi/litellm/` (a directory deleted whole
in v2.67.0). Nothing here is specific to
one substrate; the k3s profile (`deploy/k3s/`) and the single-node profile
(`deploy/single/`) both reach into it.

## `litellm/`

| File | Who reads it |
|---|---|
| `register-models.py` | Both installers: `deploy/single/phases/llm.sh` runs it with `--policy models.json`; the k3s `llm` phase runs it with its default, `model-preferences.yaml`. Create-only; exit 3 means the operator must fill a row in the LiteLLM UI. |
| `checks.sh` | Run by hand to check the proxy is healthy and serves the required aliases. Its caller, the Executor's `litellm.apply_config_change`, was removed (DL-223). |
| `config.yaml` | k3s: `make-secrets.sh` builds the `cc-litellm-config` ConfigMap from it and `cc-update.sh` refreshes it. Read by the `litellm_read_config` tool. |
| `model-preferences.yaml` | k3s: the role aliases `register-models.py` creates as skeletons (the twin of `deploy/single/models.json`). Declares nothing about a specific model: per-model timeouts, costs and capabilities live on the rows in the proxy's database (DL-226). Read by the `litellm_read_config` tool. |

`runtime/tools.py` names `config.yaml` and `model-preferences.yaml` by path
(`litellm_read_config`); change a path there too.

## A misnomer worth stating

`config.yaml` and `model-preferences.yaml` are the **k3s profile's** proxy
config and role-alias declaration, not shared state. The single profile's
equivalents are `deploy/single/litellm-config.yaml` and
`deploy/single/models.json`. They sit here because the shared
`register-models.py` reads the declaration by its sibling path.
