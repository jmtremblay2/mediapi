import glob
import logging
import os
import threading
import time

from .media import list_video_files
from .mpv_ipc import MpvCommandError, MpvConnectionError, MpvIPCClient, MpvIPCError

log = logging.getLogger(__name__)

POLL_INTERVAL = 1.0
RECONNECT_INTERVAL = 2.0


class PlayerStateManager:
    """Owns the single connection to mpv's IPC socket. A background thread
    polls mpv for playback state and drives "keep playing" auto-advance;
    Flask request handlers only ever read the cached snapshot or send a
    command through this class -- they never touch the socket directly."""

    def __init__(self, socket_path, video_extensions, mirror_glob=None):
        self.socket_path = socket_path
        self.video_extensions = video_extensions
        # Sockets of any per-screen "mirror" mpv instances (see start-mpv.py).
        # We only ever push playback commands to these best-effort; the primary
        # socket above is the sole source of state and the one that carries
        # audio, so a missing/broken mirror just means one screen is dark.
        self._mirror_glob = mirror_glob
        self._mirror_clients = {}
        self._mirror_lock = threading.Lock()

        self._client = MpvIPCClient(socket_path)
        self._lock = threading.Lock()
        self._stop = threading.Event()

        self._state = {
            "connected": False,
            "filename": None,
            "position": None,
            "duration": None,
            "paused": None,
            "volume": None,
            "keep_playing": False,
        }

        self._queue_folder = None
        self._queue_files = []
        self._queue_index = -1
        self._prev_idle = True

    def start(self):
        thread = threading.Thread(target=self._run, name="player-state-manager", daemon=True)
        thread.start()

    def stop(self):
        self._stop.set()

    # -- background loop -------------------------------------------------

    def _run(self):
        while not self._stop.is_set():
            if not self._client.connected:
                try:
                    self._client.connect()
                    log.info("connected to mpv socket at %s", self.socket_path)
                except OSError:
                    self._set_disconnected()
                    time.sleep(RECONNECT_INTERVAL)
                    continue

            try:
                self._poll_once()
            except MpvConnectionError as exc:
                log.warning("lost connection to mpv: %s", exc)
                self._set_disconnected()
                time.sleep(RECONNECT_INTERVAL)
                continue

            time.sleep(POLL_INTERVAL)

    def _set_disconnected(self):
        with self._lock:
            self._state["connected"] = False

    def _get_property_safe(self, name):
        """Like client.get_property, but treats "property unavailable"
        (normal for time-pos/duration/filename while mpv is idle) as None
        instead of a fatal error -- only a real MpvConnectionError should
        tear down the connection."""
        try:
            return self._client.get_property(name)
        except MpvCommandError:
            return None

    def _poll_once(self):
        idle = bool(self._get_property_safe("idle-active"))
        filename = self._get_property_safe("filename")
        position = self._get_property_safe("time-pos")
        duration = self._get_property_safe("duration")
        paused = self._get_property_safe("pause")
        volume = self._get_property_safe("volume")

        if idle and not self._prev_idle:
            self._maybe_advance()
            idle = self._get_property_safe("idle-active")

        self._prev_idle = bool(idle)

        with self._lock:
            self._state.update({
                "connected": True,
                "filename": filename,
                "position": position,
                "duration": duration,
                "paused": paused,
                "volume": volume,
            })

    def _maybe_advance(self):
        """Called from the poll loop when mpv just went idle. If keep-playing
        is on and there's a next file in the current folder queue, load it."""
        with self._lock:
            keep_playing = self._state["keep_playing"]
            has_next = (
                self._queue_files
                and 0 <= self._queue_index + 1 < len(self._queue_files)
            )
            if keep_playing and has_next:
                self._queue_index += 1
                next_file = self._queue_files[self._queue_index]
            else:
                next_file = None

        if next_file:
            try:
                self._client.command("loadfile", next_file, "replace")
            except MpvIPCError as exc:
                log.warning("auto-advance loadfile failed: %s", exc)
            self._broadcast("loadfile", next_file, "replace")

    # -- public read API ---------------------------------------------------

    def get_status(self):
        with self._lock:
            state = dict(self._state)
            state["keep_playing"] = self._state["keep_playing"]
        return state

    # -- mirror screens (best-effort) --------------------------------------

    def _broadcast(self, *command_args):
        """Echo a playback command to every mirror mpv, best-effort. Never
        raises: a mirror that's absent, mid-restart, or wedged must not affect
        the primary screen or the HTTP response. Mirrors are re-synced on every
        loadfile, so a command missed here self-heals at the next video."""
        if not self._mirror_glob:
            return
        with self._mirror_lock:
            paths = set(glob.glob(self._mirror_glob))
            # Drop clients whose socket disappeared (display unplugged).
            for gone in set(self._mirror_clients) - paths:
                self._mirror_clients.pop(gone).close()
            for path in paths:
                client = self._mirror_clients.get(path)
                if client is None:
                    client = self._mirror_clients[path] = MpvIPCClient(path)
                try:
                    if not client.connected:
                        client.connect()
                    client.command(*command_args)
                except (OSError, MpvIPCError) as exc:
                    client.close()
                    log.debug("mirror %s command failed: %s", path, exc)

    # -- public command API (called from Flask routes) ---------------------

    def play_file(self, path):
        self._client.command("loadfile", path, "replace")
        self._broadcast("loadfile", path, "replace")
        # Build the auto-advance queue from the containing folder, positioned
        # at the file just started -- so "keep playing" continues through the
        # rest of the folder even when you start from the middle.
        folder = os.path.dirname(path)
        files = list_video_files(folder, self.video_extensions)
        try:
            index = files.index(path)
        except ValueError:
            # played file isn't in the folder listing (unusual) -- queue just it
            files = [path]
            index = 0
        with self._lock:
            self._queue_folder = folder
            self._queue_files = files
            self._queue_index = index

    def play_folder(self, folder_path):
        files = list_video_files(folder_path, self.video_extensions)
        if not files:
            raise ValueError(f"no video files in {folder_path}")
        self._client.command("loadfile", files[0], "replace")
        self._broadcast("loadfile", files[0], "replace")
        with self._lock:
            self._queue_folder = folder_path
            self._queue_files = files
            self._queue_index = 0

    def playpause(self):
        self._client.command("cycle", "pause")
        self._broadcast("cycle", "pause")

    def seek(self, offset_seconds):
        self._client.command("seek", offset_seconds, "relative")
        self._broadcast("seek", offset_seconds, "relative")

    def set_volume(self, value):
        value = max(0, min(100, value))
        self._client.set_property("volume", value)

    def set_keep_playing(self, enabled):
        with self._lock:
            self._state["keep_playing"] = bool(enabled)
