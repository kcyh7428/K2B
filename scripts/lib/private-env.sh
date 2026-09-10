#!/usr/bin/env bash
# Source a per-machine K2B environment file only after strict ownership checks.
# This file is a library: source it, then call k2b_load_private_env.

k2b_load_private_env() {
  local env_file="${1:-${K2B_ENV_FILE:-${HOME:-}/.k2b-env}}"

  if [[ -z "$env_file" || "$env_file" == "/.k2b-env" ]]; then
    echo "ERROR: HOME or K2B_ENV_FILE must be set to locate the K2B credential file." >&2
    return 1
  fi

  # Test -L as well as -e so a broken symlink is rejected explicitly.
  if [[ ! -e "$env_file" && ! -L "$env_file" ]]; then
    echo "ERROR: K2B credential file not found: $env_file" >&2
    return 1
  fi

  if ! python3 - "$env_file" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
try:
    info = os.lstat(path)
except OSError:
    raise SystemExit(1)

valid = (
    stat.S_ISREG(info.st_mode)
    and not stat.S_ISLNK(info.st_mode)
    and info.st_uid == os.getuid()
    and stat.S_IMODE(info.st_mode) == 0o600
)
raise SystemExit(0 if valid else 1)
PY
  then
    echo "ERROR: K2B credential file must be a regular, non-symlink file owned by the current user with mode 0600: $env_file" >&2
    return 1
  fi

  # The validated file is inside the current user's trust boundary. Do not use
  # `set -a`: callers explicitly export only the credential their child needs.
  # shellcheck disable=SC1090
  if ! source "$env_file"; then
    echo "ERROR: Could not load K2B credential file: $env_file" >&2
    return 1
  fi
}
