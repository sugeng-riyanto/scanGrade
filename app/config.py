import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
    SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
    NGROK_DOMAIN = os.getenv("NGROK_DOMAIN", "")
    SENTRY_DSN = os.getenv("SENTRY_DSN", "")
    MIDTRANS_SERVER_KEY = os.getenv("MIDTRANS_SERVER_KEY", "")
    FONNTE_API_KEY = os.getenv("FONNTE_API_KEY", "")
    REDIS_URL = os.getenv("REDIS_URL", "")
    APP_URL = os.getenv("APP_URL", "http://localhost:5000")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024

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


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}


def get_config(env=None):
    env = env or os.getenv("FLASK_ENV", "development")
    return config_map.get(env, DevelopmentConfig)
