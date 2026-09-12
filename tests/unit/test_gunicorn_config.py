"""Guard: gevent workers must NOT preload the app.

With preload_app=True the gunicorn master imports httpx/httpcore/ssl before the
gevent worker monkey-patches ssl, so the worker's patched ssl breaks every
outbound HTTPS call:

    AuthRetryableError('super(type, obj): obj must be an instance or subtype of type')
      httpcore ... stream = stream.start_tls(**kwargs)

In production that silently broke Supabase auth (login always answered "Email
atau password salah") and every API call, while the process still looked
healthy. This test fails loudly if someone re-enables preloading.
"""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_gunicorn_conf():
    spec = importlib.util.spec_from_file_location("_scangrade_gunicorn_conf", ROOT / "gunicorn.conf.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_worker_class_is_gevent():
    conf = _load_gunicorn_conf()
    assert conf.worker_class == "gevent"


def test_preload_app_is_disabled():
    conf = _load_gunicorn_conf()
    assert conf.preload_app is False, (
        "preload_app must stay False with gevent workers — preloading imports ssl "
        "before monkey-patching and breaks all outbound HTTPS (Supabase/auth)"
    )
