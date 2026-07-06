import os
from datetime import timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
SECRET_KEY_PATH = os.path.join(INSTANCE_DIR, "secret_key")


def load_secret_key():
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    if not os.path.exists(SECRET_KEY_PATH):
        with open(SECRET_KEY_PATH, "w") as f:
            f.write(os.urandom(32).hex())
    with open(SECRET_KEY_PATH) as f:
        return f.read().strip()


class Config:
    SECRET_KEY = load_secret_key()
    PERMANENT_SESSION_LIFETIME = timedelta(days=3650)
    SESSION_COOKIE_SAMESITE = "Lax"

    USERNAME = "jujualexevan"
    PASSWORD = "bambas"

    MEDIA_ROOTS = ["/localmedia"]
    MPV_SOCKET = "/run/mediapi/mpv.sock"

    VIDEO_EXTENSIONS = {
        ".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".mpg", ".mpeg", ".ts", ".flv", ".wmv",
    }
