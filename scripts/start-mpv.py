#!/usr/bin/env python3
"""Launch one mpv per connected HDMI output so playback mirrors across every
attached display.

mpv's DRM output can only ever drive a single connector, and the Pi's vc4-kms
driver can't clone two HDMI connectors onto one framebuffer -- so "mirror on
both screens" means running one mpv per connector. This script enumerates the
connected connectors at start and launches:

  * a PRIMARY mpv on the first connected connector -- owns audio and the IPC
    socket the Flask app talks to (/run/mediapi/mpv.sock);
  * a MIRROR mpv on each additional connector -- video only (--ao=null), each
    on its own IPC socket (/run/mediapi/mpv-mirror-<connector>.sock) so the app
    can echo loadfile/pause/seek to it best-effort.

Design choices, all in service of "must work unattended in a car":

  * If no connector can be detected (unexpected sysfs naming, etc.) it falls
    back to a single auto-picking mpv -- i.e. exactly the old behavior, never
    worse.
  * It supervises the children: if any mpv exits, it tears the rest down and
    exits non-zero so systemd restarts the whole unit (which re-enumerates
    displays -- e.g. a screen powered on since last start).
  * The mirror is strictly best-effort; the primary is what carries audio and
    control, so a dead mirror degrades to single-screen playback.

Runs as a plain stdlib script (no venv needed) so it can start early at boot.
"""

import glob
import os
import re
import signal
import subprocess
import sys
import time

RUNTIME_DIR = os.environ.get("MEDIAPI_RUNTIME_DIR", "/run/mediapi")
PRIMARY_SOCKET = os.environ.get("MEDIAPI_MPV_SOCKET", os.path.join(RUNTIME_DIR, "mpv.sock"))

# Flags shared by every instance. Kept in sync with what the mpv systemd unit
# used to pass directly.
COMMON_ARGS = [
    "/usr/bin/mpv",
    "--idle=yes",
    "--vo=gpu-next",
    "--gpu-context=drm",
    "--hwdec=auto-safe",  # falls back to software if the HW decoder is busy
    "--fullscreen",
    "--no-terminal",
    "--keep-open=no",
]


def connected_hdmi_outputs():
    """Return [(card_device, connector_name), ...] for every connected HDMI
    connector, e.g. [("/dev/dri/card1", "HDMI-A-1"), ...], sorted by connector
    name so the assignment of primary/mirror is stable across restarts."""
    outputs = []
    for status_path in sorted(glob.glob("/sys/class/drm/card*-HDMI-*/status")):
        try:
            with open(status_path) as f:
                status = f.read().strip()
        except OSError:
            continue
        if status != "connected":
            continue
        # .../card1-HDMI-A-1/status -> card="card1", connector="HDMI-A-1"
        m = re.match(r"(card\d+)-(HDMI-\S+)", os.path.basename(os.path.dirname(status_path)))
        if not m:
            continue
        card, connector = m.group(1), m.group(2)
        outputs.append((f"/dev/dri/{card}", connector))
    return outputs


def mirror_socket_path(connector):
    return os.path.join(os.path.dirname(PRIMARY_SOCKET), f"mpv-mirror-{connector}.sock")


def build_commands():
    """Build the list of mpv argv lists to launch."""
    outputs = connected_hdmi_outputs()

    if not outputs:
        # Couldn't identify any connector -- fall back to a single auto-picking
        # mpv (the old behavior). Never worse than before.
        print("start-mpv: no HDMI connector detected, launching single auto mpv", flush=True)
        return [COMMON_ARGS + [f"--input-ipc-server={PRIMARY_SOCKET}", "--ao=alsa"]]

    commands = []
    primary_card, primary_connector = outputs[0]
    print(f"start-mpv: primary on {primary_connector} ({primary_card})", flush=True)
    commands.append(
        COMMON_ARGS
        + [
            f"--drm-device={primary_card}",
            f"--drm-connector={primary_connector}",
            f"--input-ipc-server={PRIMARY_SOCKET}",
            "--ao=alsa",
        ]
    )

    for card, connector in outputs[1:]:
        sock = mirror_socket_path(connector)
        print(f"start-mpv: mirror on {connector} ({card}) -> {sock}", flush=True)
        commands.append(
            COMMON_ARGS
            + [
                f"--drm-device={card}",
                f"--drm-connector={connector}",
                f"--input-ipc-server={sock}",
                "--ao=null",
                "--mute=yes",
            ]
        )
    return commands


def main():
    # Clean up any stale mirror sockets from a previous run so the app doesn't
    # try to talk to a connector that's no longer attached.
    for stale in glob.glob(os.path.join(os.path.dirname(PRIMARY_SOCKET), "mpv-mirror-*.sock")):
        try:
            os.unlink(stale)
        except OSError:
            pass

    procs = [subprocess.Popen(cmd) for cmd in build_commands()]

    stopping = {"flag": False}

    def kill_children():
        for p in procs:
            if p.poll() is None:
                p.terminate()

    def on_signal(*_):
        # Told to stop (systemctl stop / Ctrl-C): kill the children and let
        # main exit 0 so systemd treats it as a clean stop, not a crash.
        stopping["flag"] = True
        kill_children()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    # Poll the children (via subprocess so its bookkeeping stays consistent --
    # don't os.wait() out from under it). The first unexpected exit means a
    # screen dropped its mpv: tear the rest down and exit non-zero so systemd
    # restarts the unit, re-enumerating displays in the process. time.sleep is
    # interrupted by SIGTERM, so a stop is handled promptly too.
    while not stopping["flag"]:
        dead = next((p for p in procs if p.poll() is not None), None)
        if dead is not None:
            print(f"start-mpv: an mpv exited unexpectedly (pid {dead.pid}), restarting unit", flush=True)
            kill_children()
            break
        time.sleep(1)

    # Reap whatever's left.
    for p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()

    sys.exit(0 if stopping["flag"] else 1)


if __name__ == "__main__":
    main()
