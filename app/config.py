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

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # 50 MB (decimal) — keep in sync with app.errors.format_mb so the limit
    # shown to users matches the limit actually enforced.
    MAX_CONTENT_LENGTH = 50 * 1000 * 1000

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

    SUPABASE_URL = os.getenv("TEST_SUPABASE_URL", "http://127.0.0.1:54321")
    SUPABASE_SERVICE_KEY = _TEST_JWT
    SUPABASE_ANON_KEY = _TEST_JWT
    SECRET_KEY = os.getenv("TEST_SECRET_KEY", "test-secret-key")

    # Disable every optional integration so tests stay offline and fast.
    REDIS_URL = ""
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
