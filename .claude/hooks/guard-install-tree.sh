#!/usr/bin/env bash
# PreToolUse guard — a deployment carries no local patches (design record
# docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md, D10
# mechanism 2: "the agent is held to .env by a hook, not a sentence").
#
# ACTIVE ONLY ON A DEPLOYMENT. "Deployment" here means a checkout whose
# repo-root .env names a CC_STATE_DIR that already holds ledger.tsv — the
# ledger is created by the first ./setup.sh / configure / check run (D2,
# mechanism 1, a sibling change in this same release). A development
# checkout — the Pi's live checkout, the instance repo, every worktree under
# .claude/worktrees/ — never runs ./setup.sh against itself, so it never
# grows a ledger: this hook is a silent no-op there, with or without
# CC_DEV_SESSION. CC_DEV_SESSION=1 is the explicit exemption for the OTHER
# case — a development session held open ON an actual deployment tree (a
# work-site laptop), where the ledger IS present but the session's whole
# purpose is to touch the tree.
#
# Command-position matching (the POS anchor below) and the exit-2 contract
# mirror the operator's existing ~/.claude/hooks/guard-live-deploy.sh.
#
# Never prints an .env VALUE. The denial text below is a fixed string, and
# the one value this script reads out of .env (CC_STATE_DIR) is used only to
# build paths for comparison — it is never echoed, logged, or interpolated
# into stderr.
set -u

# ---- 0. the explicit developer exemption, before anything else -------------
[[ "${CC_DEV_SESSION:-}" == "1" ]] && exit 0

DENY_MSG="DENIED by .claude/hooks/guard-install-tree.sh (design record 2026-10-01 D10): a deployment carries no local patches. You may change .env; everything else is a finding — run ./setup.sh report and hand the operator the file. (A development session on this tree sets CC_DEV_SESSION=1.)"

deny() {
  printf '%s\n' "$DENY_MSG" >&2
  exit 2
}

input="$(cat)"

# ---- 1. resolve the repo root; ACTIVE only when a ledger already exists ----
ROOT="${CLAUDE_PROJECT_DIR:-}"
if [[ -z "$ROOT" ]]; then
  ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
fi
[[ -n "$ROOT" && -d "$ROOT" ]] || exit 0

ENV_FILE="$ROOT/.env"
[[ -f "$ENV_FILE" ]] || exit 0

# Tiny dotenv reader, self-contained on purpose — mirrors deploy/env-lib.sh's
# cc_get_kv (last assignment wins, CRLF-safe) but never SOURCES env-lib.sh:
# this hook must stay fast and must never execute anything an operator's
# .env could contain.
cc_hook_get_kv() { # cc_hook_get_kv <file> <key>
  local f="$1" k="$2" line out=""
  [[ -f "$f" ]] || { printf ''; return 0; }
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" == "$k="* ]] && out="${line#*=}"
  done <"$f"
  printf '%s' "$out"
}

STATE_DIR="$(cc_hook_get_kv "$ENV_FILE" CC_STATE_DIR)"
[[ -n "$STATE_DIR" ]] || exit 0
[[ -f "$STATE_DIR/ledger.tsv" ]] || exit 0

# ---- 2. parse stdin: tool_name, tool_input.file_path, tool_input.command ---
PY=""
for c in python3 python; do
  # The WindowsApps python3 stub answers `command -v` but fails to run
  # (deploy/single/verify.sh hit this 2026-08-21) — probe by RUNNING it.
  command -v "$c" >/dev/null 2>&1 && "$c" -c '' 2>/dev/null && { PY="$c"; break; }
done

TOOL_NAME="" FILE_PATH="" COMMAND="" extracted=0

if [[ -n "$PY" ]]; then
  raw="$(printf '%s' "$input" | "$PY" -c '
import json, sys, base64
try:
    d = json.load(sys.stdin)
    if not isinstance(d, dict):
        raise ValueError("not an object")
    tn = d.get("tool_name") or ""
    ti = d.get("tool_input") or {}
    if not isinstance(ti, dict):
        ti = {}
    fp = ti.get("file_path") or ""
    cmd = ti.get("command") or ""
    if not isinstance(tn, str): tn = ""
    if not isinstance(fp, str): fp = ""
    if not isinstance(cmd, str): cmd = ""
    out = [base64.b64encode(v.encode("utf-8", "replace")).decode("ascii") for v in (tn, fp, cmd)]
    sys.stdout.write("\n".join(out))
except Exception:
    sys.exit(1)
' 2>/dev/null)"
  rc=$?
  if [[ $rc -eq 0 ]]; then
    mapfile -t _cc_lines <<< "$raw"
    TOOL_NAME="$(printf '%s' "${_cc_lines[0]:-}" | base64 -d 2>/dev/null)"
    FILE_PATH="$(printf '%s' "${_cc_lines[1]:-}" | base64 -d 2>/dev/null)"
    COMMAND="$(printf '%s' "${_cc_lines[2]:-}" | base64 -d 2>/dev/null)"
    extracted=1
  fi
fi

if [[ "$extracted" -ne 1 ]]; then
  # No python at all, or it failed to parse: a crude textual extraction of
  # just the two string values this hook needs (no tool_name — the branch
  # below falls back to "which field is non-empty" instead). JSON proper
  # never carries a raw newline inside a string, so flattening structural
  # whitespace between tokens costs nothing and makes the single-line sed
  # below reliable even for pretty-printed input.
  flat="$(printf '%s' "$input" | tr -d '\r\n')"

  unescape_minimal() {
    # Order matters: collapse real \\ pairs to a placeholder FIRST so a
    # literal backslash immediately before a 'n' or 't' isn't mistaken for
    # an escape introduced by this pass, then handle \" \n \t, then restore
    # the placeholder to one literal backslash.
    sed -e 's/\\\\/\x01/g' -e 's/\\"/"/g' -e 's/\\n/\n/g' -e 's/\\t/\t/g' -e 's/\x01/\\/g'
  }

  FILE_PATH="$(printf '%s' "$flat" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\(\([^"\\]\|\\.\)*\)".*/\1/p' | head -n1 | unescape_minimal)"
  COMMAND="$(printf '%s' "$flat" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\(\([^"\\]\|\\.\)*\)".*/\1/p' | head -n1 | unescape_minimal)"
