import logging
import os
import re
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def load_env_file(path=None) -> str:
    """Read the environment file; return ``""``, or why it could not be read.

    Both systemd units hand this file to their process as ``EnvironmentFile=``,
    which systemd reads **as root** — so the worker's environment does not depend
    on the file's mode. But a checkout runs this module directly too (``wsgi.py``,
    ``python manage.py``, a one-off ``python -c``), and there the read is the
    process's own. python-dotenv lets a failed ``open()`` out, and a bare
    ``load_dotenv()`` therefore turned a ``.env`` whose mode changed — the box's is
    ``600`` (``docs/SECRET_ROTATION.md``) — into ``PermissionError`` at **import of
    this module**: gunicorn could not boot, the Celery worker crash-looped under
    ``Restart=always`` with one identical traceback every five seconds and nothing
    saying why, and the suite or a manage.py command died before printing anything
    useful. A file this user cannot read is a **missing** file, not a fatal one: the
    reason is recorded on :data:`DOTENV_ERROR` and the process carries on with the
    environment systemd and the shell already provided.

    This cannot turn a real misconfiguration into a silent one. Whether the
    environment is *complete* is a different question, and ``Config.validate()``
    still refuses to boot production without the required variables — with a
    message naming them, which is better than a traceback out of a file reader.

    ``path`` is for a caller with an explicit file (and for the tests below); the
    default is what ``load_dotenv()`` resolves — the nearest ``.env`` above this
    module.
    """
    try:
        load_dotenv() if path is None else load_dotenv(path)
    except OSError as exc:              # unreadable, a directory, a broken link
        reason = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "could not read the environment file (%s); continuing with the "
            "environment as given — `EnvironmentFile=` in the systemd unit is "
            "what supplies the box's variables", reason)
        return reason
    return ""


#: Why the environment file could not be read, or ``""`` when it was read (or when
#: the box has none, which python-dotenv treats as success). Published so a test, a
#: probe or a future health check can tell "no ``.env`` here" from "a ``.env`` this
#: user cannot read" — the two look identical from the environment alone.
DOTENV_ERROR = load_env_file()


def env_file_path() -> str:
    """The file :func:`load_env_file` would read here, or ``""`` when there is none.

    The same resolution the loader rides on, so a reader that wants to *name* the
    file — or to say that this account cannot open it — cannot disagree with the
    loader about which file that is. Python-dotenv walks up from this module, so the
    answer is the nearest ``.env`` above the checkout.
    """
    from dotenv import find_dotenv

    try:
        return find_dotenv(usecwd=False) or ""
    except (OSError, ValueError):
        return ""

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


#: Every name an SMTP app password may arrive under. `SMTP_PASSWORD` is what the
#: installer writes and what this app has always read; the Gmail spelling is what
#: the provider's own page produces and what people therefore put in `.env` — and a
#: box holding the right secret under the other name reported itself unable to send
#: mail at all. Measured on this checkout: `.env` carried `app_password_gmail=`
#: while `SMTP_PASSWORD` was commented out, and every reset email was skipped with a
#: warning nobody was reading.
SMTP_PASSWORD_NAMES = ("SMTP_PASSWORD", "APP_PASSWORD_GMAIL", "app_password_gmail")

#: The names whose value is *squeezed* — Google prints an app password in four
#: groups of four (`abcd efgh ijkl mnop`) and that grouped string authenticates as
#: garbage. `SMTP_PASSWORD` is deliberately not here: an ordinary SMTP password may
#: contain a space as a character, and this helper must never be the reason a box
#: that was sending mail stops.
SMTP_PASSWORD_SQUEEZED = ("APP_PASSWORD_GMAIL", "app_password_gmail")


def env_password():
    """The app password, under any name it may arrive in, or "" when there is none."""
    for name in SMTP_PASSWORD_NAMES:
        raw = env_str(name, "")
        if not raw:
            continue
        return "".join(raw.split()) if name in SMTP_PASSWORD_SQUEEZED else raw
    return ""


