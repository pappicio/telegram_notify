#!/bin/sh
set -eu

PLUGIN_DIR="${OC_PLUGIN_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"
INSTALL_SCRIPT="$PLUGIN_DIR/scripts/install.sh"

if [ ! -f "$INSTALL_SCRIPT" ]; then
    echo "[Telegram Notify] install.sh not found." >&2
    exit 1
fi

echo "[Telegram Notify] Preparing plugin directories..."
OC_PLUGIN_DIR="$PLUGIN_DIR" sh "$INSTALL_SCRIPT"

echo "[Telegram Notify] Update preparation completed."
echo "[Telegram Notify] Existing configuration and runtime data were preserved."
