#!/usr/bin/env bash
# ============================================================================
# resolve-images.sh — turn images.txt's constraints + locks into concrete
# refs this registry can actually serve.
#
#   Design record: docs/superpowers/specs/2026-09-03-deploy-refactor-design.md
#   (D2). Rigid @sha256 pinning broke real installs when an enterprise mirror
#   lacked the exact artifact. So, per image, against the EFFECTIVE registry
#   (the CC_REGISTRY_* mirror when set, else the public host):
#
#     locked tag present   PASS — use it, and check the registry's digest
#                          against images.txt's. A mismatch on the LOCKED tag
#                          is a drifted or poisoned tag: FAIL, never a shrug.
#                          Unless the lock's digest column is `-`, which
#                          declares the tag ROLLING (a series or a channel:
#                          postgres:16, main-stable) — those move on every
#                          upstream rebuild, so the tag is the pin and the
#                          digest is provenance only.
#     another tag fits     WARN naming the substitution — use the highest by
#                          version sort. NO digest check: a substituted tag is
#                          trusted from the enterprise mirror on purpose (the
#                          digest pin defended against public-registry tag
#                          poisoning, a threat the mirrored air gap does not
#                          carry). A WARN-level substitution plus a green
#                          verify IS a supported install — capability is
#                          proven by probes, not by version strings.
#     nothing fits         FAIL naming the constraint and what the mirror has.
#
#   Output: CC_IMG_<NAME>=<host/path:tag> written into deploy/single/.env
#   (compose.yaml reads them), plus deploy/single/installed.manifest — the
#   provenance record, and phase 2's rollback record.
#
#   Same output protocol and exit taxonomy as setup.sh:
#     PASS|WARN|FAIL|USERACTION <check>: <message> on stdout, detail on stderr
#     exit 0 clean · 1 hard failure · 2 warnings · 3 stopped for the operator
#
#   Usage:  ./resolve-images.sh          # every component the flags want
#           ./resolve-images.sh --dry-run   # resolve and report, write nothing
#           ./resolve-images.sh --self-test # exercise the tag matcher, touch nothing
# ============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$HERE/.env"
IMAGES="$HERE/images.txt"
MANIFEST="$HERE/installed.manifest"
LOGFILE="$HERE/setup-log.txt"

# Windows has no real python3: the WindowsApps stub answers `command -v` but
# exits 49 — so probe by RUNNING it. $PY may be multiple words (the uv
# fallback), so always invoke it unquoted.
PY=""
for c in python3 python; do
  command -v "$c" >/dev/null 2>&1 && "$c" -c '' 2>/dev/null && { PY="$c"; break; }
done
[[ -n "$PY" ]] || PY="uv run --python 3.12 python"

DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1
SELFTEST=0
[[ "${1:-}" == "--self-test" ]] && SELFTEST=1

# ── output protocol (identical to setup.sh's) ───────────────────────────────
FAILS=0; WARNS=0; ACTIONS=0
logline() { printf '%s %s %s\n' "$(date -u +%FT%TZ)" resolve "$*" >>"$LOGFILE" 2>/dev/null || true; }
pass() { printf 'PASS %s: %s\n' "$1" "$2"; logline "PASS $1: $2"; }
warn() { printf 'WARN %s: %s\n' "$1" "$2"; WARNS=$((WARNS+1)); logline "WARN $1: $2"; }
fail() { printf 'FAIL %s: %s\n' "$1" "$2"; FAILS=$((FAILS+1)); logline "FAIL $1: $2"; }
useraction() { printf 'USERACTION %s: %s\n' "$1" "$2"; ACTIONS=$((ACTIONS+1)); logline "USERACTION $1: $2"; }
note() { printf '%s\n' "$*" >&2; }

(( SELFTEST )) || [[ -f "$ENV_FILE" ]] || { fail "answer-file" "$ENV_FILE not found — start from: cp env.example .env"; exit 1; }
[[ -f "$IMAGES" ]] || { fail "images-txt" "$IMAGES missing"; exit 1; }
set -a
# shellcheck disable=SC1090
(( SELFTEST )) || . "$ENV_FILE"
set +a
: "${CC_ENABLE_N8N:=0}"
: "${CC_ENABLE_CRAWLER:=1}"
: "${CC_ENABLE_SANDBOX:=1}"
: "${CC_ENABLE_SPEECH:=1}"

