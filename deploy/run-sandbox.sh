#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_real="$(readlink -f "$project_dir/.venv/bin/python")"
python_prefix="$(dirname "$(dirname "$python_real")")"
install -d -m 0700 "$project_dir/data"

# Bubblewrap needs mount-point parents that do not already come from system binds.
mount_dirs=()
declare -A seen_mount_dirs=()
add_mount_parents() {
  local path="$1" parent
  local -a pending=()
  while [[ "$path" != "/" ]]; do
    case "$path" in
      /usr|/usr/*|/lib|/lib/*|/lib64|/lib64/*) break ;;
    esac
    pending+=("$path")
    path="$(dirname "$path")"
  done
  for ((i=${#pending[@]}-1; i>=0; i--)); do
    parent="${pending[i]}"
    if [[ ! -v "seen_mount_dirs[$parent]" ]]; then
      mount_dirs+=(--dir "$parent")
      seen_mount_dirs["$parent"]=1
    fi
  done
}
add_mount_parents "$project_dir"
add_mount_parents "$python_prefix"

export PYTHONDONTWRITEBYTECODE=1
export AUTOJUDGER_HISTORY_DB="$project_dir/data/history.sqlite3"
exec bwrap \
  --ro-bind /usr /usr \
  --ro-bind /lib /lib \
  --ro-bind /lib64 /lib64 \
  "${mount_dirs[@]}" \
  --ro-bind "$python_prefix" "$python_prefix" \
  --ro-bind "$project_dir/.venv" "$project_dir/.venv" \
  --ro-bind "$project_dir/src" "$project_dir/src" \
  --bind "$project_dir/data" "$project_dir/data" \
  --dir /etc \
  --ro-bind /etc/ssl /etc/ssl \
  --ro-bind /etc/resolv.conf /etc/resolv.conf \
  --ro-bind /etc/nsswitch.conf /etc/nsswitch.conf \
  --ro-bind /etc/hosts /etc/hosts \
  --tmpfs /tmp --proc /proc --dev /dev \
  --unshare-user --uid 65532 --gid 65532 --unshare-pid --share-net \
  --chdir "$project_dir" \
  "$project_dir/.venv/bin/python" -m uvicorn autojudger.api:app \
  --host 127.0.0.1 --port "${AUTOJUDGER_PORT:-8765}" \
  --limit-concurrency 16 --timeout-keep-alive 5 --no-proxy-headers --no-server-header
