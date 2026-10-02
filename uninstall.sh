#!/usr/bin/env bash
# Remove the citrix-clipboard-bridge systemd user service and its files.
#
# Usage: ./uninstall.sh
set -euo pipefail

readonly BIN_DIR="${HOME}/.local/bin"
readonly UNIT_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
readonly SERVICE_NAME="citrix-clipboard-bridge"

systemctl --user disable --now "${SERVICE_NAME}" 2>/dev/null || true
rm -f "${BIN_DIR}/${SERVICE_NAME}" "${UNIT_DIR}/${SERVICE_NAME}.service"
systemctl --user daemon-reload
echo "${SERVICE_NAME} removed."
