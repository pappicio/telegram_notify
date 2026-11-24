#!/bin/sh
set -eu

PLUGIN_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PYTHON="/usr/data/python-hf/bin/python3"

exec "$PYTHON" "$PLUGIN_DIR/app/telegram_notify.py"
