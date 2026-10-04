# shellcheck shell=bash
# ============================================================================
# phases/fetch.sh — the `fetch` phase of the single-node install (deploy/single).
#
#   FETCH: acquire every dependency (public or mirror) before anything is
#   deployed — the one phase that needs the network; it stops for the
#   operator per artifact, naming the .env seam.
#
#   SOURCED by deploy/single/setup.sh, never executed: setup.sh sources the
#   ten phase files in steps.tsv's phase order before main runs, and this
#   file defines functions and constants and does nothing else, so sourcing it
#   twice is harmless. What more than one phase uses — the output protocol,
#   load_env and the .env helpers, compose, the image catch-up, the process
#   helpers `stop` shares, the probes two phases read — is setup.sh's.
#
#   Design record: docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md
#   D11 — "a thin orchestrator sourcing step files in order" (Sentry's
#   installer), one file per manifest phase (v2.58.0). The functions below
#   MOVED here from setup.sh unchanged, comments and all; the records they
#   cite are the ones that shaped them.
#
#   ROWS (steps.tsv, phase `fetch`) — step, kind, probe; a probe marked
#   (setup.sh) is shared with another phase or the driver and lives there:
#     resolve-images                     run    p_resolve_images
#     image-postgres                     run    p_image_postgres
#     image-neo4j                        run    p_image_neo4j
#     image-redis                        run    p_image_redis
#     image-berriai_litellm_database     run    p_image_berriai_litellm_database
#     image-n8nio_n8n                    run    p_image_n8nio_n8n
#     image-python                       run    p_image_python
#     image-playwright_python            run    p_image_playwright_python
#     image-speaches_ai_speaches         run    p_image_speaches_ai_speaches
#     image-sandbox                      run    p_image_sandbox  (setup.sh)
#     image-crawler                      run    p_image_crawler  (setup.sh)
#     venv                               run    p_venv  (setup.sh)
#     cockpit                            run    p_cockpit_npm
# ============================================================================

[[ -n "${CC_PHASE_FETCH_LOADED:-}" ]] && return 0
CC_PHASE_FETCH_LOADED=1

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: fetch — acquire EVERY dependency before anything is deployed.
# ─────────────────────────────────────────────────────────────────────────────
# The check IS the acquisition: an image is proven by RESOLVING it against the
# registry (resolve-images.sh: constraint/lock/resolve, design record D2) and
# then pulling the resolved ref, a build by building it (podman caches layers,
# so a retry costs the failing layer), the Python graph by resolving it, the
# cockpit by `npm ci`. Each failure names the seam that governs it, and the
# phase ends in USERACTION (exit 3): the operator fixes the mirror seam
# (deploy/discover.sh maps what the network can reach) and re-runs — acquired
# artifacts fast-forward. Nothing falls back on its own — a fallback chosen at
# 11pm by a script is a decision nobody can find later.
# CAPTURE BEFORE GREPPING: `podman images | grep -q` inverts under pipefail
# (grep exits at the first match, podman takes SIGPIPE, the pipeline reports
# failure on success).
have_image() { local imgs; imgs="$(podman images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null)"; grep -qx "$1" <<<"$imgs"; }

