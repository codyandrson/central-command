#!/usr/bin/env bash
# ============================================================================
# update.sh — air-gapped-friendly updates for a zip-installed deployment.
#
#   The transport in is a single file: the source zip downloaded from the
#   public repo (github.com/codyandrson/central-command → Code → Download
#   ZIP). The deployment tree IS the repo checkout (setup.sh installs
#   editable into .venv at the repo root), so updates are git-native and in
#   place — a locally-initialized repo carries two branches:
#
#     upstream   pristine imports, one commit per downloaded zip
#     local      the deployment: upstream + your local modifications
#
#   Three-way merge is what preserves local changes across updates, and it
#   only sees changes that are COMMITTED — hand-edits left uncommitted on
#   `local` are invisible to it (plan/apply refuse until they are committed).
#
#   Subcommands (each idempotent — resume is re-run, setup.sh's shape):
#
#     init            one-time: turn the unzipped tree into the git repo above
#     import <zip>    commit a newly downloaded source zip onto `upstream`
#     plan            what an apply would do: diff summary, migration/deps/
#                     cockpit flags, predicted merge conflicts. Mutates nothing.
#     apply           merge upstream into local, then deploy the result:
#                     schema -> ./setup.sh app -> ./setup.sh verify
#     rollback        reset `local` to the last pre-update tag and re-deploy
#                     the restored tree through the same idempotent steps
#
#   Output protocol matches setup.sh: `PASS|WARN|FAIL <check>: <msg>` on
#   stdout, everything else on stderr, exit 0 clean / 1 hard failure /
#   2 completed with warnings.
#
#   What rollback does NOT undo: schema statements already applied to the
#   live Postgres. That is safe BY DESIGN — the schema discipline is
#   additive-only (`create table/add column if not exists`), so previous
#   code runs happily against a newer schema. Stated here so nobody assumes
#   a full state rollback.
# ============================================================================

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
ENV_FILE="$HERE/.env"

# ── output protocol (setup.sh's) ────────────────────────────────────────────
FAILS=0; WARNS=0
pass() { printf 'PASS %s: %s\n' "$1" "$2"; }
warn() { printf 'WARN %s: %s\n' "$1" "$2"; WARNS=$((WARNS+1)); }
fail() { printf 'FAIL %s: %s\n' "$1" "$2"; FAILS=$((FAILS+1)); }
note() { printf '%s\n' "$*" >&2; }

step() { # step <check-name> <success-message> <cmd...>
  local name="$1" msg="$2"; shift 2
  note "--> $*"
  if "$@" >&2; then pass "$name" "$msg"; return 0; fi
  fail "$name" "failed — see stderr for the command's own output"
  return 1
}

G() { git -C "$REPO_ROOT" "$@"; }

