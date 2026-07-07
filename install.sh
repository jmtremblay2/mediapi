#!/usr/bin/env bash
#
# install.sh -- (re)deploy mediapi on the Raspberry Pi from the CURRENT checkout.
#
# Runs ON the Pi. Unlike a pull-based deployer, this script deploys whatever
# git ref is currently checked out -- so the intended workflow is:
#
#     git fetch --tags
#     git checkout v1.2.0      # (or any tag/branch/commit)
#     ./install.sh
#
# or, as a one-liner that does the checkout for you:
#
#     ./install.sh v1.2.0
#
# It is fully re-runnable (idempotent): each run re-renders the systemd units,
# upserts the WiFi AP connection, syncs deps, and restarts the services,
# cleaning up the previous deployment in place. After a healthy deploy it
# records the deployed commit in instance/deployed_ref; if a new deploy fails
# its health check, it rolls back to that last-known-good ref automatically.
#
# What it does, in order:
#   0. refuses to run if the root filesystem is a read-only overlay
#   1. (optional) checks out the ref passed as $1
#   2. syncs Python deps with uv
#   3. ensures the session secret key exists (while the card is writable)
#   4. re-applies the WiFi country + AP connection from .env
#   5. renders + installs the systemd unit templates
#   6. restarts the services
#   7. health-checks the app, rolling back to the last-good ref if it fails
#
# Config comes from .env (copy .env.example -> .env first). Needs sudo for
# nmcli / systemctl / writing to /etc; you'll be prompted as needed.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

DEPLOYED_REF_FILE="instance/deployed_ref"
TARGET_REF="${1:-}"

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
  cd $PROJECT_DIR && ./install.sh${TARGET_REF:+ $TARGET_REF}
  sudo raspi-config nonint do_overlayfs 0    # re-enable overlay
  sudo reboot
EOF
  exit 1
fi

# --- 1. (optional) check out the requested ref ----------------------
# The last healthy deploy's commit, if any -- our rollback target.
PREV_GOOD=""
if [[ -f "$DEPLOYED_REF_FILE" ]]; then
  PREV_GOOD="$(cat "$DEPLOYED_REF_FILE")"
fi

if [[ -n "$TARGET_REF" ]]; then
  echo "==> Checking out '$TARGET_REF' ..."
  git checkout --detach "$TARGET_REF"
fi

CURRENT_REF="$(git rev-parse HEAD)"
CURRENT_DESC="$(git describe --tags --always 2>/dev/null || echo "$CURRENT_REF")"
echo "==> Deploying $CURRENT_DESC ($CURRENT_REF)"
if [[ -n "$PREV_GOOD" && "$PREV_GOOD" != "$CURRENT_REF" ]]; then
  echo "    (last healthy deploy was $PREV_GOOD -- rollback target if this fails)"
fi

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

# --- deploy the currently-checked-out tree --------------------------
# Factored out so both the initial deploy and a rollback run the exact same
# steps against whatever ref is checked out at the time.
deploy_current() {
  echo "==> Syncing dependencies (uv sync) ..."
  "$UV" sync

  if [[ ! -f instance/secret_key ]]; then
    echo "==> Generating instance/secret_key ..."
    mkdir -p instance
    python3 -c "import os; print(os.urandom(32).hex())" > instance/secret_key
    chmod 600 instance/secret_key
  fi

  # The mpv service runs as MEDIAPI_USER and needs the video+render groups to
  # reach the GPU/DRM devices for HDMI output. Idempotent; systemd picks up the
  # new membership when it (re)starts the service below, so no logout needed.
  if ! id -nG "${MEDIAPI_USER}" | tr ' ' '\n' | grep -qx render; then
    echo "==> Adding '${MEDIAPI_USER}' to video,render groups ..."
    sudo usermod -aG video,render "${MEDIAPI_USER}"
  fi

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

  echo "==> Installing systemd units + restarting services ..."
  install_units
  sudo systemctl enable mediapi-mpv mediapi-app >/dev/null 2>&1 || true
  sudo systemctl restart mediapi-mpv
  sudo systemctl restart mediapi-app
}

# --- health check ---------------------------------------------------
app_healthy() {
  for _ in $(seq 1 15); do
    if curl -fsS -o /dev/null "http://127.0.0.1:${MEDIAPI_PORT}/login"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# --- 2-6. deploy ----------------------------------------------------
deploy_current

# --- 7. health check + rollback ------------------------------------
echo "==> Health check on http://127.0.0.1:${MEDIAPI_PORT}/login ..."
if app_healthy; then
  echo "$CURRENT_REF" > "$DEPLOYED_REF_FILE"
  echo "==> Deploy OK. $CURRENT_DESC healthy on port ${MEDIAPI_PORT}."
  systemctl --no-pager --lines=0 status mediapi-mpv mediapi-app || true
  exit 0
fi

echo "ERROR: app did not become healthy after deploying $CURRENT_DESC." >&2
echo "--- last 30 lines of mediapi-app log: ---" >&2
sudo journalctl -u mediapi-app -n 30 --no-pager >&2 || true

if [[ -z "$PREV_GOOD" || "$PREV_GOOD" == "$CURRENT_REF" ]]; then
  echo "ERROR: no previous healthy deploy to roll back to. Left as-is." >&2
  exit 1
fi

echo "==> Rolling back to last healthy deploy $PREV_GOOD ..." >&2
git checkout --detach "$PREV_GOOD"
deploy_current
if app_healthy; then
  echo "==> Rolled back to $PREV_GOOD; it is healthy on port ${MEDIAPI_PORT}." >&2
else
  echo "ERROR: rollback to $PREV_GOOD ALSO failed its health check. Manual fix needed." >&2
fi
exit 1
