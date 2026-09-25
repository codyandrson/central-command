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
#                          (On a CONFIGURED mirror the same mismatch is a WARN,
#                          recorded locked-mirror: pushes re-serialise manifests.)
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
#   AN OPERATOR PIN WINS (2026-09-23 design record, D2). A CC_IMG_<NAME> the
#   operator set by hand is authoritative: it is the seam for a mirror that
#   re-namespaces PATHS (only the host is a variable) and for "use this tag, I
#   checked". The catch the design had to solve: this script writes that same
#   key, so "present in .env" cannot tell a pin from the resolver's own last
#   write. The manifest is what distinguishes them — it records the ref written
#   per image, so a value in .env that DIFFERS from the recorded one (or has no
#   record at all) is the operator's. A pin is VERIFIED to exist (a manifest
#   HEAD by tag, or by digest when the ref carries @sha256:), parsed from the
#   PINNED ref rather than images.txt (a re-namespacing mirror's path differs),
#   reported as a WARN, recorded as `pinned`, and never rewritten. A pin that
#   does not exist is a FAIL naming the key: the whole point is that a pin is
#   checked, not trusted.
#
#   TLS: CC_CA_BUNDLE and CC_TLS_INSECURE are fanned out by
#   deploy/env-lib.sh's cc_export_tls_env (which is what CURL_CA_BUNDLE /
#   CURL_HOME come from); podman pulls take --tls-verify=false separately,
#   because no exported variable reaches them.
#
#   Output: CC_IMG_<NAME>=<host/path:tag> written into the repo-root .env
#   (compose.yaml reads them, via --env-file), plus
#   $CC_STATE_DIR/installed.manifest — the provenance record, and phase 2's
#   rollback record. Since v2.42.0 there is ONE answer file and NOTHING is
#   written inside the checkout (2026-09-23 design record, D1 + D7).
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
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
# shellcheck source=../env-lib.sh
. "$REPO_ROOT/deploy/env-lib.sh"
ENV_FILE="$REPO_ROOT/.env"
IMAGES="$HERE/images.txt"
# Resolved below, once --self-test is known: the self-test must touch NOTHING,
# and resolving the state dir persists CC_STATE_DIR into .env.
MANIFEST=""
LOGFILE=""

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
if (( ! SELFTEST )); then
  STATE_DIR="$(cc_state_dir "$ENV_FILE" "$REPO_ROOT")" || STATE_DIR="${TMPDIR:-/tmp}/central-command-state"
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  MANIFEST="$STATE_DIR/installed.manifest"
  LOGFILE="$STATE_DIR/setup-log.txt"
fi

# ── output protocol (identical to setup.sh's) ───────────────────────────────
FAILS=0; WARNS=0; ACTIONS=0
logline() { [[ -n "$LOGFILE" ]] || return 0; printf '%s %s %s\n' "$(date -u +%FT%TZ)" resolve "$*" >>"$LOGFILE" 2>/dev/null || true; }
pass() { printf 'PASS %s: %s\n' "$1" "$2"; logline "PASS $1: $2"; }
warn() { printf 'WARN %s: %s\n' "$1" "$2"; WARNS=$((WARNS+1)); logline "WARN $1: $2"; }
fail() { printf 'FAIL %s: %s\n' "$1" "$2"; FAILS=$((FAILS+1)); logline "FAIL $1: $2"; }
useraction() { printf 'USERACTION %s: %s\n' "$1" "$2"; ACTIONS=$((ACTIONS+1)); logline "USERACTION $1: $2"; }
note() { printf '%s\n' "$*" >&2; }

(( SELFTEST )) || [[ -f "$ENV_FILE" ]] || { fail "answer-file" "$ENV_FILE not found — run ./setup.sh configure (the one command that creates it)"; exit 1; }
[[ -f "$IMAGES" ]] || { fail "images-txt" "$IMAGES missing"; exit 1; }
set -a
# shellcheck disable=SC1090
(( SELFTEST )) || . "$ENV_FILE"
set +a
: "${CC_ENABLE_N8N:=0}"
: "${CC_ENABLE_CRAWLER:=1}"
: "${CC_ENABLE_SANDBOX:=1}"
: "${CC_ENABLE_SPEECH:=1}"
: "${CC_TLS_INSECURE:=0}"

