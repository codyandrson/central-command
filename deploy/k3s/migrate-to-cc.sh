#!/usr/bin/env bash
# migrate-to-cc.sh — carry a v1.0.x deployment across the v2.0.0 rename.
#
#   v2.0.0 renames the Python package, the CC_* env prefix, every pod/unit/
#   image/namespace, the databases and role, the Graphiti group ids, the
#   LiteLLM default alias and the n8n webhook paths. Kubernetes cannot rename
#   a namespace and the cockpit updater cannot rename its own units mid-run,
#   so this release is applied BY HAND, once, with downtime:
#
#     cd /home/codyslab/central-command
#     git fetch origin && git merge --ff-only origin/master     # the v2.0.0 tree
#     sudo DUMP_ONLY=1 ./deploy/k3s/migrate-to-cc.sh            # rehearsal: backups + plan
#     sudo ./deploy/k3s/migrate-to-cc.sh                        # the migration
#     ./deploy/k3s/verify.sh                                    # 32 assertions, new names
#     sudo ./deploy/k3s/migrate-to-cc.sh cleanup                # AFTER verify: delete the old
#                                                               #   namespaces, PV data, units
#     sudo ./deploy/k3s/migrate-to-cc.sh rollback               # reverse, from the journal
#
#   Order of the forward pass (each step journals itself and is skipped on a
#   re-run, so a failed run is resumed by running the script again):
#     backup      pg_dump the three Postgres stores + the decryption keys, with
#                 the OLD pod names, into ~/cc-backups (~/gv-backups renamed).
#     stop        the old gv-* units (the old namespace keeps running: the data
#                 rewrites below need its pods).
#     postgres    rewrite every text/jsonb column of the spine that carries the
#                 old name (the append-only log included — rewritten in place,
#                 once, by operator decision), then ALTER DATABASE / ROLE RENAME.
#     neo4j       rewrite group_id on every node AND relationship.
#     litellm     rename the DB-stored alias and re-scope the virtual keys.
#     n8n         rename the three façade webhook paths inside the workflows.
#     env         rewrite the three gitignored .env files to the new names
#                 (backups first), rename ~/.gv-private-identifiers.
#     k8s         scale the old namespace to 0, delete its LoadBalancer
#                 Services (they hold the host ports), retag the three local
#                 images on both nodes, relabel the nodes, create the new
#                 namespaces from the manifests, copy each PV's data into the
#                 new claim's directory on its node, start the new pods.
#     host        install the cc-* units, reinstall the package, rebuild the
#                 cockpit, start everything.
#   Nothing is deleted by the forward pass: the old namespace (scaled to 0),
#   its PV directories, and the old unit files all survive until `cleanup`.
#
#   Rollback restores the step-1 dumps into the OLD pods (the data rewrites
#   happened in place there), reverses the Neo4j/LiteLLM/n8n edits exactly,
#   restores the .env backups, re-applies the old LoadBalancer Services from
#   the v1.0.7 tag and re-enables the old units. The repo itself is the
#   operator's to reset (`git reset --hard v1.0.7`).
set -euo pipefail

REPO="${CC_REPO:-/home/codyslab/central-command}"   # override for a rehearsal from a worktree
OWNER=codyslab
HOME_DIR=/home/$OWNER
RUNAS=(runuser -u "$OWNER" --)
OLD_NS=grandvision; NEW_NS=central-command
OLD_DB=grandvision; NEW_DB=central_command
COMPUTE_SSH="${CC_COMPUTE_SSH:-chromebox_admin@100.113.118.28}"
SSHQ=("${RUNAS[@]}" ssh -o BatchMode=yes -o ConnectTimeout=10)   # as the operator: root has no key/known_hosts for the compute node
K=(k3s kubectl)
STATE=/var/lib/cc-migrate
JOURNAL=$STATE/journal
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUPS=$HOME_DIR/cc-backups
DUMP_ONLY="${DUMP_ONLY:-0}"
OLD_TAG=v1.0.7

