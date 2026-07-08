hardware: raspberry pi 4 4GB ram
os: raspberry pi os lite (64 bits)
use-cas:  almost always offline. except when doing maintenance

## Git remotes

Two remotes are configured; GitHub is just a mirror of the Forgejo repo.

* `origin` (primary) — `ssh://forgejo@forgejo.jmopines.com:2222/jm/mediapi.git`
* `github` (mirror)  — `git@github.com:jmtremblay2/mediapi.git`

`origin` is set up with two push URLs (forgejo + github), so a single
`git push` mirrors to both. Fetch still comes from forgejo only.

```bash
git push origin master   # pushes to forgejo AND github
```

To re-create the dual-push setup on a fresh clone:

```bash
git remote set-url --add --push origin ssh://forgejo@forgejo.jmopines.com:2222/jm/mediapi.git
git remote set-url --add --push origin git@github.com:jmtremblay2/mediapi.git
```

# AP on the raspberry pi
* must have access point that boots up on system boot
* ssid + password come from `.env` (`MEDIAPI_AP_SSID` / `MEDIAPI_AP_PASSWORD`)
* fine if it's slow, fine if it does not boot right away (wait for other services)
* (I will connect to the AP from my phone and control what the pi plays)

## Setup (Raspberry Pi OS Bookworm / NetworkManager)

