import os


class PathError(Exception):
    pass


def _resolved_roots(media_roots):
    return [os.path.realpath(r) for r in media_roots]


def resolve_path(requested_path, media_roots):
    """Resolve requested_path (absolute or relative to a root) and ensure it
    stays within one of the configured media roots. Raises PathError otherwise."""
    roots = _resolved_roots(media_roots)

    if not requested_path:
        # no path given -> the roots themselves are the top-level listing
        return None

    real = os.path.realpath(requested_path)
    for root in roots:
        if real == root or real.startswith(root + os.sep):
            return real
    raise PathError(f"path escapes configured media roots: {requested_path}")


def list_directory(path, media_roots, video_extensions):
    """Return {"folders": [...], "files": [...]} for the given resolved path,
    or for the top-level roots themselves when path is None."""
    if path is None:
        folders = []
        for root in media_roots:
            real = os.path.realpath(root)
            if os.path.isdir(real):
                folders.append({"name": os.path.basename(real) or real, "path": real})
        folders.sort(key=lambda e: e["name"].lower())
        return {"folders": folders, "files": []}

    folders = []
    files = []
    with os.scandir(path) as it:
        for entry in it:
            if entry.name.startswith("."):
                continue
            if entry.is_dir(follow_symlinks=True):
                folders.append({"name": entry.name, "path": entry.path})
            elif entry.is_file(follow_symlinks=True):
                ext = os.path.splitext(entry.name)[1].lower()
                if ext in video_extensions:
                    files.append({"name": entry.name, "path": entry.path})

    folders.sort(key=lambda e: e["name"].lower())
    files.sort(key=lambda e: e["name"].lower())
    return {"folders": folders, "files": files}


def list_video_files(path, video_extensions):
    """Sorted list of video file paths directly inside `path` (no recursion)."""
    files = []
    with os.scandir(path) as it:
        for entry in it:
            if entry.name.startswith("."):
                continue
            if entry.is_file(follow_symlinks=True):
                ext = os.path.splitext(entry.name)[1].lower()
                if ext in video_extensions:
                    files.append(entry.path)
    files.sort(key=lambda p: os.path.basename(p).lower())
    return files
