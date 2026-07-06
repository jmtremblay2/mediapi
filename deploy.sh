#!/usr/bin/env bash
#
# deploy.sh -- pull the latest mediapi and (re)deploy it on the Raspberry Pi.
#
# Runs ON the Pi. It:
#   0. refuses to run if the root filesystem is a read-only overlay
#   1. pulls the latest commit from origin (forgejo) with --ff-only
#   2. syncs Python deps with uv
#   3. ensures the session secret key exists (while the card is writable)
#   4. re-applies the WiFi country + AP connection from .env
#   5. renders + installs the systemd unit templates
#   6. restarts the services
#   7. health-checks the app, and rolls back to the previous commit if it
#      fails to come up
#
# Config comes from .env (copy .env.example -> .env first). Needs sudo for
# nmcli / systemctl / writing to /etc; you'll be prompted as needed.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# --- load config ----------------------------------------------------
if [[ ! -f .env ]]; then
  echo "ERROR: .env not found. Copy .env.example to .env and fill it in." >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
source .env
set +a

MEDIAPI_USER="${MEDIAPI_USER:-$(id -un)}"
MEDIAPI_PORT="${MEDIAPI_PORT:-8080}"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

# --- 0. refuse to run on a read-only overlay ------------------------
if findmnt -no FSTYPE / | grep -q overlay; then
  cat >&2 <<EOF
ERROR: the root filesystem is a read-only overlay -- any changes would vanish
on the next reboot. Disable the overlay, reboot, re-run this script, then
re-enable it:

  sudo raspi-config nonint do_overlayfs 1    # disable overlay
  sudo reboot
  # ... after reboot:
  cd $PROJECT_DIR && ./deploy.sh
  sudo raspi-config nonint do_overlayfs 0    # re-enable overlay
  sudo reboot
EOF
  exit 1
fi

# --- 1. pull latest -------------------------------------------------
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
PREV_REF="$(git rev-parse HEAD)"
echo "==> Pulling latest from origin/$BRANCH ..."
if ! git pull --ff-only origin "$BRANCH"; then
  echo "ERROR: git pull failed (uncommitted changes or non-fast-forward)." >&2
  exit 1
fi
NEW_REF="$(git rev-parse HEAD)"
echo "    $PREV_REF -> $NEW_REF"

# --- render + install systemd units (used again on rollback) --------
install_units() {
  for unit in mediapi-mpv mediapi-app; do
    sed -e "s|\${MEDIAPI_USER}|${MEDIAPI_USER}|g" \
        -e "s|\${PROJECT_DIR}|${PROJECT_DIR}|g" \
        -e "s|\${UV}|${UV}|g" \
        "systemd/${unit}.service.template" \
      | sudo tee "/etc/systemd/system/${unit}.service" >/dev/null
  done
  sudo systemctl daemon-reload
}

# --- 2. sync python deps -------------------------------------------
echo "==> Syncing dependencies (uv sync) ..."
"$UV" sync

# --- 3. ensure session secret key exists ---------------------------
if [[ ! -f instance/secret_key ]]; then
  echo "==> Generating instance/secret_key ..."
  mkdir -p instance
  python3 -c "import os; print(os.urandom(32).hex())" > instance/secret_key
  chmod 600 instance/secret_key
fi

# --- 4. wifi country + AP connection -------------------------------
echo "==> Applying WiFi country + AP config ..."
sudo raspi-config nonint do_wifi_country "${MEDIAPI_WIFI_COUNTRY}"
if nmcli -g NAME con show | grep -qx "${MEDIAPI_AP_CONN_NAME}"; then
  echo "    updating existing AP connection '${MEDIAPI_AP_CONN_NAME}'"
  sudo nmcli con modify "${MEDIAPI_AP_CONN_NAME}" \
    802-11-wireless.ssid "${MEDIAPI_AP_SSID}" \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "${MEDIAPI_AP_PASSWORD}"
else
  echo "    creating AP connection '${MEDIAPI_AP_CONN_NAME}'"
  sudo nmcli con add type wifi ifname wlan0 con-name "${MEDIAPI_AP_CONN_NAME}" \
    autoconnect yes connection.autoconnect-priority 100 save yes \
    802-11-wireless.mode ap 802-11-wireless.band bg \
    802-11-wireless.ssid "${MEDIAPI_AP_SSID}" \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "${MEDIAPI_AP_PASSWORD}" \
    ipv4.method shared
fi

# --- 5 & 6. install units + restart --------------------------------
echo "==> Installing systemd units + restarting services ..."
install_units
sudo systemctl enable mediapi-mpv mediapi-app >/dev/null 2>&1 || true
sudo systemctl restart mediapi-mpv
sudo systemctl restart mediapi-app

# --- 7. health check + rollback ------------------------------------
echo "==> Health check on http://127.0.0.1:${MEDIAPI_PORT}/login ..."
healthy=0
for _ in $(seq 1 15); do
  if curl -fsS -o /dev/null "http://127.0.0.1:${MEDIAPI_PORT}/login"; then
    healthy=1
    break
  fi
  sleep 1
done

if [[ "$healthy" -ne 1 ]]; then
  echo "ERROR: app did not become healthy -- rolling back to $PREV_REF" >&2
  git reset --hard "$PREV_REF"
  "$UV" sync
  install_units
  sudo systemctl restart mediapi-mpv mediapi-app
  echo "--- last 30 lines of mediapi-app log: ---" >&2
  sudo journalctl -u mediapi-app -n 30 --no-pager >&2 || true
  exit 1
fi

echo "==> Deploy OK. Services healthy on port ${MEDIAPI_PORT}."
systemctl --no-pager --lines=0 status mediapi-mpv mediapi-app || true