get_kv() { # get_kv <file> <key>   (dotenv read without sourcing)
  local f="$1" k="$2" line out=""
  [[ -f "$f" ]] || { printf ''; return 0; }
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" == "$k="* ]] && out="${line#*=}"
  done <"$f"
  printf '%s' "$out"
}

# ── shared guards ───────────────────────────────────────────────────────────
need_git() {
  command -v git >/dev/null 2>&1 && return 0
  fail "git" "git not found — it is a setup prerequisite (./setup.sh preflight)"
  return 1
}

# Commits need an identity; an air-gapped box often has none configured.
# Repo-local, set once, never overrides an operator's own configuration.
ensure_identity() {
  G config user.name  >/dev/null 2>&1 || G config user.name  "Central Command"
  G config user.email >/dev/null 2>&1 || G config user.email "update@localhost"
}

initialized() { G rev-parse --verify -q upstream >/dev/null 2>&1; }

need_initialized() {
  initialized && return 0
  fail "init" "no \`upstream\` branch — run ./update.sh init once first"
  return 1
}

# Uncommitted changes to TRACKED files are invisible to a three-way merge and
# would be clobbered by reset — both plan and apply care.
dirty_tracked() { [[ -n "$(G status --porcelain --untracked-files=no)" ]]; }

on_local_branch() { [[ "$(G rev-parse --abbrev-ref HEAD 2>/dev/null)" == "local" ]]; }

merged_already() { G merge-base --is-ancestor upstream local 2>/dev/null; }

# ── init ────────────────────────────────────────────────────────────────────
cmd_init() {
  need_git || return 1
  if [[ -d "$REPO_ROOT/.git" ]]; then
    ensure_identity
    if ! G rev-parse --verify -q HEAD >/dev/null 2>&1; then
      # A half-finished init: repo exists but nothing committed yet.
      G checkout -qb local 2>/dev/null || G checkout -q local
      step "baseline" "deployed tree committed as the baseline" \
        bash -c 'git -C "$1" add -A && git -C "$1" commit -qm "baseline: deployed tree at init"' _ "$REPO_ROOT" || return 1
    fi
    G rev-parse --verify -q local    >/dev/null 2>&1 || G branch local
    G rev-parse --verify -q upstream >/dev/null 2>&1 || G branch upstream
    if on_local_branch; then
      pass "init" "repo ready — branches \`upstream\` and \`local\` present"
    else
      warn "init" "branches present, but HEAD is on \`$(G rev-parse --abbrev-ref HEAD)\` — apply expects the deployment to live on \`local\`"
    fi
    return 0
  fi
  step "git-init" "repository initialized at $REPO_ROOT" G init -q || return 1
  ensure_identity
  G checkout -qb local
  step "baseline" "deployed tree committed as the baseline (respects .gitignore — .env/.venv/secrets stay out)" \
    bash -c 'git -C "$1" add -A && git -C "$1" commit -qm "baseline: deployed tree at init"' _ "$REPO_ROOT" || return 1
  G branch upstream
  pass "init" "branches \`upstream\` (imports) and \`local\` (deployment) created — commit any local file tweaks to \`local\` as you make them"
}

# ── import <zip> ────────────────────────────────────────────────────────────
cmd_import() {
  local zip="${1:-}"
  [[ -n "$zip" && -f "$zip" ]] || { fail "zip" "usage: ./update.sh import <downloaded-source-zip>"; return 1; }
  need_git || return 1
  need_initialized || return 1
  command -v unzip >/dev/null 2>&1 || { fail "unzip" "unzip not found — install it, or unpack the archive yourself and re-zip is not needed: any tool producing the same tree works, but this script drives unzip"; return 1; }

  local tmp; tmp="$(mktemp -d)" || { fail "tmp" "mktemp failed"; return 1; }
  # The worktree lives under $tmp; we never cd into it from this shell, so the
  # remove below cannot hit the remove-while-cwd-inside trap.
  trap 'git -C "$REPO_ROOT" worktree remove --force "$tmp/wt" >/dev/null 2>&1; git -C "$REPO_ROOT" worktree prune >/dev/null 2>&1; rm -rf "$tmp"' RETURN

  step "unzip" "archive unpacked" unzip -q "$zip" -d "$tmp/x" || return 1

  # GitHub zips wrap everything in a single `<repo>-<ref>/` directory.
  local src="$tmp/x" entries=()
  while IFS= read -r -d '' e; do entries+=("$e"); done < <(find "$tmp/x" -mindepth 1 -maxdepth 1 -print0)
  [[ ${#entries[@]} -eq 1 && -d "${entries[0]}" ]] && src="${entries[0]}"
  [[ -f "$src/central_command/db/schema.sql" ]] || { fail "zip-shape" "this does not look like a Central Command source zip (no central_command/db/schema.sql)"; return 1; }

  ensure_identity
  G worktree prune >/dev/null 2>&1   # a crashed prior import leaves a stale registration behind
  step "worktree" "pristine \`upstream\` checked out aside" G worktree add -q "$tmp/wt" upstream || return 1
  # Wipe tracked files FIRST so files the new release deleted actually go
  # away — unpacking on top would silently keep them alive forever.
  git -C "$tmp/wt" rm -rfq -- . >/dev/null 2>&1 || true
  step "unpack" "new tree staged over \`upstream\`" \
    bash -c '(cd "$1" && tar cf - .) | (cd "$2" && tar xf -)' _ "$src" "$tmp/wt" || return 1
  git -C "$tmp/wt" add -A
  if [[ -z "$(git -C "$tmp/wt" status --porcelain)" ]]; then
    pass "import" "no changes — \`upstream\` already matches $(basename "$zip")"
    return 0
  fi
  step "commit" "imported as one commit on \`upstream\`" \
    git -C "$tmp/wt" commit -qm "import $(basename "$zip")" || return 1
  note ""
  note "== what this import brings (vs the deployed \`local\`):"
  G diff --stat local...upstream >&2 || true
  pass "import" "$(basename "$zip") committed to \`upstream\` — next: ./update.sh plan"
}

# ── plan ────────────────────────────────────────────────────────────────────
cmd_plan() {
  need_git || return 1
  need_initialized || return 1

  if dirty_tracked; then
    warn "dirty" "uncommitted changes to tracked files — commit them to \`local\` before apply, or the merge cannot see (and preserve) them"
    G status --porcelain --untracked-files=no >&2
  fi
  if merged_already; then
    pass "plan" "\`local\` already contains everything on \`upstream\` — nothing to apply"
    return 0
  fi

  local names; names="$(G diff --name-only local...upstream)"
  note ""
  note "== diff summary (what upstream brings):"
  G diff --stat local...upstream >&2
  pass "diff" "$(printf '%s\n' "$names" | grep -c .) file(s) differ — full detail: git diff local...upstream"

  grep -q '^central_command/db/schema.sql$' <<<"$names" \
    && pass "flag-schema" "schema.sql changed — apply will re-run it against the live spine (additive-only, idempotent)" \
    || pass "flag-schema" "no schema change"
  grep -q '^requirements.lock$' <<<"$names" \
    && pass "flag-deps" "requirements.lock changed — apply will re-install python deps from the lock" \
    || pass "flag-deps" "no python dependency change"
  grep -q '^web/' <<<"$names" \
    && pass "flag-cockpit" "web/ changed — apply will rebuild the cockpit (needs node >= 22, else skipped with a WARN)" \
    || pass "flag-cockpit" "no cockpit change"

  # Predict conflicts without touching the working tree (git >= 2.38).
  if G merge-tree --write-tree local upstream >/dev/null 2>&1; then
    pass "conflicts" "merge is clean — no conflicts predicted"
  else
    local rc=$?
    if (( rc == 1 )); then
      warn "conflicts" "merge WILL conflict — apply will stop for you to resolve; files:"
      G merge-tree --write-tree --name-only local upstream 2>/dev/null | sed -n '2,$p' >&2
    else
      warn "conflicts" "this git cannot predict conflicts (needs >= 2.38) — apply will surface them the normal way"
    fi
  fi
  note ""
  note "next: ./update.sh apply"
}

# ── the deploy steps (shared by apply and rollback; all idempotent) ─────────
apply_schema() {
  local pfx ctr
  pfx="$(get_kv "$ENV_FILE" CC_POD_PREFIX)"; pfx="${pfx:-cc-}"
  ctr="${pfx}postgres-postgres"
  if ! podman container exists "$ctr" 2>/dev/null; then
    fail "schema" "spine postgres container '$ctr' not found — is the stack up? (./setup.sh stack)"
    return 1
  fi
  # The whole file is re-executable by design: `if not exists` everywhere,
  # seeds guarded by `on conflict do nothing`. ON_ERROR_STOP makes any
  # violation of that discipline a loud FAIL instead of a half-applied file.
  step "schema" "schema.sql applied to the live spine (idempotent)" \
    bash -c 'podman exec -i "$1" psql -q -U central_command -d central_command -v ON_ERROR_STOP=1 <"$2"' \
      _ "$ctr" "$REPO_ROOT/central_command/db/schema.sql"
}

deploy_current_tree() {
  # Ordering is load-bearing: schema BEFORE the code goes live. Additive-only
  # schema means old code tolerates the new columns, so a failed migration
  # leaves the old code running unharmed and the update simply stops here.
  apply_schema || return 1
  step "app" "venv/deps/cockpit reconciled (./setup.sh app)" "$HERE/setup.sh" app || return 1
  step "verify" "deployed + live verification passed (./setup.sh verify)" "$HERE/setup.sh" verify || return 1
  warn "restart-required" "a merged change is not live until its process restarts — restart your uvicorn API (and the sandbox runner, if you run one), then re-check with: ./setup.sh status"
}

# ── apply ───────────────────────────────────────────────────────────────────
cmd_apply() {
  need_git || return 1
  need_initialized || return 1
  on_local_branch || { fail "branch" "HEAD is on \`$(G rev-parse --abbrev-ref HEAD)\` — apply runs on the deployment branch: git checkout local"; return 1; }
  if [[ -e "$REPO_ROOT/.git/MERGE_HEAD" ]]; then
    fail "merge-in-progress" "resolve the in-progress merge first: fix conflicts, \`git add\` them, \`git commit\` — then re-run ./update.sh apply (or back out with: git merge --abort)"
    return 1
  fi
  if dirty_tracked; then
    fail "dirty" "uncommitted changes to tracked files — commit them to \`local\` first (they are invisible to the merge and would be lost):"
    G status --porcelain --untracked-files=no >&2
    return 1
  fi

  ensure_identity
  if merged_already; then
    pass "merge" "\`local\` already contains \`upstream\` — continuing with the deploy steps"
  else
    local tag; tag="pre-update-$(date -u +%Y%m%dT%H%M%SZ)"
    step "checkpoint" "rollback point tagged: $tag" G tag "$tag" || return 1
    note "--> git merge --no-edit upstream"
    if ! G merge --no-edit upstream >&2; then
      fail "merge" "conflicts between your local changes and the update — resolve them (git status), \`git add\` each, \`git commit\`, then RE-RUN ./update.sh apply; every later step is idempotent and picks up where this stopped. To back out instead: git merge --abort"
      return 1
    fi
    pass "merge" "upstream merged into local (your committed changes preserved by three-way merge)"
  fi

  deploy_current_tree
}

# ── rollback ────────────────────────────────────────────────────────────────
cmd_rollback() {
  need_git || return 1
  need_initialized || return 1
  local tag; tag="$(G tag -l 'pre-update-*' | sort | tail -1)"
  [[ -n "$tag" ]] || { fail "tag" "no pre-update-* tag found — nothing recorded to roll back to"; return 1; }

  [[ -e "$REPO_ROOT/.git/MERGE_HEAD" ]] && G merge --abort >/dev/null 2>&1
  if dirty_tracked && [[ "${CC_UPDATE_FORCE:-0}" != "1" ]]; then
    fail "dirty" "uncommitted changes to tracked files would be DESTROYED by rollback — commit/stash them, or re-run with CC_UPDATE_FORCE=1 to discard them"
    return 1
  fi

  step "reset" "\`local\` reset to $tag (working tree restored)" G reset -q --hard "$tag" || return 1
  note "NOTE: schema changes already applied to Postgres are NOT undone — additive-only schema makes the restored code run fine against them."
  deploy_current_tree
}

# ─────────────────────────────────────────────────────────────────────────────
usage() {
  cat >&2 <<USAGE
usage: ./update.sh <init | import <zip> | plan | apply | rollback>

  init            one-time: turn this unzipped tree into a git repo
                  (branch \`upstream\` for imports, \`local\` for the deployment)
  import <zip>    commit a newly downloaded source zip onto \`upstream\`
  plan            dry-run report: diff, migration/deps/cockpit flags,
                  predicted conflicts. Mutates nothing; re-runnable.
  apply           merge + deploy: schema -> ./setup.sh app -> ./setup.sh verify
  rollback        reset \`local\` to the last pre-update tag and re-deploy

  exit codes      0 clean · 1 hard failure · 2 completed with warnings
                  (apply always ends 2: the restart is yours to perform)
USAGE
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
    init)     cmd_init ;;
    import)   cmd_import "${2:-}" ;;
    plan)     cmd_plan ;;
    apply)    cmd_apply ;;
    rollback) cmd_rollback ;;
    -h|--help|help|"") usage; [[ -n "$cmd" ]] && exit 0 || exit 1 ;;
    *) usage; exit 1 ;;
  esac
  (( FAILS )) && exit 1
  (( WARNS )) && exit 2
  exit 0
}

main "$@"