say()  { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
die()  { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
done_step() { grep -qx "done $1" "$JOURNAL" 2>/dev/null; }
mark()      { echo "done $1" >>"$JOURNAL"; }
record()    { echo "$1=$2" >>"$JOURNAL"; }         # rollback facts
recorded()  { sed -n "s/^$1=//p" "$JOURNAL" | tail -1; }

# --- store access, OLD names (the old pods carry the data until k8s step) ---
psql_old()   { "${K[@]}" -n $OLD_NS exec -i "deploy/$1" -- psql -v ON_ERROR_STOP=1 -U "$2" -d "$3" "${@:4}"; }
cypher_old() { "${K[@]}" -n $OLD_NS exec -i deploy/gv-neo4j -- cypher-shell -u neo4j -p "$NEO4J_PASSWORD" --format plain "$@"; }

load_env() {
  # deploy/pi/.env holds the store credentials (never printed). Read it
  # whichever prefix it carries — the env step rewrites it mid-run.
  [[ -f $REPO/deploy/pi/.env ]] || die "missing $REPO/deploy/pi/.env"
  local k; for k in NEO4J_PASSWORD N8N_ENCRYPTION_KEY LITELLM_SALT_KEY N8N_DB_USER N8N_DB_NAME; do
    printf -v "$k" '%s' "$(sed -n "s/^$k=//p" "$REPO/deploy/pi/.env" | tail -1 | sed "s/^['\"]//; s/['\"]\$//")"
  done
  : "${NEO4J_PASSWORD:?NEO4J_PASSWORD missing from deploy/pi/.env}"
  N8N_DB_USER="${N8N_DB_USER:-n8n}"; N8N_DB_NAME="${N8N_DB_NAME:-n8n}"
}

# Same rewrite the code rename used, applied to store contents and .env files.
# ORDER MATTERS: the specific forms before the generic ones.
PERL_RULES='
  s/GrandVision-instance/CentralCommand-instance/g;
  s/_grandvision_/_central-command_/g;
  s/(namespace[:= ]+"?|-n[ =]"?)grandvision\b/$1central-command/g;
  s/grandvision/central_command/g;
  s/Grand ?Vision/Central Command/g;
  s/\bGV_/CC_/g; s/\bGV\b/Central Command/g;
  s/\bgv-/cc-/g; s/\bgv([_:.!])/cc$1/g; s/\bgv\b/cc/g;
'

preflight() {
  [[ $EUID -eq 0 ]] || die "run with sudo"
  [[ "$(sed -n 's/^version=//p' "$REPO/VERSION")" == 2.0.0 ]] || die "$REPO is not at v2.0.0 — merge the release first"
  [[ -d "$REPO/central_command" ]] || die "no central_command/ package in $REPO"
  "${K[@]}" get ns $OLD_NS >/dev/null 2>&1 || die "namespace $OLD_NS not found — nothing to migrate"
  "${SSHQ[@]}" "$COMPUTE_SSH" true || die "cannot ssh to the compute node ($COMPUTE_SSH)"
  command -v perl >/dev/null || die "perl missing"
  command -v rsync >/dev/null || die "rsync missing on this node"
  "${SSHQ[@]}" "$COMPUTE_SSH" 'command -v rsync >/dev/null' || die "rsync missing on the compute node"
  install -d -m 700 "$STATE"; touch "$JOURNAL"
  load_env
  say "preflight ok (journal: $JOURNAL)"
}

step_backup() {
  done_step backup && { note "backup: already done"; return; }
  say "backup"
  if [[ $DUMP_ONLY != 1 && -d $HOME_DIR/gv-backups && ! -e $BACKUPS ]]; then
    mv "$HOME_DIR/gv-backups" "$BACKUPS"; record backups_moved 1
  fi
  [[ -d $BACKUPS ]] || { [[ -d $HOME_DIR/gv-backups ]] && BACKUPS=$HOME_DIR/gv-backups; }   # rehearsal: dump beside the existing backups
  install -d -m 700 -o $OWNER -g $OWNER "$BACKUPS"
  local out="$BACKUPS/pre-v2"; install -d -m 700 -o $OWNER -g $OWNER "$out"
  umask 077
  psql_old gv-postgres   $OLD_DB $OLD_DB -c '' >/dev/null   # connectivity
  "${K[@]}" -n $OLD_NS exec deploy/gv-postgres   -- pg_dump -U $OLD_DB $OLD_DB     | gzip >"$out/${OLD_DB}_$STAMP.sql.gz"
  "${K[@]}" -n $OLD_NS exec deploy/gv-litellm-db -- pg_dump -U llmproxy litellm    | gzip >"$out/litellm_$STAMP.sql.gz"
  "${K[@]}" -n $OLD_NS exec deploy/gv-n8n-db     -- pg_dump -U "$N8N_DB_USER" "$N8N_DB_NAME" | gzip >"$out/n8n_$STAMP.sql.gz"
  { echo "N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY:-}"; echo "LITELLM_SALT_KEY=${LITELLM_SALT_KEY:-}"; echo "NEO4J_PASSWORD=$NEO4J_PASSWORD"; } >"$out/keys_$STAMP.env"
  for f in .env deploy/pi/.env web/.env; do [[ -f $REPO/$f ]] && cp -p "$REPO/$f" "$out/$(tr / _ <<<"$f")_$STAMP"; done
  chown -R $OWNER:$OWNER "$out"
  for f in "$out"/*_"$STAMP".sql.gz; do [[ $(stat -c %s "$f") -gt 1024 ]] || die "dump $f is suspiciously small"; done
  record backup_stamp "$STAMP"; record backup_dir "$out"
  ls -la "$out" | sed 's/^/   /'
  [[ $DUMP_ONLY == 1 ]] || mark backup
}

step_stop() {
  done_step stop && return
  say "stop the old units"
  systemctl disable --now gv-update.path gv-backup.timer 2>/dev/null || true
  systemctl stop gv-uvicorn gv-nerve gv-sandbox-runner gv-graph-bolt 2>/dev/null || true
  mark stop
}

step_postgres() {
  done_step postgres && return
  say "postgres: rewrite stored names, rename database + role"
  # Every text / varchar / json / jsonb column in public gets the ordered
  # replace list — one transaction, so an FK surprise rolls the whole sweep back.
  psql_old gv-postgres $OLD_DB $OLD_DB <<SQL
begin;
do \$\$
declare r record; n bigint; total bigint := 0;
begin
  for r in select table_name t, column_name c, data_type d from information_schema.columns
           where table_schema='public' and data_type in ('text','character varying','json','jsonb') loop
    -- one statement per pair keeps the ordering explicit
    execute format('update %I set %I = replace(%I::text, ''grandvision_'', ''central_command_'')%s where %I::text like ''%%grandvision\\_%%''',
                   r.t, r.c, r.c, case when r.d in ('json','jsonb') then '::'||r.d else '' end, r.c);
    execute format('update %I set %I = replace(%I::text, ''grandvision'', ''central_command'')%s where %I::text like ''%%grandvision%%''',
                   r.t, r.c, r.c, case when r.d in ('json','jsonb') then '::'||r.d else '' end, r.c);
    execute format('update %I set %I = replace(replace(%I::text, ''GrandVision'', ''Central Command''), ''Grand Vision'', ''Central Command'')%s where %I::text like ''%%Grand%%Vision%%''',
                   r.t, r.c, r.c, case when r.d in ('json','jsonb') then '::'||r.d else '' end, r.c);
    execute format('update %I set %I = replace(%I::text, ''GV_'', ''CC_'')%s where %I::text like ''%%GV\\_%%''',
                   r.t, r.c, r.c, case when r.d in ('json','jsonb') then '::'||r.d else '' end, r.c);
    execute format('update %I set %I = replace(replace(replace(%I::text, ''gv-'', ''cc-''), ''gv.'', ''cc.''), ''gv:'', ''cc:'')%s where %I::text ~ ''(^|[^A-Za-z0-9_])gv[-.:]''',
                   r.t, r.c, r.c, case when r.d in ('json','jsonb') then '::'||r.d else '' end, r.c);
    get diagnostics n = row_count; total := total + n;
  end loop;
  raise notice 'sweep done';
end \$\$;
commit;
SQL
  psql_old gv-postgres $OLD_DB $OLD_DB <<'SQL'
do $$ declare r record; n bigint; t bigint := 0; begin
  for r in select table_name t, column_name c from information_schema.columns
           where table_schema='public' and data_type in ('text','character varying','json','jsonb') loop
    execute format('select count(*) from %I where %I::text ~* ''grandvision|(^|[^a-z0-9_])gv[-_.:]''', r.t, r.c) into n; t := t + n;
  end loop; raise notice 'rows still carrying the old name: %', t; end $$;
SQL
  # Rename. postgres db as the maintenance connection; no client is connected
  # (the app units are stopped). RENAME clears an MD5 password, so set it
  # explicitly to the value the new manifest injects (20-postgres.yaml).
  psql_old gv-postgres $OLD_DB postgres <<SQL
alter database $OLD_DB rename to $NEW_DB;
do \$\$ begin if exists (select 1 from pg_database where datname='${OLD_DB}_test') then execute 'alter database ${OLD_DB}_test rename to ${NEW_DB}_test'; end if; end \$\$;
alter role $OLD_DB rename to $NEW_DB;
alter role $NEW_DB password '$NEW_DB';
SQL
  mark postgres
}

step_neo4j() {
  done_step neo4j && return
  say "neo4j: rewrite group_id on nodes and relationships"
  cypher_old "MATCH (n) WHERE n.group_id STARTS WITH '$OLD_DB' SET n.group_id = '$NEW_DB' + substring(n.group_id, size('$OLD_DB')) RETURN count(n) AS nodes;"
  cypher_old "MATCH ()-[r]->() WHERE r.group_id STARTS WITH '$OLD_DB' SET r.group_id = '$NEW_DB' + substring(r.group_id, size('$OLD_DB')) RETURN count(r) AS rels;"
  cypher_old "MATCH (n) WHERE n.group_id STARTS WITH '$OLD_DB' RETURN count(n) AS leftover_nodes;"
  mark neo4j
}

step_litellm() {
  done_step litellm && return
  say "litellm: rename the DB-stored alias, re-scope keys"
  # Only the plaintext columns. litellm_params / credential blobs are
  # Fernet ciphertext (base64url — a blind replace would corrupt them).
  psql_old gv-litellm-db llmproxy litellm <<'SQL'
begin;
update "LiteLLM_ProxyModelTable" set model_name = replace(model_name, 'gv-', 'cc-') where model_name like 'gv-%';
update "LiteLLM_VerificationToken" set models = array_replace(models, 'gv-default', 'cc-default') where 'gv-default' = any(models);
update "LiteLLM_VerificationToken" set key_alias = replace(key_alias, 'gv-', 'cc-') where key_alias like 'gv-%';
update "LiteLLM_VerificationToken" set metadata = replace(metadata::text, 'gv-', 'cc-')::jsonb where metadata::text like '%gv-%';
do $$ begin
  if to_regclass('"LiteLLM_SpendLogs"') is not null then
    update "LiteLLM_SpendLogs" set model = replace(model, 'gv-', 'cc-') where model like 'gv-%';
    update "LiteLLM_SpendLogs" set model_group = replace(model_group, 'gv-', 'cc-') where model_group like 'gv-%';
  end if;
  if to_regclass('"LiteLLM_TeamTable"') is not null then
    update "LiteLLM_TeamTable" set models = array_replace(models, 'gv-default', 'cc-default') where 'gv-default' = any(models);
  end if;
end $$;
commit;
select model_name from "LiteLLM_ProxyModelTable" order by 1;
select key_alias, models from "LiteLLM_VerificationToken" order by 1;
SQL
  mark litellm
}

step_n8n() {
  done_step n8n && return
  say "n8n: rename the façade webhook paths"
  psql_old gv-n8n-db "$N8N_DB_USER" "$N8N_DB_NAME" <<'SQL'
begin;
update webhook_entity set "webhookPath" = replace("webhookPath", 'gv-', 'cc-') where "webhookPath" like '%gv-%';
update workflow_entity set name = replace(name, 'gv-', 'cc-'), nodes = replace(nodes::text, 'gv-', 'cc-')::json
  where name like '%gv-%' or nodes::text like '%gv-%';
commit;
select "webhookPath" from webhook_entity order by 1;
SQL
  mark n8n
}

step_env() {
  done_step env && return
  say "env: rewrite the gitignored .env files, rename the identifiers file"
  for f in .env deploy/pi/.env web/.env; do
    [[ -f $REPO/$f ]] || continue
    "${RUNAS[@]}" perl -pi -e "$PERL_RULES" "$REPO/$f"
    note "$f: $(grep -c '^CC_' "$REPO/$f") CC_ keys, $(grep -ciE 'grandvision|\bgv[-_.]' "$REPO/$f") old-name lines left"
  done
  if [[ -f $HOME_DIR/.gv-private-identifiers && ! -e $HOME_DIR/.cc-private-identifiers ]]; then
    mv "$HOME_DIR/.gv-private-identifiers" "$HOME_DIR/.cc-private-identifiers"; record identifiers_moved 1
  fi
  load_env
  mark env
}

pv_path() { "${K[@]}" get pv "$("${K[@]}" -n "$1" get pvc "$2" -o jsonpath='{.spec.volumeName}')" -o jsonpath='{.spec.local.path}{.spec.hostPath.path}'; }
pvc_node() { "${K[@]}" get pv "$("${K[@]}" -n "$1" get pvc "$2" -o jsonpath='{.spec.volumeName}')" -o jsonpath='{.spec.nodeAffinity.required.nodeSelectorTerms[0].matchExpressions[0].values[0]}'; }
on_node() { # on_node <node> <cmd...>  — run locally on the anchor, over ssh on the compute node
  local n="$1"; shift
  if [[ "$n" == "$(hostname)" ]]; then bash -c "$*"; else "${SSHQ[@]}" "$COMPUTE_SSH" "sudo bash -c $(printf '%q' "$*")"; fi
}
retag() { # <node> <old> <new>
  on_node "$1" "i=\$(k3s ctr -n k8s.io images ls -q); grep -qx '$2' <<<\"\$i\" && k3s ctr -n k8s.io images tag --force '$2' '$3' >/dev/null && echo '   $1: $3' || true"
}

# pvc name mapping old→new is 1:1 on the prefix
PVCS=(gv-pgdata gv-litellm-pgdata gv-litellm-redisdata gv-n8n-pgdata gv-n8n-data gv-neo4j-data gv-vlogs-data)
# deployment that owns each pvc (new names)
owner_of() { case "$1" in
  cc-pgdata) echo cc-postgres;; cc-litellm-pgdata) echo cc-litellm-db;; cc-litellm-redisdata) echo cc-litellm-redis;;
  cc-n8n-pgdata) echo cc-n8n-db;; cc-n8n-data) echo cc-n8n;; cc-neo4j-data) echo cc-neo4j;; cc-vlogs-data) echo cc-vlogs;; esac; }

step_k8s() {
  done_step k8s && return
  say "k8s: park the old namespace, bring up the new one, move the data"
  # 1. record + delete the old LoadBalancer Services (ServiceLB binds the host
  #    ports on every node regardless of endpoints) and scale everything to 0.
  local lbs; lbs="$("${K[@]}" -n $OLD_NS get svc -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.name} {end}')"
  record old_lb_services "$lbs"
  for s in $lbs; do "${K[@]}" -n $OLD_NS delete svc "$s" --ignore-not-found; done
  "${K[@]}" -n $OLD_NS scale deploy --all --replicas=0
  "${K[@]}" -n $OLD_NS wait --for=delete pod --all --timeout=300s || true
  for ns in gv-mcp gv-sandbox; do "${K[@]}" get ns $ns >/dev/null 2>&1 && "${K[@]}" -n $ns scale deploy --all --replicas=0 2>/dev/null || true; done
  # 2. images and node labels
  local anchor compute
  anchor="$("${K[@]}" get nodes -l gv-role/anchor=true -o jsonpath='{.items[0].metadata.name}')"
  compute="$("${K[@]}" get nodes -l gv-role/compute=true -o jsonpath='{.items[0].metadata.name}')"
  [[ -n $anchor && -n $compute ]] || die "could not resolve the anchor/compute nodes from gv-role labels"
  "${K[@]}" label node "$anchor" cc-role/anchor=true --overwrite; "${K[@]}" label node "$compute" cc-role/compute=true --overwrite
  local tag; tag="$(grep -oE 'cc-graphiti:[A-Za-z0-9._-]+' "$REPO/deploy/k3s/40-graph.yaml" | head -1 | cut -d: -f2)"
  for n in "$anchor" "$compute"; do
    retag "$n" "docker.io/library/gv-graphiti:$tag" "docker.io/library/cc-graphiti:$tag"
    retag "$n" docker.io/library/gv-sandbox:1 docker.io/library/cc-sandbox:1
    retag "$n" docker.io/library/gv-crawler:1 docker.io/library/cc-crawler:1
  done
  # 3. the new namespaces: secrets/configmaps from deploy/pi/.env, then manifests
  "${RUNAS[@]}" "$REPO/deploy/k3s/make-secrets.sh"
  "${K[@]}" apply -f "$REPO/deploy/k3s/"
  # 4. data: local-path provisions a claim's directory on first pod start, so
  #    let each owner start once, stop it, replace the fresh directory's
  #    contents with the old claim's, start it again.
  for old in "${PVCS[@]}"; do
    local new="${old/gv-/cc-}" owner; owner="$(owner_of "$new")"
    "${K[@]}" -n $NEW_NS wait --for=jsonpath='{.status.phase}'=Bound "pvc/$new" --timeout=300s
    "${K[@]}" -n $NEW_NS scale "deploy/$owner" --replicas=0
    "${K[@]}" -n $NEW_NS wait --for=delete pod -l "app=$owner" --timeout=300s || true
    local node src dst; node="$(pvc_node $NEW_NS "$new")"; src="$(pv_path $OLD_NS "$old")"; dst="$(pv_path $NEW_NS "$new")"
    [[ -n $src && -n $dst && $src != "$dst" ]] || die "bad PV paths for $old -> $new ($src -> $dst)"
    [[ "$(pvc_node $OLD_NS "$old")" == "$node" ]] || die "$old and $new landed on different nodes"
    note "$new on $node: $src -> $dst"
    on_node "$node" "rsync -aHAX --delete '$src/' '$dst/'"
    record "pv_$new" "$node:$dst"
    "${K[@]}" -n $NEW_NS scale "deploy/$owner" --replicas=1
  done
  for d in $("${K[@]}" -n $NEW_NS get deploy -o name); do "${K[@]}" -n $NEW_NS rollout status "$d" --timeout=600s; done
  # 5. the per-namespace kubeconfigs the app reads (SA tokens are namespace-bound)
  for s in make-mcp-kubeconfig.sh make-sandbox-kubeconfig.sh make-litellm-kubeconfig.sh; do "$REPO/deploy/k3s/$s"; done
  mark k8s
}

step_host() {
  done_step host && return
  say "host: units, package, cockpit"
  for f in "$REPO"/deploy/k3s/cc-*.service "$REPO"/deploy/k3s/cc-*.path "$REPO"/deploy/k3s/cc-*.timer "$REPO/deploy/pi/cc-nerve.service"; do
    install -m 644 "$f" /etc/systemd/system/
  done
  install -m 644 "$REPO/deploy/k3s/cc-update-tmpfiles.conf" /etc/tmpfiles.d/cc-update.conf
  systemd-tmpfiles --create /etc/tmpfiles.d/cc-update.conf
  [[ -f /var/lib/gv-update/status.json ]] && cp -p /var/lib/gv-update/status.json /var/lib/cc-update/status.json || true
  systemctl disable gv-uvicorn gv-nerve gv-sandbox-runner gv-graph-bolt gv-backup.timer gv-update.path 2>/dev/null || true
  systemctl daemon-reload
  "${RUNAS[@]}" bash -lc "cd '$REPO' && uv pip install -e '.[dev,runtime]'"
  "${RUNAS[@]}" bash -lc "cd '$REPO/web' && npm ci --no-audit --no-fund && npm run build"
  systemctl enable --now cc-graph-bolt cc-sandbox-runner cc-uvicorn cc-nerve cc-backup.timer cc-update.path
  mark host
}

verify() {
  say "smoke"
  local i; for i in $(seq 1 60); do curl -fsS --max-time 3 http://127.0.0.1:8080/health >/dev/null 2>&1 && break; sleep 2; done
  curl -fsS --max-time 5 http://127.0.0.1:8080/health; echo
  curl -fsS --max-time 5 -o /dev/null -w '   cockpit: HTTP %{http_code}\n' http://127.0.0.1:3080/ || true
  note "now run: $REPO/deploy/k3s/verify.sh   (then: sudo $0 cleanup)"
}

cleanup() {
  # Only after verify.sh passes and the operator says so — this DELETES.
  say "cleanup: deleting the old namespaces, PV data, and unit files"
  read -r -p "   Type DELETE to remove namespace $OLD_NS, gv-mcp, gv-sandbox, their PV data and the gv-* unit files: " ans
  [[ $ans == DELETE ]] || die "aborted"
  for old in "${PVCS[@]}"; do
    local node p; node="$(pvc_node $OLD_NS "$old" 2>/dev/null || true)"; p="$(pv_path $OLD_NS "$old" 2>/dev/null || true)"
    [[ -n $node && $p == /var/lib/rancher/k3s/storage/* ]] && on_node "$node" "rm -rf '$p'" && note "removed $node:$p"
  done
  for ns in $OLD_NS gv-mcp gv-sandbox; do "${K[@]}" delete ns $ns --ignore-not-found --timeout=300s; done
  "${K[@]}" label node --all gv-role/anchor- gv-role/compute- 2>/dev/null || true
  for n in "$(hostname)" chromebox; do
    on_node "$n" "for i in \$(k3s ctr -n k8s.io images ls -q | grep -E '/gv-(graphiti|sandbox|crawler|mcp-echo-demo):'); do k3s ctr -n k8s.io images rm \"\$i\" >/dev/null && echo \"   $n: removed \$i\"; done" || true
  done
  rm -f /etc/systemd/system/gv-*.service /etc/systemd/system/gv-*.timer /etc/systemd/system/gv-*.path /etc/tmpfiles.d/gv-update.conf
  rm -rf /run/gv-update /var/lib/gv-update
  rm -f "$HOME_DIR"/.gv-*.kubeconfig
  systemctl daemon-reload
  mark cleanup
  note "done. Local artifacts left on purpose: ~/GrandVision-instance (rename by hand), retired checkouts."
}

rollback() {
  say "rollback (from $JOURNAL)"
  local dir stamp; dir="$(recorded backup_dir)"; stamp="$(recorded backup_stamp)"
  [[ -n $dir && -d $dir ]] || die "no backup recorded — nothing to roll back from"
  systemctl disable --now cc-uvicorn cc-nerve cc-sandbox-runner cc-graph-bolt cc-backup.timer cc-update.path 2>/dev/null || true
  if done_step k8s; then
    "${K[@]}" -n $NEW_NS scale deploy --all --replicas=0 2>/dev/null || true
    "${K[@]}" -n $NEW_NS wait --for=delete pod --all --timeout=300s 2>/dev/null || true
    # the new namespace's LoadBalancer Services hold the host ports — drop them,
    # then re-create the old namespace's resources (Services included) from the old tag
    for s in $("${K[@]}" -n $NEW_NS get svc -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.name} {end}'); do
      "${K[@]}" -n $NEW_NS delete svc "$s" --ignore-not-found; done
    for y in $("${RUNAS[@]}" git -C "$REPO" ls-tree --name-only "$OLD_TAG" deploy/k3s/ | grep '\.yaml$'); do
      "${RUNAS[@]}" git -C "$REPO" show "$OLD_TAG:$y" | "${K[@]}" apply -f - >/dev/null; done
    "${K[@]}" -n $OLD_NS scale deploy --all --replicas=1
    for d in $("${K[@]}" -n $OLD_NS get deploy -o name); do "${K[@]}" -n $OLD_NS rollout status "$d" --timeout=600s; done
  fi
  # the spine: drop and restore from the pre-migration dump (the rewrite was in place)
  local u=$OLD_DB; done_step postgres && u=$NEW_DB
  psql_old gv-postgres "$u" postgres <<SQL
do \$\$ begin
  if exists (select 1 from pg_database where datname='$NEW_DB') then execute 'alter database $NEW_DB rename to $OLD_DB'; end if;
  if exists (select 1 from pg_database where datname='${NEW_DB}_test') then execute 'alter database ${NEW_DB}_test rename to ${OLD_DB}_test'; end if;
  if exists (select 1 from pg_roles where rolname='$NEW_DB') then execute 'alter role $NEW_DB rename to $OLD_DB'; execute 'alter role $OLD_DB password ''$OLD_DB'''; end if;
end \$\$;
SQL
  "${K[@]}" -n $OLD_NS exec deploy/gv-postgres -- psql -U $OLD_DB -d postgres -c "drop database $OLD_DB with (force)" -c "create database $OLD_DB owner $OLD_DB"
  gunzip -c "$dir/${OLD_DB}_$stamp.sql.gz" | psql_old gv-postgres $OLD_DB $OLD_DB -q
  cypher_old "MATCH (n) WHERE n.group_id STARTS WITH '$NEW_DB' SET n.group_id = '$OLD_DB' + substring(n.group_id, size('$NEW_DB')) RETURN count(n);"
  cypher_old "MATCH ()-[r]->() WHERE r.group_id STARTS WITH '$NEW_DB' SET r.group_id = '$OLD_DB' + substring(r.group_id, size('$NEW_DB')) RETURN count(r);"
  gunzip -c "$dir/litellm_$stamp.sql.gz" | { "${K[@]}" -n $OLD_NS exec -i deploy/gv-litellm-db -- psql -U llmproxy -d postgres -c "drop database litellm with (force)" -c "create database litellm owner llmproxy" >/dev/null; "${K[@]}" -n $OLD_NS exec -i deploy/gv-litellm-db -- psql -q -U llmproxy -d litellm; }
  gunzip -c "$dir/n8n_$stamp.sql.gz" | { "${K[@]}" -n $OLD_NS exec -i deploy/gv-n8n-db -- psql -U "$N8N_DB_USER" -d postgres -c "drop database $N8N_DB_NAME with (force)" -c "create database $N8N_DB_NAME owner $N8N_DB_USER" >/dev/null; "${K[@]}" -n $OLD_NS exec -i deploy/gv-n8n-db -- psql -q -U "$N8N_DB_USER" -d "$N8N_DB_NAME"; }
  for f in .env deploy/pi/.env web/.env; do local b="$dir/$(tr / _ <<<"$f")_$stamp"; [[ -f $b ]] && install -o $OWNER -g $OWNER -m 600 "$b" "$REPO/$f"; done
  [[ $(recorded identifiers_moved) == 1 && -f $HOME_DIR/.cc-private-identifiers ]] && mv "$HOME_DIR/.cc-private-identifiers" "$HOME_DIR/.gv-private-identifiers"
  [[ $(recorded backups_moved) == 1 && -d $BACKUPS && ! -e $HOME_DIR/gv-backups ]] && mv "$BACKUPS" "$HOME_DIR/gv-backups"
  systemctl daemon-reload
  systemctl enable --now gv-graph-bolt gv-sandbox-runner gv-backup.timer gv-update.path 2>/dev/null || true
  note "stores and units are back on the old names. Now: git -C $REPO reset --hard $OLD_TAG && uv pip install -e '.[dev,runtime]' && (cd web && npm run build) && sudo systemctl start gv-uvicorn gv-nerve"
  mv "$JOURNAL" "$JOURNAL.rolled-back-$STAMP"
}

case "${1:-}" in
  rollback) preflight_min() { [[ $EUID -eq 0 ]] || die "run with sudo"; load_env; }; preflight_min; rollback ;;
  cleanup)  preflight; cleanup ;;
  "")
    preflight
    step_backup
    if [[ $DUMP_ONLY == 1 ]]; then
      say "DUMP_ONLY: rehearsal complete — nothing changed. The forward pass would:"
      note "old LB services: $("${K[@]}" -n $OLD_NS get svc -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.name} {end}')"
      for old in "${PVCS[@]}"; do note "$old on $(pvc_node $OLD_NS "$old"): $(pv_path $OLD_NS "$old")"; done
      note "spine rows carrying the old name: $(psql_old gv-postgres $OLD_DB $OLD_DB -tA -c "select count(*) from audit_event where payload::text ~* 'grandvision|gv-'") audit_event, $(psql_old gv-postgres $OLD_DB $OLD_DB -tA -c "select count(*) from work_item where message_id like '%grandvision%'") work_item"
      note "graph: $(cypher_old "MATCH (n) WHERE n.group_id STARTS WITH '$OLD_DB' RETURN count(n);" | tail -1) nodes to relabel"
      exit 0
    fi
    step_stop; step_postgres; step_neo4j; step_litellm; step_n8n; step_env; step_k8s; step_host; verify ;;
  *) die "usage: sudo $0 [rollback|cleanup]   (DUMP_ONLY=1 for a rehearsal)" ;;
esac