fi

# ---- 3. decide which branch applies -----------------------------------------
BRANCH=""
if [[ -n "$TOOL_NAME" ]]; then
  case "$TOOL_NAME" in
    Edit|Write|NotebookEdit) BRANCH=file ;;
    Bash) BRANCH=bash ;;
    *) exit 0 ;;
  esac
elif [[ -n "$FILE_PATH" ]]; then
  BRANCH=file
elif [[ -n "$COMMAND" ]]; then
  BRANCH=bash
else
  exit 0
fi

normalize_path() { # normalize_path <path>
  local p="$1"
  p="${p//\\//}"
  if command -v cygpath >/dev/null 2>&1; then
    local u
    u="$(cygpath -u "$p" 2>/dev/null)" && [[ -n "$u" ]] && p="$u"
  fi
  printf '%s' "$p"
}

# ---- 4. file tools: Edit / Write / NotebookEdit -----------------------------
if [[ "$BRANCH" == "file" ]]; then
  [[ -n "$FILE_PATH" ]] || exit 0

  fp="$(normalize_path "$FILE_PATH")"
  root_norm="$(normalize_path "$ROOT")"
  state_norm="$(normalize_path "$STATE_DIR")"

  # Outside the repo entirely: not this hook's concern.
  if [[ "$fp" != "$root_norm" && "$fp" != "$root_norm"/* ]]; then
    exit 0
  fi

  [[ "$fp" == "$root_norm/.env" ]] && exit 0
  if [[ "$fp" == "$state_norm" || "$fp" == "$state_norm"/* ]]; then
    exit 0
  fi
  if git -C "$ROOT" check-ignore -q -- "$fp" 2>/dev/null; then
    exit 0
  fi
  deny
fi

# ---- 5. Bash -----------------------------------------------------------------
cmd="$COMMAND"
[[ -n "$cmd" ]] || exit 0

POS='(^|[;&|(])[[:space:]]*(sudo[[:space:]]+(-[^[:space:]]+[[:space:]]+)*)?'

# sed -i / --in-place, at command position.
printf '%s\n' "$cmd" | grep -Eq "${POS}sed[[:space:]]+(-[A-Za-z0-9]*[[:space:]]+)*(-i[A-Za-z0-9.]*|--in-place(=[^[:space:]]*)?)([[:space:]]|\$)" && deny

# tee, unconditionally — it writes wherever its arguments say, so the whole
# command is refused rather than parsed.
printf '%s\n' "$cmd" | grep -Eq "${POS}tee([[:space:]]|\$)" && deny

# Destructive git subcommands — discarding or rewriting local state.
printf '%s\n' "$cmd" | grep -Eq "${POS}git[[:space:]]+(checkout[[:space:]]+--|restore|stash|apply|am|cherry-pick|revert|reset[[:space:]]+--hard|clean|rebase|merge|commit|add)([[:space:]]|\$)" && deny

# npm install family — `npm ci` is explicitly allowed by omission.
printf '%s\n' "$cmd" | grep -Eq "${POS}npm[[:space:]]+(install|i|update|audit[[:space:]]+fix)([[:space:]]|\$)" && deny

# uv lock/add/remove/sync — the venv and the lockfile are setup's to manage.
printf '%s\n' "$cmd" | grep -Eq "${POS}uv[[:space:]]+(lock|add|remove|sync)([[:space:]]|\$)" && deny

printf '%s\n' "$cmd" | grep -Eq "${POS}pip-compile([[:space:]]|\$)" && deny
printf '%s\n' "$cmd" | grep -Eq "${POS}pip[[:space:]]+install([[:space:]]|\$)" && deny

# Redirections: > and >> whose target is not .env, not under the state dir,
# not /dev/null, and not /tmp. Matched textually, not shell-aware: a target
# under $ROOT that isn't gitignored denies, and an unresolved relative
# target denies too — fail closed on ambiguity for Bash (Edit is the
# sanctioned way to touch .env anyway).
is_redirect_target_allowed() { # is_redirect_target_allowed <target>
  local t="$1"
  t="${t%\"}"; t="${t#\"}"
  t="${t%\'}"; t="${t#\'}"
  [[ "$t" =~ ^\&[0-9]+$ ]] && return 0   # fd dup (2>&1) — not a file at all
  [[ "$t" == ".env" || "$t" == "./.env" || "$t" == "$ROOT/.env" ]] && return 0
  [[ "$t" == "/dev/null" ]] && return 0
  [[ "$t" == "/tmp" || "$t" == /tmp/* ]] && return 0
  [[ "$t" == "$STATE_DIR" || "$t" == "$STATE_DIR"/* ]] && return 0
  if [[ "$t" == "$ROOT"/* ]]; then
    git -C "$ROOT" check-ignore -q -- "$t" 2>/dev/null && return 0
    return 1
  fi
  [[ "$t" == /* ]] && return 0   # absolute, outside root/state/tmp: not ours
  return 1   # relative and otherwise unresolved: fail closed
}

targets="$(printf '%s' "$cmd" | grep -oE '>{1,2}[[:space:]]*[^[:space:];&|)]+' | sed -E 's/^>{1,2}[[:space:]]*//')"
if [[ -n "$targets" ]]; then
  while IFS= read -r tgt; do
    [[ -z "$tgt" ]] && continue
    is_redirect_target_allowed "$tgt" || deny
  done <<< "$targets"
fi

# cp/mv/rm/install/patch/truncate whose argument text names a path under
# $ROOT: a textual match on $ROOT itself, or a relative path git already
# tracks. Split crudely at top-level separators first — textual, not a real
# shell parse, same spirit as the redirection check above.
check_tree_write_commands() { # check_tree_write_commands <cmd> -> 0 = deny
  local full="$1" segment
  while IFS= read -r segment; do
    [[ -z "$segment" ]] && continue
    segment="${segment#"${segment%%[![:space:]]*}"}"
    if [[ "$segment" =~ ^sudo[[:space:]]+(-[^[:space:]]+[[:space:]]+)*(.*)$ ]]; then
      segment="${BASH_REMATCH[2]}"
    fi
    if [[ "$segment" =~ ^(cp|mv|rm|install|patch|truncate)([[:space:]]|$) ]]; then
      local -a words
      read -ra words <<< "$segment"
      local i tok
      for (( i=1; i<${#words[@]}; i++ )); do
        tok="${words[$i]}"
        [[ "$tok" == -* ]] && continue
        tok="${tok%\"}"; tok="${tok#\"}"
        tok="${tok%\'}"; tok="${tok#\'}"
        if [[ "$tok" == "$ROOT"* ]]; then
          return 0
        fi
        if git -C "$ROOT" ls-files --error-unmatch -- "$tok" >/dev/null 2>&1; then
          return 0
        fi
      done
    fi
  done <<< "$(printf '%s' "$full" | sed -E 's/(&&|\|\||;|\|)/\n/g')"
  return 1
}

if check_tree_write_commands "$cmd"; then
  deny
fi

exit 0
