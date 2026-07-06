hardware: raspberry pi 4 4GB ram
os: raspberry pi os lite (64 bits)
use-cas:  almost always offline. except when doing maintenance

# AP on the raspberry pi
* must have access point that boots up on system boot
* ssid: mediapipi, pw: mediapipi
* fine if it's slow, fine if it does not boot right away (wait for other services)
* (I will connect to the AP from my phone and control what the pi plays)

## Setup (Raspberry Pi OS Bookworm / NetworkManager)

```bash
# set the wifi regulatory domain (required, affects allowed channels/power)
sudo raspi-config nonint do_wifi_country US

# create the AP connection profile on wlan0
# ipv4.method=shared makes NetworkManager run its own DHCP server (dnsmasq)
# on wlan0 for the phone to get an address from -- no internet sharing/NAT
# involved since there's no other upstream connection active
sudo nmcli connection add type wifi ifname wlan0 con-name mediapi-ap \
  autoconnect yes connection.autoconnect-priority 100 save yes \
  802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.ssid mediapipi \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk mediapipi \
  ipv4.method shared

# bring it up now (it will also auto-start on every boot from here on)
sudo nmcli connection up mediapi-ap

# verify
nmcli connection show mediapi-ap
ip addr show wlan0
```

To remove it later: `sudo nmcli connection delete mediapi-ap`

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
user: jujualexevan
pw: bambas
fine to keep known devices logged in forever pretty much

easy to run ... I don't want to have to download a gazillion things

## Setup (mediapi app)

Implemented as a small Flask app (`mediapi/`) + a permanently-running `mpv`
player controlled over its JSON IPC socket. mpv renders video to HDMI
directly (DRM/KMS, no desktop needed); the phone browser only shows
metadata/controls, never the video image itself.

```bash
# system deps: mpv for HDMI/DRM playback
sudo apt update
sudo apt install -y mpv

# mpv needs access to the GPU/DRM devices to render to HDMI
sudo usermod -aG video,render jm
# log out/in (or reboot) for the new group membership to take effect

# install uv (Python package/venv manager) if not already present
curl -LsSf https://astral.sh/uv/install.sh | sh
# installs to ~/.local/bin/uv -- confirm with `which uv`; if it differs,
# update the ExecStart path in systemd/mediapi-app.service to match

cd /home/jm/mediapi
uv sync   # creates .venv and installs dependencies from pyproject.toml

# install the two systemd services (mpv player + the web app)
sudo cp systemd/mediapi-mpv.service systemd/mediapi-app.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mediapi-mpv mediapi-app

# verify
systemctl status mediapi-mpv mediapi-app
ls -l /run/mediapi/mpv.sock
```

Then, from a phone connected to the `mediapipi` AP, browse to
`http://<pi-ap-ip>:8080/` (the AP's gateway address, typically `10.42.0.1` —
confirm with `ip addr show wlan0` on the pi) and log in with the
`jujualexevan` / `bambas` credentials above.

Notes / things to double check on the actual hardware (couldn't be verified
from a dev machine):
* `dtoverlay=vc4-kms-v3d` should already be set in `/boot/firmware/config.txt`
  on current Bookworm Pi4 images (needed for DRM output) — worth a quick check.
* If HDMI isn't picked automatically, `mpv --drm-connector=help` (with a
  display attached) lists connectors to pin one explicitly via the
  `ExecStart` line in `systemd/mediapi-mpv.service`.
* If audio doesn't come out of the TV, check `aplay -l` for the HDMI ALSA
  device name (usually `vc4-hdmi`) and add e.g.
  `--audio-device=alsa/plughw:CARD=vc4hdmi0,DEV=0` to the mpv unit, or run
  `sudo raspi-config nonint do_audio 2` to force HDMI as the default output.

To stop/remove: `sudo systemctl disable --now mediapi-mpv mediapi-app`
