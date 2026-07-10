#!/usr/bin/env bash
#
# mediapi-watchdog -- reboot the Pi if the mpv player wedges.
#
# Guards against the failure seen 2026-07-09: a kernel oops left the player
# stuck in uninterruptible (D) state while the rest of the box -- systemd, SSH,
# the Flask app -- stayed alive and responsive. A plain systemd/hardware
# watchdog only fires on a TOTAL system hang, so it would NOT have caught that
# partial wedge. Instead we ping mpv over its JSON IPC socket; if mpv stays
# unresponsive for ~3 minutes we force a reboot, turning a dead-all-night wedge
# into a ~30s auto-recovery.
#
# Runs as root (needs to force a reboot). Installed + enabled by install.sh.
set -u

SOCK="${MEDIAPI_MPV_SOCKET:-/run/mediapi/mpv.sock}"
INTERVAL="${MEDIAPI_WATCHDOG_INTERVAL:-30}"        # seconds between checks
FAILS_TO_REBOOT="${MEDIAPI_WATCHDOG_FAILS:-6}"     # consecutive fails -> reboot (~3 min)
PING_TIMEOUT="${MEDIAPI_WATCHDOG_PING_TIMEOUT:-6}" # per-check hard timeout
DRYRUN="${MEDIAPI_WATCHDOG_DRYRUN:-}"              # non-empty: log instead of rebooting

# Return 0 iff mpv replies to a JSON IPC command within PING_TIMEOUT. A player
# wedged in D-state accepts the socket connect but never replies, so the recv
# blocks and `timeout` trips it -- exactly the case we want to catch.
ping_mpv() {
  timeout "$PING_TIMEOUT" python3 - "$SOCK" <<'PY'
import socket, json, sys
try:
    s = socket.socket(socket.AF_UNIX); s.settimeout(4); s.connect(sys.argv[1])
    s.sendall(b'{"command":["get_property","mpv-version"],"request_id":1}\n')
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            sys.exit(1)
        buf += chunk
    for line in buf.decode(errors="replace").splitlines():
        m = json.loads(line)
        if m.get("request_id") == 1:
            sys.exit(0 if m.get("error") == "success" else 1)
    sys.exit(1)
except Exception:
    sys.exit(1)
PY
}

do_reboot() {
  if [ -n "$DRYRUN" ]; then
    echo "mediapi-watchdog: DRYRUN -- would reboot now" >&2
    return
  fi
  # Best-effort clean reboot first; fall back to SysRq, which reboots at the
  # kernel level even when userspace is wedged in D-state (the case we guard).
  sync &
  systemctl reboot -ff &
  sleep 12
  echo 1 > /proc/sys/kernel/sysrq 2>/dev/null || true
  echo b > /proc/sysrq-trigger 2>/dev/null || true
}

echo "mediapi-watchdog: watching $SOCK (reboot after ${FAILS_TO_REBOOT}x${INTERVAL}s unresponsive)" >&2
fails=0
while true; do
  if ping_mpv; then
    fails=0
  else
    fails=$((fails + 1))
    echo "mediapi-watchdog: mpv ping FAILED ($fails/$FAILS_TO_REBOOT)" >&2
    if [ "$fails" -ge "$FAILS_TO_REBOOT" ]; then
      echo "mediapi-watchdog: mpv unresponsive ~$((INTERVAL * FAILS_TO_REBOOT))s -- rebooting" >&2
      do_reboot
      fails=0
    fi
  fi
  sleep "$INTERVAL"
done
