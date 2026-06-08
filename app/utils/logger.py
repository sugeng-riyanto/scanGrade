import json
import logging
from datetime import datetime, timezone


class StructuredFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        for key in ("user_id", "school_id", "exam_id", "error_code", "submission_id"):
            val = getattr(record, key, None)
            if val:
                log_data[key] = val
        return json.dumps(log_data, ensure_ascii=False)


def setup_logging(app):
    app.logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
    if app.debug:
        app.logger.setLevel(logging.DEBUG)


def get_logger(name):
    return logging.getLogger(f"scangarde.{name}")
