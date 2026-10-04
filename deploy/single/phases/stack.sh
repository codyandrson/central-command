# shellcheck shell=bash
# ============================================================================
# phases/stack.sh — the `stack` phase of the single-node install (deploy/single).
#
#   STACK: assert the local images, then bring the whole stack up on
#   compose.yaml's healthchecks, recreating any container whose image
#   drifted from what its ref resolves to now.
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
#   ROWS (steps.tsv, phase `stack`) — step, kind, probe; a probe marked
#   (setup.sh) is shared with another phase or the driver and lives there:
#     image-sandbox                      run    p_image_sandbox  (setup.sh)
#     image-crawler                      run    p_image_crawler  (setup.sh)
#     embed-dimension                    run    p_embed_dim  (setup.sh)
#     up-stack                           run    p_up_stack  (setup.sh)
#     restart-on-boot                    run    p_restart_on_boot
#     n8n-credential                     human  p_n8n_credential
#     n8n-workflows                      run    p_n8n_workflows
# ============================================================================

[[ -n "${CC_PHASE_STACK_LOADED:-}" ]] && return 0
CC_PHASE_STACK_LOADED=1

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: stack — assert the local images, then bring the whole stack up.
# ─────────────────────────────────────────────────────────────────────────────
# Images are the fetch phase's job; here they are only ASSERTED, so a missing
# one is a clear "run fetch" and never a surprise build (or pull) mid-deploy.
# "In local storage" means AS FETCH LEAVES IT (v2.57.0): the tag present AND
# its build-inputs label equal to this tree's. The stack rows' probes are the
# same p_image_* functions fetch's rows use, which compare the label — so a
# need_image that passed on the tag alone would PASS here and then have its
# own row FAIL on the probe after the phase. A stale image (a release changed
# its Dockerfile and fetch has not run since, or failed to rebuild) is the same
# clear "run fetch", never a build here.
need_image() { # need_image <check-name> <image-ref> <build-script>
  case "$(local_image_state "$2" "$3")" in
    current) pass "$1" "$2 present and built from this tree's build inputs"; return 0 ;;
    absent)  fail "$1" "$2 is not in local storage — run: ./setup.sh (it resumes at fetch)" ;;
    *)       fail "$1" "$2 is in local storage but was not built from this tree's build inputs (its $(cc_build_inputs_label) label differs or is missing) — run: ./setup.sh, which resumes at fetch and rebuilds it" ;;
  esac
  return 1
}