`install.sh` creates/updates the AP connection for you from the `.env` values
(see [Configuration](#configuration-env) below), so normally you don't run
these by hand. For reference, this is what it does — substitute the
`MEDIAPI_*` values from your `.env`:

```bash
# set the wifi regulatory domain (required, affects allowed channels/power)
sudo raspi-config nonint do_wifi_country "$MEDIAPI_WIFI_COUNTRY"

# create the AP connection profile on wlan0
# ipv4.method=shared makes NetworkManager run its own DHCP server (dnsmasq)
# on wlan0 for the phone to get an address from -- no internet sharing/NAT
# involved since there's no other upstream connection active
sudo nmcli connection add type wifi ifname wlan0 con-name "$MEDIAPI_AP_CONN_NAME" \
  autoconnect yes connection.autoconnect-priority 100 save yes \
  802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.ssid "$MEDIAPI_AP_SSID" \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$MEDIAPI_AP_PASSWORD" \
  ipv4.method shared

# bring it up now (it will also auto-start on every boot from here on)
sudo nmcli connection up "$MEDIAPI_AP_CONN_NAME"

# verify
nmcli connection show "$MEDIAPI_AP_CONN_NAME"
ip addr show wlan0
```

To remove it later: `sudo nmcli connection delete "$MEDIAPI_AP_CONN_NAME"`

# app to browse media
media will be stored at 
* /localmedia
* more TBD

I want an app that runs on the raspbery pi with two modes:
* playback
* control
the app should have two buttons up top that show the two modes all the time. the manager will switch back and forth between the two. the rest of the screen will be used to show the content. Must fit on one phone screen

## control mode
* shows the media stores
* the user can click on any one of them to "go" inside the folder
* at any point in time the user can chose to "play" a folder, or a file (how TBD)
* radio buttons up top of that panel (below and separately from the two global modes)
    * keep playing
    * more TBD
* has basic navigation (pretty buch go back one level at the time)

## playback mode:
* display the file being played
* displays playback info (current time, total video time), volume control
* pause/play, back and fast forward 30 seconds (or whatever you can get on the framework you use)
* plays on both HDMI mirrored if connected.

## basic auth
* login username + password come from `.env` (`MEDIAPI_USERNAME` / `MEDIAPI_PASSWORD`)
* fine to keep known devices logged in forever pretty much

easy to run ... I don't want to have to download a gazillion things

## Configuration (.env)

All per-deployment config (login, media paths, port, the AP ssid/password,
the Linux user the services run as) lives in a single `.env` file at the repo
root. It is **gitignored** — never commit it. Copy the template and fill it in:

```bash
cp .env.example .env
# then edit .env
```

`.env` is read two ways, so it stays the single source of truth:
* the Flask app parses it at startup (`mediapi/config.py`, no extra dependency),
* `install.sh` sources it to configure the AP + render the systemd units.

Keep values simple (no spaces / shell-special characters).

## Setup (mediapi app)

Implemented as a small Flask app (`mediapi/`) + a permanently-running `mpv`
player controlled over its JSON IPC socket. mpv renders video to HDMI; the
phone browser only shows metadata/controls, never the video image itself.

**Dual-HDMI mirroring.** The Pi 4's two HDMI connectors share a single vc4 DRM
card, and DRM *master* is exclusive per card — so two independent mpv processes
can't both drive a screen (the second gets `Failed to acquire DRM master`).
Instead the `mediapi-mpv` unit runs a minimal **X server** via `xinit`
(`scripts/mediapi-session.sh`): X is the one DRM master, `xrandr --same-as`
clones the first output's framebuffer onto every other connected HDMI output,
and a **single** fullscreen mpv renders into it — so the same frames appear on
both screens, decoded once. mpv carries audio and the app's only IPC socket
(`/run/mediapi/mpv.sock`); `--hwdec=v4l2m2m-copy` uses the Pi's hardware H.264/
HEVC decoder (~⅓ the CPU of software decoding), falling back to software for
codecs it can't handle. The session waits for connectors to probe as connected
before mirroring, so a cold-boot HDMI race doesn't drop it to one screen. One
screen attached → just that screen, no config needed.

### First-time install (on the Pi)

```bash
# system deps: mpv for playback (install.sh installs the minimal X server itself)
sudo apt update
sudo apt install -y mpv

# install uv (Python package/venv manager) if not already present
curl -LsSf https://astral.sh/uv/install.sh | sh   # installs to ~/.local/bin/uv

# create your .env (see Configuration above)
cp .env.example .env && $EDITOR .env

# deploy the currently-checked-out ref: syncs deps, adds the service user to
# the video+render groups (GPU/DRM access for HDMI), configures the AP,
# installs+starts the services. Run as your normal user -- NOT with sudo, or
# the services get rendered to run as root.
./install.sh

# verify
systemctl status mediapi-mpv mediapi-app
ls -l /run/mediapi/mpv.sock
```

Then, from a phone connected to the AP (`MEDIAPI_AP_SSID`), browse to
`http://<pi-ap-ip>:8080/` (the AP's gateway address, typically `10.42.0.1` —
confirm with `ip addr show wlan0` on the pi) and log in with the
`MEDIAPI_USERNAME` / `MEDIAPI_PASSWORD` from your `.env`.

### Deploying updates

`install.sh` deploys **whatever git ref is currently checked out** — it does
*not* pull on its own, so you control exactly which version goes live (pin a
tag for a reproducible car deployment). It syncs deps, re-applies the AP config,
reinstalls the systemd units (rendered from the `.template` files using your
`.env`), restarts the services, and health-checks the app. The same script is
used for the first install and every update; each run is idempotent and cleans
up the previous deployment in place.

After a healthy deploy it records the deployed commit in `instance/deployed_ref`.
If a new deploy fails its health check, it **rolls back to that last-known-good
ref automatically** and re-checks.

```bash
git fetch --tags
git checkout v1.2.0     # pin the version you want
./install.sh

# or let the script do the checkout for you:
./install.sh v1.2.0
```

Passing a ref checks it out with `git checkout --detach` before deploying; with
no argument it deploys the current checkout as-is (branch or tag).

It **refuses to run while the read-only overlay is active** (changes would
vanish on reboot) and prints the disable/re-enable steps. So the update loop is:

```bash
sudo raspi-config nonint do_overlayfs 1 && sudo reboot   # disable overlay
# ... after reboot:
cd ~/mediapi && ./install.sh v1.2.0
sudo raspi-config nonint do_overlayfs 0 && sudo reboot   # re-enable overlay
```

Notes / things to double check on the actual hardware (couldn't be verified
from a dev machine):
* `dtoverlay=vc4-kms-v3d` should already be set in `/boot/firmware/config.txt`
  on current Bookworm Pi4 images (needed for DRM output) — worth a quick check.
* Mirroring clones every HDMI output that reads `connected` at session start
  onto the first. The session waits for connectors to probe (cold-boot race),
  then runs `xrandr --same-as`. See what it did with
  `sudo journalctl -u mediapi-mpv | grep mediapi-session`; inspect the outputs
  live with `DISPLAY=:0 xrandr` (as the service user). Both screens should share
  a common resolution; if they differ, `xrandr` clones at the first output's
  mode. Confirm the one socket: `ls -l /run/mediapi/mpv.sock`.
* Non-root X requires `/etc/X11/Xwrapper.config` with `allowed_users=anybody`
  (install.sh writes it). If the service crash-loops with `Could not create
  server lock file: /tmp/.X0-lock`, a previous X died uncleanly — remove
  `/tmp/.X0-lock` (and `/tmp/.X11-unix/X0`) and restart. The X log is at
  `~/.local/share/xorg/Xorg.0.log`.
* If audio doesn't come out of the TV, check `aplay -l` for the HDMI ALSA
  device name (usually `vc4-hdmi`) and add e.g.
  `--audio-device=alsa/plughw:CARD=vc4hdmi0,DEV=0` to the mpv unit template, or
  run `sudo raspi-config nonint do_audio 2` to force HDMI as the default output.

To stop/remove: `sudo systemctl disable --now mediapi-mpv mediapi-app`