# ── .env writers (copied verbatim from setup.sh: printf/read are BUILTINS, so
# unlike `sed -i s|..|VALUE|` the value never appears in an argv) ────────────
set_kv() { # set_kv <file> <key> <value>
  local f="$1" k="$2" v="$3" tmp line found=0
  tmp="$(mktemp)" || return 1
  chmod 600 "$tmp" 2>/dev/null
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == "$k="* ]]; then
      printf '%s=%s\n' "$k" "$v" >>"$tmp"; found=1
    else
      printf '%s\n' "$line" >>"$tmp"
    fi
  done <"$f"
  (( found )) || printf '%s=%s\n' "$k" "$v" >>"$tmp"
  cat "$tmp" >"$f"     # rewrite in place: keeps the mode and the inode
  rm -f "$tmp"
}

registry_host() { # registry_host <key>
  case "$1" in
    dockerio) echo "${CC_REGISTRY_DOCKERIO:-docker.io}" ;;
    ghcr)     echo "${CC_REGISTRY_GHCR:-ghcr.io}" ;;
    mcr)      echo "${CC_REGISTRY_MCR:-mcr.microsoft.com}" ;;
    *) return 1 ;;
  esac
}

component_wanted() { # images.txt's last column vs the flags
  case "$1" in
    core)          return 0 ;;
    n8n)           [[ "$CC_ENABLE_N8N" == 1 ]] ;;
    graphiti-base) return 0 ;;
    sandbox-base)  [[ "$CC_ENABLE_SANDBOX" == 1 ]] ;;
    crawler-base)  [[ "$CC_ENABLE_CRAWLER" == 1 ]] ;;
    speech)        [[ "$CC_ENABLE_SPEECH" == 1 ]] ;;
    *) return 1 ;;
  esac
}

# CC_IMG_<NAME> from the path: uppercase, / and - become _, drop LIBRARY_.
# Path-derived rather than basename-derived because library/python and
# playwright/python would otherwise collide.
img_var() { local n="${1^^}"; n="${n//[\/-]/_}"; printf 'CC_IMG_%s' "${n#LIBRARY_}"; }

# docker.io is the PULL name; its registry API lives on registry-1.docker.io.
# Every other host (including a mirror) answers /v2 on itself.
api_host() { [[ "$1" == "docker.io" || "$1" == "index.docker.io" ]] && echo registry-1.docker.io || echo "$1"; }

