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
#   - Epoch AI's Benchmarking Hub CSV export (CC-BY 4.0) — broad multi-
#     benchmark scores over time, one CSV per benchmark.
#   - LMArena leaderboard-dataset (CC-BY 4.0) — human-preference Elo, the
#     "latest" text-category snapshot only (not the 55k-battle history).
#   - BFCL (Berkeley Function Calling Leaderboard) overall results
#     (Apache-2.0, from the gorilla repo's gh-pages branch) — tool-calling
#     scores per model.
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

echo "Fetching Epoch AI Benchmarking Hub ..."
fetch "https://epoch.ai/data/benchmark_data.zip" "$tmp/epoch_benchmark_data.zip"
mkdir -p "$dest/epoch"
unzip -oq "$tmp/epoch_benchmark_data.zip" -d "$dest/epoch"
# The zip's own README.md states the CC-BY licensing + citation — kept as
# the license reference instead of inventing a separate LICENSE file.
mv "$dest/epoch/README.md" "$dest/epoch_LICENSE.md"

fetch "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset/resolve/main/text/latest-00000-of-00001.parquet" \
  "$dest/lmarena_leaderboard_latest.parquet"
cat > "$dest/lmarena_LICENSE.md" <<'EOF'
# LMArena leaderboard-dataset — license

Source: https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset
The dataset repository declares `license:cc-by-4.0` in its HF tags/README
frontmatter. No separate LICENSE file is committed in the repo; this file
records the tag. https://creativecommons.org/licenses/by/4.0/
EOF

fetch "https://raw.githubusercontent.com/ShishirPatil/gorilla/gh-pages/data_overall.csv" \
  "$dest/bfcl_data_overall.csv"
fetch "https://raw.githubusercontent.com/ShishirPatil/gorilla/main/LICENSE" \
  "$dest/bfcl_LICENSE.txt"

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
  echo "  - name: epoch/"
  echo "    url: https://epoch.ai/data/benchmark_data.zip"
  echo "    license: CC-BY-4.0"
  echo "    note: unzipped; epoch_LICENSE.md is the zip's own README.md"
  echo "  - name: lmarena_leaderboard_latest.parquet"
  echo "    url: https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset/resolve/main/text/latest-00000-of-00001.parquet"
  echo "    license: CC-BY-4.0"
  echo "    sha256: $(sha "$dest/lmarena_leaderboard_latest.parquet")"
  echo "  - name: bfcl_data_overall.csv"
  echo "    url: https://raw.githubusercontent.com/ShishirPatil/gorilla/gh-pages/data_overall.csv"
  echo "    license: Apache-2.0"
  echo "    sha256: $(sha "$dest/bfcl_data_overall.csv")"
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
| `epoch/*.csv` | Epoch AI's Benchmarking Hub — broad multi-benchmark scores over time (GPQA Diamond, MATH level 5, SWE-bench Verified, FrontierMath, and more, one CSV per benchmark) | CC-BY 4.0 |
| `lmarena_leaderboard_latest.parquet` | LMArena's current text-category leaderboard — human-preference Elo + confidence interval per model (latest snapshot only, not the battle history) | CC-BY 4.0 |
| `bfcl_data_overall.csv` | BFCL (Berkeley Function Calling Leaderboard) overall results — tool-calling accuracy per model, broken out by category | Apache-2.0 |

### Considered and not bundled

- **Epoch AI's Airtable-backed "internal runs"/"external runs" views** —
  only reachable through the Airtable API (needs a key); skipped in favor of
  the CSV export above, which needs none.

See `MANIFEST.yaml` for exact source URLs, fetch timestamp, and a sha256 per
file. `*_LICENSE.md`/`.txt` alongside each source are the upstream license
texts. Public numbers here are a HYPOTHESIS for a private/gated variant, never
a substitute for `litellm_probe_model` — see
`skills/model-evaluation/references/public-library.md`.
EOF

echo "Model library fetched into $dest"
du -sh "$dest"
