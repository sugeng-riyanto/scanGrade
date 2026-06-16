import os
import time
import logging
import threading
from pathlib import Path

logger = logging.getLogger("app")

# Known temp/cache directories relative to project root
TEMP_DIRS = [
    "static/uploads/scans/tmp",          # OMR scan temp files
    "static/uploads/scans/bulk_tmp",     # Bulk ZIP extraction temp
]
# Directories where old intermediate files accumulate
PURGE_DIRS = [
    # whiteboard render temps: delete _render_*.png older than 24h
    ("static/uploads/whiteboard", 86400, lambda f: f.startswith("_render_")),
]


def _get_project_root():
    """Find project root (where app/ directory lives)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def clean_temp_files(max_age=3600):
    """Remove all files in TEMP_DIRS older than max_age seconds."""
    root = _get_project_root()
    removed = 0
    for rel_dir in TEMP_DIRS:
        d = os.path.join(root, rel_dir)
        if not os.path.isdir(d):
            continue
        now = time.time()
        for fname in os.listdir(d):
            fpath = os.path.join(d, fname)
            if os.path.isfile(fpath) and now - os.path.getmtime(fpath) > max_age:
                try:
                    os.remove(fpath)
                    removed += 1
                except OSError as e:
                    logger.debug("cleanup: cannot remove %s: %s", fpath, e)
    return removed


def purge_old_files():
    """Remove old intermediate/cache files from PURGE_DIRS."""
    root = _get_project_root()
    removed = 0
    for rel_dir, max_age, filter_fn in PURGE_DIRS:
        d = os.path.join(root, rel_dir)
        if not os.path.isdir(d):
            continue
        now = time.time()
        for sub in os.listdir(d):
            sub_path = os.path.join(d, sub)
            if not os.path.isdir(sub_path):
                continue
            for fname in os.listdir(sub_path):
                if not filter_fn(fname):
                    continue
                fpath = os.path.join(sub_path, fname)
                if os.path.isfile(fpath) and now - os.path.getmtime(fpath) > max_age:
                    try:
                        os.remove(fpath)
                        removed += 1
                    except OSError as e:
                        logger.debug("cleanup: cannot remove %s: %s", fpath, e)
    return removed


def clean_all(max_age=3600):
    """Run all cleanup tasks and return counts."""
    t1 = clean_temp_files(max_age)
    t2 = purge_old_files()
    return {"temp_files": t1, "purged": t2}


# ── Periodic scheduler ──

_cleanup_thread = None
_cleanup_interval = 1800  # 30 minutes
_running = False


def _run_cleanup_loop():
    global _running
    _running = True
    while _running:
        try:
            result = clean_all()
            if result["temp_files"] > 0 or result["purged"] > 0:
                logger.info("Cleanup: removed %d temp files, %d purged files",
                            result["temp_files"], result["purged"])
        except Exception as e:
            logger.error("Cleanup error: %s", e)
        time.sleep(_cleanup_interval)


def start_cleanup_scheduler(interval=1800):
    """Start background cleanup thread (safe to call multiple times)."""
    global _cleanup_thread, _cleanup_interval
    if _cleanup_thread and _cleanup_thread.is_alive():
        return
    _cleanup_interval = interval
    _cleanup_thread = threading.Thread(target=_run_cleanup_loop, daemon=True)
    _cleanup_thread.start()
    logger.info("Cleanup scheduler started (interval=%ds)", _cleanup_interval)


def stop_cleanup_scheduler():
    global _running
    _running = False
