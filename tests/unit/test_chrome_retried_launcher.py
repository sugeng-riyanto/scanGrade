"""Offline tests: Chrome startup retries must not leak processes or profiles."""
import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from deploy import chrome_retried_launcher as launcher


class FakeProcess:
    def __init__(self, *args, **kwargs):
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if not self.terminated else 0

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


def run(coro):
    return asyncio.run(coro)


def test_retry_then_success_and_cleanup():
    processes = []
    calls = 0

    def factory(*args, **kwargs):
        p = FakeProcess()
        processes.append(p)
        return p

    def targets(port):
        nonlocal calls
        calls += 1
        return [] if calls == 1 else [{"type": "page", "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/test"}]

    with patch.object(launcher.touch_gate, "_free_port", side_effect=[12001, 12002]), \
         patch.object(launcher.touch_gate, "browser_launch", side_effect=lambda binary, port, profile: ([binary, "about:blank"], {})):
        result = run(launcher.start_browser("chrome", role="pupil-a", attempts=2,
                     polls_per_attempt=1, poll_delay=0,
                     process_factory=factory, get_targets=targets))
    assert len(processes) == 2
    assert processes[0].terminated
    assert not processes[1].terminated
    profile = Path(result.profile)
    assert profile.exists()
    result.close()
    assert processes[1].terminated
    assert not profile.exists()


def test_exhaustion_is_a_failure_and_cleans_every_attempt():
    processes = []

    def factory(*args, **kwargs):
        p = FakeProcess()
        processes.append(p)
        return p

    with patch.object(launcher.touch_gate, "_free_port", side_effect=[12003, 12004]), \
         patch.object(launcher.touch_gate, "browser_launch", side_effect=lambda binary, port, profile: ([binary, "about:blank"], {})), \
         patch.object(launcher.tempfile, "mkdtemp", wraps=launcher.tempfile.mkdtemp) as mk:
        with pytest.raises(RuntimeError, match="after 2 attempts"):
            run(launcher.start_browser("chrome", role="pupil-b", attempts=2,
                 polls_per_attempt=1, poll_delay=0,
                 process_factory=factory, get_targets=lambda port: []))
        assert all(p.terminated for p in processes)
        assert mk.call_count == 2
