"""A URL that changes when the file's bytes change, and only then.

nginx serves everything under ``/static/`` with ``expires 365d`` and
``Cache-Control: public, immutable`` (see ``deploy/nginx.conf``). That is the
right policy here — a student on a phone with two bars of signal should not
re-download Tailwind on every page — but it has a sharp edge: a stylesheet that
keeps its URL is never re-fetched for a *year*, so an edit to it reaches only the
browsers that have never seen it. A fix would appear on the developer's machine
and on a fresh phone, and nowhere else, with no error to explain it.

Appending a short hash of the file's contents removes the edge: the URL changes
exactly when the bytes do, and stays byte-identical when they do not, so the
one-year cache is safe to keep.

The hash is memoised on (path, mtime, size) rather than recomputed, because this
runs inside the template of every page. A file that is missing yields the plain
path instead of raising: a template should still render, and a stylesheet that
silently does not load is a bug this codebase's contrast gate reports anyway.
"""
import hashlib
import os

# (abspath, mtime_ns, size) -> version string. Small and bounded: it holds one
# row per stylesheet per release, and a changed file simply adds a new row.
_versions: dict[tuple, str] = {}


def _version_for(path: str) -> str | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (path, stat.st_mtime_ns, stat.st_size)
    cached = _versions.get(key)
    if cached is not None:
        return cached
    try:
        with open(path, "rb") as handle:
            digest = hashlib.md5(handle.read(), usedforsecurity=False).hexdigest()
    except OSError:
        return None
    version = digest[:8]
    _versions[key] = version
    return version


def asset_v(relative_path: str) -> str:
    """``css/theme.css`` -> ``/static/css/theme.css?v=1a2b3c4d``.

    Falls back to the unversioned URL when the file cannot be read, so a missing
    stylesheet is a 404 in the network tab rather than a 500 on the page.
    """
    relative_path = relative_path.lstrip("/")
    url = f"/static/{relative_path}"
    # Imported here so this module stays importable without an app (the guard
    # tests import it as a plain function).
    try:
        from flask import current_app
    except ImportError:  # pragma: no cover - Flask is always present in this app
        return url
    static_folder = None
    try:
        static_folder = current_app.static_folder
    except RuntimeError:
        # Outside a request/app context: unversioned is the honest answer.
        return url
    if not static_folder:
        return url
    version = _version_for(os.path.join(static_folder, relative_path))
    return f"{url}?v={version}" if version else url
