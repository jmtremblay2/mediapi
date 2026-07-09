import logging
import os
import threading
import time

from .media import list_video_files
from .mpv import MpvConnectionError, MpvError

log = logging.getLogger(__name__)

POLL_INTERVAL = 1.0
RECONNECT_INTERVAL = 2.0


class PlayerStateManager:
    """Controls mpv over its JSON IPC socket and caches its playback state.

    Playback is driven through mpv's own PLAYLIST: playing a file or folder
    loads the whole folder into mpv's playlist and jumps to the chosen entry.
    mpv then advances through the folder (and loops it, when "keep playing" is
    on) ALL BY ITSELF -- so playback keeps going even if this app, the phone, or
    the WiFi disconnect. mediapi only issues commands and polls state; it is
    never in the playback loop. Next/Previous are mpv playlist navigation; "keep
    playing" is mpv's `loop-playlist`.
    """

    def __init__(self, mpv_client, video_extensions):
        self._mpv = mpv_client
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

    # -- background loop (status only; mpv owns auto-advance) --------------

    def _run(self):
        while not self._stop.is_set():
            try:
                self._poll_once()
            except MpvConnectionError as exc:
                log.debug("mpv not reachable: %s", exc)
                self._set_disconnected()
                time.sleep(RECONNECT_INTERVAL)
                continue
            except MpvError as exc:
                log.warning("mpv poll error: %s", exc)
            time.sleep(POLL_INTERVAL)

    def _set_disconnected(self):
        with self._lock:
            self._state["connected"] = False

    def _has_media(self):
        """True if mpv currently has a file loaded (not idle). Raises
        MpvConnectionError if mpv is unreachable."""
        return self._mpv.try_get("path") is not None

    def _poll_once(self):
        # `path` is unavailable while mpv sits idle; try_get returns None then.
        # A genuine socket failure raises MpvConnectionError and marks us down.
        path = self._mpv.try_get("path")  # raises MpvConnectionError if down

        filename = position = duration = None
        paused = None

        if path is not None:
            filename = os.path.basename(path) or self._mpv.try_get("media-title")
            position = self._mpv.try_get("time-pos")
            duration = self._mpv.try_get("duration")
            paused = bool(self._mpv.try_get("pause", False))

        volume = self._mpv.try_get("volume")
        if volume is not None:
            volume = int(round(volume))

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
        """Load `files` into mpv's playlist (folder order) and start at
        start_index. mpv advances through them on its own from here on."""
        # `loadfile ... replace` starts a fresh playlist with the first file;
        # append the rest to rebuild the folder in order, then jump to the
        # chosen entry so the playlist matches the folder exactly.
        self._mpv.command("loadfile", files[0], "replace")
        for f in files[1:]:
            self._mpv.command("loadfile", f, "append")
        if start_index > 0:
            self._mpv.set_property("playlist-pos", start_index)
        self._apply_repeat()

    def _apply_repeat(self):
        """Set mpv's playlist loop to match keep-playing."""
        self._mpv.set_property("loop-playlist", "inf" if self._keep_playing else "no")

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
        if self._has_media():
            self._mpv.command("cycle", "pause")

    def next(self):
        if self._has_media():
            self._mpv.command("playlist-next", "weak")

    def previous(self):
        if self._has_media():
            self._mpv.command("playlist-prev", "weak")

    def seek(self, offset_seconds):
        if self._has_media():
            self._mpv.command("seek", int(offset_seconds), "relative")

    def seek_to(self, position_seconds):
        """Seek to an absolute position (seconds from the start) -- used by the
        draggable progress bar."""
        if self._has_media():
            pos = max(0, int(position_seconds))
            self._mpv.command("seek", pos, "absolute")

    def set_volume(self, value):
        value = max(0, min(100, value))
        self._mpv.set_property("volume", value)

    def set_keep_playing(self, enabled):
        with self._lock:
            self._keep_playing = bool(enabled)
            self._state["keep_playing"] = self._keep_playing
        self._apply_repeat()
