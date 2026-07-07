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
player controlled over its JSON IPC socket. mpv renders video to HDMI
directly (DRM/KMS, no desktop needed); the phone browser only shows
metadata/controls, never the video image itself.

**Dual-HDMI mirroring.** A single mpv on DRM can only drive one connector, and
the Pi's `vc4-kms` driver can't clone two HDMI outputs onto one framebuffer —
so mirroring is done by running *one mpv per connected screen*.
`scripts/start-mpv.py` (launched by the `mediapi-mpv` unit) enumerates the
connected HDMI connectors and starts a **primary** mpv (audio + the app's IPC
socket, `mpv.sock`) plus a **mirror** mpv per extra screen (video-only, on
`mpv-mirror-<connector>.sock`). The app plays/pauses/seeks the primary and
echoes those to the mirrors best-effort, re-syncing on every video. One screen
→ same as before; a dead mirror just leaves that screen dark, never the audio
screen. Because each screen runs its own mpv, the same file decodes once per
screen (`--hwdec=auto-safe` falls back to software if the HW decoder is busy).

### First-time install (on the Pi)

```bash
# system deps: mpv for HDMI/DRM playback
sudo apt update
sudo apt install -y mpv

# mpv needs access to the GPU/DRM devices to render to HDMI
# (use the MEDIAPI_USER from your .env)
sudo usermod -aG video,render "$USER"
# log out/in (or reboot) for the new group membership to take effect

# install uv (Python package/venv manager) if not already present
curl -LsSf https://astral.sh/uv/install.sh | sh   # installs to ~/.local/bin/uv

# create your .env (see Configuration above)
cp .env.example .env && $EDITOR .env

# deploy the currently-checked-out ref: syncs deps, configures the AP,
# installs+starts the services
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
* Mirroring picks up whatever HDMI connectors read `connected` under
  `/sys/class/drm/card*-HDMI-*/status` at service start — so plug in both
  screens **before** `mediapi-mpv` starts (or `sudo systemctl restart
  mediapi-mpv` after). Check what it launched with
  `sudo journalctl -u mediapi-mpv | grep start-mpv` and confirm both sockets:
  `ls -l /run/mediapi/mpv*.sock`. `mpv --drm-connector=help` (with a display
  attached) lists the connector names if the sysfs guess is ever wrong.
* If audio doesn't come out of the TV, check `aplay -l` for the HDMI ALSA
  device name (usually `vc4-hdmi`) and add e.g.
  `--audio-device=alsa/plughw:CARD=vc4hdmi0,DEV=0` to the mpv unit template, or
  run `sudo raspi-config nonint do_audio 2` to force HDMI as the default output.

To stop/remove: `sudo systemctl disable --now mediapi-mpv mediapi-app`
