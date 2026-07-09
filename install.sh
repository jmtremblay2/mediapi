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
# First run on a fresh Pi may have no .env yet -- seed it from the example so the
# script can proceed, but make it loud: the defaults ship an insecure AP.
if [[ ! -f .env ]]; then
  echo "WARNING: .env not found -- creating it from .env.example." >&2
  echo "         Edit .env with your real AP/login passwords, then re-run." >&2
  cp .env.example .env
fi
set -a
# shellcheck disable=SC1091
source .env
set +a

MEDIAPI_USER="${MEDIAPI_USER:-$(id -un)}"
MEDIAPI_PORT="${MEDIAPI_PORT:-8080}"
UV=""  # set by bootstrap_system once uv is installed

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

# --- system bootstrap (fresh Pi: assume ONLY git is installed) -------
# Installs everything else the deploy needs so a bare Raspberry Pi OS Lite goes
# from "git clone + ./install.sh" to a working player. NetworkManager (nmcli)
# and raspi-config already ship on Pi OS. Sets the global UV path afterwards.
bootstrap_system() {
  echo "==> apt update + base packages (curl, python3) ..."
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    curl ca-certificates python3

  if ! command -v uv >/dev/null 2>&1 && [[ ! -x "$HOME/.local/bin/uv" ]]; then
    echo "==> Installing uv ..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
  if [[ ! -x "$UV" ]]; then
    echo "ERROR: uv is still not available at '$UV' after install." >&2
    exit 1
  fi
}

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

  # mpv (the player) runs as MEDIAPI_USER on KMS/DRM and needs these groups to
  # reach the GPU/DRM, audio, input and console devices. Idempotent; systemd
  # picks up the new membership when it (re)starts the service below.
  if ! id -nG "${MEDIAPI_USER}" | tr ' ' '\n' | grep -qx render; then
    echo "==> Adding '${MEDIAPI_USER}' to video,render,input,audio,tty groups ..."
    sudo usermod -aG video,render,input,audio,tty "${MEDIAPI_USER}"
  fi

  # Install mpv (the actual media player -- mediapi drives it via its IPC socket).
  if ! command -v mpv >/dev/null 2>&1; then
    echo "==> Installing mpv ..."
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends mpv
  fi

  # Ensure the media browse roots exist so browsing works and there's a place to
  # copy content into. (mkdir -p is a no-op if it's already a mount point.)
  IFS=':' read -ra _media_roots <<< "${MEDIAPI_MEDIA_ROOTS:-/localmedia}"
  for r in "${_media_roots[@]}"; do
    [[ -n "$r" && ! -d "$r" ]] || continue
    echo "==> Creating media root $r ..."
    sudo mkdir -p "$r"
    sudo chown "${MEDIAPI_USER}:${MEDIAPI_USER}" "$r"
  done

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

  echo "==> Installing systemd units ..."
  install_units
  sudo systemctl enable mediapi-mpv mediapi-app >/dev/null 2>&1 || true

  # Migration cleanup: earlier versions ran Kodi as the player. A still-running
  # Kodi keeps the GPU's DRM master and the tty1 seat, which stops mpv from ever
  # acquiring the display -- and removing a unit file does NOT stop an already
  # running process. So explicitly tear any Kodi down before starting mpv.
  if [[ -e /etc/systemd/system/mediapi-kodi.service ]] || pgrep -x kodi.bin >/dev/null 2>&1; then
    echo "==> Removing leftover Kodi player ..."
    sudo systemctl unmask mediapi-kodi.service 2>/dev/null || true
    sudo systemctl disable --now mediapi-kodi.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/mediapi-kodi.service
    sudo pkill -9 -x kodi.bin 2>/dev/null || true
    sudo pkill -9 -f kodi-standalone 2>/dev/null || true
    sudo systemctl daemon-reload
  fi

  echo "==> Starting services ..."
  sudo systemctl restart mediapi-mpv
  sudo systemctl restart mediapi-app

  # Wait for mpv's IPC socket so a first-run deploy leaves a driveable player.
  echo "==> Waiting for mpv IPC socket (${MEDIAPI_MPV_SOCKET:-/run/mediapi/mpv.sock}) ..."
  for _ in $(seq 1 30); do
    if sudo test -S "${MEDIAPI_MPV_SOCKET:-/run/mediapi/mpv.sock}"; then
      echo "    mpv is up."
      break
    fi
    sleep 1
  done
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
bootstrap_system
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
