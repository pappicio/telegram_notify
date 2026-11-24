#!/bin/sh
set -eu

PLUGIN_DIR="${OC_PLUGIN_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"

echo "[Telegram Notify] Running uninstall hook..."
echo "[Telegram Notify] No external Moonraker or Klipper integrations need removal."
echo "[Telegram Notify] The uninstall hook does not delete plugin data."
echo "[Telegram Notify] Plugin directory: $PLUGIN_DIR"
echo "[Telegram Notify] Use 'oc-plugin remove --keep-data telegram_notify' to preserve the data folder."
echo "[Telegram Notify] Uninstall hook completed."
