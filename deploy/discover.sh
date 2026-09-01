#!/usr/bin/env bash
# ============================================================================
# discover.sh — the environment prober for a restricted / air-gapped network.
#
#   deploy/AIRGAP.md maps what CENTRAL COMMAND needs and which .env seam
#   redirects it. This script answers the question that comes BEFORE that map:
#   what does this network actually let a developer reach, and when it says no,
#   HOW does it say no. A DNS miss, a TCP reject, a silent drop, a corporate
#   TLS re-signer and a 403 from a proxy policy engine all present as "it
#   doesn't work" — and each one has a completely different operator move. The
#   failure MODE is the finding; "unavailable" alone is worth almost nothing.
#
#   It deliberately probes MORE than Central Command uses (crates, maven,
#   nuget, ecr, gcr…): the report is meant to serve as the environment guide
#   for any person or agent that later develops, deploys or operates here, not
#   just for this one install.
#
#   USAGE
#     ./discover.sh [probe|report|selftest] [--out DIR]
#
#       (no arg)  probe, then report
#       probe     run every probe, write raw/ results
#       report    re-render discovery-report.md + discovery.env from raw/
#                 (no network — safe to re-run after hand-reading raw/)
#       selftest  offline unit check of the classifier. Touches no network.
#
#     --out DIR   default deploy/discovery.out (relative to this script)
#
#   OUTPUT PROTOCOL — the same one deploy/single/setup.sh uses:
#     stdout   one line per check: `PASS|WARN|FAIL|USERACTION <check>: <msg>`
#     stderr   everything else — progress, detail
#     exit 0   clean · 1 hard failure · 2 warnings · 3 USER ACTION REQUIRED
#              (precedence ACTIONS > FAILS > WARNS: a blocked resource is
#              almost always somebody's move, not a defect, and the exit code
#              should say so)
#
#   OPERATOR CONFIG — deploy/discovery.conf, if present, is sourced. It is
#   GITIGNORED and operator-local by design: it names internal hosts and may
#   point at credentials. Keys, all optional:
#
#     DISCO_CA_BUNDLE=/path/to/corp-root.pem   --cacert on every probe
#     DISCO_PROXY=http://host:port             --proxy on every probe
#     DISCO_NETRC=1                            --netrc (creds stay in ~/.netrc,
#                                              never in this conf)
#     DISCO_INSECURE=1                         -k on every probe. DIAGNOSTIC
#                                              ONLY — it tells you whether TLS
#                                              interception is the cause. It is
#                                              never the fix, and standardizing
#                                              on it trades a broken build for
#                                              an unverifiable supply chain.
#     DISCO_MIRROR_<KEY>=https://...           an internal mirror for one
#                                              resource, probed IN ADDITION to
#                                              the public endpoint. KEY is the
#                                              probe key uppercased with `-`
#                                              turned into `_` (DISCO_MIRROR_PYPI,
#                                              DISCO_MIRROR_NPM, DISCO_MIRROR_DOCKERIO,
#                                              DISCO_MIRROR_APT, DISCO_MIRROR_GITHUB…).
#                                              A mirror that works becomes the
#                                              report's recommended source.
#
#   The loop this is built for: run it, read the USERACTION lines, ask your
#   platform team for the one thing each names, put the answer in
#   discovery.conf, re-run. The report converges as the conf fills in.
#
#   Only hard dependency: bash + curl. git/podman/python3/node/openssl/jq and
#   friends are all optional — their ABSENCE is a finding, reported, never an
#   error, because "you cannot even check this here" is exactly the kind of
#   thing the next person needs told.
#
#   Credentials are never printed or persisted: every URL passes through
#   redact() before it reaches the console, the report or a raw file.
# ============================================================================

# NOT -e. A probe that fails IS the measurement; aborting on the first
# non-zero curl would end the run on the first blocked host and produce an
# empty report of a dark network — the exact case this exists for. -u and
# pipefail still hold. (Same reasoning as deploy/single/setup.sh's header.)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="$HERE/discovery.conf"
OUT="$HERE/discovery.out"

# ── output protocol ─────────────────────────────────────────────────────────
FAILS=0; WARNS=0; ACTIONS=0
pass() { printf 'PASS %s: %s\n' "$1" "$2"; }
warn() { printf 'WARN %s: %s\n' "$1" "$2"; WARNS=$((WARNS+1)); }
fail() { printf 'FAIL %s: %s\n' "$1" "$2"; FAILS=$((FAILS+1)); }
useraction() { printf 'USERACTION %s: %s\n' "$1" "$2"; ACTIONS=$((ACTIONS+1)); }
note() { printf '%s\n' "$*" >&2; }

# The single redaction seam. Anything that might carry `user:pass@` — a proxy
# URL from the environment, a mirror URL from the conf, a git remote — goes
# through here before it is echoed, written to a raw file, or put in the
# report. One helper so there is one place to audit.
redact() { printf '%s' "$1" | sed -E 's#(://)[^/@[:space:]]*@#\1REDACTED@#g'; }

# ── classifier ──────────────────────────────────────────────────────────────
# PURE: (curl exit, http code, group) -> class string. No I/O, no globals —
# that is what makes `selftest` possible without a network.
#
# curl exit 60 comes back as `tls-cert` rather than a verdict: the caller
# re-probes with -k, and only the ANSWER to that second probe distinguishes
# "an interceptor is re-signing this" from "the cert is genuinely broken".
#
# The 4th argument is the PROXY CONTEXT — `proxy-https` when a proxy is in
# play and the target is https. It exists for exit 56 alone: through a CONNECT
# tunnel a 407 never surfaces as an HTTP status, because there is no HTTP
# response to read — curl reports the tunnel setup failure as exit 56
# ("Recv failure"), which without that context is an ordinary mid-transfer
# reset. See curl.se/mail/archive-2022-08/0009.html.
classify() { # classify <curl_exit> <http_code> [group] [proxy_ctx]
  local rc="$1" code="$2" grp="${3:-}" pctx="${4:-}"
  case "$rc" in
    0)  ;;                              # fall through to the HTTP code
    5)  printf 'proxy-dns'; return ;;   # cannot resolve the PROXY's own name
    6)  printf 'dns'; return ;;
    7)  printf 'refused'; return ;;     # TCP reject: firewall REJECT, or a
                                        # proxy expected but not configured
    28) printf 'timeout'; return ;;     # silent drop: classic default-deny
    56) [[ "$pctx" == proxy-https ]] && { printf 'proxy-auth'; return; }
        printf 'unreachable'; return ;;
    60) printf 'tls-cert'; return ;;    # caller re-probes with -k
    35|51|58|59|66|77|80|83|91) printf 'tls-other'; return ;;
    *)  printf 'unreachable'; return ;;
  esac
  case "$code" in
    407) printf 'proxy-auth' ;;
    401)
      # A /v2/ registry root answering 401 is the SPEC, not a problem: the
      # OCI distribution API advertises its token flow that way, and every
      # anonymous `podman pull` starts with exactly this 401.
      if [[ "$grp" == containers ]]; then printf 'ok'; else printf 'auth'; fi ;;
    403) printf 'denied' ;;
    # Reachable, but the anonymous quota is the ceiling — its own class because
    # the move is different from every other 4xx: authenticate, or put a
    # pull-through cache in front. Docker Hub is where this bites.
    429) printf 'throttled' ;;
    # Reachability is what is probed. Any other 4xx means we reached the real
    # origin and it answered about the REQUEST — 404/405 at an API root that
    # only serves POST, 421 from a vhost that refuses a bare `/` (api.openai.com
    # does exactly this). All of them prove the path.
    2??|3??|4??) printf 'ok' ;;
    5??) printf 'error' ;;
    *)   printf 'unknown' ;;
  esac
}

