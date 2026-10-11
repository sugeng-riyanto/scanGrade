"""The filesystem this process touches *at import*, made to report instead of raise.

Three shapes have already taken a process down here, and each one was found by hand:

* **an environment file.** `app/config.py`'s python-dotenv read let a failed `open()`
  out, so a `.env` an operator had been told to `chmod 600` killed gunicorn's worker
  boot, the Celery worker (in a `Restart=always` loop, one identical traceback every
  five seconds) and every `manage.py` command before it printed anything useful.
  `load_env_file()` is that one's answer, and it stays where it is: it is the reader,
  not a helper for readers.
* **the temp directory.** `tempfile.gettempdir()` is not a lookup. It *opens a file* in
  each candidate directory to prove it is writable, and raises `FileNotFoundError`
  when none of them is: a full disk, a read-only `/tmp`, a `TMPDIR` pointing at a
  directory that has since been removed. Both marker modules compute their default
  path from it **at module scope** (`DEFAULT_STATE_FILE` is a constant), so that raise
  is an import-time death for gunicorn, the worker and `manage.py` at once — and both
  of those modules exist to promise that no state problem ever reaches a student's
  save. A process that cannot start is the one outcome they must not have.
* **a module's own path.** `Path(__file__).resolve()` is a probe — an `lstat`, and a
  `readlink` for every link along the way — and it raises: `RuntimeError` on a symlink
  loop, `OSError` when a link cannot be read. Four modules resolve their repository
  root this way at import, to decide where to read from.

What is shared here is only the *shape of the answer*: a value, plus the reason it is
not the value we wanted. Nothing here hides a failure. Every reason is a module
constant, logged once at import, that a page, a probe or a test can read.
"""
from __future__ import annotations

import logging
import os
import pathlib
import tempfile

logger = logging.getLogger(__name__)


def _temp_dir() -> tuple[str, str]:
    """The system temp directory and the reason it could not be read. Never raises.

    `OSError` is what `tempfile._get_default_tempdir()` raises when no candidate
    directory can be written to (`FileNotFoundError`, its subclass, is the usual
    one); catching the family rather than the subclass is deliberate, because the
    next candidate it tries could fail with `EACCES` or `ENOSPC` instead.
    """
    try:
        return tempfile.gettempdir(), ""
    except OSError as exc:
        return "", f"{type(exc).__name__}: {exc}"


#: The temp directory this process may keep state in, or ``""`` when it has none.
TEMP_DIR, TEMP_DIR_ERROR = _temp_dir()

if TEMP_DIR_ERROR:
    logger.warning(
        "no usable temp directory (%s) — anything that keeps state there will say so "
        "rather than keep it, so what to look at is `TMPDIR` and the mode of `/tmp`",
        TEMP_DIR_ERROR,
    )


def default_state_file(name: str) -> str:
    """A path for a marker under the temp directory, or ``""`` when there is none.

    Callers treat ``""`` as *nowhere to keep this*, which is a state they already
    know how to render: writing nothing and reading nothing is what both marker
    modules do the moment the filesystem says no, and this is that answer one step
    earlier — before the path exists, rather than when it is first used.

    Returned as `str` and not `pathlib.Path` on purpose: the value ends up in a module
    constant that a test compares against the repository root, so the type it has
    always had is the type it keeps.
    """
    return os.path.join(TEMP_DIR, name) if TEMP_DIR else ""


def resolved(path: pathlib.Path) -> pathlib.Path:
    """`path` with its links followed, or `path` itself when that cannot be read.

    Resolution is a probe, not a property, so it can fail on a box where the file is
    perfectly readable: a symlink loop raises `RuntimeError` and an unreadable link
    raises `OSError`. Called from a module body — which is where every caller here
    calls it, to find the repository root — that failure is an import that never
    finishes, with a traceback naming `pathlib` rather than anything an operator can
    act on. The unresolved path is still the path the module lives at, and every
    caller uses it to choose a *directory to read from*, which an unresolved path
    does just as well.
    """
    try:
        return path.resolve()
    except (OSError, RuntimeError) as exc:
        logger.warning("could not resolve %s (%s: %s) — using it as given",
                       path, type(exc).__name__, exc)
        return path
