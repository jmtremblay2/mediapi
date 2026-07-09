import logging
import os
import threading
import time

from .kodi import KodiConnectionError, KodiError, seconds_from_time
from .media import list_video_files

log = logging.getLogger(__name__)

POLL_INTERVAL = 1.0
RECONNECT_INTERVAL = 2.0

# Kodi's video playlist id (Playlist.GetPlaylists: 0=audio, 1=video, 2=picture).
VIDEO_PLAYLIST_ID = 1


class PlayerStateManager:
    """Controls Kodi over JSON-RPC and caches its playback state.

    Playback is driven through Kodi's own PLAYLIST: playing a file or folder
    loads the whole folder into Kodi's video playlist and starts it. Kodi then
    advances through the folder (and loops it, when "keep playing" is on) ALL BY
    ITSELF -- so playback keeps going even if this app, the phone, or the WiFi
    disconnect. mediapi only issues commands and polls state; it is never in the
    playback loop. Next/Previous are Kodi playlist navigation; "keep playing" is
    Kodi's repeat mode.
    """

    def __init__(self, kodi_client, video_extensions):
        self._kodi = kodi_client
        self.video_extensions = video_extensions

        self._lock = threading.Lock()
        self._stop = threading.Event()

        # Desired repeat state. Default ON: a lean-back player (kids' videos in a
        # car) should keep running through/looping the folder, not stop.
        self._keep_playing = True

        self._state = {
            "connected": False,
            "filename": None,
            "position": None,
            "duration": None,
            "paused": None,
            "volume": None,
            "keep_playing": True,
        }

    def start(self):
        thread = threading.Thread(target=self._run, name="player-state-manager", daemon=True)
        thread.start()

    def stop(self):
        self._stop.set()

    # -- background loop (status only; Kodi owns auto-advance) -------------

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

    def _active_player_id(self):
        player = self._active_player()
        return player["playerid"] if player else None

    def _poll_once(self):
        player = self._active_player()  # raises KodiConnectionError if down

        filename = position = duration = None
        paused = None

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

        with self._lock:
            self._state.update({
                "connected": True,
                "filename": filename,
                "position": position,
                "duration": duration,
                "paused": paused,
                "volume": volume,
                "keep_playing": self._keep_playing,
            })

    # -- public read API ---------------------------------------------------

    def get_status(self):
        with self._lock:
            return dict(self._state)

    # -- helpers -----------------------------------------------------------

    def _play_playlist(self, files, start_index):
        """Load `files` into Kodi's video playlist and start at start_index.
        Kodi advances through them on its own from here on."""
        self._kodi.call("Playlist.Clear", playlistid=VIDEO_PLAYLIST_ID)
        for f in files:
            self._kodi.call("Playlist.Add", playlistid=VIDEO_PLAYLIST_ID, item={"file": f})
        self._kodi.call(
            "Player.Open", item={"playlistid": VIDEO_PLAYLIST_ID, "position": start_index}
        )
        self._apply_repeat()

    def _apply_repeat(self):
        """Set Kodi's repeat mode on the active player to match keep-playing.
        Retries briefly: right after Player.Open the player may not be active
        for a beat."""
        repeat = "all" if self._keep_playing else "off"
        for _ in range(10):
            pid = self._active_player_id()
            if pid is not None:
                self._kodi.call("Player.SetRepeat", playerid=pid, repeat=repeat)
                return
            time.sleep(0.2)

    # -- public command API (called from Flask routes) ---------------------

    def play_file(self, path):
        # Queue the whole containing folder so playback continues through the
        # rest of it, starting at the chosen file.
        folder = os.path.dirname(path)
        files = list_video_files(folder, self.video_extensions)
        try:
            index = files.index(path)
        except ValueError:
            files = [path]
            index = 0
        self._play_playlist(files, index)

    def play_folder(self, folder_path):
        files = list_video_files(folder_path, self.video_extensions)
        if not files:
            raise ValueError(f"no video files in {folder_path}")
        self._play_playlist(files, 0)

    def playpause(self):
        pid = self._active_player_id()
        if pid is not None:
            self._kodi.call("Player.PlayPause", playerid=pid)

    def next(self):
        pid = self._active_player_id()
        if pid is not None:
            self._kodi.call("Player.GoTo", playerid=pid, to="next")

    def previous(self):
        pid = self._active_player_id()
        if pid is not None:
            self._kodi.call("Player.GoTo", playerid=pid, to="previous")

    def seek(self, offset_seconds):
        pid = self._active_player_id()
        if pid is not None:
            # Kodi takes a relative jump as value={"seconds": N}.
            self._kodi.call("Player.Seek", playerid=pid, value={"seconds": int(offset_seconds)})

    def seek_to(self, position_seconds):
        """Seek to an absolute position (seconds from the start) -- used by the
        draggable progress bar."""
        pid = self._active_player_id()
        if pid is not None:
            pos = max(0, int(position_seconds))
            self._kodi.call(
                "Player.Seek",
                playerid=pid,
                value={"time": {
                    "hours": pos // 3600,
                    "minutes": (pos % 3600) // 60,
                    "seconds": pos % 60,
                    "milliseconds": 0,
                }},
            )

    def set_volume(self, value):
        value = max(0, min(100, value))
        self._kodi.call("Application.SetVolume", volume=value)

    def set_keep_playing(self, enabled):
        with self._lock:
            self._keep_playing = bool(enabled)
            self._state["keep_playing"] = self._keep_playing
        self._apply_repeat()