# One registry GET/HEAD, unauthenticated first and then with a bearer token if
# the registry asks for one (docker.io always does; most mirrors do not).
# Prints the body (GET) or the headers (HEAD) on stdout; the response headers
# are also left in $LAST_HEADERS — a FILE, not a variable, because callers
# invoke reg_req inside $(...) and a variable set there dies with the subshell
# (tag listing needs the Link header afterwards).
LAST_HEADERS="$(mktemp)"; trap 'rm -f "$LAST_HEADERS"' EXIT
reg_req() { # reg_req <method> <api-host> <url-path> [accept]
  local method="$1" api="$2" path="$3" accept="${4:-}"
  local url="https://${api}${path}" hdr body code realm service scope tok args=()
  [[ -n "$accept" ]] && args+=(-H "Accept: $accept")
  [[ "$method" == HEAD ]] && args+=(-I)
  hdr="$(mktemp)" || return 1
  body="$(curl -sS --max-time 30 -D "$hdr" "${args[@]}" "$url" 2>/dev/null)"
  code="$(awk 'toupper($1) ~ /^HTTP/ {c=$2} END{print c}' "$hdr")"
  if [[ "$code" == "401" ]]; then
    # Bearer realm="…",service="…",scope="…" — ask for exactly what it wants.
    local auth; auth="$(tr -d '\r' <"$hdr" | sed -n 's/^[Ww]ww-[Aa]uthenticate: *[Bb]earer //p' | tail -1)"
    realm="$(sed -n 's/.*realm="\([^"]*\)".*/\1/p' <<<"$auth")"
    service="$(sed -n 's/.*service="\([^"]*\)".*/\1/p' <<<"$auth")"
    scope="$(sed -n 's/.*scope="\([^"]*\)".*/\1/p' <<<"$auth")"
    if [[ -n "$realm" ]]; then
      tok="$(curl -sS --max-time 30 "${realm}?service=${service}&scope=${scope}" 2>/dev/null \
             | $PY -c 'import json,sys; d=json.load(sys.stdin); print(d.get("token") or d.get("access_token") or "")' 2>/dev/null)"
      if [[ -n "$tok" ]]; then
        # Via `-H @-` (stdin), never an argv — the same discipline the master
        # key travels under in setup.sh. NOT `-H @<(...)`: native Windows curl
        # cannot open MSYS's /proc fd paths.
        body="$(printf 'Authorization: Bearer %s\n' "$tok" | \
                curl -sS --max-time 30 -D "$hdr" -H @- "${args[@]}" "$url" 2>/dev/null)"
        code="$(awk 'toupper($1) ~ /^HTTP/ {c=$2} END{print c}' "$hdr")"
      fi
    fi
  fi
  tr -d '\r' <"$hdr" >"$LAST_HEADERS"
  if [[ "$method" == HEAD ]]; then cat "$LAST_HEADERS"; else printf '%s' "$body"; fi
  rm -f "$hdr"
  [[ "$code" == "200" ]]
}

# Every tag, following the registry's pagination. THE trap: /v2/.../tags/list
# returns the FIRST page in lexical order, so on a busy repository (postgres,
# python) a single unpaginated request answers with tags from 2018 and every
# current tag looks absent. The registry says where the next page is in a
# `Link: <...>; rel="next"` header; 40 pages of 1000 is a generous ceiling
# that still terminates on a registry that loops.
reg_tags() { # reg_tags <api-host> <path>
  local api="$1" p="$2" pages=0 body next url
  # A separate statement: bash expands every word of one `local` BEFORE any
  # of its assignments run, so `${p}` on the same line is unbound under set -u.
  url="/v2/${p}/tags/list?n=1000"
  while [[ -n "$url" ]] && (( pages < 40 )); do
    body="$(reg_req GET "$api" "$url")" || return 1
    $PY -c 'import json,sys
try: print("\n".join(json.load(sys.stdin).get("tags") or []))
except Exception: pass' <<<"$body" 2>/dev/null
    next="$(sed -n 's/^[Ll]ink: *<\([^>]*\)>.*rel="next".*/\1/p' "$LAST_HEADERS" | tail -1)"
    url="$next"
    pages=$(( pages + 1 ))
  done
}

# A tag's FLAVOUR is everything after its leading version: `` for 16 and
# 5.26.2, `-alpine` for 7-alpine, `-slim-bookworm` for 3.12-slim-bookworm.
flavour() { local t="${1#v}"; while [[ "$t" == [0-9.]* ]]; do t="${t#?}"; done; printf '%s' "$t"; }
# Does a tag satisfy the constraint? The constraint is a VERSION SERIES (16,
# 5.26, 7, main-stable): the tag equals it or continues it at a non-digit
# (16 admits 16.10, not 160). The substitute must also keep the LOCKED tag's
# flavour: a prefix alone lets postgres `16` admit 16.10-alpine3.22 and neo4j
# `5.26` admit 5.26.2-enterprise (needs a licence env var; never starts) —
# and sort -V ranks exactly those highest.
fits() { # fits <tag> <constraint> <locked-tag>
  [[ ( "$1" == "$2" || "$1" == "$2"[!0-9]* ) && "$(flavour "$1")" == "$(flavour "$3")" ]]
}
self_test() {
  local ok=1
  t() { if fits "$1" "$2" "$3"; then [[ "$4" == yes ]] || { echo "FAIL fits $1 $2 $3 -> accepted"; ok=0; }
        else [[ "$4" == no ]] || { echo "FAIL fits $1 $2 $3 -> rejected"; ok=0; }; fi; }
  t 16.10 16 16 yes;                t 16.10-alpine3.22 16 16 no;      t 16-bookworm 16 16 no
  t 160.1 16 16 no;                 t 17.2 16 16 no
  t 5.26.4 5.26 5.26.2 yes;         t 5.26.4-enterprise 5.26 5.26.2 no; t 5.27.0 5.26 5.26.2 no
  t 7.4-alpine 7 7-alpine yes;      t 7.4 7 7-alpine no;              t 7.4-alpine3.22 7 7-alpine no
  t 3.12.8-slim-bookworm 3.12 3.12-slim-bookworm yes; t 3.12-alpine 3.12 3.12-slim-bookworm no
  t v1.62.1-noble v1.62 v1.62.0-noble yes; t v1.62.1-jammy v1.62 v1.62.0-noble no
  t 0.8.4-cpu 0.8 0.8.3-cpu yes;    t 0.8.4-cuda 0.8 0.8.3-cpu no;    t 2.30.1 2 2.29.0 yes
  t 1.0.3-standalone 1.0 1.0.2-standalone yes; t 1.0.3 1.0 1.0.2-standalone no
  t main-stable main-stable main-stable yes; t main-stable-2 main-stable main-stable no
  (( ok )) && echo "self-test: fits ok"
  (( ok ))
}
(( SELFTEST )) && { self_test; exit $?; }

MANIFEST_ROWS=""
resolve_one() { # resolve_one <key> <path> <constraint> <locked-tag> <locked-digest>
  local key="$1" path="$2" cons="$3" lock="$4" ldig="$5"
  local host api check var tags cand="" chosen="" mode="" dig=""
  host="$(registry_host "$key")" || { fail "images-txt" "unknown registry key '$key' in images.txt"; return 1; }
  api="$(api_host "$host")"
  var="$(img_var "$path")"
  # Named after the variable, not the path's last segment: library/python and
  # playwright/python share that segment and would report as one check.
  check="image-${var#CC_IMG_}"; check="${check,,}"

  # The locked tag is asked about DIRECTLY — a manifest HEAD is authoritative
  # and, unlike a tags list, has no pagination to get wrong. The full listing
  # is only needed when the lock is absent and something must be substituted.
  local accept='application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'
  if dig="$(reg_req HEAD "$api" "/v2/${path}/manifests/${lock}" "$accept" \
            | sed -n 's/^[Dd]ocker-[Cc]ontent-[Dd]igest: *//p' | tail -1)" && [[ -n "$dig" ]]; then
    chosen="$lock"; mode=locked
    if [[ "$ldig" == "-" ]]; then
      # A ROLLING tag (a series or a channel: postgres:16, redis:7-alpine,
      # main-stable). Its digest moves every upstream rebuild, so there is
      # nothing to lock and a mismatch would fire weekly on a healthy
      # registry. The tag itself is the pin; the digest is recorded in
      # installed.manifest as provenance, not asserted.
      pass "$check" "${host}/${path}:${lock} (locked tag; rolling — digest recorded, not pinned)"
    elif [[ "$dig" != "$ldig" ]]; then
      fail "$check" "${host}/${path}:${lock} DIGEST MISMATCH — images.txt locks ${ldig}, the registry serves ${dig}. That tag has drifted or been poisoned; do not install it. Bump the lock deliberately (a release) or use a registry that serves the tested artifact."
      return 1
    else
      pass "$check" "${host}/${path}:${lock} (locked tag, digest verified)"
    fi
    MANIFEST_ROWS="${MANIFEST_ROWS}${host}/${path} ${chosen} ${dig} ${mode} $(date -u +%FT%TZ)"$'\n'
    if (( DRY )); then note "    would set ${var}=${host}/${path}:${chosen}"
    else set_kv "$ENV_FILE" "$var" "${host}/${path}:${chosen}"; fi
    return 0
  fi

  tags="$(reg_tags "$api" "$path")"

  if [[ -z "$tags" ]]; then
    # A mirror may serve pulls and no tags-list API at all. Try the lock the
    # blind way — a successful pull is the only proof that matters — before
    # calling it a failure.
    if command -v podman >/dev/null 2>&1 && podman pull -q "${host}/${path}:${lock}" >/dev/null 2>&1; then
      warn "$check" "${host}/${path}: the registry answered no tag list — resolved BLIND to the locked tag ${lock}, which pulled"
      chosen="$lock"; mode=locked; dig="(blind)"
    else
      fail "$check" "${host}/${path}: the registry's tags API is unreachable and the locked tag ${lock} could not be pulled either — seam: CC_REGISTRY_${key^^} in .env (or a registries.conf mirror podman sees)"
      return 1
    fi
  elif grep -qx -- "$lock" <<<"$tags"; then
    # Listed but the manifest HEAD gave no digest: the tag is there and the
    # registry will not say what it hashes to (some proxies strip the header).
    chosen="$lock"; mode=locked; dig="(unverified)"
    warn "$check" "${host}/${path}:${lock} — the registry served no Docker-Content-Digest, so the lock could not be verified"
  else
    # Highest tag satisfying the constraint. sort -V is the version sort every
    # coreutils/busybox has; ties go to the last line.
    local t
    while IFS= read -r t; do
      [[ -z "$t" ]] && continue
      fits "$t" "$cons" "$lock" && cand="$cand$t"$'\n'
    done <<<"$tags"
    chosen="$(sort -V <<<"${cand%$'\n'}" | tail -1)"
    if [[ -z "$chosen" ]]; then
      local sample; sample="$(sort -V <<<"$tags" | tail -8 | tr '\n' ' ')"
      fail "$check" "${host}/${path}: nothing satisfies the constraint '${cons}' (locked tag ${lock} is absent). The registry offers, most recent last: ${sample}— seam: CC_REGISTRY_${key^^} in .env, or mirror the tested tag"
      return 1
    fi
    mode=substituted
    dig="(substituted)"
    warn "$check" "${host}/${path}: locked tag ${lock} is ABSENT — substituting ${chosen} (satisfies '${cons}'). Untested combination: a green ./setup.sh verify is what makes it supported."
  fi

  local ref="${host}/${path}:${chosen}"
  MANIFEST_ROWS="${MANIFEST_ROWS}${host}/${path} ${chosen} ${dig} ${mode} $(date -u +%FT%TZ)"$'\n'
  if (( DRY )); then
    note "    would set ${var}=${ref}"
  else
    set_kv "$ENV_FILE" "$var" "$ref"
  fi
  return 0
}

note "resolving images against $( [[ -n "${CC_REGISTRY_DOCKERIO:-}${CC_REGISTRY_GHCR:-}${CC_REGISTRY_MCR:-}" ]] && echo "the configured mirrors" || echo "the public registries" )"
while read -r key path cons lock ldig comp; do
  [[ -z "${key:-}" || "$key" == \#* ]] && continue
  if [[ -z "${comp:-}" ]]; then
    fail "images-txt" "expected 6 columns, got: $key $path ${cons:-} ${lock:-} ${ldig:-}"
    continue
  fi
  component_wanted "$comp" || continue
  # </dev/null: curl and podman must not eat the manifest we are reading from.
  resolve_one "$key" "$path" "$cons" "$lock" "$ldig" </dev/null
done <"$IMAGES"

if (( ! DRY )) && [[ -n "$MANIFEST_ROWS" ]]; then
  # The provenance record: what this install actually runs, and (phase 2) what
  # a rollback restores. Rewritten each run; only the wanted components appear.
  {
    echo "# GENERATED by resolve-images.sh — what this install resolved."
    echo "# <ref> <tag> <digest> <locked|substituted> <resolved-at>"
    printf '%s' "$MANIFEST_ROWS"
  } >"$MANIFEST"
  pass "installed-manifest" "wrote $(basename "$MANIFEST")"
fi

if (( FAILS )); then
  useraction "resolve-images" "$FAILS image(s) could not be resolved — fix the seam(s) named above in deploy/single/.env and re-run: ./setup.sh fetch (deploy/discover.sh maps what this network can reach; deploy/AIRGAP.md maps the seams)"
fi
(( ACTIONS )) && exit 3
(( FAILS )) && exit 1
(( WARNS )) && exit 2
exit 0
