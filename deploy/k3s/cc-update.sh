#!/usr/bin/env bash
# ============================================================================
# cc-update.sh — the EXTERNAL updater for the k3s deployment (one-click apply).
#
#   Design (2026-08-28, from the two-survey research pass; see the
#   setup-update contract spec): the cockpit NEVER applies its own update.
#   cc-nerve's only privileged act is writing /run/cc-update/trigger;
#   cc-update.path starts cc-update.service (root, Type=oneshot), whose
#   ExecStartPre CONSUMES the trigger (mv -> request.json) so the
#   recheck-on-exit loop in systemd.path(5) cannot fire, and this script does
#   everything else. Prior art: Portainer's detached updater + persisted
#   schedule record; greenboot's the-updater-owns-health-and-rollback;
#   PackageKit's never-mutate-the-live-process.
#
#   Pipeline (no-op exits BEFORE any mutation):
#     resolve target (highest semver tag on origin, fetched) -> version gate
#     (no downgrade, min_upgrade_from honored) -> checkpoint tag -> PREBUILD
#     the locally-built images whose inputs changed, WHILE THE SYSTEM IS UP
#     (operator decision 2026-08-29: a build is the long pole and it does not
#     need downtime; a failed build then costs nothing) -> DB dumps (spine,
#     litellm, n8n + the two decryption keys — see below) -> stop cc-uvicorn +
#     cc-sandbox-runner (cc-nerve stays up serving progress; restarted last)
#     -> merge the tag -> schema (additive-only, idempotent) -> pip + cockpit
#     rebuild AS codyslab (root-owned files in the checkout break every later
#     build) -> apply changed k8s manifests + install changed unit files ->
#     start services -> health poll -> on failure: git reset --hard to the
#     checkpoint, rebuild, restore the images/manifests the forward pass
#     touched, restart, re-poll — the rolled_back state is honest about what
#     schema kept (additive-only means old code runs fine against it).
#
#   DB-dump scope deliberately excludes Neo4j: dumping it means scaling
#   cc-graphiti/cc-neo4j to 0 on the CHROMEBOX (backup.sh's scale-down/dump/
#   scale-up cycle), which this update never touches or migrates and which
#   would add real downtime to an already-live rollout. The nightly
#   `cc-backup.timer` covers Neo4j DR; this pre-update dump is only for the
#   three stores an update can actually put in a bad state.
#
#   Status: /var/lib/cc-update/status.json, written ATOMICALLY (tmp + mv) at
#   every phase change — the UI re-reads it after its own restart; the full
#   detail channel is `journalctl -u cc-update`. Files live OUTSIDE the repo
#   tree on purpose: rollback's reset --hard would clobber anything inside.
# ============================================================================
set -uo pipefail

REPO=/home/codyslab/central-command
RUNAS=(runuser -u codyslab --)
RUNDIR=/run/cc-update
STATEDIR=/var/lib/cc-update
STATUS="$STATEDIR/status.json"
REQUEST="$RUNDIR/request.json"
STAGE_REQUEST="$RUNDIR/stage-request.json"
STAGE_MODE=0   # 1 = `cc-update.sh stage`: prebuild caches only, never mutate
BACKUPS=/home/codyslab/cc-backups
K=(k3s kubectl -n central-command)
STARTED_AT="$(date -u +%FT%TZ)"

# k3s's containerd keeps Kubernetes images in the k8s.io namespace; anything
# imported or tagged elsewhere is invisible to the kubelet.
CTR_NS=k8s.io
SSHQ=(ssh -o BatchMode=yes -o ConnectTimeout=10)

# What the forward pass actually touched — rollback reverses only these.
RETAGGED=()          # "<node>|<ref>" per node whose running image we preserved
IMAGE_DEPLOYS=()     # deployments whose image bytes changed under the same tag
TOUCHED_DEPLOYS=()   # deployments whose manifest changed
APPLIED_MANIFESTS=0
WT=""                # build worktree, "" when none exists

set -a
# shellcheck disable=SC1091
[[ -f "$REPO/deploy/pi/.env" ]] && . "$REPO/deploy/pi/.env"
set +a

