#!/usr/bin/env bash
# ============================================================================
# env-path.sh — where the k3s profile's CONTAINERS' env lives, and the one move.
#
#   The file is `deploy/k3s/.env` (gitignored; template `deploy/k3s/.env.example`).
#   It used to be `deploy/pi/.env`, and it is UNTRACKED: no merge moves it and
#   no `git reset --hard` (the updater's rollback) restores it — so the move is
#   a filesystem act, done ONCE, by ONE function, and the old path is left as
#   a symlink so a release that still reads it (a rollback landing before
#   v2.66.0) finds the same bytes. Spec: the deploy/pi split, 2026-10-17.
#
#   SOURCE this file; it defines two functions and sets nothing.
#
#     cc_k3s_env_file <repo-root>     print the path every k3s script reads:
#                                     deploy/k3s/.env when it exists (file or
#                                     symlink), else deploy/pi/.env. Never
#                                     creates anything.
#     cc_k3s_env_migrate <repo-root>  deploy/k3s/.env absent and deploy/pi/.env
#                                     a regular file → mv (mode and owner kept)
#                                     and a relative symlink at the old path;
#                                     new present and old a symlink or absent →
#                                     nothing, 0; BOTH regular files → refuse,
#                                     1, touch neither (the operator decides).
#
#   The two keys in this file (LITELLM_SALT_KEY, N8N_ENCRYPTION_KEY) are never
#   regenerated and never printed; nothing here reads the file's contents.
# ============================================================================

cc_k3s_env_file() { # <repo-root>
  local root="$1"
  if [[ -e "$root/deploy/k3s/.env" || -L "$root/deploy/k3s/.env" ]]; then
    printf '%s\n' "$root/deploy/k3s/.env"
  else
    printf '%s\n' "$root/deploy/pi/.env"
  fi
}

cc_k3s_env_migrate() { # <repo-root>
  local new="$1/deploy/k3s/.env" old="$1/deploy/pi/.env"
  if [[ -e "$new" || -L "$new" ]]; then
    if [[ -f "$old" && ! -L "$old" ]]; then
      echo "REFUSED: both $new and $old are regular files — decide which one is" \
           "current, remove the other, and re-run; nothing was touched" >&2
      return 1
    fi
    return 0
  fi
  # Only a REGULAR file moves: a dangling or foreign symlink at the old path
  # is not the env.
  [[ -f "$old" && ! -L "$old" ]] || return 0
  mv "$old" "$new" || return 1
  if ! ln -s ../k3s/.env "$old"; then
    echo "WARNING: moved $old to $new but could not leave a symlink at the old path" >&2
  fi
  echo "moved deploy/pi/.env -> deploy/k3s/.env (a symlink is left at the old path)"
  return 0
}
