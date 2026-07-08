"""Minimal Kodi JSON-RPC client over HTTP.

Kodi does the actual playback (hardware-decoded, straight on KMS, like
LibreELEC); mediapi is just the phone-facing UI that drives it. We talk to
Kodi's web server JSON-RPC endpoint (Settings > Services > Control > "Allow
remote control via HTTP"). Uses only the stdlib so the app keeps no extra deps.
"""

import base64
import json
import socket
import urllib.error
import urllib.request


class KodiError(Exception):
    """Base class for all Kodi control errors."""


class KodiConnectionError(KodiError):
    """Couldn't reach Kodi's JSON-RPC endpoint (down, wrong port, auth)."""


class KodiCommandError(KodiError):
    """Kodi received the request but returned a JSON-RPC error."""


class KodiClient:
    def __init__(self, url, username=None, password=None, timeout=4):
        self.url = url
        self.timeout = timeout
        self._auth = None
        if username:
            token = base64.b64encode(f"{username}:{password or ''}".encode()).decode()
            self._auth = "Basic " + token
        self._id = 0

    def call(self, method, **params):
        """Invoke a JSON-RPC method and return its "result". Raises
        KodiConnectionError if Kodi is unreachable, KodiCommandError if Kodi
        rejects the request."""
        self._id += 1
        body = json.dumps(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        ).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=body, headers={"Content-Type": "application/json"}
        )
        if self._auth:
            req.add_header("Authorization", self._auth)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                msg = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, socket.timeout) as exc:
            raise KodiConnectionError(str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise KodiConnectionError(f"bad JSON from kodi: {exc}") from exc
        if isinstance(msg, dict) and "error" in msg:
            raise KodiCommandError(str(msg["error"]))
        return msg.get("result") if isinstance(msg, dict) else None


def seconds_from_time(t):
    """Kodi returns times as {hours, minutes, seconds, milliseconds}; flatten
    to float seconds. Returns None for a missing/empty value."""
    if not isinstance(t, dict):
        return None
    return (
        t.get("hours", 0) * 3600
        + t.get("minutes", 0) * 60
        + t.get("seconds", 0)
        + t.get("milliseconds", 0) / 1000.0
    )