# The suggested move per class — ONE source of truth, used verbatim by both
# the console USERACTION line and the report, so an operator never has to
# reconcile two differently-worded instructions for the same finding.
move_for() { # move_for <class> <KEYVAR>
  local c="$1" k="${2:-<KEY>}"
  case "$c" in
    dns) printf 'internal DNS does not resolve public names — if a mirror exists for this resource, set DISCO_MIRROR_%s in discovery.conf and re-run' "$k" ;;
    refused|timeout) printf 'egress to this host is blocked — ask for the sanctioned mirror/proxy; set DISCO_MIRROR_%s or DISCO_PROXY and re-run' "$k" ;;
    tls-intercept) printf 'TLS is intercepted by %s — export the corporate root CA and set DISCO_CA_BUNDLE in discovery.conf; do NOT standardize on disabling verification' "${TLS_ISSUER_HINT:-the corporate proxy}" ;;
    # One text for both surfaces of the same fact: a 407 on a plain-HTTP
    # request, and a refused CONNECT tunnel (exit 56) on an HTTPS one.
    proxy-auth) printf 'the proxy wants credentials or refused the CONNECT tunnel (auth required or policy block) — set DISCO_PROXY with credentials or use DISCO_NETRC=1 with a ~/.netrc entry, and re-run' ;;
    throttled) printf 'reachable but rate-limited (anonymous quota) — expect this on Docker Hub; authenticated pulls or a pull-through mirror raise the limit' ;;
    auth) printf 'reachable but wants credentials — add a ~/.netrc entry and set DISCO_NETRC=1, and re-run' ;;
    denied) printf 'reachable but denied — likely proxy policy; ask what the sanctioned source for this resource is' ;;
    proxy-dns) printf 'the configured proxy name does not resolve — check DISCO_PROXY / http_proxy spelling and internal DNS' ;;
    tls-other|tls-cert) printf 'TLS handshake failed for a reason -k does not explain — capture raw/%s.err and ask the network team' "$(printf '%s' "$k" | tr 'A-Z_' 'a-z-')" ;;
    error) printf 'the endpoint answered 5xx — transient, or a mirror in front of it is unhealthy; re-run before escalating' ;;
    portal) printf 'a captive portal or content-injecting proxy is rewriting plain HTTP — authenticate to the portal, and treat every unauthenticated HTTP result in this report as suspect' ;;
    softfail) printf 'certificate revocation checking (OCSP) is blocked — TLS still works because clients soft-fail, but handshakes may stall for seconds; allowlist the OCSP responders if TLS feels mysteriously slow' ;;
    ok) printf 'reachable' ;;
    *) printf 'unclassified — read the raw/ files for this probe' ;;
  esac
}

# Verdict bucket for a class: pass / warn / fail. `auth`, `tls-intercept` and
# `proxy-auth` are WARN because the resource IS reachable — the payload came
# back or would with one config line; that is a nuance, not an outage.
verdict_for() { # verdict_for <class>
  case "$1" in
    ok) printf 'pass' ;;
    auth|tls-intercept|proxy-auth|throttled|portal|softfail) printf 'warn' ;;
    *) printf 'fail' ;;
  esac
}

# Report status column.
status_for() { # status_for <class>
  case "$(verdict_for "$1")" in
    pass) printf 'AVAILABLE' ;;
    warn) printf 'NUANCE (%s)' "$1" ;;
    *) printf 'UNAVAILABLE (%s)' "$1" ;;
  esac
}

# key -> the DISCO_MIRROR_ / DISCO_ suffix form.
keyvar() { printf '%s' "$1" | tr 'a-z-' 'A-Z_'; }

# ── conf + curl flags ───────────────────────────────────────────────────────
load_conf() {
  if [[ -f "$CONF" ]]; then
    # shellcheck disable=SC1090
    . "$CONF"
    note "conf: sourced $CONF"
  fi
  CURL_FLAGS=()
  [[ -n "${DISCO_CA_BUNDLE:-}" ]] && CURL_FLAGS+=(--cacert "$DISCO_CA_BUNDLE")
  [[ -n "${DISCO_PROXY:-}" ]] && CURL_FLAGS+=(--proxy "$DISCO_PROXY")
  [[ "${DISCO_NETRC:-0}" == 1 ]] && CURL_FLAGS+=(--netrc)
  [[ "${DISCO_INSECURE:-0}" == 1 ]] && CURL_FLAGS+=(-k)
}

# ── result records ──────────────────────────────────────────────────────────
# One file per probe under raw/. Deliberately a flat `k=v` file rather than
# JSON: `report` must work with nothing but bash, and a human diagnosing a
# weird finding reads these directly. Values with `=` are safe — the reader
# splits on the FIRST one only.
record() { # record <key> <label> <url> <group> <class> <http> <detail> [extra k=v ...]
  local key="$1" label="$2" url="$3" grp="$4" cls="$5" http="$6" detail="$7"; shift 7
  {
    printf 'key=%s\n' "$key"
    printf 'label=%s\n' "$label"
    printf 'url=%s\n' "$(redact "$url")"
    printf 'group=%s\n' "$grp"
    printf 'class=%s\n' "$cls"
    printf 'http=%s\n' "$http"
    printf 'detail=%s\n' "$detail"
    local kv; for kv in "$@"; do printf '%s\n' "$kv"; done
  } >"$OUT/raw/$key.result"
}

# Read one field out of a result file.
rfield() { # rfield <file> <key>
  local f="$1" want="$2" k v
  while IFS='=' read -r k v; do
    [[ "$k" == "$want" ]] && { printf '%s' "$v"; return 0; }
  done <"$f"
  printf ''
}

# ── the probe engine ────────────────────────────────────────────────────────
# host:port out of a URL — shared by the -k recovery path and the interception
# scan, so the two can never disagree about what they inspected.
hostport_of() { # hostport_of <url>
  local h="${1#*://}"; h="${h%%/*}"
  [[ "$h" == *:* ]] || h="$h:443"
  printf '%s' "$h"
}

# The issuer of the certificate this host actually presents to US. Empty when
# openssl is absent or the handshake fails — never an error.
cert_issuer() { # cert_issuer <url>
  command -v openssl >/dev/null 2>&1 || return 0
  local hp host i
  hp="$(hostport_of "$1")"; host="${hp%%:*}"
  i="$(echo | timeout 10 openssl s_client -connect "$hp" -servername "$host" 2>/dev/null \
         | openssl x509 -noout -issuer 2>/dev/null)"
  printf '%s' "${i#issuer=}"
}

# Known TLS-inspection appliance vendors. A corporate root ALREADY IN the OS
# trust store makes every probe succeed silently — the -k recovery path can
# only ever catch the untrusted case — so on a healthy-looking network the
# issuer name is the only evidence that a middlebox is reading the traffic.
# That matters even when nothing is broken: it is why a pinned client, a
# `--cacert`-passing container, or a Go binary using its own root pool will
# fail here while curl works.
# ponytail: substring match on a vendor list — it names the common appliances,
# not every one. A private corporate CA with no vendor string in it is caught
# instead by the cross-check against the public web PKI issuers below.
INTERCEPT_VENDORS='zscaler|netskope|blue ?coat|proxysg|fortinet|fortigate|umbrella|palo ?alto|forcepoint|mcafee|web gateway|websense|sophos|checkpoint|check point|trustwave|cloudflare gateway|ssl inspection|deep ?packet'

# A 403 has two completely different meanings and the body tells them apart.
# Object stores and signed-URL CDNs answer a bare `/` with 403 because there
# is no object at that path — static.crates.io and downloads.marketplace.
# jetbrains.com return S3's `<Code>AccessDenied</Code>`, and Docker Hub's blob
# host returns `invalid URL signature`. That is the ORIGIN talking, which
# proves reachability. A proxy policy block, the thing `denied` is for,
# returns a block page instead. Matching on the body is evidence; matching on
# the `Server:` header would not be (a block page can be Cloudflare-fronted).
denied_is_origin() { # denied_is_origin <bodyfile>
  [[ -f "$1" ]] || return 1
  head -c 2000 "$1" 2>/dev/null | grep -qiE \
    '<Code>(AccessDenied|NoSuchKey|InvalidAccessKeyId|SignatureDoesNotMatch)</Code>|invalid URL signature|NoSuchBucket'
}

