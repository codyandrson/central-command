#!/usr/bin/env bash
# Fetch a dependency's real doc source from GitHub and vendor it into
# docs/vendor/<name>/ for the air-gapped deployment. Run on a machine WITH
# internet access, before pushing the repo to the air-gapped box — everything
# this writes travels inside the normal repo archive, no separate bundle.
#
# Usage: vendor_docs_fetch.sh <org/repo> <ref> <local-name> [subpath...]
#   ref        a git tag, branch, or commit SHA that exists in the repo
#   local-name directory name under docs/vendor/
#   subpath    one or more paths inside the repo to keep (default: docs README* LICENSE*)
#
# Only pulls what the ref actually contains — never invents a URL. Verify the
# org/repo and ref exist (GitHub API or the repo's releases page) before calling this.
set -euo pipefail

repo="$1"; ref="$2"; name="$3"; shift 3
if [ "$#" -gt 0 ]; then
  subpaths=("$@")
else
  subpaths=(docs "README*" "LICENSE*")
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dest="$root/docs/vendor/$name"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

url="https://codeload.github.com/${repo}/tar.gz/${ref}"
echo "Fetching $repo @ $ref ..."
curl -fsSL "$url" -o "$tmp/src.tar.gz"
tar -xzf "$tmp/src.tar.gz" -C "$tmp"

srcdir="$(find "$tmp" -mindepth 1 -maxdepth 1 -type d)"
mkdir -p "$dest"

copied=0
for sp in "${subpaths[@]}"; do
  for match in "$srcdir"/$sp; do
    [ -e "$match" ] || continue
    cp -r "$match" "$dest/"
    copied=1
  done
done

if [ "$copied" = 0 ]; then
  echo "WARNING: none of the requested subpaths (${subpaths[*]}) existed at $repo@$ref" >&2
fi

cat > "$dest/MANIFEST.yaml" <<EOF
name: $name
source_repo: https://github.com/$repo
source_ref: $ref
fetched_url: $url
fetched_date: $(date -u +%Y-%m-%d)
subpaths: [${subpaths[*]}]
EOF

echo "Vendored into $dest"