# The two trust knobs, fanned out in ONE place (design record D4). It also
# writes the .curlrc that carries `insecure` for every curl below, so the
# explicit flag further down is belt-and-braces rather than the only path.
(( SELFTEST )) || cc_export_tls_env "${STATE_DIR:-}"
CURL_TLS=()
PULL_TLS=()
if [[ "$CC_TLS_INSECURE" == "1" ]]; then
  CURL_TLS=(-k)
  PULL_TLS=(--tls-verify=false)
  if (( ! SELFTEST )); then
    tls_consumers="this script's registry probes (curl) and its fallback podman pull"
    logline "WARN tls-insecure: $(cc_tls_insecure_warn_text "$tls_consumers")"
    cc_tls_insecure_warn_once "$tls_consumers" && WARNS=$((WARNS+1))
  fi
fi

# ── .env writers (copied verbatim from setup.sh: printf/read are BUILTINS, so
# unlike `sed -i s|..|VALUE|` the value never appears in an argv) ────────────
set_kv() { # set_kv <file> <key> <value>
  local f="$1" k="$2" v="$3" tmp line found=0
  tmp="$(mktemp)" || return 1
  chmod 600 "$tmp" 2>/dev/null
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"   # images.txt may be CRLF: the merge that brings .gitattributes writes it first (2026-09-18 Windows run)
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

public_host() { # public_host <key> — the registry images.txt names, before any CC_REGISTRY_* mirror
  case "$1" in
    dockerio) echo "docker.io" ;;
    ghcr)     echo "ghcr.io" ;;
    mcr)      echo "mcr.microsoft.com" ;;
    *) return 1 ;;
  esac
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
LAST_CURL_ERR=""
LAST_HEADERS="$(mktemp)"; trap 'rm -f "$LAST_HEADERS"' EXIT
reg_req() { # reg_req <method> <api-host> <url-path> [accept]
  local method="$1" api="$2" path="$3" accept="${4:-}"
  local url="https://${api}${path}" hdr body code realm service scope tok args=()
  [[ -n "$accept" ]] && args+=(-H "Accept: $accept")
  [[ "$method" == HEAD ]] && args+=(-I)
  hdr="$(mktemp)" || return 1
  body="$(curl -sS --max-time 30 "${CURL_TLS[@]}" -D "$hdr" "${args[@]}" "$url" 2>"$hdr.err")"
  LAST_CURL_ERR="$(head -c 200 "$hdr.err" 2>/dev/null)"; rm -f "$hdr.err"
  code="$(awk 'toupper($1) ~ /^HTTP/ {c=$2} END{print c}' "$hdr")"
  if [[ "$code" == "401" ]]; then
    # Bearer realm="…",service="…",scope="…" — ask for exactly what it wants.
    local auth; auth="$(tr -d '\r' <"$hdr" | sed -n 's/^[Ww]ww-[Aa]uthenticate: *[Bb]earer //p' | tail -1)"
    realm="$(sed -n 's/.*realm="\([^"]*\)".*/\1/p' <<<"$auth")"
    service="$(sed -n 's/.*service="\([^"]*\)".*/\1/p' <<<"$auth")"
    scope="$(sed -n 's/.*scope="\([^"]*\)".*/\1/p' <<<"$auth")"
    if [[ -n "$realm" ]]; then
      tok="$(curl -sS --max-time 30 "${CURL_TLS[@]}" "${realm}?service=${service}&scope=${scope}" 2>/dev/null \
             | $PY -c 'import json,sys; d=json.load(sys.stdin); print(d.get("token") or d.get("access_token") or "")' 2>/dev/null)"
      if [[ -n "$tok" ]]; then
        # Via `-H @-` (stdin), never an argv — the same discipline the master
        # key travels under in setup.sh. NOT `-H @<(...)`: native Windows curl
        # cannot open MSYS's /proc fd paths.
        body="$(printf 'Authorization: Bearer %s\n' "$tok" | \
                curl -sS --max-time 30 "${CURL_TLS[@]}" -D "$hdr" -H @- "${args[@]}" "$url" 2>/dev/null)"
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
# ── the operator pin (D2) ───────────────────────────────────────────────────
# Split a pinned ref into host / path / reference, where the reference is a TAG
# or a `sha256:...` digest. Parsed from the PIN, never from images.txt: a
# re-namespacing mirror's path is exactly what differs, and that is the case
# this seam exists for. A ref with no registry host cannot be verified against
# a registry, so it is rejected rather than guessed at.
#
# Prints `<host> <path> <reference>`; returns 1 on a ref this cannot parse.
pin_parts() { # pin_parts <ref>
  local ref="$1" host rest path r
  [[ "$ref" == */* ]] || return 1          # `postgres:16` — no registry to ask
  host="${ref%%/*}"; rest="${ref#*/}"
  [[ "$host" == *.* || "$host" == *:* || "$host" == localhost ]] || return 1
  if [[ "$rest" == *@* ]]; then
    path="${rest%%@*}"; r="${rest#*@}"
  else
    [[ "$rest" == *:* ]] || return 1       # no tag: nothing definite to verify
    path="${rest%:*}"; r="${rest##*:}"
  fi
  [[ -n "$path" && -n "$r" ]] || return 1
  printf '%s %s %s' "$host" "$path" "$r"
}

# Is the CC_IMG_* value in .env an OPERATOR PIN, and if so does it exist?
# THE problem this solves: the resolver writes that key itself, so presence is
# no signal. The manifest's recorded ref for the same image is — a value that
# differs from it (or that has no record at all, including a first run) is the
# operator's.
#
# Prints one word:
#   resolve   no pin, or the value is this resolver's own last write
#   honour    an operator pin, and the registry has it
#   missing   an operator pin the registry does not have
pin_decide() { # pin_decide <env-value> <manifest-ref> <exists:0|1>
  local val="$1" prev="$2" exists="$3"
  [[ -n "$val" ]] || { printf 'resolve'; return 0; }
  [[ "$val" != "$prev" ]] || { printf 'resolve'; return 0; }
  [[ "$exists" == 1 ]] && printf 'honour' || printf 'missing'
}

# The ref this script last wrote for <var> AS ITS OWN RESOLUTION, out of the
# manifest. The var is the manifest's FIRST column precisely so this lookup is
# unambiguous when a pin renames the path — the images.txt row's identity is the
# variable, not the ref.
#
# A row recorded `pinned` is deliberately INVISIBLE here. The caller's test for
# "is this the operator's pin?" is `.env value != what I last wrote`, and an
# honoured pin is recorded verbatim (`<var> <host>/<path> <tag> (pinned)
# pinned`) — so counting it as a self-write made `pinval == prev` on the very
# next run and the pin was silently re-resolved away. An operator pin was
# therefore honoured EXACTLY ONCE: measured on the 2026-09-24 Windows run, where
# `CC_IMG_REDIS=localhost:5000/mirror/redis:7-alpine` survived the first fetch
# and was then quietly replaced by `localhost:5000/library/redis:7-alpine` on
# the next check. That only looked harmless because the test mirror happened to
# hold the canonical path too; on a real path-RENAMING mirror — the case D2
# created the pin for — the second run would FAIL the row instead. The mode
# column already carried the fact; it just was not read.
manifest_ref_for() { # manifest_ref_for <var>
  [[ -n "$MANIFEST" && -f "$MANIFEST" ]] || { printf ''; return 0; }
  awk -v v="$1" '$1 == v && $5 != "pinned" { r = $2 ":" $3 } END { print r }' \
    "$MANIFEST" 2>/dev/null
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
  t 1.1.1-standalone 1.1 1.1.0-standalone yes; t 1.1.1 1.1 1.1.0-standalone no; t 1.0.2-standalone 1.1 1.1.0-standalone no
  t main-stable main-stable main-stable yes; t main-stable-2 main-stable main-stable no
  (( ok )) && echo "self-test: fits ok"

  # ── the pin decision (D2). These four cases ARE the rule, and none of them
  # needs a registry: the existence probe is injected as the third argument.
  local okp=1
  d() { # d <env-value> <manifest-ref> <exists> <expected>
    local got; got="$(pin_decide "$1" "$2" "$3")"
    [[ "$got" == "$4" ]] || { echo "FAIL pin_decide '$1' '$2' $3 -> $got, expected $4"; okp=0; }
  }
  # nothing set: normal resolution
  d "" "" 0 resolve
  d "" "mirror.corp/library/postgres:16" 0 resolve
  # the resolver's own last write, present in .env: normal resolution
  d "mirror.corp/library/postgres:16" "mirror.corp/library/postgres:16" 1 resolve
  # an operator pin that exists: honoured
  d "mirror.corp/library/postgres:16.9" "mirror.corp/library/postgres:16" 1 honour
  # an operator pin that does NOT exist: named, never silently re-resolved
  d "mirror.corp/library/postgres:16.9" "mirror.corp/library/postgres:16" 0 missing
  # a PATH-RENAMED pin (the case only this seam can express), first run: no
  # manifest row at all, so it is the operator's either way
  d "mirror.corp/mirrored/dockerhub/library/postgres:16" "" 1 honour
  d "mirror.corp/mirrored/dockerhub/library/postgres:16" "" 0 missing
  (( okp )) && echo "self-test: pin_decide ok"

  # ── parsing the pin, which must come from the PIN and not from images.txt
  local okq=1
  q() { # q <ref> <expected-output-or-"-">
    local got rc=0; got="$(pin_parts "$1")" || rc=1
    if [[ "$2" == "-" ]]; then
      (( rc )) || { echo "FAIL pin_parts '$1' -> accepted ('$got'), expected a refusal"; okq=0; }
    else
      [[ "$got" == "$2" ]] || { echo "FAIL pin_parts '$1' -> '$got', expected '$2'"; okq=0; }
    fi
  }
  q "docker.io/library/postgres:16" "docker.io library/postgres 16"
  q "mirror.corp.example/mirrored/dockerhub/library/postgres:16.9" \
    "mirror.corp.example mirrored/dockerhub/library/postgres 16.9"
  q "mirror.corp.example:5000/library/neo4j:5.26.2" \
    "mirror.corp.example:5000 library/neo4j 5.26.2"
  q "mirror.corp.example/library/neo4j@sha256:099b9f74968c123209972835417985ed2a1cc19c0422c0753a313e26a736c365" \
    "mirror.corp.example library/neo4j sha256:099b9f74968c123209972835417985ed2a1cc19c0422c0753a313e26a736c365"
  q "postgres:16" -                    # no registry host: nothing to ask
  q "mirror.corp.example/library/postgres" -   # no tag: nothing definite
  (( okq )) && echo "self-test: pin_parts ok"

  (( ok && okp && okq ))
}
(( SELFTEST )) && { self_test; exit $?; }

# Every manifest media type a registry may answer a HEAD with — an index, a
# manifest list, or a single-arch manifest. Module-level because BOTH the pin
# probe and the locked-tag probe ask the same question.
ACCEPT_MANIFEST='application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'

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

  # ── an operator pin wins, once it has been PROVEN to exist (D2) ───────────
  # Read from the FILE rather than the environment: this script rewrites the
  # key as it goes, and a sourced value from earlier in the same run would make
  # a pin out of its own last write.
  local pinval prev pexists=0 phost ppath pref parts decision
  pinval="$(cc_get_kv "$ENV_FILE" "$var")"
  prev="$(manifest_ref_for "$var")"
  if [[ -n "$pinval" && "$pinval" != "$prev" ]]; then
    if ! parts="$(pin_parts "$pinval")"; then
      fail "$check" "$var is set to '${pinval}', which is not a ref this can verify — an operator pin must be FULLY QUALIFIED with a tag or a digest (<registry-host>/<path>:<tag>, or <registry-host>/<path>@sha256:...). Fix or unset $var in .env."
      return 1
    fi
    read -r phost ppath pref <<<"$parts"
    # `/v2/<path>/manifests/<tag-or-digest>` is the same endpoint either way —
    # the registry accepts a digest in place of a tag, which is what makes a
    # digest pin verifiable without a second code path.
    if reg_req HEAD "$(api_host "$phost")" "/v2/${ppath}/manifests/${pref}" "$ACCEPT_MANIFEST" >/dev/null; then
      pexists=1
    fi
    decision="$(pin_decide "$pinval" "$prev" "$pexists")"
    case "$decision" in
      honour)
        warn "$check" "operator pin honoured — ${pinval} (not re-resolved; unset $var in .env to resolve against images.txt again)"
        MANIFEST_ROWS="${MANIFEST_ROWS}${var} ${phost}/${ppath} ${pref} (pinned) pinned $(date -u +%FT%TZ)"$'\n'
        (( DRY )) && note "    would leave ${var}=${pinval} alone (operator pin)"
        return 0
        ;;
      missing)
        fail "$check" "$var is set to ${pinval} but the registry has no such manifest — fix or unset it"
        return 1
        ;;
    esac
    # `resolve` cannot happen here (the guard above already excluded it), but
    # falling through to normal resolution is the safe reading of it anyway.
  fi

  # The locked tag is asked about DIRECTLY — a manifest HEAD is authoritative
  # and, unlike a tags list, has no pagination to get wrong. The full listing
  # is only needed when the lock is absent and something must be substituted.
  if dig="$(reg_req HEAD "$api" "/v2/${path}/manifests/${lock}" "$ACCEPT_MANIFEST" \
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
      # The lock's digest defends against PUBLIC-registry tag poisoning — the
      # header above says the mirrored air gap does not carry that threat, yet
      # until 2026-09-24 this branch enforced it against the operator's own
      # mirror: a mirror seeded by pull+push re-serialises the manifest, so
      # 4 of 8 rows FAILed "poisoned" on a healthy mirror (Windows testbed).
      # Under a CONFIGURED mirror (CC_REGISTRY_* set to something other than
      # the public host) a mismatch on the locked tag is a WARN naming both
      # digests and is recorded as locked-mirror; on the public host it stays
      # a FAIL.
      if [[ "$host" != "$(public_host "$key")" ]]; then
        warn "$check" "${host}/${path}:${lock} digest differs from the lock — images.txt locks ${ldig}, the mirror serves ${dig}. A mirror seeded by push re-serialises manifests, so this is expected there; the tag is the pin. Recorded as locked-mirror."
        mode=locked-mirror
      else
        fail "$check" "${host}/${path}:${lock} DIGEST MISMATCH — images.txt locks ${ldig}, the registry serves ${dig}. That tag has drifted or been poisoned; do not install it. Bump the lock deliberately (a release) or use a registry that serves the tested artifact."
        return 1
      fi
    else
      pass "$check" "${host}/${path}:${lock} (locked tag, digest verified)"
    fi
    MANIFEST_ROWS="${MANIFEST_ROWS}${var} ${host}/${path} ${chosen} ${dig} ${mode} $(date -u +%FT%TZ)"$'\n'
    if (( DRY )); then note "    would set ${var}=${host}/${path}:${chosen}"
    else set_kv "$ENV_FILE" "$var" "${host}/${path}:${chosen}"; fi
    return 0
  fi

  tags="$(reg_tags "$api" "$path")"

  if [[ -z "$tags" ]]; then
    # A mirror may serve pulls and no tags-list API at all. Try the lock the
    # blind way — a successful pull is the only proof that matters — before
    # calling it a failure.
    if command -v podman >/dev/null 2>&1 && podman pull -q "${PULL_TLS[@]}" "${host}/${path}:${lock}" >/dev/null 2>&1; then
      warn "$check" "${host}/${path}: the registry answered no tag list${LAST_CURL_ERR:+ ($LAST_CURL_ERR)} — resolved BLIND to the locked tag ${lock}, which pulled (podman's own egress, which a proxy/CA seam on the HOST does not govern — see deploy/AIRGAP.md)"
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
  MANIFEST_ROWS="${MANIFEST_ROWS}${var} ${host}/${path} ${chosen} ${dig} ${mode} $(date -u +%FT%TZ)"$'\n'
  if (( DRY )); then
    note "    would set ${var}=${ref}"
  else
    set_kv "$ENV_FILE" "$var" "$ref"
  fi
  return 0
}

note "resolving images against $( [[ -n "${CC_REGISTRY_DOCKERIO:-}${CC_REGISTRY_GHCR:-}${CC_REGISTRY_MCR:-}" ]] && echo "the configured mirrors" || echo "the public registries" )"
while read -r key path cons lock ldig comp; do
  comp="${comp%$'\r'}"; key="${key%$'\r'}"   # CRLF images.txt: a blank line reads as a 1-column row otherwise
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
    echo "# <CC_IMG_var> <ref> <tag> <digest> <locked|substituted|pinned> <resolved-at>"
    printf '%s' "$MANIFEST_ROWS"
  } >"$MANIFEST"
  pass "installed-manifest" "wrote $MANIFEST"
fi

if (( FAILS )); then
  useraction "resolve-images" "$FAILS image(s) could not be resolved — fix the seam(s) named above in the repo-root .env and re-run: ./setup.sh fetch. The seams are CC_REGISTRY_DOCKERIO/_GHCR/_MCR (the mirror HOST) and CC_IMG_<NAME> (an exact ref this resolver must use as-is, including a re-namespaced PATH). deploy/discover.sh maps what this network can reach; deploy/AIRGAP.md maps the seams."
fi
(( ACTIONS )) && exit 3
(( FAILS )) && exit 1
(( WARNS )) && exit 2
exit 0
