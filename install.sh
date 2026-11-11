#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python_ready() {
  command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
}
if ! python_ready; then
  printf '%s\n' 'OpenSW needs Python 3.11 or newer for its setup wizard.' 'Install Python from https://www.python.org/downloads/ and return here.'
  if [ -t 0 ]; then
    printf '%s' 'Press Enter after installing Python: '
    read -r answer
  fi
fi
if ! python_ready; then
  printf '%s\n' 'Python 3.11 or newer is still unavailable. Install it, open a new terminal, and run ./install.sh again.' >&2
  exit 1
fi
exec python3 "$ROOT/tools/install.py" "$@"
