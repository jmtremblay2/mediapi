import logging
import os
from datetime import timedelta

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
SECRET_KEY_PATH = os.path.join(INSTANCE_DIR, "secret_key")
DOTENV_PATH = os.path.join(BASE_DIR, ".env")


def load_dotenv(path=DOTENV_PATH):
    """Minimal .env loader (no dependency). Populates os.environ for any key
    not already set, so real environment variables always win over the file.
    Works for both `uv run run.py` and the systemd-launched service."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, val)


def require(name):
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"{name} is not set -- copy .env.example to .env and fill it in"
        )
    return val


def load_secret_key():
    """Read the persisted session-signing key, generating it if missing.

    On a read-only overlay filesystem the write will fail; in that case we
    fall back to an ephemeral in-memory key so the app still starts (sessions
    just won't survive a restart). install.sh generates this file while the
    card is writable, so in normal operation the write path isn't hit."""
    if os.path.exists(SECRET_KEY_PATH):
        with open(SECRET_KEY_PATH) as f:
            return f.read().strip()

    key = os.urandom(32).hex()
    try:
        os.makedirs(INSTANCE_DIR, exist_ok=True)
        with open(SECRET_KEY_PATH, "w") as f:
            f.write(key)
    except OSError:
        log.warning(
            "could not persist secret key (read-only filesystem?) -- using an "
            "ephemeral key; logins won't survive an app restart"
        )
    return key


load_dotenv()


class Config:
    SECRET_KEY = load_secret_key()
    PERMANENT_SESSION_LIFETIME = timedelta(days=3650)
    SESSION_COOKIE_SAMESITE = "Lax"

    USERNAME = require("MEDIAPI_USERNAME")
    PASSWORD = require("MEDIAPI_PASSWORD")

    MEDIA_ROOTS = [
        p for p in os.environ.get("MEDIAPI_MEDIA_ROOTS", "/localmedia").split(":") if p
    ]
    # mpv does the playback; we drive it over its JSON IPC Unix socket. mpv runs
    # as its own systemd service with --input-ipc-server pointed at this path
    # (install.sh configures this).
    MPV_SOCKET = os.environ.get("MEDIAPI_MPV_SOCKET", "/run/mediapi/mpv.sock")
    PORT = int(os.environ.get("MEDIAPI_PORT", "8080"))

    VIDEO_EXTENSIONS = {
        ".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".mpg", ".mpeg", ".ts", ".flv", ".wmv",
    }