phase_stack() {
  load_env || return 1

  if [[ "$CC_ENABLE_SANDBOX" == "1" ]]; then
    need_image "image-sandbox" "localhost/cc-sandbox:1" "$HERE/build-sandbox-image.sh" || return 1
  else
    pass "image-sandbox" "skipped (CC_ENABLE_SANDBOX=0)"
  fi
  if [[ "$CC_ENABLE_CRAWLER" == "1" ]]; then
    need_image "image-crawler" "localhost/cc-crawler:1" "$HERE/build-crawler-image.sh" || return 1
  else
    pass "image-crawler" "skipped (CC_ENABLE_CRAWLER=0)"
  fi

  # The dimension is written into the Neo4j vector index and is effectively
  # permanent, so the graph may not be created before it has been MEASURED.
  [[ -n "${CC_EMBED_DIM:-}" ]] || {
    fail "embed-dimension" "CC_EMBED_DIM is empty in .env — run: ./setup.sh (it resumes at llm, which measures it through the cc-embedding alias)"
    return 1
  }

  # ONE call brings the whole stack up: compose.yaml's depends_on/healthchecks
  # carry the order that used to be a sequence of plays, and --wait blocks
  # until every service with a healthcheck is healthy. The optional components
  # are profiles, so the CC_ENABLE_* flags select them by name and nothing has
  # to be skipped by hand.
  compose_profile_flags
  step "up-stack" "the stack is up and healthy (${PROFILE_FLAGS[*]:-no optional profiles})" \
    compose "${PROFILE_FLAGS[@]}" up -d --wait || return 1
  # `up` converged the DEFINITIONS; this converges the IMAGES (v2.57.0). A
  # release that rebuilt the sandbox or the crawler under its fixed tag, or a
  # re-pulled third-party tag, leaves the old container running through `up`
  # — every enabled service whose container is not on the image its ref
  # resolves to now is recreated here, by name, and said so (image_drift).
  catch_up_images "up-stack" || return 1
  retire_graphiti_container || return 1

  # `restart: always` is honoured by podman-restart.service, which a podman
  # MACHINE (Windows/macOS) ships disabled: after a host reboot every
  # container sat Exited (2026-09-17 Windows run). Enable it where there is
  # a machine; a Linux host with a system podman has no machine and skips.
  # The machine's containers are ROOTLESS by default (the `user` account),
  # so the unit that restarts them is the USER instance — the system unit
  # restarted nothing after the 2026-09-18 reboot. Enable both; the user's
  # session lingers on a podman machine, so the user unit runs at boot.
  if [[ -n "$(podman machine list --format '{{.Name}}' 2>/dev/null)" ]]; then
    # `--global`, not `--user enable`: the machine's ~/.config/systemd is
    # root-owned, so a user enable is "Access denied"; --global writes
    # /etc/systemd/user and covers every user instance.
    if podman machine ssh -- 'sudo systemctl --global enable podman-restart.service && XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user start podman-restart.service; sudo systemctl enable --now podman-restart.service' </dev/null >/dev/null 2>&1; then
      pass "restart-on-boot" "podman-restart.service enabled in the podman machine, user and system instances (containers return after a host reboot)"
    else
      warn "restart-on-boot" "could not enable podman-restart.service in the podman machine — run: podman machine ssh -- sudo systemctl enable --now podman-restart.service"
    fi
  else
    pass "restart-on-boot" "no podman machine (system podman) — restart policies apply natively"
  fi

  # stack/n8n-credential, the operator's move (D1/D2: a `human` row whose
  # probe is false is a GATE) — after the stack, because n8n's UI is only
  # there once the stack is up. A gate (or a FAIL) ends the phase here: the
  # workflows resolve their Gmail nodes by that credential's NAME at import,
  # so importing before it exists only leaves them unresolved.
  stack_n8n_credential || return $?
  # stack/n8n-workflows — the façade workflows, through the very script
  # update.sh used to run as a step of its own after `app`. A fresh install
  # therefore had none until its first update; `stack` is where they land now,
  # for an install and an update alike.
  stack_n8n_workflows
}

# ── the retired Graphiti server's container (design record 2026-10-04, D10) ──
# compose.yaml no longer declares a `graphiti` service — graphiti-core runs
# inside the API — but `compose up` never removes a container whose service
# left the file, and the old one is `restart: always`: it would keep its port
# and come back after every reboot. So an install that predates the removal
# has it removed here, by name, once; a fresh install finds none. Not
# --remove-orphans: whether podman-compose counts a disabled PROFILE's
# containers as orphans is not something this profile may assume. The image
# is left in local storage — `./update.sh rollback` to the previous release
# rebuilds nothing it already has.
retired_graphiti_name() { printf '%sgraphiti' "${CC_POD_PREFIX:-cc-}"; }
retire_graphiti_container() {
  local name; name="$(retired_graphiti_name)"
  podman container exists "$name" 2>/dev/null || return 0
  if podman rm -f "$name" >/dev/null 2>&1; then
    pass "up-stack" "removed the retired Graphiti server container $name — the graph client runs inside the API now"
  else
    fail "up-stack" "could not remove the retired Graphiti server container $name (podman rm -f $name) — it keeps its port and restarts at boot"
    return 1
  fi
}

# ── the n8n credentials the import needs (2026-10-01 design record, D1) ─────
# The façade workflows name their credentials — "Gmail account" in the email
# pair, "Google Calendar account" in the calendar pair — and n8n's import
# resolves a credential BY NAME (deploy/n8n/apply-workflows.sh, step 3).
# Creating one is an OAuth sign-in in the n8n UI that nothing can script, so
# the driver only ASKS whether each exists — one read-only SELECT on n8n's own
# database, the table the import resolves names against (credentials_entity),
# through the n8n-db container the stack just brought up. Not
# apply-workflows.sh's own question: that one reads the IMPORTED workflows'
# unresolved references, so it has no answer before an import and keeps saying
# "missing" until the next one, and the script around it imports, activates
# and restarts n8n — it stays as the backstop (stack/n8n-workflows turns its
# USERACTION into that row's). A credential row exists from its first save;
# whether its Google sign-in completed is in its encrypted data, which nothing
# here reads.
#
# WHICH names is not a typed list: it is every credential the workflows the
# script is about to import reference (v2.58.0 — until then the row asked for
# Gmail alone, and an install whose CC_CALENDAR_FACADE_TOKEN make-secrets.sh
# had generated stopped one row LATER, at the import, on a calendar credential
# the checklist never mentioned). The functions are below the workflows row's:
# n8n_shipped_workflow_files, n8n_required_credentials, stack_n8n_credential.

