# deploy/shared/ — assets both deployment substrates read

Until v2.66.0 these lived in `deploy/pi/litellm/` (a directory deleted whole
in v2.67.0). Nothing here is specific to
one substrate; the k3s profile (`deploy/k3s/`) and the single-node profile
(`deploy/single/`) both reach into it.

## `litellm/`

| File | Who reads it |
|---|---|
| `register-models.py` | Both installers: `deploy/single/phases/llm.sh` runs it with `--policy models.json`; the k3s `llm` phase runs it against the proxy catalog. Create-only; exit 3 means the operator must fill a row in the LiteLLM UI. |
| `policy.py` | k3s `setup.sh`, `cc-update.sh` and `verify.sh` run `--apply` / `--check`; the Executor names it by path. |
| `checks.sh` | The Executor's `litellm.apply_config_change` runs it as the one check seam before and after a config change. |
| `config.yaml` | k3s: `make-secrets.sh` builds the `cc-litellm-config` ConfigMap from it and `cc-update.sh` refreshes it. Written by `litellm.apply_config_change`. |
| `model-preferences.yaml` | k3s: the declared routing policy `policy.py` applies. Written by `litellm.apply_config_change`. |

`gateway/executor.py` and `runtime/tools.py` name these files by path; change a
path in both places and in `tests/test_litellm_config_change.py` together.

## A misnomer worth stating

`config.yaml` and `model-preferences.yaml` are the **k3s profile's declared
policy**, not shared state. The single profile's equivalents are
`deploy/single/litellm-config.yaml` and `deploy/single/models.json`. They sit
here because they are edited by the same Executor action and checked by the
same scripts as the genuinely shared files.
