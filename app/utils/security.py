import re
import html


def sanitize_input(value: str) -> str:
    return html.escape(value.strip())


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


def validate_timestamp(ts: float, tolerance: int = 300) -> bool:
    import time
    return abs(time.time() - ts) <= tolerance
