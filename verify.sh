#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'OpenSW verification needs Python 3.11 or newer. Install it and run ./verify.sh again.' >&2
  exit 1
fi
exec python3 "$ROOT/tools/verify_installation.py" "$@"
