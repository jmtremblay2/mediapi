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

Playback is done by **Kodi** (`mediapi-kodi` unit), running standalone on
GBM/KMS straight on the hardware — the same smooth, hardware-decoded path as
LibreELEC, no desktop. mediapi itself is a small Flask app (`mediapi/`) that is
just the phone-facing **remote**: it browses the media folders and drives Kodi
over its **JSON-RPC HTTP API** (`Player.Open`, `Player.PlayPause`,
`Player.Seek`, `Application.SetVolume`, `Player.GetProperties`). The phone
browser only shows metadata/controls, never the video image itself.

Kodi handles decode, HDMI audio and display; mediapi keeps a background poll of
Kodi's player state (position/duration/pause/volume) and drives "keep playing"
auto-advance through a folder. `install.sh` installs Kodi, autostarts it, and
enables its web server (JSON-RPC) headlessly by seeding `guisettings.xml` (see
`scripts/configure-kodi.py`). Set the Kodi port/credentials in `.env`
(`MEDIAPI_KODI_*`); keep the port different from `MEDIAPI_PORT`.

> **Dual HDMI:** the Pi 4 can't cleanly mirror both HDMI ports in software
> (DRM master is exclusive per card; the X-mirror path can't keep up). Drive
> both car screens from one HDMI port through an external powered HDMI splitter.

### First-time install (on the Pi)

```bash
# install uv (Python package/venv manager) if not already present
curl -LsSf https://astral.sh/uv/install.sh | sh   # installs to ~/.local/bin/uv

# create your .env (see Configuration above)
cp .env.example .env && $EDITOR .env

# deploy the currently-checked-out ref: installs Kodi + Python deps, enables
# Kodi's JSON-RPC web server, configures the AP, installs+starts the services.
# Run as your normal user -- NOT with sudo, or the services get rendered to run
# as root.
./install.sh

# verify
systemctl status mediapi-kodi mediapi-app
curl -s "http://kodi:$(grep KODI_PASSWORD .env|cut -d= -f2)@127.0.0.1:8090/jsonrpc" \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"JSONRPC.Ping"}'   # -> {"result":"pong"}
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

Notes / things to double check on the actual hardware:
* `dtoverlay=vc4-kms-v3d` should already be set in `/boot/firmware/config.txt`
  on current Pi4 images (needed for KMS output) — worth a quick check.
* Kodi runs standalone on GBM as the `mediapi-kodi` service (on `tty1`). If the
  screen stays on the console, check `sudo journalctl -u mediapi-kodi`; Kodi's
  own log is at `~/.kodi/temp/kodi.log`. It needs the service user in the
  `video render input audio tty` groups (install.sh adds them).
* If the web API is unreachable (`Connection refused` on
  `:${MEDIAPI_KODI_PORT}`), the web server didn't get enabled. Kodi rewrites
  `guisettings.xml` on exit, so re-run `install.sh` (it stops Kodi, seeds the
  setting via `scripts/configure-kodi.py`, and restarts), or toggle
  Settings → Services → Control → *Allow remote control via HTTP* in the Kodi
  GUI once. Verify with `JSONRPC.Ping` (see install snippet above).
* Kodi handles HDMI audio itself (Settings → System → Audio). If there's no
  sound, set the audio output device to the HDMI sink there.

To stop/remove: `sudo systemctl disable --now mediapi-mpv mediapi-app`
