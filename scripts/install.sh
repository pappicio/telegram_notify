#!/bin/sh
set -eu

PLUGIN_DIR="${OC_PLUGIN_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"

mkdir -p \
    "$PLUGIN_DIR/data" \
    "$PLUGIN_DIR/logs" \
    "$PLUGIN_DIR/backup"

echo "Telegram Notify directories prepared."
