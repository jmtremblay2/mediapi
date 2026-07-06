import json
import socket
import threading


class MpvIPCError(Exception):
    """Base class for all mpv IPC errors."""


class MpvConnectionError(MpvIPCError):
    """The underlying socket is broken; caller should reconnect."""


class MpvCommandError(MpvIPCError):
    """mpv responded but rejected the command (e.g. a property that's
    legitimately unavailable while idle, like time-pos with nothing loaded).
    The connection itself is fine."""


class MpvIPCClient:
    """Minimal client for mpv's JSON IPC protocol over a unix domain socket.

    One request in flight at a time (guarded by a lock) since responses on
    the socket aren't tagged with a request id in a way we bother matching --
    we just send a command and read the next response line.
    """

    def __init__(self, socket_path):
        self.socket_path = socket_path
        self._sock = None
        self._file = None
        self._lock = threading.Lock()

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect(self.socket_path)
        self._sock = sock
        self._file = sock.makefile("rwb")

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._file = None

    @property
    def connected(self):
        return self._sock is not None

    def command(self, *args):
        """Send an mpv command, return its "data" field. Raises MpvIPCError
        on failure or if not connected -- caller (PlayerStateManager) is
        responsible for reconnect logic."""
        if not self.connected:
            raise MpvConnectionError("not connected")

        payload = json.dumps({"command": list(args)}) + "\n"
        with self._lock:
            try:
                self._file.write(payload.encode("utf-8"))
                self._file.flush()
                while True:
                    line = self._file.readline()
                    if not line:
                        raise MpvConnectionError("socket closed")
                    msg = json.loads(line)
                    # skip async event notifications, wait for the command reply
                    if "event" in msg:
                        continue
                    if msg.get("error") != "success":
                        raise MpvCommandError(msg.get("error", "unknown error"))
                    return msg.get("data")
            except (OSError, json.JSONDecodeError) as exc:
                self.close()
                raise MpvConnectionError(str(exc)) from exc

    def get_property(self, name):
        return self.command("get_property", name)

    def set_property(self, name, value):
        return self.command("set_property", name, value)