# ONE read-only `select count(*) …` against n8n's own database, through the
# n8n-db container — the store both n8n rows' probes ask (the credential, the
# workflows). Prints the count; 1 = the database did not answer, or answered
# something that is not a count. Callers pass a SELECT and nothing else: the
# probe walker (tests/test_single_steps_schema.py) reads this body, not the SQL.
n8n_db_count() { # n8n_db_count <select count(*) …>
  local n
  n="$(podman exec "$(p_flag CC_POD_PREFIX cc-)n8n-db" \
        psql -U "$(p_flag N8N_DB_USER n8n)" -d "$(p_flag N8N_DB_NAME n8n)" -tA \
        -c "$1" </dev/null 2>/dev/null | tr -d ' \r')" || return 1
  [[ "$n" =~ ^[0-9]+$ ]] || return 1
  printf '%s' "$n"
}

# ── the n8n façade workflows (stack/n8n-workflows) ──────────────────────────
# deploy/n8n/apply-workflows.sh is the ONE way the shipped workflows reach a
# running n8n (the k3s driver runs the same script with --k3s). What it does
# that shapes this row, read off the script:
#   * IDEMPOTENT — `n8n import:workflow` upserts by the workflow ids in the
#     files (stable on purpose), so a re-run converges; a hand edit on the
#     canvas is overwritten, which is the point (the files are the source);
#   * it ACTIVATES by SQL (the CLI cannot outside queue mode) and RESTARTS n8n
#     on every run (webhooks are registered at boot), then polls the email
#     façade's webhook until it answers — so the row costs an n8n restart each
#     time the stack phase runs, which the ledger limits to a phase that is not
#     already done;
#   * the calendar pair is applied only when CC_CALENDAR_FACADE_TOKEN is set;
#   * a credential its import could not resolve BY NAME is not a non-zero exit:
#     it prints `USERACTION n8n: …` on stderr and carries on (activation,
#     restart, exit 0). A missing credential is the operator's move, so it
#     reaches the protocol as THIS row's USERACTION (exit 3) — never a PASS
#     over a line nobody counted (what update.sh's `step` made of it), never a
#     FAIL;
#   * it prints only narration, all on stderr; a non-zero exit is a FAIL
#     (`die`'s last `apply-workflows: …` line says why).
# Its output also goes to <state>/n8n-workflows.log, which is what the
# USERACTION is read back from — the state dir, never the checkout (D7).
#
# 0 = applied, or not applicable; 1 = a FAIL; 3 = its USERACTION.
stack_n8n_workflows() {
  local log="$STATE_DIR/n8n-workflows.log" rc why ua
  if [[ "${CC_ENABLE_N8N:-0}" != "1" ]]; then
    pass "n8n-workflows" "CC_ENABLE_N8N is not 1 — no n8n, so no façade workflows to apply"
    return 0
  fi
  note "--> bash deploy/n8n/apply-workflows.sh --podman   (its output is kept in $log)"
  # tee, not a $(…): the import, the restart and the webhook poll take minutes,
  # and the operator watches them happen. PIPESTATUS is the script's own exit.
  bash "$REPO_ROOT/deploy/n8n/apply-workflows.sh" --podman </dev/null 2>&1 | tee "$log" >&2
  rc="${PIPESTATUS[0]}"
  if (( rc != 0 )); then
    why="$(grep '^apply-workflows: ' "$log" 2>/dev/null | tail -1 | tr -d '\r')"
    fail "n8n-workflows" "deploy/n8n/apply-workflows.sh failed (exit $rc)${why:+: ${why#apply-workflows: }} — its whole output is in $log"
    return 1
  fi
  ua="$(sed -n 's/^USERACTION n8n: //p' "$log" 2>/dev/null | tr -d '\r' | tail -1)"
  if [[ -n "$ua" ]]; then
    useraction "n8n-workflows" "$ua"
    return 3
  fi
  pass "n8n-workflows" "the façade workflows are imported, active, and the email façade's webhook answers (deploy/n8n/apply-workflows.sh)"
  return 0
}

