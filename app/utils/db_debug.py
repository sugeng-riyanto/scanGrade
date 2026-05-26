import time
import logging

logger = logging.getLogger(__name__)


class QueryProfiler:
    def __init__(self, label: str = "query"):
        self.label = label

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, *args):
        duration = time.time() - self.start
        logger.debug("%s took %.3fs", self.label, duration)
