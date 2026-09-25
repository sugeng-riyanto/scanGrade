import os
import re
from dotenv import load_dotenv

load_dotenv()

_INLINE_COMMENT = re.compile(r"\s+#")


def env_str(name, default=""):
    """Read an env var, tolerating the usual .env sloppiness.

    python-dotenv strips inline comments, but values exported by other tooling
    (shell ``export $(cat .env)``, Docker --env-file, CI panels) keep them — and
    a stray trailing space silently breaks API keys. A comment only counts when
    whitespace precedes ``#``, so secrets containing ``#`` survive intact.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    value = _INLINE_COMMENT.split(raw, 1)[0].strip()
    return value if value else default


def env_key(name, default=""):
    """Like env_str, but also cuts a comment glued straight onto the value.

    API keys (Midtrans, Fonnte) never contain '#', and people paste them as
    ``MIDTRANS_SERVER_KEY=Mid-server-abc# Payment gateway``. That value
    authenticates as garbage, so for key-shaped fields everything from the
    first '#' is dropped.
    """
    value = env_str(name, default)
    return value.split("#", 1)[0].strip() if value else default


def env_int(name, default=0):
    try:
        return int(env_str(name, "") or default)
    except (TypeError, ValueError):
        return default


def env_bool(name, default=False):
    """Read a boolean env var, accepting the spellings people actually type."""
    raw = env_str(name, "")
    if not raw:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = env_str("FLASK_SECRET_KEY", "dev-secret-change-me")
    SUPABASE_URL = env_str("SUPABASE_URL", "")
    SUPABASE_SERVICE_KEY = env_str("SUPABASE_SERVICE_KEY", "")
    SUPABASE_ANON_KEY = env_str("SUPABASE_ANON_KEY", "")
    NGROK_DOMAIN = env_str("NGROK_DOMAIN", "")
    SENTRY_DSN = env_str("SENTRY_DSN", "")
    SENTRY_ENVIRONMENT = env_str("SENTRY_ENVIRONMENT", "development")
    APP_VERSION = env_str("APP_VERSION", "1.0.0")
    # Midtrans keys get pasted with stray whitespace or a glued comment — a key
    # with either authenticates as 401, so normalise both away.
    MIDTRANS_SERVER_KEY = env_key("MIDTRANS_SERVER_KEY", "")
    MIDTRANS_CLIENT_KEY = env_key("MIDTRANS_CLIENT_KEY", "")
    FONNTE_API_KEY = env_key("FONNTE_API_KEY", "")
    # Email is the active notification channel (WhatsApp/Fonnte is not used).
    SMTP_EMAIL = env_str("SMTP_EMAIL", "scangrade9@gmail.com")
    SMTP_PASSWORD = env_str("SMTP_PASSWORD", "")
    SMTP_HOST = env_str("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = env_int("SMTP_PORT", 465)
    SMTP_FROM = env_str("SMTP_FROM", "") or env_str("SMTP_EMAIL", "scangrade9@gmail.com")
    REDIS_URL = env_str("REDIS_URL", "")
    APP_URL = env_str("APP_URL", "http://localhost:5000")
    DEMO_AI_KEY = env_str("DEMO_AI_KEY", "")

    @classmethod
    def email_configured(cls):
        return bool(cls.SMTP_EMAIL and cls.SMTP_PASSWORD)

    # Number of trusted reverse-proxy hops in front of the app. Set >0 only
    # when a proxy (nginx) overwrites X-Forwarded-For. Without it every client
    # IP collapses to the proxy address (127.0.0.1), which breaks per-IP rate
    # limits, audit logs, and anti-cheat device-mismatch detection. Defaults to
    # 0 so a directly reachable instance can't have its client IP spoofed.
    TRUSTED_PROXY_HOPS = env_int("TRUSTED_PROXY_HOPS", 0)

    # Seconds a resolved auth session (user + role + school) may be reused
    # without re-hitting Supabase (~290 ms per request saved). Short enough that
    # role changes propagate quickly; logout invalidates explicitly.
    # 0 disables the cache entirely.
    AUTH_SESSION_CACHE_TTL = env_int("AUTH_SESSION_CACHE_TTL", 30)

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # 50 MB (decimal) — keep in sync with app.errors.format_mb so the limit
    # shown to users matches the limit actually enforced.
    MAX_CONTENT_LENGTH = 50 * 1000 * 1000

    # The cleanup and retention loops each run a pass the moment they start, not
    # after the first interval. Anything that merely *constructs* the app — the
    # test suite, a deploy smoke test — therefore triggered a real purge (a
    # destructive one, against whatever Supabase the config points at). Building
    # the app must not mutate data, so this is switchable.
    START_BACKGROUND_SCHEDULERS = env_bool("START_BACKGROUND_SCHEDULERS", True)

    #: True when this construction is the *deploy's* probe rather than the app
    #: about to serve. `deploy/scangrade-deploy.sh` sets
    #: START_BACKGROUND_SCHEDULERS=false for the gate that proves the new commit
    #: constructs, and nothing else in the repository sets it — it dates from the
    #: first commit of that script, so every copy of the runner ever installed
    #: carries it. That is what lets the app refuse a release staged by a runner
    #: which is not the checkout's (see app/utils/armament.py) while gunicorn
    #: constructing the same app never sees the question.
    DEPLOY_PROBE = not START_BACKGROUND_SCHEDULERS

    # The runner-staleness alert (app/services/deploy_alert_service.py). Tunable
    # from the environment on purpose: the alert exists for the case where the
    # release machinery is not working, so arming or quietening it must not require
    # a release. `DEPLOY_ALERT_MIN_COMMITS` is the "more than a few commits" line,
    # and the interval is six hours — 12 cheap `git` calls a day on a 1 vCPU box.
    DEPLOY_ALERT_MIN_COMMITS = env_int("DEPLOY_ALERT_MIN_COMMITS", 5)
    DEPLOY_ALERT_INTERVAL_SECONDS = env_int("DEPLOY_ALERT_INTERVAL_SECONDS", 6 * 3600)

    #: How often the server closes sittings whose deadline has passed. The exam
    #: page's countdown is a display; this is the enforcement, so it runs whether or
    #: not a browser is still open (app/services/deadline_service.py).
    DEADLINE_SWEEP_INTERVAL_SECONDS = env_int("DEADLINE_SWEEP_INTERVAL_SECONDS", 60)
    #: Where the last-alert record is kept. Defaults to Flask's instance folder.
    DEPLOY_ALERT_STATE_DIR = env_str("SCANGRADE_ALERT_STATE_DIR", "") or None

    @classmethod
    def validate(cls):
        required = ["SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SECRET_KEY"]
        missing = [v for v in required if not getattr(cls, v)]
        if missing:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SENTRY_ENVIRONMENT = "production"
    # Production is fronted by nginx, which appends the real client address to
    # X-Forwarded-For. Trust exactly one hop.
    TRUSTED_PROXY_HOPS = env_int("TRUSTED_PROXY_HOPS", 1)


# Well-formed but non-functional credentials — supabase-py only validates the
# shape at construction time. Tests never talk to a real project, and every
# network call is mocked in the test suite.
_TEST_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.QA"


class TestingConfig(Config):
    """Isolated config for the test suite — no real network, no debug leak."""

    TESTING = True
    DEBUG = False
    PROPAGATE_EXCEPTIONS = False
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    WTF_CSRF_ENABLED = True
    # Creating the app must stay side-effect free: the retention loop purges
    # immediately on start. Tests that exercise it start it themselves.
    START_BACKGROUND_SCHEDULERS = False
    # Set explicitly rather than derived from the line above: tests construct the
    # app the same side-effect-free way the deploy's probe does, and the armament
    # question — "is this box armed" — is about a VPS, not about a test run.
    DEPLOY_PROBE = False

    SUPABASE_URL = os.getenv("TEST_SUPABASE_URL", "http://127.0.0.1:54321")
    SUPABASE_SERVICE_KEY = _TEST_JWT
    SUPABASE_ANON_KEY = _TEST_JWT
    SECRET_KEY = os.getenv("TEST_SECRET_KEY", "test-secret-key")

    # Disable every optional integration so tests stay offline and fast.
    REDIS_URL = ""
    # ...and named rather than merely blanked: an empty REDIS_URL still loses to a
    # REDIS_URL in the environment (the checkout's .env sets one), so a test run was
    # dialling a Redis on every construct and waiting four seconds to be refused.
    # Naming the limiter's storage ends the argument.
    RATELIMIT_STORAGE_URI = "memory://"
    SENTRY_DSN = ""
    MIDTRANS_SERVER_KEY = ""
    FONNTE_API_KEY = ""
    NGROK_DOMAIN = ""
    SMTP_PASSWORD = ""

    @classmethod
    def validate(cls):
        # Test credentials are self-contained; never require real env vars.
        return None


config_map = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def _resolve_config(name):
    """Resolve a config from a short name ("testing"), a dotted path
    ("app.config.TestingConfig"), or an already-imported class."""
    if name is None:
        return None
    if isinstance(name, type):
        return name
    name = str(name)
    if name in config_map:
        return config_map[name]
    module_path, _, attr = name.rpartition(".")
    if not module_path:
        return None
    try:
        import importlib
        module = importlib.import_module(module_path)
    except ImportError:
        return None
    return getattr(module, attr, None)


def get_config(env=None):
    # env_str() drops inline comments — "production  # deploy" must not silently
    # fall through to DevelopmentConfig (debug on) in production.
    env = env or env_str("FLASK_ENV", "development")
    env = str(env).strip()
    resolved = _resolve_config(env)
    if resolved is None:
        resolved = config_map.get(env.lower(), DevelopmentConfig)
    return resolved