# Is a proxy in play for this URL? Only matters for exit 56 (see classify).
proxy_ctx_for() { # proxy_ctx_for <url>
  [[ "$1" == https://* ]] || return 0
  if [[ -n "${DISCO_PROXY:-}${https_proxy:-}${HTTPS_PROXY:-}${http_proxy:-}${HTTP_PROXY:-}" ]]; then
    printf 'proxy-https'
  fi
}

# Probes one URL, classifies, writes raw/<key>.{body,head,err,result}. Runs in
# a background job during the bulk phase, so it must touch NO shared state
# beyond its own files.
probe_one() { # probe_one <key> <label> <url> <group>
  local key="$1" label="$2" url="$3" grp="$4"
  local body="$OUT/raw/$key.body" head="$OUT/raw/$key.head" err="$OUT/raw/$key.err"
  local code rc cls detail="" issuer="" skew=""

  code="$(curl -sS -o "$body" -D "$head" -w '%{http_code}' \
            --connect-timeout 5 -m 8 -A 'cc-discover/1.0' \
            "${CURL_FLAGS[@]}" "$url" 2>"$err")"
  rc=$?
  cls="$(classify "$rc" "$code" "$grp" "$(proxy_ctx_for "$url")")"

  # The interception test. A cert error that DISAPPEARS with -k is a
  # re-signer, not a broken server — and the issuer CN is the single most
  # useful string in this whole report, because it names the CA the operator
  # has to go get.
  if [[ "$cls" == tls-cert ]]; then
    if curl -sS -k -o "$body" -D "$head" -m 8 -A 'cc-discover/1.0' \
         "${CURL_FLAGS[@]}" "$url" >/dev/null 2>>"$err"; then
      cls="tls-intercept"
      issuer="$(cert_issuer "$url")"
    else
      cls="tls-other"
    fi
  fi

  # Clock skew, harvested from any probe that got a Date header back. TLS
  # validity windows and signed package indexes both break on a skewed clock,
  # and it presents as a confusing certificate error — worth catching here.
  local dh
  dh="$(grep -i '^date:' "$head" 2>/dev/null | tail -1 | cut -d' ' -f2-)"
  if [[ -n "$dh" ]]; then
    local server_s now_s
    server_s="$(date -d "$dh" +%s 2>/dev/null)" || server_s=""
    now_s="$(date -u +%s)"
    if [[ -n "$server_s" ]]; then
      skew=$(( now_s - server_s )); skew=${skew#-}
    fi
  fi

  if [[ "$cls" == denied ]] && denied_is_origin "$body"; then
    cls="ok"
    detail="reachable — the 403 is the object store answering for an empty path, not a policy block"
  fi

  # Docker Hub's 401 is normal enough to be worth saying out loud in the
  # report, so a reader does not "fix" a working registry.
  [[ "$grp" == containers && "$code" == 401 ]] && \
    detail="V2 API up; anonymous pulls use the token flow"

  # An operator-declared mirror is probed in the same job — it is the
  # ALTERNATIVE to this endpoint, so the two belong in one finding.
  local mvar mirror="" mcode mrc mcls=""
  mvar="DISCO_MIRROR_$(keyvar "$key")"
  mirror="${!mvar:-}"
  if [[ -n "$mirror" ]]; then
    mcode="$(curl -sS -o "$OUT/raw/$key.mirror.body" -D "$OUT/raw/$key.mirror.head" \
               -w '%{http_code}' --connect-timeout 5 -m 8 -A 'cc-discover/1.0' \
               "${CURL_FLAGS[@]}" "$mirror" 2>"$OUT/raw/$key.mirror.err")"
    mrc=$?
    mcls="$(classify "$mrc" "$mcode" "$grp")"
  fi

  [[ -z "$detail" ]] && detail="$(TLS_ISSUER_HINT="$issuer" move_for "$cls" "$(keyvar "$key")")"
  record "$key" "$label" "$url" "$grp" "$cls" "$code" "$detail" \
    "curl_exit=$rc" "issuer=$issuer" "skew=$skew" \
    "mirror=$(redact "$mirror")" "mirror_class=$mcls"
}

# Record a finding that is not an HTTP probe (host facts, env inspection).
# Same shape so the report renderer has one code path.
record_note() { # record_note <key> <label> <group> <class> <detail> [extra k=v ...]
  local k="$1" l="$2" g="$3" c="$4" d="$5"; shift 5
  record "$k" "$l" "-" "$g" "$c" "-" "$d" "$@"
}

# ── catalog ─────────────────────────────────────────────────────────────────
# key|label|url|group. Fixed order — the report renders in catalog order, not
# in whatever order the parallel jobs happened to finish.
CATALOG=()
add() { CATALOG+=("$1|$2|$3|$4"); }

build_catalog() {
  CATALOG=()

  # net — the fundamentals. Probed FIRST and serially: if plain HTTP works and
  # HTTPS does not, that one fact explains half the rest of the report.
  add http-egress   "plain HTTP egress"        "http://deb.debian.org"    net
  add https-egress  "HTTPS egress + TLS check" "https://pypi.org"         net

  # python
  add pypi          "PyPI simple index"        "https://pypi.org/simple/"      python
  add pythonhosted  "PyPI package files"       "https://files.pythonhosted.org" python
  local pipidx=""
  if command -v pip >/dev/null 2>&1; then
    pipidx="$(pip config get global.index-url 2>/dev/null)"
  fi
  [[ -z "$pipidx" ]] && pipidx="${PIP_INDEX_URL:-}"
  [[ -z "$pipidx" ]] && pipidx="${UV_DEFAULT_INDEX:-}"
  add pypa-bootstrap "pip/get-pip bootstrap"  "https://bootstrap.pypa.io"     python
  [[ -n "$pipidx" ]] && add pip-configured "locally configured Python index" "$pipidx" python

  # node
  add npm           "npm registry"             "https://registry.npmjs.org/"   node
  add nodejs-dist   "Node.js distributions"    "https://nodejs.org/dist/"      node
  add nodesource    "NodeSource apt repo"      "https://deb.nodesource.com"    node
  if command -v npm >/dev/null 2>&1; then
    local nreg; nreg="$(npm config get registry 2>/dev/null)"
    if [[ -n "$nreg" && "$nreg" != "https://registry.npmjs.org/" && "$nreg" != undefined ]]; then
      add npm-configured "locally configured npm registry" "$nreg" node
    fi
  fi

  # os-packages — whatever THIS host is actually configured against matters
  # more than the canonical publics, so both get probed.
  local n=0 u
  for u in $(apt_source_hosts); do
    n=$((n+1)); add "apt-configured-$n" "configured apt source" "$u" os-packages
  done
  add deb-debian     "Debian archive"          "https://deb.debian.org"        os-packages
  add deb-security   "Debian security"         "https://security.debian.org"   os-packages
  add ubuntu-archive "Ubuntu archive"          "http://archive.ubuntu.com"     os-packages
  add ubuntu-security "Ubuntu security"        "https://security.ubuntu.com"   os-packages
  add alpine         "Alpine package CDN"      "https://dl-cdn.alpinelinux.org" os-packages

  # containers — V2 API roots. A 401 here is the spec (see classify()).
  add dockerio      "Docker Hub registry"      "https://registry-1.docker.io/v2/" containers
  add dockerio-auth "Docker Hub token service" "https://auth.docker.io/token"     containers
  add ghcr          "GitHub Container Registry" "https://ghcr.io/v2/"             containers
  add quay          "Quay.io"                  "https://quay.io/v2/"              containers
  add mcr           "Microsoft Container Registry" "https://mcr.microsoft.com/v2/" containers
  add registry-k8s  "registry.k8s.io"          "https://registry.k8s.io/v2/"      containers
  add gcr           "Google Container Registry" "https://gcr.io/v2/"              containers
  add ecr-public    "AWS ECR Public"           "https://public.ecr.aws/v2/"       containers
  # The registry root and the LAYER STORAGE are different hosts. An allowlist
  # with only registry-1.docker.io authenticates, resolves the manifest, and
  # then stalls on the first blob — which looks like a hung pull, not a
  # blocked host.
  add dockerhub-blobs "Docker Hub layer storage" "https://production.cloudflare.docker.com" containers

  # forges
  add github          "GitHub web"             "https://github.com"                  forges
  add github-api      "GitHub API"             "https://api.github.com"              forges
  add github-raw      "GitHub raw content"     "https://raw.githubusercontent.com"   forges
  add github-codeload "GitHub tarball/codeload" "https://codeload.github.com"        forges
  add github-objects  "GitHub release objects/LFS" "https://objects.githubusercontent.com" forges
  add gitlab          "GitLab"                 "https://gitlab.com"                  forges
  add bitbucket       "Bitbucket"              "https://bitbucket.org"               forges
  add github-lfs      "GitHub LFS"             "https://lfs.github.com"              forges

  # ecosystems — probed whether or not the toolchain is installed here: a
  # colleague's language is still this environment's problem to document.
  add goproxy       "Go module proxy"          "https://proxy.golang.org"            ecosystems
  add crates        "crates.io"                "https://crates.io"                   ecosystems
  add crates-static "crates.io downloads"      "https://static.crates.io"            ecosystems
  add rubygems      "RubyGems"                 "https://rubygems.org"                ecosystems
  add maven-central "Maven Central"            "https://repo.maven.apache.org/maven2/" ecosystems
  add nuget         "NuGet"                    "https://api.nuget.org/v3/index.json"  ecosystems
  add anaconda      "Anaconda repo"            "https://repo.anaconda.com"           ecosystems
  add conda-forge   "conda-forge channel"      "https://conda.anaconda.org"          ecosystems
  add helm          "Helm releases"            "https://get.helm.sh"                 ecosystems
  add k8s-dl        "Kubernetes downloads"     "https://dl.k8s.io"                   ecosystems
  add k3s           "k3s install script"       "https://get.k3s.io"                  ecosystems
  add hashicorp     "HashiCorp releases"       "https://releases.hashicorp.com"      ecosystems
  # Second-host traps: the index authenticates fine and the DOWNLOAD is a
  # different name, so the failure lands mid-install and reads as a corrupt
  # package rather than a blocked host.
  add gosum         "Go checksum database"     "https://sum.golang.org"              ecosystems
  add crates-index  "crates.io sparse index"   "https://index.crates.io"             ecosystems
  add yarn          "Yarn registry"            "https://registry.yarnpkg.com"        ecosystems

  # ai — the LFS/CDN hostname has churned repeatedly (cdn-lfs.huggingface.co
  # no longer resolves at all), so probing a guess would manufacture a false
  # `dns` finding. The hub host is the seam that matters.
  add huggingface   "Hugging Face Hub"         "https://huggingface.co"              ai
  add anthropic     "Anthropic API"            "https://api.anthropic.com"           ai
  add openai        "OpenAI API"               "https://api.openai.com"              ai
  add ollama        "Ollama model registry"    "https://registry.ollama.ai/v2/"      ai

  # devtools — an IDE that cannot update or install an extension is a silent
  # productivity tax nobody files a ticket about.
  add vscode-update      "VS Code updates"       "https://update.code.visualstudio.com"      devtools
  add vscode-marketplace "VS Code marketplace"   "https://marketplace.visualstudio.com"      devtools
  add vscode-cdn         "VS Code CDN"           "https://main.vscode-cdn.net"               devtools
  add playwright-cdn     "Playwright browsers"   "https://cdn.playwright.dev"                devtools
  add playwright-cdn2    "Playwright browsers (legacy CDN)" "https://playwright.azureedge.net" devtools
  add jetbrains-dl       "JetBrains downloads"   "https://download.jetbrains.com"            devtools
  add jetbrains-plugins  "JetBrains plugin repo" "https://downloads.marketplace.jetbrains.com" devtools

  # cdn
  add jsdelivr      "jsDelivr CDN"             "https://cdn.jsdelivr.net"            cdn
  add unpkg         "unpkg CDN"                "https://unpkg.com"                   cdn
  add gfonts        "Google Fonts"             "https://fonts.googleapis.com"        cdn
}

# Crude on purpose: every URL in the apt config, deduped by HOST. A real
# sources parser would be a project; what matters here is "which hosts must
# this machine reach to patch itself", and the scheme+host is that answer.
apt_source_hosts() {
  local f
  {
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
      [[ -f "$f" ]] || continue
      grep -Eo 'https?://[^ 	"]+' "$f" 2>/dev/null
    done
  } | sed -E 's#(https?://[^/]+).*#\1#' | sort -u
}

# ── phase: probe ────────────────────────────────────────────────────────────
phase_probe() {
  mkdir -p "$OUT/raw" || { fail "out-dir" "cannot create $OUT/raw"; return 1; }
  rm -f "$OUT"/raw/*.result "$OUT"/raw/*.body "$OUT"/raw/*.head "$OUT"/raw/*.err 2>/dev/null

  command -v curl >/dev/null 2>&1 || { fail "curl" "curl is the one hard dependency and it is not installed"; return 1; }

  probe_net_fundamentals
  build_catalog
  printf '%s\n' "${CATALOG[@]}" >"$OUT/raw/catalog.txt"

  # Parallelism exists for exactly one case: a default-deny network, where
  # every probe burns the full 8s timeout. Serially that is ten minutes of
  # nothing; in batches of 8 it is about a minute. No job server, no xargs -P
  # dependency — a counter and `wait` is enough.
  note ""
  note "probing ${#CATALOG[@]} endpoints (batches of 8)…"
  local line inflight=0
  for line in "${CATALOG[@]}"; do
    IFS='|' read -r k l u g <<<"$line"
    # net fundamentals were already probed serially above.
    [[ "$g" == net ]] && continue
    probe_one "$k" "$l" "$u" "$g" &
    inflight=$((inflight+1))
    if (( inflight >= 8 )); then wait; inflight=0; fi
  done
  wait

  scan_tls_inspection # after every probe: it only inspects hosts that PASSED
  record_clock_skew   # after every probe: it reads their Date headers
  collect_host_facts
  report_probe_lines
}

# The fundamentals go first and serially, because everything after them is
# interpreted in their light: no DNS at all makes 60 `dns` classes noise
# rather than 60 findings.
probe_net_fundamentals() {
  local dnsclass="unknown" dnsdetail=""
  if command -v getent >/dev/null 2>&1; then
    if getent hosts pypi.org >/dev/null 2>&1; then
      dnsclass="ok"; dnsdetail="public names resolve (getent hosts pypi.org)"
    else
      dnsclass="dns"; dnsdetail="$(move_for dns PYPI)"
    fi
  else
    dnsdetail="no getent on this host — DNS posture inferred from the probes below"
  fi
  record_note dns-sanity "public DNS resolution" net "$dnsclass" "$dnsdetail"

  # Proxy environment, REDACTED. A proxy set for root and not for the user (or
  # vice versa) is a classic "works for me" and worth writing down.
  local pl="" v n
  for n in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY no_proxy NO_PROXY; do
    v="${!n:-}"; [[ -n "$v" ]] && pl+="$n=$(redact "$v") "
  done
  [[ -n "${DISCO_PROXY:-}" ]] && pl+="DISCO_PROXY=$(redact "$DISCO_PROXY") "
  if [[ -n "$pl" ]]; then
    record_note proxy-env "proxy configuration" net ok "detected: ${pl% }"
  else
    record_note proxy-env "proxy configuration" net ok "no proxy variables set — probes go direct"
  fi

  local line
  for line in \
    "http-egress|plain HTTP egress|http://deb.debian.org|net" \
    "https-egress|HTTPS egress + TLS check|https://pypi.org|net" \
    "ocsp|certificate revocation (OCSP)|http://ocsp.digicert.com|net"; do
    IFS='|' read -r k l u g <<<"$line"
    probe_one "$k" "$l" "$u" "$g"
  done

  # OCSP is a NUANCE, never a failure: every mainstream TLS client soft-fails
  # a blocked responder, so the handshake still completes — after stalling for
  # seconds. That is the "TLS is mysteriously slow here" ticket, and it is
  # unrecognisable unless someone wrote this line down.
  if [[ "$(rfield "$OUT/raw/ocsp.result" class)" != ok ]]; then
    record_note ocsp "certificate revocation (OCSP)" net softfail "$(move_for softfail)"
  fi

  probe_captive_portal
}

# Captive-portal / content-injection detection. Both endpoints are operated by
# their vendors for exactly this purpose (chromium.org's network portal
# detection design) and their answers are fixed: 204 with an empty body, and
# the literal string `success`. Anything else means something between here and
# them rewrote a plain-HTTP response — which makes EVERY http:// finding in
# this report suspect, including the apt mirrors.
probe_captive_portal() {
  local bad=""
  local code body
  code="$(curl -sS -o "$OUT/raw/portal-204.body" -w '%{http_code}' \
            --connect-timeout 5 -m 8 -A 'cc-discover/1.0' "${CURL_FLAGS[@]}" \
            http://www.gstatic.com/generate_204 2>"$OUT/raw/portal-204.err")"
  if [[ "$code" != 204 || -s "$OUT/raw/portal-204.body" ]]; then
    bad="gstatic generate_204 answered $code with a $(wc -c <"$OUT/raw/portal-204.body" 2>/dev/null || echo 0)-byte body (expected 204, empty)"
  fi
  body="$(curl -sS -m 8 -A 'cc-discover/1.0' "${CURL_FLAGS[@]}" \
            http://detectportal.firefox.com/success.txt 2>"$OUT/raw/portal-txt.err")"
  body="${body//[$'\r\n']/}"
  if [[ "$body" != success ]]; then
    bad="${bad:+$bad; }detectportal.firefox.com returned '${body:0:60}' (expected 'success')"
  fi

  if [[ -n "$bad" ]]; then
    record_note captive-portal "captive portal / content injection" net portal \
      "$bad — $(move_for portal)"
  else
    record_note captive-portal "captive portal / content injection" net ok \
      "plain-HTTP responses arrive unmodified (both vendor canaries answered exactly as specified)"
  fi
}

# Interception detection for the case the -k path structurally cannot see: the
# corporate root is already installed, so nothing fails and the whole report
# reads green while a middlebox terminates every session. Three hosts from
# THREE DIFFERENT groups, because inspection policy is routinely per-SNI —
# exempting a package index while inspecting github is a common config, and a
# single sample would report either "clean" or "intercepted" for the whole
# network on the strength of one lucky pick.
scan_tls_inspection() {
  command -v openssl >/dev/null 2>&1 || return 0
  local pairs=("pypi https://pypi.org" "dockerio https://registry-1.docker.io" "github https://github.com")
  local hit="" issuers="" p k u iss
  : >"$OUT/raw/tls-issuers.txt"
  for p in "${pairs[@]}"; do
    read -r k u <<<"$p"
    # Only inspect what actually answered: an issuer for an unreachable host
    # would be a fabricated finding.
    [[ -f "$OUT/raw/$k.result" && "$(rfield "$OUT/raw/$k.result" class)" == ok ]] || continue
    iss="$(cert_issuer "$u")"
    [[ -n "$iss" ]] || continue
    printf '%s|%s\n' "$(hostport_of "$u")" "$iss" >>"$OUT/raw/tls-issuers.txt"
    issuers+="$iss; "
    [[ -z "$hit" ]] && grep -qiE "$INTERCEPT_VENDORS" <<<"$iss" && hit="$iss"
    # The second signal: a bundle the operator had to supply is itself the
    # proof. If the default trust store cannot complete this handshake but
    # DISCO_CA_BUNDLE can, something is re-signing.
    if [[ -z "$hit" && -n "${DISCO_CA_BUNDLE:-}" ]]; then
      curl -sS -o /dev/null -m 8 "$u" >/dev/null 2>&1 || hit="$iss"
    fi
  done
  [[ -n "$hit" ]] || return 0
  record_note tls-inspection "TLS inspection (probes passed)" net tls-intercept \
    "every probe succeeded, but the presented issuer is an inspection CA: $hit — the corporate root is already in this host's trust store, so anything with its OWN root pool (Go binaries, pinned clients, a container that does not mount the host bundle) will still fail here" \
    "issuer=$hit" "issuers_seen=${issuers%; }"
}

# Clock skew, over every probe that answered. The statistic is the MINIMUM
# |now - Date|, not the maximum: a CDN happily serves a cached response with
# a twelve-hour-old Date header (download.docker.com does exactly this), so a
# single large reading measures the cache, not the clock. A cached Date can
# only be stale, never fresher than real — so the smallest reading is the
# honest one. >120s is where signed apt indexes and short-lived TLS certs
# start rejecting you with errors that look like anything but a clock.
record_clock_skew() {
  local f s m=""
  for f in "$OUT"/raw/*.result; do
    [[ -f "$f" ]] || continue
    s="$(rfield "$f" skew)"
    [[ -n "$s" ]] || continue
    [[ -z "$m" ]] && m="$s"
    (( s < m )) && m="$s"
  done
  if [[ -z "$m" ]]; then
    record_note clock-skew "clock skew vs. server time" net unknown \
      "no probe returned a Date header — clock could not be checked against anything"
  elif (( m > 120 )); then
    record_note clock-skew "clock skew vs. server time" net error \
      "host clock is ${m}s off server time — fix NTP first; skew surfaces as certificate and signed-index errors that look like network faults"
  else
    record_note clock-skew "clock skew vs. server time" net ok "within ${m}s of server time"
  fi
}

# There is no universal version flag, and the failure modes are all
# DIFFERENT: `openssl --version` is an invalid command, `helm --version` an
# unknown flag, and `kubectl version` shells out to a cluster it may not be
# able to reach (printing a kubeconfig warning and an error, exit 0). So: try
# each spelling, then take the first line that actually LOOKS like a version.
# Reporting an error message in the version column is worse than "unknown".
tool_version() { # tool_version <tool>
  local t="$1" out v spelling
  for spelling in "--version" "version --client" "version"; do
    # shellcheck disable=SC2086
    out="$("$t" $spelling 2>&1)"
    v="$(printf '%s' "$out" | grep -viE 'level=(warning|error)|^(warn|error)' \
           | grep -m1 -E '[0-9]+\.[0-9]+')"
    [[ -n "$v" ]] && { printf '%s' "$v"; return 0; }
  done
  printf 'installed (version not reported)'
}

# Host facts. All local; none of it can fail the run — a missing tool is a
# finding for the report, and the report is the point.
collect_host_facts() {
  local h="$OUT/raw/hostinfo.env"
  local osname arch kern
  osname="$( . /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-unknown}" )"
  arch="$(uname -m)"; kern="$(uname -r)"
  # QUOTED: `report` sources this file, and a PRETTY_NAME like
  # `Debian GNU/Linux 12 (bookworm)` is a bash syntax error unquoted.
  {
    printf 'HOST_OS="%s"\n' "${osname//\"/}"
    printf 'HOST_ARCH="%s"\n' "$arch"
    printf 'HOST_KERNEL="%s"\n' "$kern"
    printf 'HOST_GENERATED="%s"\n' "$(date -u +%FT%TZ)"
  } >"$h"

  # Tool inventory: name + version, or the absence. There is no universal
  # version flag — `openssl --version` and `helm --version` are errors, and
  # kubectl prints a kubeconfig warning BEFORE its version — so try both
  # spellings and drop the log-ish noise lines rather than reporting an error
  # message as if it were a version string.
  local t v
  : >"$OUT/raw/tools.txt"
  for t in curl git openssl python3 pip uv node npm podman docker kubectl helm jq make gcc; do
    if command -v "$t" >/dev/null 2>&1; then
      v="$(tool_version "$t")"
      printf '%s|present|%s\n' "$t" "${v:-unknown}" >>"$OUT/raw/tools.txt"
    else
      printf '%s|absent|not installed\n' "$t" >>"$OUT/raw/tools.txt"
    fi
  done

  # Config-file PRESENCE only, never contents: ~/.netrc and .npmrc routinely
  # hold credentials and this report is a document people paste around.
  : >"$OUT/raw/paths.txt"
  local p
  for p in "$HOME/.netrc" "$HOME/.config/pip/pip.conf" /etc/pip.conf "$HOME/.npmrc" /etc/npmrc \
           "$HOME/.config/containers/registries.conf" /etc/containers/registries.conf \
           /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt; do
    [[ -e "$p" ]] && printf '%s|present\n' "$p" >>"$OUT/raw/paths.txt"
  done

  # podman's own mirror view. REPORTED, not probed — a registries.conf mirror
  # silently changes where every pull goes, and that is exactly the kind of
  # nuance someone debugging an unexpected image later needs to know existed.
  if command -v podman >/dev/null 2>&1; then
    podman info --format '{{.Registries}}' >"$OUT/raw/podman-registries.txt" 2>/dev/null || \
      printf 'podman present but `podman info` failed\n' >"$OUT/raw/podman-registries.txt"
  fi

  # git over HTTPS is the real-protocol truth: git's smart-HTTP transport uses
  # its own TLS stack and its own proxy handling, so it can fail where a plain
  # curl to github.com succeeds.
  if command -v git >/dev/null 2>&1; then
    if timeout 15 git ls-remote https://github.com/octocat/Hello-World >/dev/null 2>"$OUT/raw/git-https.err"; then
      record_note git-https "git clone over HTTPS (real protocol)" forges ok \
        "git ls-remote against github.com succeeded"
    else
      record_note git-https "git clone over HTTPS (real protocol)" forges refused \
        "git ls-remote failed where plain HTTPS may work — git uses its own TLS/proxy config; see raw/git-https.err, and set http.sslCAInfo / http.proxy"
    fi
  else
    record_note git-https "git clone over HTTPS (real protocol)" forges unknown \
      "git is not installed — could not test the real clone protocol"
  fi

  df -Pk / "$OUT" 2>/dev/null >"$OUT/raw/disk.txt"
}

# One console line per probe, in catalog order, plus ONE useraction per
# distinct class. Sixty identical "ask for the mirror" lines is noise that
# hides the two findings that differ; the move is per-class, so say it once.
report_probe_lines() {
  local f key cls label seen=" " v
  note ""
  for f in $(result_files_in_order); do
    key="$(rfield "$f" key)"; cls="$(rfield "$f" class)"; label="$(rfield "$f" label)"
    local mcls; mcls="$(rfield "$f" mirror_class)"
    local msg; msg="$label — $(status_for "$cls")"
    [[ "$mcls" == ok ]] && msg="$msg; configured mirror works"
    v="$(verdict_for "$cls")"
    # A working mirror demotes an unreachable public endpoint to a nuance:
    # the resource IS consumable here, just not from the public name.
    [[ "$v" == fail && "$mcls" == ok ]] && v=warn
    case "$v" in
      pass) pass "$key" "$msg" ;;
      warn) warn "$key" "$msg" ;;
      *)    fail "$key" "$msg" ;;
    esac
    if [[ "$v" != pass && "$seen" != *" $cls "* ]]; then
      seen+="$cls "
      local iss; iss="$(rfield "$f" issuer)"
      useraction "$cls" "$(TLS_ISSUER_HINT="$iss" move_for "$cls" "$(keyvar "$key")")"
    fi
  done
}

# Result files in catalog order, with anything not in the catalog (host facts,
# net notes, git-https) appended so nothing is silently dropped.
result_files_in_order() {
  local line k f seen=" "
  if [[ -f "$OUT/raw/catalog.txt" ]]; then
    while IFS='|' read -r k _ _ _; do
      f="$OUT/raw/$k.result"
      [[ -f "$f" ]] && { printf '%s\n' "$f"; seen+="$k "; }
    done <"$OUT/raw/catalog.txt"
  fi
  for f in "$OUT"/raw/*.result; do
    [[ -f "$f" ]] || continue
    k="$(basename "$f" .result)"
    [[ "$seen" == *" $k "* ]] || printf '%s\n' "$f"
  done
}

# ── phase: report ───────────────────────────────────────────────────────────
# Renders from raw/ ONLY — no probing. That separation is deliberate: a report
# is cheap to regenerate after hand-editing a raw result while diagnosing, and
# re-probing a hostile network to fix a markdown table is a waste of ten
# minutes and somebody's IDS alert budget.
phase_report() {
  local n; n=$(ls "$OUT"/raw/*.result 2>/dev/null | wc -l)
  if (( n == 0 )); then
    fail "report" "no probe results in $OUT/raw — run ./discover.sh probe first"
    return 1
  fi

  # shellcheck disable=SC1090
  [[ -f "$OUT/raw/hostinfo.env" ]] && . "$OUT/raw/hostinfo.env"

  write_env_file
  write_report_md
  pass "report" "$OUT/discovery-report.md and $OUT/discovery.env written from $n probe results"
}

write_env_file() {
  local f k cls out="$OUT/discovery.env"
  {
    printf '# generated by deploy/discover.sh — sourceable; classes only, never contents\n'
    printf 'DISCO_GENERATED="%s"\n' "$(date -u +%FT%TZ)"
    for f in $(result_files_in_order); do
      k="$(keyvar "$(rfield "$f" key)")"; cls="$(rfield "$f" class)"
      printf 'DISCO_%s="%s"\n' "$k" "$cls"
    done
    local iss; iss="$(tls_issuer)"
    if [[ -n "$iss" ]]; then
      printf 'DISCO_TLS_INTERCEPT="1"\n'
      printf 'DISCO_TLS_ISSUER="%s"\n' "${iss//\"/}"
    else
      printf 'DISCO_TLS_INTERCEPT="%s"\n' "$(tls_intercepted && printf 1 || printf 0)"
    fi
    printf 'DISCO_PROXY_DETECTED="%s"\n' "$(rfield "$OUT/raw/proxy-env.result" detail)"
    printf 'DISCO_CLOCK_SKEW_S="%s"\n' "$(min_skew)"
  } >"$out"
}

tls_intercepted() { grep -lq '^class=tls-intercept$' "$OUT"/raw/*.result 2>/dev/null; }
tls_issuer() {
  local f i
  for f in "$OUT"/raw/*.result; do
    [[ -f "$f" ]] || continue
    [[ "$(rfield "$f" class)" == tls-intercept ]] || continue
    i="$(rfield "$f" issuer)"; [[ -n "$i" ]] && { printf '%s' "$i"; return 0; }
  done
  printf ''
}
# See record_clock_skew for why this is a minimum and not a maximum.
min_skew() {
  local f s m=""
  for f in "$OUT"/raw/*.result; do
    [[ -f "$f" ]] || continue
    s="$(rfield "$f" skew)"; [[ -n "$s" ]] || continue
    [[ -z "$m" ]] && m="$s"
    (( s < m )) && m="$s"
  done
  printf '%s' "${m:-0}"
}

write_report_md() {
  local md="$OUT/discovery-report.md"
  local np=0 nw=0 nf=0 f v
  for f in $(result_files_in_order); do
    v="$(verdict_for "$(rfield "$f" class)")"
    case "$v" in pass) np=$((np+1));; warn) nw=$((nw+1));; *) nf=$((nf+1));; esac
  done

  {
    printf '# Environment discovery report\n\n'
    printf -- '- generated: `%s` (UTC)\n' "$(date -u +%FT%TZ)"
    printf -- '- host: `%s` · `%s` · kernel `%s`\n' "${HOST_OS:-unknown}" "${HOST_ARCH:-unknown}" "${HOST_KERNEL:-unknown}"
    printf -- '- verdict: **%d available · %d with nuance · %d unavailable**\n\n' "$np" "$nw" "$nf"
    printf 'What this measures is not "does the internet work" but *how this network says no* —\n'
    printf 'a DNS miss, a TCP reject, a silent drop, a TLS re-signer and a proxy 403 each need a\n'
    printf 'different move, and the move is in the last column.\n\n'

    write_nuances_section
    write_group_tables
    write_howto_section
    write_tools_section

    printf -- '---\n\n'
    printf 'For Central Command'"'"'s own external sources and the `.env` seam that redirects each\n'
    printf 'one, see `deploy/AIRGAP.md` — this report says what the environment allows; that\n'
    printf 'document says which knob to turn.\n\n'
    printf '**Keep this file out of public trees.** It names internal mirror hosts, proxies and\n'
    printf 'CA issuers. It lives under `deploy/discovery.out/`, which is gitignored.\n'
  } >"$md"
}

# The cross-cutting section, first, because these five facts explain most of
# the individual rows below them.
write_nuances_section() {
  printf '## Environment nuances\n\n'

  local pd; pd="$(rfield "$OUT/raw/proxy-env.result" detail 2>/dev/null)"
  printf -- '- **Proxy:** %s\n' "${pd:-not probed}"

  local iss; iss="$(tls_issuer)"
  if tls_intercepted; then
    printf -- '- **TLS interception: YES.** '
    [[ -n "$iss" ]] && printf 'Presented issuer: `%s`. ' "$iss"
    if [[ -f "$OUT/raw/tls-inspection.result" ]]; then
      printf 'Note that **every probe still passed** — the corporate root is already in this host'"'"'s trust store, so the breakage will land on whatever brings its OWN root pool (Go binaries, pinned clients, a container that does not mount the host bundle) rather than on curl. '
    fi
    printf 'Export that root CA to a PEM file, set `DISCO_CA_BUNDLE` in `discovery.conf`, and configure each toolchain (see below). Do NOT standardize on disabling verification — it converts one broken build into an unverifiable supply chain.\n'
  else
    printf -- '- **TLS interception:** none detected — no probe recovered under `-k` after a certificate failure, and no inspection-appliance issuer was seen on the hosts that succeeded.\n'
  fi
  if [[ -s "$OUT/raw/tls-issuers.txt" ]]; then
    printf '  Issuers observed (inspection policy is often per-SNI, so these can differ):\n'
    local hl il
    while IFS='|' read -r hl il; do printf '    - `%s` ← `%s`\n' "$hl" "$il"; done <"$OUT/raw/tls-issuers.txt"
  fi

  local cp; cp="$(rfield "$OUT/raw/captive-portal.result" detail 2>/dev/null)"
  [[ -n "$cp" ]] && printf -- '- **Plain-HTTP integrity:** %s\n' "$cp"
  if [[ "$(rfield "$OUT/raw/ocsp.result" class 2>/dev/null)" == softfail ]]; then
    printf -- '- **OCSP:** %s\n' "$(move_for softfail)"
  fi

  local sk; sk="$(min_skew)"
  if (( sk > 120 )); then
    printf -- '- **Clock skew: %ss.** Fix NTP before debugging anything else — skew surfaces as certificate and signed-index errors that look like network faults.\n' "$sk"
  else
    printf -- '- **Clock skew:** %ss — fine.\n' "$sk"
  fi

  local dnsd; dnsd="$(rfield "$OUT/raw/dns-sanity.result" detail 2>/dev/null)"
  printf -- '- **DNS posture:** %s\n' "${dnsd:-not probed}"

  if [[ -s "$OUT/raw/podman-registries.txt" ]]; then
    printf -- '- **podman registries (configured, not probed):** `%s`\n' "$(head -c 400 "$OUT/raw/podman-registries.txt" | tr -d '\n')"
    printf '  A `[[registry.mirror]]` here silently redirects every pull; an image that resolves differently on another host starts its explanation in this line.\n'
  fi
  printf '\n'
}

write_group_tables() {
  local g title f
  for g in net python node os-packages containers forges ecosystems ai devtools cdn; do
    # Skip a group with no results rather than emitting an empty table.
    local any=0
    for f in $(result_files_in_order); do
      [[ "$(rfield "$f" group)" == "$g" ]] && { any=1; break; }
    done
    (( any )) || continue
    case "$g" in
      net) title="Network fundamentals" ;;
      python) title="Python" ;;
      node) title="Node.js" ;;
      os-packages) title="OS packages" ;;
      containers) title="Container registries" ;;
      forges) title="Source forges" ;;
      ecosystems) title="Other language ecosystems and tooling" ;;
      ai) title="AI / model services" ;;
      devtools) title="Developer tooling (IDEs, browsers, plugins)" ;;
      cdn) title="CDNs" ;;
      *) title="$g" ;;
    esac
    printf '## %s\n\n' "$title"
    [[ "$g" == containers ]] && printf 'A `401` at a `/v2/` root is the OCI distribution spec advertising its token flow — that is AVAILABLE, not a problem.\n\n'
    printf '| resource | endpoint | status | detail |\n|---|---|---|---|\n'
    for f in $(result_files_in_order); do
      [[ "$(rfield "$f" group)" == "$g" ]] || continue
      local key lbl url cls det mir mcls st
      key="$(rfield "$f" key)"; lbl="$(rfield "$f" label)"; url="$(rfield "$f" url)"
      cls="$(rfield "$f" class)"; det="$(rfield "$f" detail)"
      mir="$(rfield "$f" mirror)"; mcls="$(rfield "$f" mirror_class)"
      st="$(status_for "$cls")"
      if [[ "$mcls" == ok ]]; then
        st="NUANCE (via mirror)"
        det="use the configured mirror \`$mir\` — the public endpoint is $cls"
        [[ "$cls" == ok ]] && { st="AVAILABLE"; det="public endpoint works; configured mirror \`$mir\` also works"; }
      elif [[ -n "$mcls" && "$mcls" != ok ]]; then
        det="$det (configured mirror also failed: $mcls)"
      fi
      printf '| %s (`%s`) | `%s` | %s | %s |\n' "$lbl" "$key" "$url" "$st" "$det"
    done
    printf '\n'
  done
}

# Only for findings that EXIST. A boilerplate dump of every possible config
# line is how a document stops being read.
write_howto_section() {
  local body="" f key cls mir mcls
  local iss; iss="$(tls_issuer)"

  # Per-resource mirror configuration.
  for f in $(result_files_in_order); do
    key="$(rfield "$f" key)"; cls="$(rfield "$f" class)"
    mir="$(rfield "$f" mirror)"; mcls="$(rfield "$f" mirror_class)"
    [[ "$mcls" == ok ]] || continue
    case "$key" in
      pypi|pythonhosted|pip-configured)
        body+="$(printf '**Python — use the mirror:**\n```bash\nexport PIP_INDEX_URL=%s\nexport UV_DEFAULT_INDEX=%s   # uv reads no PIP_* variable\n```\n\n' "$mir" "$mir")"$'\n\n' ;;
      npm|npm-configured)
        body+="$(printf '**npm — use the mirror:**\n```bash\nexport NPM_CONFIG_REGISTRY=%s\n# or persist: npm config set registry %s   (writes ~/.npmrc)\n```\n\n' "$mir" "$mir")"$'\n\n' ;;
      dockerio|ghcr|quay|mcr|registry-k8s|gcr|ecr-public)
        body+="$(printf '**Container registry `%s` — mirror it in `~/.config/containers/registries.conf`:**\n```toml\n[[registry]]\nprefix = "%s"\nlocation = "%s"\n  [[registry.mirror]]\n  location = "%s"\n  pull-from-mirror = "digest-only"\n```\n\n' "$key" "$(reg_host "$key")" "$(reg_host "$key")" "${mir#*://}")"$'\n\n' ;;
      apt*|deb-*|ubuntu-archive)
        body+="$(printf '**apt — point sources at the mirror:**\n```\ndeb %s <suite> main\n```\n(and the matching security suite: security is a separate path on every mirror)\n\n' "$mir")"$'\n\n' ;;
      *)
        body+="$(printf '**%s:** consume via `%s` (public endpoint is %s).\n\n' "$key" "$mir" "$cls")"$'\n\n' ;;
    esac
  done

  # TLS interception touches every toolchain separately — none of them read
  # the same variable, which is why an interception "fix" always looks half
  # done for a week.
  if tls_intercepted; then
    body+="$(printf '**TLS interception — teach every toolchain the corporate root** (export it once, e.g. `/usr/local/share/ca-certificates/corp-root.crt`, then `update-ca-certificates`):\n```bash\nexport SSL_CERT_FILE=/path/corp-root.pem       # openssl, curl\nexport REQUESTS_CA_BUNDLE=/path/corp-root.pem  # python requests/httpx\nexport NODE_EXTRA_CA_CERTS=/path/corp-root.pem # node, npm\ngit config --global http.sslCAInfo /path/corp-root.pem\npip config set global.cert /path/corp-root.pem\n```\nIssuer seen: `%s`\n\n' "${iss:-unknown}")"$'\n\n'
  fi

  # Proxy.
  if [[ -n "${DISCO_PROXY:-}" ]] || grep -q 'detected:' "$OUT/raw/proxy-env.result" 2>/dev/null; then
    local px; px="$(redact "${DISCO_PROXY:-${https_proxy:-${HTTPS_PROXY:-http://proxy.corp.example:8080}}}")"
    # Both cases, deliberately: curl reads the lowercase names, several JVM and
    # Go tools only the uppercase, and git needs its own config entry.
    body+="$(printf '**Proxy — export it for every tool:**\n```bash\nP=%s\nexport http_proxy=$P https_proxy=$P HTTP_PROXY=$P HTTPS_PROXY=$P\nexport no_proxy=localhost,127.0.0.1,.internal NO_PROXY=$no_proxy\ngit config --global http.proxy $P\n```\nKeep credentials in `~/.netrc` (mode 600), never in a committed file or a shell history line.\n' "$px")"$'\n\n'
  fi

  [[ -n "$body" ]] || return 0
  printf '## How to consume each resource here\n\n'
  printf '%s\n' "$body"
}

# The registry host a probe key stands for — only used to render the
# registries.conf snippet, so a lookup table is honest and a parser is not.
reg_host() {
  case "$1" in
    dockerio) printf 'docker.io' ;;
    ghcr) printf 'ghcr.io' ;;
    quay) printf 'quay.io' ;;
    mcr) printf 'mcr.microsoft.com' ;;
    registry-k8s) printf 'registry.k8s.io' ;;
    gcr) printf 'gcr.io' ;;
    ecr-public) printf 'public.ecr.aws' ;;
    *) printf '%s' "$1" ;;
  esac
}

write_tools_section() {
  [[ -f "$OUT/raw/tools.txt" ]] || return 0
  printf '## Tool inventory\n\n'
  printf 'An absent tool is a finding, not an error — it tells the next person what they must bring.\n\n'
  printf '| tool | state | version |\n|---|---|---|\n'
  local t s v
  while IFS='|' read -r t s v; do
    printf '| `%s` | %s | %s |\n' "$t" "$s" "$v"
  done <"$OUT/raw/tools.txt"
  printf '\n'
  if [[ -s "$OUT/raw/paths.txt" ]]; then
    printf 'Config files present (paths only — contents are never read or reported):\n\n'
    local p st
    while IFS='|' read -r p st; do printf -- '- `%s` (%s)\n' "$p" "$st"; done <"$OUT/raw/paths.txt"
    printf '\n'
  fi
  if [[ -s "$OUT/raw/disk.txt" ]]; then
    printf 'Disk:\n\n```\n%s\n```\n\n' "$(cat "$OUT/raw/disk.txt")"
  fi
}

# ── phase: selftest ─────────────────────────────────────────────────────────
# The classifier is the only real logic in this script and it is the one thing
# that cannot be checked by running the script against a network (you cannot
# make a network produce curl exit 60 on demand). So it is checked here, with
# synthetic inputs, offline.
phase_selftest() {
  local bad=0
  check() { # check <expected> <curl_exit> <http> [group] [proxy_ctx]
    local got; got="$(classify "$2" "$3" "${4:-}" "${5:-}")"
    if [[ "$got" == "$1" ]]; then
      pass "classify" "rc=$2 http=$3 grp=${4:--} ctx=${5:--} -> $got"
    else
      fail "classify" "rc=$2 http=$3 grp=${4:--} ctx=${5:--} -> $got (expected $1)"; bad=1
    fi
  }
  check dns          6  000
  check refused      7  000
  check timeout      28 000
  check tls-cert     60 000
  check tls-other    35 000
  check proxy-dns    5  000
  check unreachable  22 000
  check proxy-auth   0  407
  check auth         0  401
  check ok           0  401 containers   # the OCI token flow, not a failure
  check denied       0  403
  check ok           0  200
  check ok           0  204
  check ok           0  301
  check ok           0  404              # API roots that 404 on GET are up
  check ok           0  405
  check ok           0  421              # vhost refuses `/`, path still proven
  check throttled    0  429              # reachable, anonymous quota is the wall
  check error        0  503
  check error        0  502
  # Exit 56 only means "proxy refused the CONNECT tunnel" when a proxy is in
  # play on an https target; bare, it is an ordinary mid-transfer reset.
  check unreachable  56 000
  check unreachable  56 000 forges
  check proxy-auth   56 000 forges proxy-https
  check proxy-auth   56 000 ""     proxy-https

  local got
  got="$(redact 'https://svc:hunter2@mirror.corp.example/simple/')"
  if [[ "$got" == 'https://REDACTED@mirror.corp.example/simple/' ]]; then
    pass "redact" "credentials stripped from a URL"
  else
    fail "redact" "got '$got'"; bad=1
  fi

  for got in ok auth tls-intercept dns timeout denied throttled portal softfail proxy-auth; do
    if [[ -n "$(move_for "$got" PYPI)" ]]; then
      pass "move-text" "$got has an operator move"
    else
      fail "move-text" "$got has no operator move"; bad=1
    fi
  done

  # `throttled`, `portal` and `softfail` are all REACHABLE — a rate limit, an
  # injecting proxy and a blocked OCSP responder are nuances, not outages, and
  # must never bucket as failures.
  [[ "$(verdict_for ok)" == pass && "$(verdict_for auth)" == warn && "$(verdict_for dns)" == fail \
     && "$(verdict_for throttled)" == warn && "$(verdict_for portal)" == warn \
     && "$(verdict_for softfail)" == warn ]] \
    && pass "verdict" "pass/warn/fail mapping holds" \
    || { fail "verdict" "mapping wrong"; bad=1; }

  # The vendor list is a regex; a typo in it would silently disable every
  # already-trusted-CA interception finding.
  local i
  for i in "CN = Zscaler Intermediate Root CA" "O = Netskope Inc" "CN = FortiGate CA" "CN = Blue Coat ProxySG"; do
    grep -qiE "$INTERCEPT_VENDORS" <<<"$i" \
      && pass "intercept-vendors" "matches: $i" \
      || { fail "intercept-vendors" "missed: $i"; bad=1; }
  done
  grep -qiE "$INTERCEPT_VENDORS" <<<"CN = R11, O = Let's Encrypt, C = US" \
    && { fail "intercept-vendors" "false positive on a public web PKI issuer"; bad=1; } \
    || pass "intercept-vendors" "no false positive on Let's Encrypt"

  # A 403 from an object store proves reachability; a proxy block page does
  # not. Both are 403, so only the body separates them.
  local tmp; tmp="$(mktemp)"
  printf '<?xml version="1.0"?><Error><Code>AccessDenied</Code></Error>' >"$tmp"
  denied_is_origin "$tmp" && pass "denied-origin" "S3 AccessDenied reads as reachable" \
    || { fail "denied-origin" "missed an S3 error body"; bad=1; }
  printf '{"status":403,"message":"invalid URL signature"}' >"$tmp"
  denied_is_origin "$tmp" && pass "denied-origin" "signed-URL CDN 403 reads as reachable" \
    || { fail "denied-origin" "missed a signed-URL 403"; bad=1; }
  printf '<html><h1>Access Denied</h1><p>Blocked by corporate policy: category Software Downloads</p></html>' >"$tmp"
  denied_is_origin "$tmp" && { fail "denied-origin" "a proxy block page must stay denied"; bad=1; } \
    || pass "denied-origin" "proxy block page stays denied"
  rm -f "$tmp"

  [[ "$(hostport_of https://github.com/foo)" == github.com:443 \
     && "$(hostport_of https://reg.corp.example:8443/v2/)" == reg.corp.example:8443 ]] \
    && pass "hostport" "host:port parsed from a URL" \
    || { fail "hostport" "got $(hostport_of https://github.com/foo) / $(hostport_of https://reg.corp.example:8443/v2/)"; bad=1; }

  [[ "$(keyvar registry-k8s)" == REGISTRY_K8S ]] \
    && pass "keyvar" "registry-k8s -> REGISTRY_K8S" \
    || { fail "keyvar" "got $(keyvar registry-k8s)"; bad=1; }

  return "$bad"
}

# ─────────────────────────────────────────────────────────────────────────────
usage() {
  cat >&2 <<'USAGE'
usage: ./discover.sh [probe|report|selftest] [--out DIR]

  (no argument)  probe, then report
  probe          run every probe; writes raw/ results under the out dir
  report         re-render discovery-report.md + discovery.env from raw/
                 (offline — safe after hand-reading a raw result)
  selftest       offline unit check of the classifier; touches no network

  --out DIR      output directory (default: deploy/discovery.out)

  exit codes     0 clean · 1 hard failure · 2 warnings
                 3 USER ACTION REQUIRED — see the USERACTION lines

  operator config: deploy/discovery.conf (gitignored, operator-local) —
  DISCO_CA_BUNDLE, DISCO_PROXY, DISCO_NETRC, DISCO_INSECURE,
  DISCO_MIRROR_<KEY>. Read the header of this file for what each means.
USAGE
}

main() {
  local cmd="" arg
  local args=()
  while (( $# )); do
    case "$1" in
      --out) OUT="$2"; shift 2 ;;
      --out=*) OUT="${1#--out=}"; shift ;;
      -h|--help|help) usage; exit 0 ;;
      *) args+=("$1"); shift ;;
    esac
  done
  arg="${args[0]:-}"
  cmd="${arg:-all}"
  # A relative --out is relative to the script, so `cd`-ing somewhere else and
  # running it by path does not scatter output directories around the disk.
  [[ "$OUT" == /* ]] || OUT="$HERE/$OUT"

  case "$cmd" in
    selftest) phase_selftest; local rc=$?; exit $rc ;;  # no conf, no network
    probe|report|all) load_conf ;;
    *) usage; exit 1 ;;
  esac

  mkdir -p "$OUT/raw" || { fail "out-dir" "cannot create $OUT/raw"; exit 1; }

  case "$cmd" in
    probe)  phase_probe ;;
    report) phase_report ;;
    all)    phase_probe; phase_report ;;
  esac

  note ""
  [[ -f "$OUT/discovery-report.md" ]] && note "output: $OUT/discovery-report.md"
  # A blocked resource is somebody's move, not a defect — so a gate outranks a
  # failure in the exit code, exactly as in deploy/single/setup.sh.
  (( ACTIONS )) && exit 3
  (( FAILS )) && exit 1
  (( WARNS )) && exit 2
  exit 0
}

main "$@"
