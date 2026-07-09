"""Minimal mpv JSON IPC client over a Unix socket.

mpv does the actual playback (hardware-decoded, straight on KMS/DRM); mediapi is
just the phone-facing UI that drives it. mpv runs as its own systemd service
with `--idle --input-ipc-server=<socket>`, so it holds the playlist and keeps
playing on its own even if this app, the phone, or the WiFi drop out -- we only
send it commands and read its state. Uses only the stdlib so the app keeps no
extra deps.

Protocol: connect to the Unix socket, write one `{"command": [...]}` JSON line,
and read newline-delimited JSON back. mpv also emits async `{"event": ...}`
lines on the same stream; we tag each request with a request_id and skip
anything that isn't the matching reply. A fresh connection per command keeps
this stateless (mpv accepts many concurrent IPC connections), mirroring how the
old Kodi client worked.
"""

import json
import socket


class MpvError(Exception):
    """Base class for all mpv control errors."""


class MpvConnectionError(MpvError):
    """Couldn't reach mpv's IPC socket (not running, wrong path, no perms)."""


class MpvCommandError(MpvError):
    """mpv received the command but returned an error (e.g. bad property)."""


class MpvClient:
    def __init__(self, socket_path, timeout=4):
        self.socket_path = socket_path
        self.timeout = timeout
        self._id = 0

    def command(self, *args):
        """Send one mpv IPC command and return its `data` field. Raises
        MpvConnectionError if mpv is unreachable, MpvCommandError if mpv rejects
        the command."""
        self._id += 1
        req_id = self._id
        payload = json.dumps({"command": list(args), "request_id": req_id}) + "\n"

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect(self.socket_path)
                sock.sendall(payload.encode("utf-8"))
                reply = self._read_reply(sock, req_id)
        except (OSError, socket.timeout) as exc:
            raise MpvConnectionError(str(exc)) from exc

        if reply.get("error") != "success":
            raise MpvCommandError(f"{args[0]}: {reply.get('error')}")
        return reply.get("data")

    def _read_reply(self, sock, req_id):
        """Read newline-delimited JSON from mpv until we see the reply whose
        request_id matches ours, skipping interleaved async event lines."""
        with sock.makefile("r", encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise MpvConnectionError(f"bad JSON from mpv: {exc}") from exc
                if msg.get("request_id") == req_id and "error" in msg:
                    return msg
        raise MpvConnectionError("mpv closed the connection without a reply")

    def get_property(self, name):
        """Return a property's value, or raise MpvCommandError if mpv can't
        supply it (e.g. `time-pos` while idle -- 'property unavailable')."""
        return self.command("get_property", name)

    def try_get(self, name, default=None):
        """Like get_property but returns `default` when the property is simply
        unavailable (idle player), so callers don't special-case idle state.
        A real connection failure still propagates as MpvConnectionError."""
        try:
            return self.command("get_property", name)
        except MpvCommandError:
            return default

    def set_property(self, name, value):
        return self.command("set_property", name, value)