# Same resolution as the build scripts and verify.sh: override in deploy/pi/.env.
COMPUTE_SSH="${CC_COMPUTE_SSH:-chromebox_admin@100.113.118.28}"

note() { printf '%s\n' "$*"; }   # journald captures stdout — that IS the log

# jq-free JSON writer: every value passes through python's json.dumps, so a
# git error message with quotes in it cannot corrupt the file.
write_status() { # write_status <state> <phase> [error]
  local tmp
  tmp="$(mktemp "$STATEDIR/.status.XXXXXX")" || return 0
  python3 - "$1" "$2" "${3:-}" "${CUR_VERSION:-}" "${TARGET_VERSION:-}" "$STARTED_AT" >"$tmp" <<'PY'
import json, sys
from datetime import datetime, timezone
state, phase, error, cur, target, started = sys.argv[1:7]
out = {"state": state, "phase": phase, "current": cur, "target": target,
       "started_at": started, "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
if error: out["error"] = error
if state in ("success", "failed", "rolled_back"): out["finished_at"] = out["updated_at"]
print(json.dumps(out))
PY
  chmod 644 "$tmp" && mv -f "$tmp" "$STATUS"
}

phase() { note "== $1"; write_status running "$1"; }

start_services()  { systemctl start cc-uvicorn cc-sandbox-runner; systemctl restart cc-nerve; }

healthy() { # poll the API up to 2 minutes; it is the load-bearing process
  local i
  for i in $(seq 1 30); do
    curl -fsS -m 5 http://127.0.0.1:8080/health >/dev/null 2>&1 && return 0
    sleep 4
  done
  return 1
}

rebuild() { # as codyslab, never root
  "${RUNAS[@]}" env VIRTUAL_ENV="$REPO/.venv" PATH="/home/codyslab/.local/bin:$PATH" \
    bash -c "cd '$REPO' && uv pip install -e '.[dev,runtime]'" || return 1
  # `npm ci`, never `npm install`: install REWRITES package-lock.json when its
  # resolution differs (2026-08-29: one metadata line), leaving a dirty tracked
  # file that makes the NEXT run die at resolve's clean-tree check.
  "${RUNAS[@]}" bash -c "cd '$REPO/web' && npm ci && npm run build" || return 1
}

# ── locally-built images ────────────────────────────────────────────────────
# The three images no registry serves. Every ref is FULLY QUALIFIED, including
# on the podman side: podman tags local builds `localhost/<name>`, which the
# kubelet never matches, and the manifests' bare names normalise to exactly
# these. Fields: name | ref | build script | deployment | nodes | change inputs.
#   - graphiti FLOATS, so both nodes must hold it (preferred nodeAffinity).
#   - sandbox and crawler carry REQUIRED affinity to the compute node.
#   - cc-sandbox has NO Deployment: the sandbox runner creates pods per run, so
#     a fresh pod picks the new bytes up on its own and there is nothing to
#     restart. Hence the empty deployment field.
# A change to a build script is a changed input like any file in its context.
IMAGES=(
  "graphiti|docker.io/library/cc-graphiti:1.0.2-anthropic|build-graphiti-image.sh|cc-graphiti|anchor compute|deploy/pi/graphiti deploy/k3s/build-graphiti-image.sh"
  "sandbox|docker.io/library/cc-sandbox:1|build-sandbox-image.sh||compute|deploy/k3s/sandbox.Dockerfile deploy/k3s/build-sandbox-image.sh"
  "crawler|docker.io/library/cc-crawler:1|build-crawler-image.sh|cc-crawler|compute|central_command/crawler deploy/k3s/build-crawler-image.sh"
)

# `ctr images ls | grep -q` reports FAILURE on success under pipefail (grep
# exits at the first match and ctr takes SIGPIPE) — capture, then grep.
ctr_images() { # ctr_images <anchor|compute>
  if [[ "$1" == anchor ]]; then
    k3s ctr -n "$CTR_NS" images ls -q 2>/dev/null
  else
    "${RUNAS[@]}" "${SSHQ[@]}" "$COMPUTE_SSH" "sudo k3s ctr -n $CTR_NS images ls -q" 2>/dev/null
  fi
}

have_image() { # have_image <node> <ref> — exact ref, never a substring
  local imgs; imgs="$(ctr_images "$1")"
  grep -qx "$2" <<<"$imgs"
}

ctr_tag() { # ctr_tag <node> <from-ref> <to-ref>
  if [[ "$1" == anchor ]]; then
    k3s ctr -n "$CTR_NS" images tag --force "$2" "$3" >/dev/null
  else
    "${RUNAS[@]}" "${SSHQ[@]}" "$COMPUTE_SSH" "sudo k3s ctr -n $CTR_NS images tag --force '$2' '$3'" >/dev/null
  fi
}

changed_between() { # changed_between <pathspec...> — output non-empty = release touches it
  "${RUNAS[@]}" git -C "$REPO" diff --name-only "$CKPT" "v$TARGET_VERSION" -- "$@"
}

deployments_in() { # deployments_in <manifest> — every Deployment it declares
  [[ -f "$1" ]] || return 0
  awk '/^kind: Deployment/{d=1} d&&/^  name:/{print $2; d=0}' "$1"
}

# Never `cd` into the worktree: a cwd inside a removed worktree is its own trap.
remove_worktree() {
  [[ -n "$WT" ]] || return 0
  "${RUNAS[@]}" git -C "$REPO" worktree remove --force "$WT" 2>/dev/null \
    || { rm -rf "$WT"; "${RUNAS[@]}" git -C "$REPO" worktree prune; }
  WT=""
}

die() { # <phase> <message> — best-effort recovery: services back up, honest status
  note "FAILED at $1: $2"
  write_status failed "$1" "$2"
  # Stage mode never stopped anything — restarting cc-nerve here would blip
  # the cockpit for no reason.
  [[ $STAGE_MODE -eq 1 ]] || start_services || true
  exit 1
}

# A SIGTERM (TimeoutStartSec) must not leave a silent corpse.
trap 'write_status failed "${PHASE_NOW:-unknown}" "terminated (timeout or stop)"; exit 143' TERM
# The build worktree must not survive ANY exit path, TERM included (the TERM
# handler exits, so this runs after it).
trap 'remove_worktree' EXIT

main() {
  mkdir -p "$STATEDIR"

  PHASE_NOW=resolve; phase "resolving target version"
  local requested=""
  [[ -f "$REQUEST" ]] && requested="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("target",""))' "$REQUEST" 2>/dev/null)"

  CUR_VERSION="$(sed -n 's/^version=//p' "$REPO/VERSION" | head -1)"
  [[ -n "$CUR_VERSION" ]] || die resolve "no version= in $REPO/VERSION"

  # Dirty tracked files would be clobbered by rollback's reset --hard — refuse
  # before touching anything (same rule as update.sh apply).
  if [[ -n "$("${RUNAS[@]}" git -C "$REPO" status --porcelain --untracked-files=no)" ]]; then
    die resolve "uncommitted changes to tracked files in $REPO — commit them first"
  fi

  "${RUNAS[@]}" git -C "$REPO" fetch origin --tags --quiet || die resolve "git fetch origin failed"
  TARGET_VERSION="${requested:-$("${RUNAS[@]}" git -C "$REPO" tag -l 'v[0-9]*' | sed 's/^v//' | sort -V | tail -1)}"
  TARGET_VERSION="${TARGET_VERSION#v}"
  [[ -n "$TARGET_VERSION" ]] || die resolve "no semver tags found on origin"

  if [[ "$TARGET_VERSION" == "$CUR_VERSION" ]]; then
    note "already at $CUR_VERSION — nothing to do"
    write_status success up-to-date
    exit 0
  fi
  # Version gate: no downgrade, and honor the target's min_upgrade_from.
  if [[ "$(printf '%s\n%s\n' "$TARGET_VERSION" "$CUR_VERSION" | sort -V | head -1)" == "$TARGET_VERSION" ]]; then
    die resolve "refusing a downgrade: installed $CUR_VERSION, target $TARGET_VERSION"
  fi
  local minfrom
  minfrom="$("${RUNAS[@]}" git -C "$REPO" show "v$TARGET_VERSION:VERSION" 2>/dev/null | sed -n 's/^min_upgrade_from=//p' | head -1)"
  if [[ -n "$minfrom" && "$(printf '%s\n%s\n' "$CUR_VERSION" "$minfrom" | sort -V | head -1)" == "$CUR_VERSION" && "$CUR_VERSION" != "$minfrom" ]]; then
    die resolve "v$TARGET_VERSION requires upgrading from >= $minfrom (installed $CUR_VERSION) — apply the intermediate release first"
  fi

  PHASE_NOW=checkpoint; phase "tagging rollback checkpoint"
  # One stamp names both rollback records: the git tag and the containerd tags
  # that preserve the currently-running image bytes.
  CKPT="pre-update-$(date -u +%Y%m%dT%H%M%SZ)"
  "${RUNAS[@]}" git -C "$REPO" config user.name  >/dev/null 2>&1 || "${RUNAS[@]}" git -C "$REPO" config user.name "Central Command"
  "${RUNAS[@]}" git -C "$REPO" config user.email >/dev/null 2>&1 || "${RUNAS[@]}" git -C "$REPO" config user.email "update@localhost"
  "${RUNAS[@]}" git -C "$REPO" tag "$CKPT" || die checkpoint "could not tag"

  # ── prebuild: the system stays UP through every build ─────────────────────
  # A failure here costs nothing but a git tag and a spare containerd tag, so
  # it happens before the stop AND before the DB dumps.
  PHASE_NOW=prebuild; phase "prebuilding changed images (services stay up)"
  local row name ref script dep nodes paths node log i rc=0
  local -a changed_rows=() build_names=() build_pids=() build_logs=()
  for row in "${IMAGES[@]}"; do
    IFS='|' read -r name ref script dep nodes paths <<<"$row"
    # shellcheck disable=SC2086  # paths is a pathspec LIST, word splitting intended
    [[ -n "$(changed_between $paths)" ]] && changed_rows+=("$row")
  done

  if [[ ${#changed_rows[@]} -eq 0 ]]; then
    note "no image inputs changed between $CKPT and v$TARGET_VERSION — nothing to build"
  else
    # Build from a detached worktree of the target tag: no merge is needed, so
    # the running checkout stays exactly as the live services expect it.
    WT="/home/codyslab/.cc-update-build-$CKPT"
    "${RUNAS[@]}" git -C "$REPO" worktree add --detach "$WT" "v$TARGET_VERSION" \
      || die prebuild "could not create the build worktree $WT — nothing was stopped or merged"

    for row in "${changed_rows[@]}"; do
      IFS='|' read -r name ref script dep nodes paths <<<"$row"
      # Rollback safety FIRST: the new image lands on the SAME tag, so this is
      # the only chance to name the bytes that are running right now.
      for node in $nodes; do
        if have_image "$node" "$ref"; then
          ctr_tag "$node" "$ref" "${ref%:*}:$CKPT" \
            || die prebuild "could not preserve the running $ref on $node — nothing was stopped or merged"
          RETAGGED+=("$node|$ref")
        else
          note "WARNING: $ref is absent from containerd on $node — nothing to preserve for rollback"
        fi
      done
      # The target tag's OWN build script, resolving its REPO_ROOT from
      # BASH_SOURCE inside the worktree; as codyslab, because git, ssh keys and
      # the docker group are the operator's, not root's.
      log="$(mktemp "/tmp/cc-update-build-$name.XXXXXX.log")"
      "${RUNAS[@]}" env CC_COMPUTE_SSH="$COMPUTE_SSH" bash "$WT/deploy/k3s/$script" >"$log" 2>&1 &
      build_pids+=("$!"); build_names+=("$name"); build_logs+=("$log")
    done

    # Concurrent, but each image's output is journalled as one block — an
    # interleaved build log is unreadable exactly when it matters.
    for i in "${!build_pids[@]}"; do
      wait "${build_pids[$i]}" || rc=1
      note "---- build output: ${build_names[$i]}"
      cat "${build_logs[$i]}"; rm -f "${build_logs[$i]}"
    done
    remove_worktree
    [[ $rc -eq 0 ]] || die prebuild "an image build failed — NOTHING was stopped, merged or restarted; the system is untouched apart from the $CKPT git tag and the spare containerd tags"

    for row in "${changed_rows[@]}"; do
      IFS='|' read -r name ref script dep nodes paths <<<"$row"
      [[ -n "$dep" ]] && IMAGE_DEPLOYS+=("$dep")
    done
  fi

  PHASE_NOW=db-backup; phase "dumping spine + litellm + n8n databases"
  mkdir -p "$BACKUPS" && chmod 700 "$BACKUPS"
  local stamp; stamp="$(date -u +%Y%m%dT%H%M%SZ)"

  dump_pg() { # dump_pg <label> <deployment> <user> <db>
    local label="$1" dep="$2" user="$3" db="$4"
    local file="$BACKUPS/${label}_pre-update_${stamp}.sql.gz"
    if ! "${K[@]}" exec "deploy/$dep" -- pg_dump -U "$user" -d "$db" | gzip >"$file" || [[ ! -s "$file" ]]; then
      rm -f "$file"; return 1
    fi
    # A truncated dump that gunzips cleanly is the failure mode that makes
    # people think they have backups — demand pg_dump's completion marker
    # (same check as backup.sh; pg_dump 18 appends a \unrestrict trailer,
    # hence tail -5, not tail -1).
    if ! gunzip -c "$file" | tail -5 | grep -q 'PostgreSQL database dump complete'; then
      rm -f "$file"; return 1
    fi
    chown codyslab:codyslab "$file" 2>/dev/null; chmod 600 "$file"
  }

  dump_pg central_command cc-postgres   central_command central_command \
    || die db-backup "pg_dump of the spine failed — refusing to update without a backup"
  dump_pg litellm     cc-litellm-db llmproxy    litellm \
    || die db-backup "pg_dump of litellm failed — refusing to update without a backup"
  dump_pg n8n         cc-n8n-db     "${N8N_DB_USER:-n8n}" "${N8N_DB_NAME:-n8n}" \
    || die db-backup "pg_dump of n8n failed — refusing to update without a backup"

  # Without these, the litellm/n8n dumps above are undecryptable ciphertext.
  local keyfile="$BACKUPS/keys_pre-update_${stamp}.env"
  {
    echo "# Captured with the $stamp pre-update dumps."
    echo "N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY:-}"
    echo "LITELLM_SALT_KEY=${LITELLM_SALT_KEY:-}"
  } >"$keyfile"
  chown codyslab:codyslab "$keyfile" 2>/dev/null; chmod 600 "$keyfile"
  [[ -n "${N8N_ENCRYPTION_KEY:-}" && -n "${LITELLM_SALT_KEY:-}" ]] \
    || note "WARNING: a decryption key is missing from deploy/pi/.env — the litellm/n8n dumps above would not be restorable"

  PHASE_NOW=stop; phase "stopping cc-uvicorn and cc-sandbox-runner"
  systemctl stop cc-uvicorn cc-sandbox-runner || die stop "systemctl stop failed"

  PHASE_NOW=merge; phase "merging v$TARGET_VERSION"
  if ! "${RUNAS[@]}" git -C "$REPO" merge --no-edit "v$TARGET_VERSION"; then
    "${RUNAS[@]}" git -C "$REPO" merge --abort 2>/dev/null
    die merge "merge of v$TARGET_VERSION conflicts with local commits — resolve by hand (services restarted on the OLD version)"
  fi

  PHASE_NOW=schema; phase "applying schema (additive-only, idempotent)"
  "${K[@]}" exec -i deploy/cc-postgres -- psql -q -U central_command -d central_command -v ON_ERROR_STOP=1 \
    <"$REPO/central_command/db/schema.sql" || die schema "schema.sql failed — old code is compatible with a partial additive apply, services restarted"

  PHASE_NOW=rebuild; phase "rebuilding venv and cockpit"
  rebuild || die rebuild "pip/npm build failed"

  PHASE_NOW=manifests; phase "applying changed manifests and unit files"
  local changed_yaml changed_units f d
  changed_yaml="$(changed_between 'deploy/k3s/*.yaml')"
  if [[ -n "$changed_yaml" ]]; then
    # `apply -f <dir>` reads yaml/json only — the scripts, units,
    # sandbox.Dockerfile and registries.yaml.example there are ignored. Apply
    # rolls anything whose SPEC changed by itself; the rollout restarts below
    # are for the OTHER case, where only the image bytes moved under a tag.
    k3s kubectl apply -f "$REPO/deploy/k3s/" || die manifests "kubectl apply -f deploy/k3s/ failed"
    APPLIED_MANIFESTS=1
    while read -r f; do
      [[ -n "$f" ]] || continue
      while read -r d; do TOUCHED_DEPLOYS+=("$d"); done < <(deployments_in "$REPO/$f")
    done <<<"$changed_yaml"
  fi

  # `apply -f <dir>` never DELETES: a resource whose manifest left the tree
  # runs forever (v2.18.3's Adminer outlived its manifest and held hostPort
  # 8092 against pgweb). removed.txt is the explicit tombstone list —
  # idempotent deletes, so re-runs and fresh installs are quiet no-ops.
  local ns kind name
  while read -r ns kind name; do
    [[ -z "$ns" || "$ns" == \#* ]] && continue
    k3s kubectl -n "$ns" delete "$kind" "$name" --ignore-not-found \
      || die manifests "delete $kind/$name from removed.txt failed"
  done <"$REPO/deploy/k3s/removed.txt"

  # A retag in place plus imagePullPolicy: IfNotPresent means a running pod
  # keeps the OLD bytes forever — only a restart re-resolves the tag.
  for d in "${IMAGE_DEPLOYS[@]:-}"; do
    [[ -n "$d" ]] || continue
    "${K[@]}" rollout restart "deploy/$d" || die manifests "rollout restart deploy/$d failed"
  done
  for d in $(printf '%s\n' "${IMAGE_DEPLOYS[@]:-}" "${TOUCHED_DEPLOYS[@]:-}" | sort -u); do
    "${K[@]}" rollout status "deploy/$d" --timeout=300s || die manifests "deploy/$d did not become ready"
  done

  changed_units="$(changed_between 'deploy/k3s/*.service' 'deploy/k3s/*.path' 'deploy/k3s/*.timer' \
                                   'deploy/k3s/*-tmpfiles.conf' 'deploy/pi/cc-nerve.service')"
  if [[ -n "$changed_units" ]]; then
    while read -r f; do
      [[ -f "$REPO/$f" ]] || continue   # a unit DELETED by the release is not ours to install
      case "$f" in
        # Installed paths mirror the runbook (README §6), not the source names.
        *-tmpfiles.conf) install -m 644 "$REPO/$f" /etc/tmpfiles.d/cc-update.conf ;;
        *)               install -m 644 "$REPO/$f" /etc/systemd/system/ ;;
      esac || die manifests "could not install $f"
    done <<<"$changed_units"
    systemctl daemon-reload || die manifests "systemctl daemon-reload failed"
    # A changed cc-update.service takes effect on the NEXT run: restarting the
    # unit whose ExecStart is this script would kill the update mid-flight.
  fi

  PHASE_NOW=restart; phase "starting services"
  start_services || die restart "systemctl start failed"

  PHASE_NOW=health; phase "health check"
  if healthy; then
    # The declared LiteLLM policy is part of the release surface (v2.6.0):
    # a release that changes model-preferences.yaml is not live until
    # --apply pushes it into the proxy's DB. WARN-only on failure — the
    # code deploy is healthy, and rolling back the tree would not fix a
    # proxy-side refusal; the operator re-runs --apply after fixing the
    # cause (verify.sh's --check keeps it loud until then). Note the
    # standing limit: like cc-update.service, a change to THIS block takes
    # effect on the NEXT run.
    phase "litellm policy apply"
    if "${RUNAS[@]}" python3 "$REPO/deploy/pi/litellm/policy.py" --apply; then
      note "litellm policy applied"
    else
      note "WARNING: policy.py --apply failed — run it by hand after fixing the cause (verify.sh --check stays red until then)"
    fi
    note "updated $CUR_VERSION -> $TARGET_VERSION"
    write_status success done
    exit 0
  fi

  # ── rollback: the updater owns this (greenboot's shape) ────────────────────
  PHASE_NOW=rollback; phase "unhealthy after update — rolling back to $CKPT"
  systemctl stop cc-uvicorn cc-sandbox-runner || true
  "${RUNAS[@]}" git -C "$REPO" reset --hard "$CKPT" || die rollback "reset --hard $CKPT failed — MANUAL intervention needed"
  rebuild || die rollback "rebuild of the rolled-back tree failed — MANUAL intervention needed"

  # Reverse only what the forward pass actually did. Best-effort from here on:
  # a half-restored cluster still beats dying with the API down.
  local ent
  for ent in "${RETAGGED[@]:-}"; do
    [[ -n "$ent" ]] || continue
    IFS='|' read -r node ref <<<"$ent"
    ctr_tag "$node" "${ref%:*}:$CKPT" "$ref" || note "WARNING: could not restore $ref on $node"
  done
  if [[ $APPLIED_MANIFESTS -eq 1 ]]; then
    # reset --hard already put the checkpoint's manifests back on disk.
    k3s kubectl apply -f "$REPO/deploy/k3s/" || note "WARNING: re-applying the checkpoint manifests failed"
  fi
  for d in "${IMAGE_DEPLOYS[@]:-}"; do
    [[ -n "$d" ]] || continue
    "${K[@]}" rollout restart "deploy/$d" || note "WARNING: could not restart deploy/$d onto the restored image"
  done
  for d in $(printf '%s\n' "${IMAGE_DEPLOYS[@]:-}" "${TOUCHED_DEPLOYS[@]:-}" | sort -u); do
    "${K[@]}" rollout status "deploy/$d" --timeout=300s || note "WARNING: deploy/$d is not ready after rollback"
  done

  start_services || true
  if healthy; then
    write_status rolled_back "rolled back to $CUR_VERSION" "v$TARGET_VERSION failed its post-update health check; schema changes (additive-only) were kept"
    exit 1
  fi
  die rollback "still unhealthy after rollback — check journalctl -u cc-uvicorn"
}

# ── stage: prebuild the image caches while the system stays up ──────────────
# Triggered by cc-update-stage.path the moment the cockpit sees a newer
# release. It resolves the same target the apply would, runs the same version
# gates (so a blocked upgrade surfaces BEFORE anyone clicks apply), and runs
# every changed image build with CC_BUILD_ONLY=1: layer caches warm on both
# nodes, nothing imported, no ref touched, no service stopped. Apply then
# re-runs the same builds and fast-forwards through the warm cache — staging
# is a cache and a proof, never a source of truth apply must trust.
# ponytail: apply still pays save/import time for big images; a staged-tag
# import protocol could remove that if it ever matters.
apply_in_flight() { # a fresh `running` status.json means the updater owns the tree
  python3 - "$STATEDIR/status.json" <<'PY'
import json, sys
from datetime import datetime, timezone
try:
    s = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
if s.get("state") != "running":
    sys.exit(1)
ts = s.get("updated_at") or s.get("started_at") or ""
try:
    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
except ValueError:
    sys.exit(1)
age = (datetime.now(timezone.utc) - t).total_seconds()
sys.exit(0 if age < 5700 else 1)   # TimeoutStartSec 5400 + slack
PY
}

stage_main() {
  mkdir -p "$STATEDIR"
  if apply_in_flight; then
    note "an update is in flight — the stage yields (apply's prebuild covers it)"
    exit 0
  fi

  PHASE_NOW=resolve; phase "resolving stage target"
  local requested=""
  [[ -f "$STAGE_REQUEST" ]] && requested="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("target",""))' "$STAGE_REQUEST" 2>/dev/null)"

  CUR_VERSION="$(sed -n 's/^version=//p' "$REPO/VERSION" | head -1)"
  [[ -n "$CUR_VERSION" ]] || die resolve "no version= in $REPO/VERSION"
  "${RUNAS[@]}" git -C "$REPO" fetch origin --tags --quiet || die resolve "git fetch origin failed"
  TARGET_VERSION="${requested:-$("${RUNAS[@]}" git -C "$REPO" tag -l 'v[0-9]*' | sed 's/^v//' | sort -V | tail -1)}"
  TARGET_VERSION="${TARGET_VERSION#v}"
  [[ -n "$TARGET_VERSION" ]] || die resolve "no semver tags found on origin"

  if [[ "$TARGET_VERSION" == "$CUR_VERSION" ]]; then
    note "already at $CUR_VERSION — nothing to stage"
    write_status success up-to-date
    exit 0
  fi
  # Same gates as apply, so a blocked upgrade is visible before the click.
  if [[ "$(printf '%s\n%s\n' "$TARGET_VERSION" "$CUR_VERSION" | sort -V | head -1)" == "$TARGET_VERSION" ]]; then
    die resolve "refusing to stage a downgrade: installed $CUR_VERSION, target $TARGET_VERSION"
  fi
  local minfrom
  minfrom="$("${RUNAS[@]}" git -C "$REPO" show "v$TARGET_VERSION:VERSION" 2>/dev/null | sed -n 's/^min_upgrade_from=//p' | head -1)"
  if [[ -n "$minfrom" && "$(printf '%s\n%s\n' "$CUR_VERSION" "$minfrom" | sort -V | head -1)" == "$CUR_VERSION" && "$CUR_VERSION" != "$minfrom" ]]; then
    die resolve "v$TARGET_VERSION requires upgrading from >= $minfrom (installed $CUR_VERSION) — apply the intermediate release first"
  fi

  PHASE_NOW=stage-build; phase "prebuilding changed image caches (nothing imported, services stay up)"
  CKPT=HEAD   # changed_between diffs $CKPT..target; at stage time HEAD is the installed state
  local row name ref script dep nodes paths log i rc=0
  local -a changed_rows=() build_names=() build_pids=() build_logs=()
  for row in "${IMAGES[@]}"; do
    IFS='|' read -r name ref script dep nodes paths <<<"$row"
    # shellcheck disable=SC2086  # paths is a pathspec LIST, word splitting intended
    [[ -n "$(changed_between $paths)" ]] && changed_rows+=("$row")
  done

  if [[ ${#changed_rows[@]} -eq 0 ]]; then
    note "no image inputs change in v$TARGET_VERSION — nothing to build"
  else
    WT="/home/codyslab/.cc-stage-build-$(date -u +%Y%m%dT%H%M%SZ)"
    "${RUNAS[@]}" git -C "$REPO" worktree add --detach "$WT" "v$TARGET_VERSION" \
      || die stage-build "could not create the build worktree $WT"
    for row in "${changed_rows[@]}"; do
      IFS='|' read -r name ref script dep nodes paths <<<"$row"
      log="$(mktemp "/tmp/cc-stage-build-$name.XXXXXX.log")"
      "${RUNAS[@]}" env CC_COMPUTE_SSH="$COMPUTE_SSH" CC_BUILD_ONLY=1 bash "$WT/deploy/k3s/$script" >"$log" 2>&1 &
      build_pids+=("$!"); build_names+=("$name"); build_logs+=("$log")
    done
    for i in "${!build_pids[@]}"; do
      wait "${build_pids[$i]}" || rc=1
      note "---- stage build output: ${build_names[$i]}"
      cat "${build_logs[$i]}"; rm -f "${build_logs[$i]}"
    done
    remove_worktree
    [[ $rc -eq 0 ]] || die stage-build "an image build failed — v$TARGET_VERSION would fail its prebuild; nothing on the system changed"
  fi

  note "v$TARGET_VERSION staged — apply will fast-forward through the warm caches"
  write_status success staged
  exit 0
}

if [[ "${1:-}" == stage ]]; then
  STAGE_MODE=1
  STATUS="$STATEDIR/stage.json"   # write_status + the TERM trap follow this
  stage_main
else
  main "$@"
fi