# Pull every ref resolve-images.sh wrote into .env. The refs are TAGGED, not
# digest-pinned: the lock's digest is verified at resolution time against the
# registry, and a substituted tag is deliberately trusted from the mirror.
# Returns 3 for the ONE seam the operator must fill: resolve-images.sh exits 3
# when the configured registry answered and cannot serve an image's tested
# artifact (the locked tag absent with no substitute, or another digest under
# it) — a `USERACTION image-<name>` naming CC_REGISTRY_* / CC_IMG_*, nothing
# this script can do about it. 1 for anything else, including the resolver's
# exit 1: an unreachable registry, an operator pin that is malformed or does
# not exist, a malformed images.txt row. D5: a failure is never reported as
# "stopped for your action".
fetch_images() {
  local rc=0 sd=""
  local -a rargs=()
  # A STAGED run (update.sh apply's acquisition, D5) resolves the NEW release's
  # images.txt into COPIES, never into the deployment's .env and
  # installed.manifest. Two reasons, both read off this resolver:
  #   * the tree is still the OLD release until the merge, and an apply that
  #     stops before it (a catalog pause, a refused fast-forward) must leave
  #     what compose reads exactly as it was;
  #   * on a PARTIAL failure — the very case staging exists for, a mirror
  #     lacking one tag — the resolver has already rewritten the keys it could
  #     resolve, so the real .env would hold half a new release. (It used to
  #     rewrite the manifest WITHOUT the one it could not, too, which turned
  #     that image's old value into an OPERATOR PIN for good; since v2.57.0 it
  #     carries the failed image's previous row forward — carry_forward.)
  # The copies are seeded from the real ones — every CC_IMG_* line (an operator
  # pin included) and the manifest — so the pin-versus-own-write judgement is
  # the same one the real run will make. Only CC_IMG_* and CC_STATE_DIR are
  # copied: the resolver reads every other answer from the environment load_env
  # exported, so no credential is duplicated. The post-merge `./setup.sh fetch`
  # then writes the real ones, with every artifact already present.
  if (( STAGED )); then
    sd="$(cc_stage_dir "$STATE_DIR")/acquire"
    mkdir -p "$sd" || { fail "resolve-images" "could not create $sd for the staged resolution"; return 1; }
    { grep -E '^(CC_IMG_[A-Z0-9_]+|CC_STATE_DIR)=' "$ENV_FILE" || true; } >"$sd/answers.env"
    chmod 600 "$sd/answers.env" 2>/dev/null || true
    rm -f "$sd/installed.manifest"
    if [[ -f "$STATE_DIR/installed.manifest" ]]; then
      cp "$STATE_DIR/installed.manifest" "$sd/installed.manifest" \
        || { fail "resolve-images" "could not copy installed.manifest into $sd"; return 1; }
    fi
    rargs=(--env-file "$sd/answers.env" --manifest "$sd/installed.manifest")
  fi
  "$HERE/resolve-images.sh" ${rargs[@]+"${rargs[@]}"} || rc=$?
  case "$rc" in
    0) pass "resolve-images" "every image resolved to its locked tag" ;;
    2) pass "resolve-images" "resolved, with substitutions — see the WARN lines above and $STATE_DIR/installed.manifest" ;;
    3) useraction "resolve-images" "image resolution stopped for you — the USERACTION image-* line(s) above name the seam (CC_REGISTRY_* for the mirror HOST, CC_IMG_<NAME> for an exact ref this resolver must use as-is). Nothing was deployed; fix the seam in the repo-root .env and re-run"
       return 3 ;;
    *) fail "resolve-images" "image resolution failed (exit $rc) — the FAIL lines above name the seam per image"; return 1 ;;
  esac
  # resolve-images.sh writes CC_IMG_* into .env; re-read so this shell has them.
  # Staged, it wrote them into the copy: source THAT, so what is pulled below is
  # the NEW release's refs (the build scripts inherit them from here too).
  if (( STAGED )); then
    set -a
    # shellcheck disable=SC1090,SC1091
    . "$sd/answers.env"
    set +a
  else
    load_env || return 1
  fi

  local var ref check
  while IFS= read -r var; do
    ref="${!var:-}"; [[ -z "$ref" ]] && continue
    check="image-${var#CC_IMG_}"; check="${check,,}"
    if have_image "$ref"; then pass "$check" "$ref present"; continue; fi
    # --tls-verify=false when the operator has turned verification off: no
    # exported variable reaches a podman pull (D4).
    if podman pull -q "${PULL_TLS[@]}" "$ref" >/dev/null 2>&1 </dev/null; then
      pass "$check" "$ref pulled"
    else
      fail "$check" "$ref could not be pulled — seams: the CC_REGISTRY_* entry for its registry in .env, $var itself (an exact ref the resolver must use as-is, including a re-namespaced PATH), or the registries drop-in the machine phase writes"
    fi
  done < <(compgen -A variable CC_IMG_ | sort)
}

