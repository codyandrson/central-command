#!/usr/bin/env bash
# Fetch the public model-fact sources into docs/vendor/model-library/ — the
# comparison library the model-evaluation skill reads to sanity-check a
# probed private model against its public counterpart. Run on a machine WITH
# internet access; the result travels inside the normal repo archive like
# every other docs/vendor/ package (see scripts/vendor_docs_fetch.sh).
#
# Sources, all MIT/CC-BY, all attributed in MANIFEST.yaml:
#   - LiteLLM's model_prices_and_context_window.json (MIT) — the supports_*/
#     limits/pricing schema Central Command's own model_info vocabulary mirrors.
#   - models.dev api.json (MIT) — open_weights, knowledge cutoff, release date,
#     limits, per-provider pricing.
#   - LLMStats (CC-BY 4.0) — benchmark scores with dates+sources; only
#     models/, providers/, schemas/, LICENSE.md are kept (no site code).
#   - Aider's polyglot leaderboard (Apache-2.0) — coding benchmark.
#
# Usage: scripts/model_library_fetch.sh
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dest="$root/docs/vendor/model-library"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$dest"

fetch() {  # fetch <url> <dest-file>
  echo "Fetching $1 ..."
  curl -fsSL --max-time 60 "$1" -o "$2"
}

fetch "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json" \
  "$dest/litellm_model_prices.json"

fetch "https://raw.githubusercontent.com/BerriAI/litellm/main/LICENSE" \
  "$dest/litellm_LICENSE.md"

fetch "https://models.dev/api.json" "$dest/models_dev_api.json"

fetch "https://raw.githubusercontent.com/sst/models.dev/dev/LICENSE" \
  "$dest/models_dev_LICENSE.md"

fetch "https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/_data/polyglot_leaderboard.yml" \
  "$dest/aider_polyglot_leaderboard.yml"

fetch "https://raw.githubusercontent.com/Aider-AI/aider/main/LICENSE.txt" \
  "$dest/aider_LICENSE.txt"

echo "Cloning LLMStats ..."
git clone --depth 1 https://github.com/AchilleasDrakou/LLMStats "$tmp/llmstats"
mkdir -p "$dest/llmstats"
cp -r "$tmp/llmstats/models" "$tmp/llmstats/providers" "$tmp/llmstats/schemas" \
      "$tmp/llmstats/LICENSE.md" "$dest/llmstats/"

# --- MANIFEST.yaml: source url, fetched_at, sha256, license --------------
manifest="$dest/MANIFEST.yaml"
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sha() { sha256sum "$1" | cut -d' ' -f1; }

{
  echo "# Regenerate with scripts/model_library_fetch.sh. Every entry below is"
  echo "# upstream data, not Central Command's own writing."
  echo "fetched_at: $now"
  echo "sources:"
  echo "  - name: litellm_model_prices.json"
  echo "    url: https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
  echo "    license: MIT"
  echo "    sha256: $(sha "$dest/litellm_model_prices.json")"
  echo "  - name: models_dev_api.json"
  echo "    url: https://models.dev/api.json"
  echo "    license: MIT"
  echo "    sha256: $(sha "$dest/models_dev_api.json")"
  echo "  - name: aider_polyglot_leaderboard.yml"
  echo "    url: https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/_data/polyglot_leaderboard.yml"
  echo "    license: Apache-2.0"
  echo "    sha256: $(sha "$dest/aider_polyglot_leaderboard.yml")"
  echo "  - name: llmstats/"
  echo "    url: https://github.com/AchilleasDrakou/LLMStats"
  echo "    license: CC-BY-4.0"
  echo "    subpaths: [models, providers, schemas, LICENSE.md]"
} > "$manifest"

cat > "$dest/README.md" <<'EOF'
# Model library — public model facts

Pre-staged comparison data for `skills/model-evaluation`: nothing here is
Central Command's own writing. Regenerate with
`scripts/model_library_fetch.sh` (needs internet); this directory then
travels inside the normal repo archive to the air-gapped target, same as the
rest of `docs/vendor/`.

| File | What it answers | License |
|---|---|---|
| `litellm_model_prices.json` | LiteLLM's own cost map: `supports_*`/limits/pricing per model — the same vocabulary as `model_info` | MIT |
| `models_dev_api.json` | open_weights, knowledge cutoff, release date, context/output limits, per-provider pricing | MIT |
| `llmstats/` (`models/`, `providers/`, `schemas/`) | benchmark scores, dated and sourced | CC-BY 4.0 |
| `aider_polyglot_leaderboard.yml` | Aider's polyglot coding benchmark | Apache-2.0 |

See `MANIFEST.yaml` for exact source URLs, fetch timestamp, and a sha256 per
file. `*_LICENSE.md`/`.txt` alongside each source are the upstream license
texts. Public numbers here are a HYPOTHESIS for a private/gated variant, never
a substitute for `litellm_probe_model` — see
`skills/model-evaluation/references/public-library.md`.
EOF

echo "Model library fetched into $dest"
du -sh "$dest"