class Config:
    SECRET_KEY = env_str("FLASK_SECRET_KEY", "dev-secret-change-me")
    SUPABASE_URL = env_str("SUPABASE_URL", "")
    SUPABASE_SERVICE_KEY = env_str("SUPABASE_SERVICE_KEY", "")
    SUPABASE_ANON_KEY = env_str("SUPABASE_ANON_KEY", "")
    NGROK_DOMAIN = env_str("NGROK_DOMAIN", "")
    SENTRY_DSN = env_str("SENTRY_DSN", "")
    SENTRY_ENVIRONMENT = env_str("SENTRY_ENVIRONMENT", "development")
    #: Where a reader should go to see *what* broke, when the rollup's count says
    #: something did. Set it for a self-hosted Sentry or a regional endpoint, where
    #: no URL can be derived from the DSN; left empty, `/super-admin/dashboard`
    #: derives it from `SENTRY_DSN`'s own hostname and project path, and shows no
    #: link at all rather than one that 404s. Nothing *reports* to Sentry through
    #: this — the SDK is configured from the DSN, and this is only the link.
    SENTRY_ISSUES_URL = env_str("SENTRY_ISSUES_URL", "")
    APP_VERSION = env_str("APP_VERSION", "1.0.0")
    # Midtrans keys get pasted with stray whitespace or a glued comment — a key
    # with either authenticates as 401, so normalise both away.
    MIDTRANS_SERVER_KEY = env_key("MIDTRANS_SERVER_KEY", "")
    MIDTRANS_CLIENT_KEY = env_key("MIDTRANS_CLIENT_KEY", "")
    FONNTE_API_KEY = env_key("FONNTE_API_KEY", "")
    # Email is the active notification channel (WhatsApp/Fonnte is not used).
    SMTP_EMAIL = env_str("SMTP_EMAIL", "scangrade9@gmail.com")
    SMTP_PASSWORD = env_password()
    SMTP_HOST = env_str("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = env_int("SMTP_PORT", 465)
    SMTP_FROM = env_str("SMTP_FROM", "") or env_str("SMTP_EMAIL", "scangrade9@gmail.com")
    REDIS_URL = env_str("REDIS_URL", "")
    APP_URL = env_str("APP_URL", "http://localhost:5000")
    DEMO_AI_KEY = env_str("DEMO_AI_KEY", "")

    #: Drive the whole payment path without a gateway account: the notification
    #: handler trusts the callback's own fields instead of asking Midtrans to parse
    #: it. Used by tests and staging so a school's checkout -> code -> redemption ->
    #: tier/invoice journey can be exercised end to end. `ProductionConfig` pins it
    #: off regardless of the environment, and a guard test reads that class
    #: attribute rather than a running app.
    PAYMENT_SIMULATION = env_bool("PAYMENT_SIMULATION", False)

    #: True only for the config that serves real users. A class attribute and not a
    #: read of the environment, for the same reason `PAYMENT_SIMULATION` above is
    #: pinned in `ProductionConfig`: a permission that a `.env` file could grant is
    #: not a permission, and the reader here (`checkout_integrity.dev_allowance`)
    #: has to judge *where this process is running*, not where someone said it was.
    IS_PRODUCTION = False

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
    #: The "more than a few" line for account-create retries (a struggling GoTrue),
    #: on the same channel and for the same reason: it must be arming-able without a
    #: release, because the box it is about is the one that cannot deploy.
    DEPLOY_ALERT_MIN_AUTH_RETRIES = env_int("DEPLOY_ALERT_MIN_AUTH_RETRIES", 5)
    DEPLOY_ALERT_INTERVAL_SECONDS = env_int("DEPLOY_ALERT_INTERVAL_SECONDS", 6 * 3600)

    #: How often the server closes sittings whose deadline has passed. The exam
    #: page's countdown is a display; this is the enforcement, so it runs whether or
    #: not a browser is still open (app/services/deadline_service.py).
    DEADLINE_SWEEP_INTERVAL_SECONDS = env_int("DEADLINE_SWEEP_INTERVAL_SECONDS", 60)
    #: How often every exam's assessment-period tag is re-derived from the calendar
    #: (app/services/period_reconcile_service.py). A day: the write doors already
    #: keep the tags current for everything the app writes, and this only catches a
    #: tag written around a door (SQL, a repair script).
    PERIOD_RECONCILE_INTERVAL_SECONDS = env_int(
        "PERIOD_RECONCILE_INTERVAL_SECONDS", 86400)
    #: Where the last-alert record is kept. Defaults to Flask's instance folder.
    DEPLOY_ALERT_STATE_DIR = env_str("SCANGRADE_ALERT_STATE_DIR", "") or None

    #: How often each worker rolls its own traffic counters into the summary tables
    #: (app/services/traffic_rollup_service.py). A minute: the write is one small
    #: RPC per worker, and a minute is fine enough that the dashboard's hourly bars
    #: are never a visible estimate — while being long enough that the 1 vCPU box
    #: spends nothing measurable on the instrument.
    TRAFFIC_ROLLUP_INTERVAL_SECONDS = env_int("TRAFFIC_ROLLUP_INTERVAL_SECONDS", 60)
    #: Retention for the rollup tables. Hourly buckets are for reading shape within
    #: a fortnight; the daily rows are what a term-over-term comparison needs, so
    #: they are kept for a little over a year. Both are *aggregates* — the retention
    #: question here is disk, not personal data, because there is none in them.
    TRAFFIC_KEEP_HOURLY_DAYS = env_int("TRAFFIC_KEEP_HOURLY_DAYS", 14)
    TRAFFIC_KEEP_DAILY_DAYS = env_int("TRAFFIC_KEEP_DAILY_DAYS", 400)

    #: The weekly cache sweep (app/services/cache_maintenance_service.py): the day
    #: of the week and the hour of the day it is allowed to run, in the school's own
    #: clock. Sunday 03:00 WIB is the default because the hourly rollup says the box
    #: is quietest then, and because a term-time exam is not sat at three in the
    #: morning. Both are configurable because the answer changes per school.
    CACHE_SWEEP_WEEKDAY = env_int("CACHE_SWEEP_WEEKDAY", 6)   # Monday=0 … Sunday=6
    CACHE_SWEEP_HOUR = env_int("CACHE_SWEEP_HOUR", 3)
    CACHE_SWEEP_INTERVAL_SECONDS = env_int("CACHE_SWEEP_INTERVAL_SECONDS", 900)

    #: The variables this app cannot run without, in the order a reader should try
    #: them. Named here rather than inline in `validate()` because a *second* reader
    #: needs the list rather than the exception: `/super-admin/deploy-status` has to
    #: name which variable a refused release was missing, and parsing a traceback to
    #: recover names this module already knows would be a second rule that can drift
    #: from the one that refuses.
    REQUIRED_ENV = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SECRET_KEY")

    @classmethod
    def missing_required(cls) -> list:
        """Which of :data:`REQUIRED_ENV` are absent or empty, by name.

        Empty counts as missing, which is the reading the refusal has always used:
        ``SUPABASE_SERVICE_KEY=`` in a ``.env`` is a key nobody can use, and the
        traceback an operator reads says `Missing required env vars`.
        """
        return [name for name in cls.REQUIRED_ENV if not getattr(cls, name)]

    @classmethod
    def validate(cls):
        missing = cls.missing_required()
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

    #: The value `Config.SECRET_KEY` falls back to when `FLASK_SECRET_KEY` is
    #: unset. It is in this repository, so a box that boots on it signs every CSRF
    #: token with a secret anyone who reads the repo can reproduce — and
    #: `Config.validate()` only checks that the value is *non-empty*, which a
    #: default always is. Production refuses this one value; development and the
    #: suite keep it, so the documented dev flow stays bootable.
    PLACEHOLDER_SECRET = "dev-secret-change-me"

    @classmethod
    def validate(cls):
        super().validate()
        if not cls.SECRET_KEY or cls.SECRET_KEY == cls.PLACEHOLDER_SECRET:
            raise RuntimeError(
                "FLASK_SECRET_KEY is unset or still the repository's placeholder; "
                "refusing to sign sessions with a publicly-known value")

    # Never simulate a payment on a box that takes real money. Set as a class
    # attribute, not a read of the environment, so no `.env` on the box can turn it
    # on; `PAYMENT_SIMULATION` in `Config` is what the other environments inherit.
    PAYMENT_SIMULATION = False
    #: And never let a development-only shortcut answer a deploy's question. The
    #: one reader is `checkout_integrity.dev_allowance`, which refuses the
    #: allowance here whatever the environment says — so a variable left exported in
    #: a shell profile cannot travel into the box with the next `git pull`.
    IS_PRODUCTION = True
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
    PAYMENT_SIMULATION = True

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
    # A class is handed back before anything is done to it. `str(env)` below turns
    # one into "<class 'app.config.TestingConfig'>", which `_resolve_config` then
    # cannot resolve — so a caller that passed an already-imported class (the shape
    # `_resolve_config` documents and `build_app()` is typed for) silently got
    # DevelopmentConfig instead: it booted against whatever the checkout's `.env`
    # held, passed whenever that happened to satisfy it, and failed on a fresh
    # worktree with "missing required env vars" — a report about the environment
    # for an argument that was being ignored.
    if isinstance(env, type):
        return env
    # env_str() drops inline comments — "production  # deploy" must not silently
    # fall through to DevelopmentConfig (debug on) in production.
    env = env or env_str("FLASK_ENV", "development")
    env = str(env).strip()
    resolved = _resolve_config(env)
    if resolved is None:
        resolved = config_map.get(env.lower(), DevelopmentConfig)
    return resolved