# Build when the image is absent OR was built from other inputs (v2.57.0): an
# unlabelled image (an earlier release built it) counts as different and is
# rebuilt once. podman's layer cache makes a rebuild of an unchanged layer
# cheap; a changed one is exactly what must be rebuilt.
#
# A STAGED run (D5) builds ASIDE: the build script itself tags
# cc_staged_image_ref's `-staged` form when CC_STAGED_FOR is set, so this asks
# about — and names — that ref. The live tag the running deployment uses does
# not move before the merge; the post-merge fetch builds it, from the cache the
# staged build warmed, and its label is what local_image_state reads.
fetch_local() { # fetch_local <check> <ref> <build-script> <seams>
  local check="$1" ref="$2" script="$3" seams="$4" said aside=""
  if (( STAGED )); then
    # The new release did not change this image's inputs: the LIVE image is
    # already the one it needs, so there is nothing to prove and nothing to
    # build aside.
    if [[ "$(local_image_state "$ref" "$script")" == current ]]; then
      pass "$check" "$ref present and built from this release's inputs already"
      return 0
    fi
    ref="$(cc_staged_image_ref "$ref")"
    aside=" (aside — the running deployment's tag does not move before the merge)"
  fi
  case "$(local_image_state "$ref" "$script")" in
    current)    pass "$check" "$ref present and built from these inputs${aside}"
                (( STAGED )) || drop_staged_tag "$ref"
                return 0 ;;
    stale)      said="rebuilt — build inputs changed since the image was made" ;;
    unlabelled) said="rebuilt — build inputs changed since the image was made (it carried no $(cc_build_inputs_label) label: an earlier release built it)" ;;
    *)          said="built" ;;
  esac
  if "$script" >&2; then
    pass "$check" "$ref ${said}${aside}"
    (( STAGED )) || drop_staged_tag "$ref"
  else
    fail "$check" "$ref failed to build against your sources — seams: $seams (see .env.example's deployment section)"
  fi
}

# Once the LIVE tag is built from these inputs, the aside tag a staged
# acquisition left (see fetch_local) has done its job. `podman untag <img>
# <name>` removes exactly that NAME: never an image, never a layer — so it
# cannot evict the cache the live build was just served from, which is why this
# is `untag` and not `rmi` (podman's rmi also prunes dangling parents unless
# told not to, and when the aside image is not the live one that is the warm
# cache). If the two tags named different images, the aside one is left
# dangling like the image any rebuild replaces; `podman image prune` is the
# operator's. The NAME is passed explicitly: `untag` with no name strips EVERY
# name of the image, the live tag included. Absent (a fresh install, an update
# that built nothing new) is the common case and says nothing. Never from a
# probe: this mutates.
drop_staged_tag() { # drop_staged_tag <live-ref>
  local aside
  aside="$(cc_staged_image_ref "$1")"
  have_image "$aside" || return 0
  if podman untag "$aside" "$aside" >/dev/null 2>&1 </dev/null; then
    note "    untagged $aside — the staged acquisition's build, now that $1 is built"
  else
    note "    could not untag $aside (harmless: no container uses it) — podman untag $aside $aside"
  fi
  return 0
}