# The optional pair apply-workflows.sh ships only when CC_CALENDAR_FACADE_TOKEN
# is set — the same two file names as the `case` in that script, which
# tests/test_single_n8n_workflows_row.py holds equal to this list.
N8N_CALENDAR_WORKFLOWS="cc-calendar-facade.json lib-google-calendar.json"

# The workflow files apply-workflows.sh is about to import, under its own rule
# (the calendar pair only with its token), one path per line — paths, because
# a Windows checkout lives under a folder with a space. Both n8n rows derive
# from this: the credentials the import needs, and the ids it must leave.
n8n_shipped_workflow_files() {
  local f base cal
  cal="$(p_flag CC_CALENDAR_FACADE_TOKEN '')"
  for f in "$REPO_ROOT/deploy/n8n/workflows/"*.json; do
    [[ -f "$f" ]] || continue
    base="${f##*/}"
    [[ -z "$cal" && " $N8N_CALENDAR_WORKFLOWS " == *" $base "* ]] && continue
    printf '%s\n' "$f"
  done
}

# The ids of the workflows THIS release ships and THIS .env selects, derived
# from deploy/n8n/workflows/*.json — never a second typed list — by the very
# pattern apply-workflows.sh reads them with (the top-level `"id"`, two-space
# indent). IDS, not names: the import upserts by id and the ids are stable on
# purpose (deploy/n8n/README.md), while a name is free text on the canvas and
# n8n does not keep it unique. CR-stripped, so a CRLF checkout reads the same.
# Prints them space-separated; 1 = a file with no id, or no files at all.
n8n_shipped_workflow_ids() {
  local f id ids=""
  while IFS= read -r f; do
    id="$(tr -d '\r' <"$f" | sed -n 's/^  "id": "\([A-Za-z0-9]*\)",$/\1/p')"
    id="${id%%$'\n'*}"
    [[ -n "$id" ]] || return 1
    ids="${ids:+$ids }$id"
  done < <(n8n_shipped_workflow_files)
  [[ -n "$ids" ]] || return 1
  printf '%s' "$ids"
}

# The credentials those files reference, one `<name>\t<type>` line per NAME,
# sorted. A reference is a node's `"credentials": { "<type>": { "id": null,
# "name": "<name>" } }` — tests/test_n8n_workflows.py refuses any other shape
# (an id would be one instance's), and the script's own unresolved-credential
# query asks exactly that `id is null` question. So: the `"name"` line right
# after an `"id": null,` line, and the `"<type>": {` line before it.
# tests/test_single_n8n_workflows_row.py holds this equal to a JSON parse of
# the same files. 1 = a name a SQL literal cannot carry (a quote), which no
# shipped file has — refused rather than quoted.
n8n_required_credentials() {
  local f out
  out="$(
    while IFS= read -r f; do
      tr -d '\r' <"$f" | awk '
        /^[[:space:]]*"[A-Za-z0-9]+": \{[[:space:]]*$/ { match($0, /"[A-Za-z0-9]+"/); type = substr($0, RSTART + 1, RLENGTH - 2); next }
        /"id": null,[[:space:]]*$/                       { want = 1; next }
        want && /"name": "/ { match($0, /"name": "[^"]*"/); print substr($0, RSTART + 9, RLENGTH - 10) "\t" type }
        { want = 0 }'
    done < <(n8n_shipped_workflow_files) | sort -u -t $'\t' -k1,1
  )"
  [[ "$out" == *"'"* ]] && return 1
  [[ -n "$out" ]] && printf '%s\n' "$out"
  return 0
}

# `'a','b'` from the names on stdin, one per line — for an `in (…)` list.
n8n_sql_names() {
  local n list=""
  while IFS= read -r n; do
    [[ -n "$n" ]] && list="${list:+$list,}'$n'"
  done
  printf '%s' "$list"
}

