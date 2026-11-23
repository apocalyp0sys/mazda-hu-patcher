#!/bin/sh
# Unix wrapper for hu_patcher.py
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
exec python3 "$HERE/hu_patcher.py" "$@"
