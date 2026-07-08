#!/bin/sh
#
# mediapi X session -- the X client launched by xinit from mediapi-mpv.service.
#
# Mirrors every extra HDMI output onto the first, then runs ONE fullscreen mpv.
#
# Why an X server at all (we used to run mpv straight on DRM/KMS for fast boot):
# DRM "master" is exclusive per card. The Pi 4's two HDMI connectors live on a
# single vc4 card, so two independent mpv processes cannot both modeset -- the
# second gets "Failed to acquire DRM master: Permission denied" and shows
# nothing. A single X server is the one DRM master and drives BOTH connectors;
# `xrandr --same-as` clones the first output's framebuffer onto the others, so
# one mpv appears on every screen. See git history for the full diagnosis.
#
set -u

RUNTIME_DIR="${MEDIAPI_RUNTIME_DIR:-/run/mediapi}"
SOCKET="${MEDIAPI_MPV_SOCKET:-$RUNTIME_DIR/mpv.sock}"

# Always-on car display: never blank or DPMS-off.
xset -dpms 2>/dev/null || true
xset s off 2>/dev/null || true

connected() { xrandr --query 2>/dev/null | awk '/ connected/{print $1}'; }

# Cold-boot race: the second HDMI connector may not be probed as "connected"
# when X starts. Wait for connected outputs to appear, then let the set settle
# so a screen that lights up a beat later is still mirrored (bounded so a
# genuinely single-screen setup doesn't hang the boot).
deadline=$(( $(date +%s) + 30 ))
outs="$(connected)"
while [ -z "$outs" ] && [ "$(date +%s)" -lt "$deadline" ]; do
  sleep 1
  outs="$(connected)"
done

stable=0
settle_end=$(( $(date +%s) + 8 ))
while [ "$(date +%s)" -lt "$settle_end" ]; do
  sleep 1
  cur="$(connected)"
  if [ "$cur" != "$outs" ]; then
    outs="$cur"
    stable=0
  else
    stable=$(( stable + 1 ))
    [ "$stable" -ge 2 ] && break
  fi
done

primary=""
rest=""
for o in $outs; do
  if [ -z "$primary" ]; then primary="$o"; else rest="$rest $o"; fi
done
echo "mediapi-session: connected outputs:$outs (primary=${primary:-none})"

for o in $rest; do
  echo "mediapi-session: mirroring $o onto $primary"
  xrandr --output "$o" --same-as "$primary" || true
done

# Single mpv, on X (so one DRM master drives every mirrored connector). Carries
# audio and the app's IPC socket; the app talks only to this one socket now.
# hwdec=v4l2m2m-copy uses the Pi's hardware H.264/HEVC decoder (falls back to
# software for codecs it can't handle). Roughly a third of the CPU of software
# decoding 1080p -- important for an always-on player in a warm car. The single
# mpv has the HW decoder to itself now, so there's no contention.
exec mpv \
  --idle=yes \
  --vo=gpu-next \
  --gpu-context=x11egl \
  --hwdec=v4l2m2m-copy \
  --fullscreen \
  --no-terminal \
  --keep-open=no \
  --input-ipc-server="$SOCKET" \
  --ao=alsa