# 0 = done or not applicable; 1 = a FAIL (n8n's database did not answer);
# 3 = the USERACTION naming exactly the MISSING credential(s) and where (the
# demo gate's shape).
stack_n8n_credential() {
  local req names n line name type missing="" ui
  if [[ "${CC_ENABLE_N8N:-0}" != "1" ]]; then
    pass "n8n-credential" "CC_ENABLE_N8N is not 1 — no n8n, so no credential to create"
    return 0
  fi
  if ! req="$(n8n_required_credentials)"; then
    fail "n8n-credential" "a credential name in deploy/n8n/workflows/ carries a quote, which this check cannot ask n8n about — a repository defect: ./setup.sh report"
    return 1
  fi
  if [[ -z "$req" ]]; then
    pass "n8n-credential" "the façade workflows this install imports reference no credential"
    return 0
  fi
  names="$(cut -f1 <<<"$req")"
  ui="http://127.0.0.1:${CC_N8N_PORT:-5678}/"
  # Which ones are missing — per name, so the USERACTION names exactly those.
  while IFS=$'\t' read -r name type; do
    if ! n="$(n8n_db_count "select count(*) from credentials_entity where name = '$name'")"; then
      fail "n8n-credential" "could not ask n8n's database (container $(p_flag CC_POD_PREFIX cc-)n8n-db) whether the \"$name\" credential exists — the stack reported it up, so read ./setup.sh status and that container's log"
      return 1
    fi
    (( n > 0 )) || missing="${missing:+$missing, }\"$name\" (n8n credential type $type)"
  done <<<"$req"
  if [[ -z "$missing" ]]; then
    pass "n8n-credential" "n8n holds every credential the façade workflows resolve by name: $(paste -sd, <<<"$names" | sed 's/,/, /g')"
    return 0
  fi
  useraction "n8n-credential" "YOUR MOVE: create the n8n credential(s) named exactly ${missing} in the n8n UI at ${ui} (Credentials -> Add credential) and complete each Google sign-in — the façade workflows this install imports find them by those names. Then re-run ./setup.sh"
  return 3
}

p_restart_on_boot() {
  local out
  [[ -n "$(podman machine list --format '{{.Name}}' 2>/dev/null)" ]] || return 0
  out="$(podman machine ssh -- 'systemctl --global is-enabled podman-restart.service 2>/dev/null || true' </dev/null 2>/dev/null | tr -d ' \r')"
  # An answer this cannot read is not evidence of absence: the phase WARNs when
  # the enable fails, and a probe inventing a FAIL out of silence would stop an
  # install over a unit query.
  [[ -z "$out" ]] && return 0
  [[ "$out" == enabled* ]]
}

# stack/n8n-credential (D1, a `human` row): off is done; on, n8n's database
# holds every credential the workflows about to be imported reference by name
# (n8n_required_credentials) — ONE read-only SELECT counting the distinct
# required names present.
p_n8n_credential() {
  p_off CC_ENABLE_N8N 0 1 && return 0
  local req n want
  req="$(n8n_required_credentials)" || return 1
  [[ -n "$req" ]] || return 0
  want="$(grep -c . <<<"$req")"
  n="$(n8n_db_count "select count(distinct name) from credentials_entity where name in ($(cut -f1 <<<"$req" | n8n_sql_names))")" || return 1
  (( n == want ))
}

# stack/n8n-workflows: off is done; on, EVERY workflow this release ships (and
# this .env selects — the calendar pair needs its token) is in n8n's
# workflow_entity AND active, which is what the script's activation writes.
# One read-only SELECT, the same store the credential probe asks. It does not
# read whether a workflow's CONTENT is this release's: that is the row's
# `@deploy/n8n` tree input, which re-runs the phase when the files change.
p_n8n_workflows() {
  p_off CC_ENABLE_N8N 0 1 && return 0
  local ids in_list n
  local -a want
  ids="$(n8n_shipped_workflow_ids)" || return 1
  read -ra want <<<"$ids"
  # The ids are [A-Za-z0-9] by the pattern that read them, so quoting them
  # into the literal list is safe (the script builds its list the same way).
  in_list="$(printf "'%s'," "${want[@]}")"; in_list="${in_list%,}"
  n="$(n8n_db_count "select count(*) from workflow_entity where active and id in ($in_list)")" || return 1
  (( n == ${#want[@]} ))
}
