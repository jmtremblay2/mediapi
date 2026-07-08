(function () {
  "use strict";

  let currentPath = null; // null = top-level media roots
  let volumeDebounce = null;

  const el = (id) => document.getElementById(id);

  function api(path, options) {
    return fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, options))
      .then((r) => r.json().then((data) => ({ ok: r.ok, data })));
  }

  function formatTime(seconds) {
    if (seconds === null || seconds === undefined || isNaN(seconds)) return "0:00";
    seconds = Math.max(0, Math.floor(seconds));
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return m + ":" + String(s).padStart(2, "0");
  }

  // -- mode switching -------------------------------------------------

  function switchMode(mode) {
    const isControl = mode === "control";
    el("mode-control").classList.toggle("active", isControl);
    el("mode-playback").classList.toggle("active", !isControl);
    el("panel-control").classList.toggle("hidden", !isControl);
    el("panel-playback").classList.toggle("hidden", isControl);
  }

  el("mode-control").addEventListener("click", () => switchMode("control"));
  el("mode-playback").addEventListener("click", () => switchMode("playback"));

  // -- browsing ---------------------------------------------------------

  function loadBrowse(path) {
    const url = path ? "/api/browse?path=" + encodeURIComponent(path) : "/api/browse";
    api(url).then(({ ok, data }) => {
      if (!ok) return;
      currentPath = data.path;
      renderListing(data);
    });
  }

  function renderListing(data) {
    el("btn-up").disabled = !currentPath;
    el("btn-play-folder").disabled = !currentPath || data.files.length === 0;

    const listing = el("listing");
    listing.innerHTML = "";

    data.folders.forEach((f) => {
      const row = document.createElement("div");
      row.className = "entry folder";
      row.innerHTML = '<span class="entry-name">📁 ' + escapeHtml(f.name) + "</span>";
      row.querySelector(".entry-name").addEventListener("click", () => loadBrowse(f.path));
      listing.appendChild(row);
    });

    data.files.forEach((f) => {
      const row = document.createElement("div");
      row.className = "entry file";
      row.innerHTML =
        '<span class="entry-name">' + escapeHtml(f.name) + '</span><button>Play</button>';
      row.querySelector("button").addEventListener("click", () => playPath(f.path, "file"));
      listing.appendChild(row);
    });
  }

  function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  function playPath(path, mode) {
    api("/api/play", { method: "POST", body: JSON.stringify({ path: path, mode: mode }) }).then(
      ({ ok }) => {
        if (ok) switchMode("playback");
      }
    );
  }

  el("btn-up").addEventListener("click", () => {
    if (!currentPath) return;
    const parent = currentPath.split("/").slice(0, -1).join("/");
    loadBrowse(parent || null);
  });

  el("btn-play-folder").addEventListener("click", () => {
    if (currentPath) playPath(currentPath, "folder");
  });

  // -- playback status polling ------------------------------------------

  function pollStatus() {
    api("/api/status").then(({ data }) => {
      const online = data.connected;
      el("player-offline").classList.toggle("hidden", online);
      el("player-online").classList.toggle("hidden", !online);
      if (!online) return;

      el("now-playing").textContent = data.filename || "—";
      el("time-pos").textContent = formatTime(data.position);
      el("time-dur").textContent = formatTime(data.duration);

      const slider = el("seek-slider");
      slider.max = data.duration || 0;
      slider.value = data.position || 0;

      if (document.activeElement !== el("volume-slider")) {
        el("volume-slider").value = data.volume || 0;
      }
      el("keep-playing-checkbox").checked = !!data.keep_playing;
    });
  }

  el("btn-playpause").addEventListener("click", () => {
    api("/api/control/playpause", { method: "POST" });
  });

  el("btn-prev").addEventListener("click", () => {
    api("/api/control/previous", { method: "POST" });
  });

  el("btn-next").addEventListener("click", () => {
    api("/api/control/next", { method: "POST" });
  });

  el("btn-back30").addEventListener("click", () => {
    api("/api/control/seek", { method: "POST", body: JSON.stringify({ offset: -30 }) });
  });

  el("btn-fwd30").addEventListener("click", () => {
    api("/api/control/seek", { method: "POST", body: JSON.stringify({ offset: 30 }) });
  });

  el("volume-slider").addEventListener("input", (e) => {
    clearTimeout(volumeDebounce);
    const value = e.target.value;
    volumeDebounce = setTimeout(() => {
      api("/api/control/volume", { method: "POST", body: JSON.stringify({ value: Number(value) }) });
    }, 150);
  });

  el("keep-playing-checkbox").addEventListener("change", (e) => {
    api("/api/control/keep-playing", {
      method: "POST",
      body: JSON.stringify({ enabled: e.target.checked }),
    });
  });

  // -- init ---------------------------------------------------------

  loadBrowse(null);
  switchMode("control");
  pollStatus();
  setInterval(pollStatus, 1500);
})();
