#!/usr/bin/env bash
# Install citrix-clipboard-bridge as a systemd user service (no root needed).
#
# Usage: ./install.sh
# Dependencies (install first): sudo apt install xclip python3-gi python3-xlib
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly BIN_DIR="${HOME}/.local/bin"
readonly UNIT_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
readonly SERVICE_NAME="citrix-clipboard-bridge"

missing=()
command -v xclip >/dev/null || missing+=("xclip")
/usr/bin/python3 -c 'import gi' 2>/dev/null || missing+=("python3-gi")
/usr/bin/python3 -c 'import Xlib' 2>/dev/null || missing+=("python3-xlib")
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "Missing dependencies: ${missing[*]}" >&2
    echo "Install them with: sudo apt install ${missing[*]}" >&2
    exit 1
fi

install -D -m 0755 "${SCRIPT_DIR}/${SERVICE_NAME}.py" "${BIN_DIR}/${SERVICE_NAME}"
install -D -m 0644 "${SCRIPT_DIR}/${SERVICE_NAME}.service" \
    "${UNIT_DIR}/${SERVICE_NAME}.service"

systemctl --user daemon-reload
systemctl --user enable --now "${SERVICE_NAME}"
systemctl --user --no-pager status "${SERVICE_NAME}" | head -n 3
