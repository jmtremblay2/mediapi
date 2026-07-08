import logging
import os
import threading
import time

from .kodi import KodiClient, KodiConnectionError, KodiError, seconds_from_time
from .media import list_video_files

log = logging.getLogger(__name__)

POLL_INTERVAL = 1.0
RECONNECT_INTERVAL = 2.0


class PlayerStateManager:
    """Owns control of Kodi. A background thread polls Kodi's JSON-RPC for
    playback state and drives "keep playing" auto-advance; Flask request
    handlers only ever read the cached snapshot or send a command through this
    class. Kodi itself does the decoding/output -- mediapi is just the remote.
    """

    def __init__(self, kodi_client, video_extensions):
        self._kodi = kodi_client
        self.video_extensions = video_extensions

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

        # Auto-advance queue (the containing folder), same model as before.
        self._queue_folder = None
        self._queue_files = []
        self._queue_index = -1
        self._prev_playing = False

    def start(self):
        thread = threading.Thread(target=self._run, name="player-state-manager", daemon=True)
        thread.start()

    def stop(self):
        self._stop.set()

    # -- background loop -------------------------------------------------

    def _run(self):
        while not self._stop.is_set():
            try:
                self._poll_once()
            except KodiConnectionError as exc:
                log.debug("kodi not reachable: %s", exc)
                self._set_disconnected()
                time.sleep(RECONNECT_INTERVAL)
                continue
            except KodiError as exc:
                log.warning("kodi poll error: %s", exc)
            time.sleep(POLL_INTERVAL)

    def _set_disconnected(self):
        with self._lock:
            self._state["connected"] = False

    def _active_player(self):
        """Return the active player dict ({playerid, type}) or None if idle."""
        players = self._kodi.call("Player.GetActivePlayers")
        for p in players or []:
            if p.get("type") in ("video", "audio"):
                return p
        return players[0] if players else None

    def _poll_once(self):
        player = self._active_player()  # raises KodiConnectionError if down

        filename = position = duration = None
        paused = None
        playing = player is not None

        if player is not None:
            pid = player["playerid"]
            props = self._kodi.call(
                "Player.GetProperties",
                playerid=pid,
                properties=["time", "totaltime", "speed"],
            ) or {}
            position = seconds_from_time(props.get("time"))
            duration = seconds_from_time(props.get("totaltime"))
            paused = props.get("speed", 1) == 0

            item = self._kodi.call(
                "Player.GetItem", playerid=pid, properties=["file"]
            ) or {}
            item = item.get("item", {})
            path = item.get("file")
            filename = os.path.basename(path) if path else item.get("label")

        app = self._kodi.call(
            "Application.GetProperties", properties=["volume"]
        ) or {}
        volume = app.get("volume")

        # Auto-advance: playback just ended (was playing, now nothing active).
        if self._prev_playing and not playing:
            self._maybe_advance()
        self._prev_playing = playing

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
        """Called from the poll loop when Kodi just went idle. If keep-playing
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
                self._open(next_file)
            except KodiError as exc:
                log.warning("auto-advance failed: %s", exc)

    # -- public read API ---------------------------------------------------

    def get_status(self):
        with self._lock:
            return dict(self._state)

    # -- helpers -----------------------------------------------------------

    def _open(self, path):
        self._kodi.call("Player.Open", item={"file": path})
        # An Open means playback is starting, so treat the next idle as a real
        # end-of-file (not the brief gap between stop and start).
        self._prev_playing = True

    def _active_player_id(self):
        player = self._active_player()
        return player["playerid"] if player else None

    # -- public command API (called from Flask routes) ---------------------

    def play_file(self, path):
        self._open(path)
        # Build the auto-advance queue from the containing folder, positioned at
        # the file just started -- so "keep playing" continues through the rest
        # of the folder even when you start from the middle.
        folder = os.path.dirname(path)
        files = list_video_files(folder, self.video_extensions)
        try:
            index = files.index(path)
        except ValueError:
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
        self._open(files[0])
        with self._lock:
            self._queue_folder = folder_path
            self._queue_files = files
            self._queue_index = 0

    def playpause(self):
        pid = self._active_player_id()
        if pid is not None:
            self._kodi.call("Player.PlayPause", playerid=pid)

    def seek(self, offset_seconds):
        pid = self._active_player_id()
        if pid is not None:
            # Kodi takes a relative jump as value={"seconds": N}.
            self._kodi.call("Player.Seek", playerid=pid, value={"seconds": int(offset_seconds)})

    def set_volume(self, value):
        value = max(0, min(100, value))
        self._kodi.call("Application.SetVolume", volume=value)

    def set_keep_playing(self, enabled):
        with self._lock:
            self._state["keep_playing"] = bool(enabled)