phase_fetch() {
  load_env || return 1
  [[ -f "$HERE/images.txt" ]] || { fail "images-txt" "$HERE/images.txt missing"; return 1; }

  # The ONE deliberate pause this phase keeps (D5): an unresolvable pin or a
  # mirror that lacks a tag is a seam only the operator can fill. Everything
  # else — a pull of a RESOLVED ref, a local build — is a FAIL, and this phase
  # returns 1 for it. Until 2026-10-01 it could not: its only FAIL path ended
  # in a USERACTION summary, the phase runner ranked USERACTION above FAIL, and
  # `update.sh apply` merged the new code straight past a failed build.
  local frc=0
  fetch_images || frc=$?

  if [[ "$CC_ENABLE_SANDBOX" == 1 ]]; then
    fetch_local "image-sandbox" "localhost/cc-sandbox:1" \
      "$HERE/build-sandbox-image.sh" "CC_IMG_PYTHON (the base ref, resolved from images.txt), CC_REGISTRY_DOCKERIO, CC_APT_MIRROR, CC_NPM_REGISTRY, CC_CA_BUNDLE, CC_TLS_INSECURE"
  else
    pass "image-sandbox" "skipped (CC_ENABLE_SANDBOX=0)"
  fi
  if [[ "$CC_ENABLE_CRAWLER" == 1 ]]; then
    fetch_local "image-crawler" "localhost/cc-crawler:1" \
      "$HERE/build-crawler-image.sh" "CC_IMG_PLAYWRIGHT_PYTHON (the base ref, resolved from images.txt), CC_REGISTRY_MCR, CC_PYPI_INDEX_URL, CC_CA_BUNDLE, CC_TLS_INSECURE"
  else
    pass "image-crawler" "skipped (CC_ENABLE_CRAWLER=0)"
  fi

  # Python: the venv now (its interpreter may itself be a download — seam
  # CC_PYTHON_MIRROR), then a resolve of exactly what the app phase installs.
  if ! venv_python >/dev/null; then
    if ! uv venv --python 3.12 "$REPO_ROOT/.venv" >&2; then
      fail "venv" "uv could not create a Python 3.12 venv — no 3.12 on PATH and the download failed; seam: CC_PYTHON_MIRROR"
    fi
  fi
  if venv_python >/dev/null; then
    export VIRTUAL_ENV="$REPO_ROOT/.venv"
    resolve_python_deps
  fi

  # Cockpit: `npm ci` is the acquisition; the build itself is the app phase's.
  fetch_cockpit

  if (( FAILS )); then
    # The guidance survives; the USERACTION does not. It was what made a failed
    # build read as "stopped for your action" (D5).
    note ""
    note "$FAILS artifact(s) could not be acquired. Fix the seam(s) named above in the"
    note "repo-root .env and re-run $( (( STAGED )) && printf './update.sh apply — nothing has been merged' || printf './setup.sh') (acquired ones fast-forward);"
    note "deploy/discover.sh maps what this network can reach, deploy/AIRGAP.md the seams."
    return 1
  fi
  (( frc == 3 )) && return 3
  return 0
}

# The cockpit's acquisition: its npm tree, when node 22+ is there to use it.
fetch_cockpit() {
  if command -v node >/dev/null 2>&1; then
    local nv; nv="$(node -v 2>/dev/null)"; nv="${nv#v}"
    if [[ "${nv%%.*}" =~ ^[0-9]+$ ]] && (( ${nv%%.*} >= 22 )); then
      fetch_cockpit_npm
    else
      warn "cockpit" "node v$nv is older than 22 — cockpit not fetched; the API runs without it"
    fi
  else
    warn "cockpit" "node not found — cockpit not fetched; the API runs without it"
  fi
}

# `npm ci` only when the tree is not already the lockfile's (P5, F12): the
# 2026-10-02 Windows run spent ~70 s reinstalling an unchanged tree on every
# fetch, adoption included. CURRENT means web/node_modules is there AND
# `<state>/cockpit.npm-inputs` equals the hash of the lockfile and node's major
# version now (deploy/env-lib.sh's cc_cockpit_current — the same question
# p_cockpit_npm asks, so the plan agrees). The record is dropped BEFORE
# `npm ci` starts (it empties node_modules first, and a run killed half-way
# must not read current) and written only after it succeeded. A STAGED run
# (update.sh apply's acquisition) installs into a throwaway tree, so it never
# skips on, and never writes, the deployment's record — the post-merge fetch
# would otherwise skip an install the new lockfile needs.
fetch_cockpit_npm() {
  if (( ! STAGED )) && cc_cockpit_current "$STATE_DIR" npm "$REPO_ROOT"; then
    pass "cockpit" "npm tree current — web/node_modules was installed from this package-lock.json (and this node major), so npm ci was skipped"
    return 0
  fi
  (( STAGED )) || cc_cockpit_forget "$STATE_DIR" npm
  if in_web npm ci >&2; then
    (( STAGED )) || cc_cockpit_record "$STATE_DIR" npm "$REPO_ROOT" \
      || warn "cockpit" "could not record the npm tree's inputs in $(cc_cockpit_record_file "$STATE_DIR" npm) — the next run reinstalls it"
    pass "cockpit" "npm tree installed from ${CC_NPM_REGISTRY:-registry.npmjs.org}"
  else
    fail "cockpit" "npm ci failed — seam: CC_NPM_REGISTRY"
  fi
}

