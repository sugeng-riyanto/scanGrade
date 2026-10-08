# The `.env` is read once, tolerantly, by `app.config` — importing the package
# below is what runs that, before `create_app()` reads a single setting. Calling
# python-dotenv here instead made an unreadable `.env` fatal to *this* file too:
# the read was unguarded, so gunicorn's workers died at import with
# `PermissionError` while the unit's own `EnvironmentFile=` was loading the box's
# variables perfectly well (see `app.config.load_env_file`).
import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_ENV", "development") == "development"
    app.run(host=host, port=port, debug=debug)
