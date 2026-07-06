from flask import Blueprint, current_app, jsonify, request

from ..media import PathError, list_directory, resolve_path
from ..mpv_ipc import MpvIPCError

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/browse")
def browse():
    cfg = current_app.config
    requested = request.args.get("path") or None
    try:
        resolved = resolve_path(requested, cfg["MEDIA_ROOTS"])
    except PathError as exc:
        return jsonify({"error": str(exc)}), 400

    listing = list_directory(resolved, cfg["MEDIA_ROOTS"], cfg["VIDEO_EXTENSIONS"])
    return jsonify({"path": resolved, **listing})


@bp.route("/play", methods=["POST"])
def play():
    cfg = current_app.config
    body = request.get_json(force=True, silent=True) or {}
    requested = body.get("path")
    mode = body.get("mode")

    if not requested or mode not in ("file", "folder"):
        return jsonify({"error": "expected {path, mode: 'file'|'folder'}"}), 400

    try:
        resolved = resolve_path(requested, cfg["MEDIA_ROOTS"])
    except PathError as exc:
        return jsonify({"error": str(exc)}), 400

    player = current_app.player
    try:
        if mode == "file":
            player.play_file(resolved)
        else:
            player.play_folder(resolved)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except MpvIPCError as exc:
        return jsonify({"error": f"player unavailable: {exc}"}), 503

    return jsonify({"ok": True})


@bp.route("/control/playpause", methods=["POST"])
def playpause():
    try:
        current_app.player.playpause()
    except MpvIPCError as exc:
        return jsonify({"error": f"player unavailable: {exc}"}), 503
    return jsonify({"ok": True})


@bp.route("/control/seek", methods=["POST"])
def seek():
    body = request.get_json(force=True, silent=True) or {}
    try:
        offset = float(body.get("offset"))
    except (TypeError, ValueError):
        return jsonify({"error": "expected {offset: number}"}), 400

    try:
        current_app.player.seek(offset)
    except MpvIPCError as exc:
        return jsonify({"error": f"player unavailable: {exc}"}), 503
    return jsonify({"ok": True})


@bp.route("/control/volume", methods=["POST"])
def volume():
    body = request.get_json(force=True, silent=True) or {}
    try:
        value = int(body.get("value"))
    except (TypeError, ValueError):
        return jsonify({"error": "expected {value: 0-100}"}), 400

    try:
        current_app.player.set_volume(value)
    except MpvIPCError as exc:
        return jsonify({"error": f"player unavailable: {exc}"}), 503
    return jsonify({"ok": True})


@bp.route("/control/keep-playing", methods=["POST"])
def keep_playing():
    body = request.get_json(force=True, silent=True) or {}
    current_app.player.set_keep_playing(bool(body.get("enabled")))
    return jsonify({"ok": True})


@bp.route("/status")
def status():
    return jsonify(current_app.player.get_status())