# Present AND installed from this lockfile: a changed package-lock.json (or a
# new node major) reads false, so the plan says WILL RUN and the row re-runs.
p_cockpit_npm() {
  p_node_ok || return 0
  cc_cockpit_current "$STATE_DIR" npm "$REPO_ROOT"
}

# ── fetch ───────────────────────────────────────────────────────────────────
# The CC_IMG_* names images.txt implies FOR THESE FLAGS — the resolver's own
# derivation (its `img_var`), mirrored here so a probe needs no second list of
# images and a disabled component's image is never demanded.
p_image_vars() {
  local line path comp n
  [[ -f "$HERE/images.txt" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    line="${line%%#*}"
    [[ -n "${line//[[:space:]]/}" ]] || continue
    # shellcheck disable=SC2086
    set -- $line
    (( $# == 6 )) || continue
    path="$2"; comp="$6"
    case "$comp" in
      n8n)          p_off CC_ENABLE_N8N 0 1 && continue ;;
      speech)       p_off CC_ENABLE_SPEECH 1 1 && continue ;;
      sandbox-base) p_off CC_ENABLE_SANDBOX 1 1 && continue ;;
      crawler-base) p_off CC_ENABLE_CRAWLER 1 1 && continue ;;
    esac
    n="${path^^}"; n="${n//[\/-]/_}"
    printf 'CC_IMG_%s\n' "${n#LIBRARY_}"
  done <"$HERE/images.txt"
  return 0
}

p_resolve_images() {
  [[ -f "$STATE_DIR/installed.manifest" ]] || return 1
  # The manifest records what the resolver DID; .env is what the deploy READS,
  # and compose interpolates from there. A manifest without the keys is the
  # v2.48.0 shape of defect, so both halves are probed.
  local var
  while IFS= read -r var; do
    [[ -n "$var" ]] || continue
    [[ -n "$(get_kv "$ENV_FILE" "$var")" ]] || return 1
  done < <(p_image_vars)
  return 0
}

p_img() { # p_img <CC_IMG_var>
  local ref
  ref="$(get_kv "$ENV_FILE" "$1")"
  [[ -n "$ref" ]] || return 1
  have_image "$ref"
}

p_image_postgres() {
  p_img CC_IMG_POSTGRES
}

p_image_neo4j() {
  p_img CC_IMG_NEO4J
}

p_image_redis() {
  p_img CC_IMG_REDIS
}

p_image_berriai_litellm_database() {
  p_img CC_IMG_BERRIAI_LITELLM_DATABASE
}

p_image_n8nio_n8n() {
  p_off CC_ENABLE_N8N 0 1 && return 0
  p_img CC_IMG_N8NIO_N8N
}

p_image_python() {
  p_off CC_ENABLE_SANDBOX 1 1 && return 0
  p_img CC_IMG_PYTHON
}

p_image_playwright_python() {
  p_off CC_ENABLE_CRAWLER 1 1 && return 0
  p_img CC_IMG_PLAYWRIGHT_PYTHON
}

p_image_speaches_ai_speaches() {
  p_off CC_ENABLE_SPEECH 1 1 && return 0
  p_img CC_IMG_SPEACHES_AI_SPEACHES
}

