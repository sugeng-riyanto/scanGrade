"""Shared retried Chrome CDP launcher for isolated staging QA browsers.

The returned process/profile are owned by the caller. No live environment is
changed, and a failed attempt always terminates Chrome and removes its profile.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import shutil
import subprocess
import tempfile

from deploy import touch_gate


@dataclass
class ChromeSession:
    process: subprocess.Popen
    profile: str
    websocket_url: str

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        shutil.rmtree(self.profile, ignore_errors=True)


async def start_browser(binary: str, *, role: str, flags=(), attempts=3,
                        polls_per_attempt=30, poll_delay=0.3,
                        process_factory=subprocess.Popen,
                        get_targets=None) -> ChromeSession:
    """Start a fresh Chrome profile on each retry and discover its page CDP URL."""
    if attempts < 1 or polls_per_attempt < 1:
        raise ValueError("attempts and polls_per_attempt must be positive")
    if get_targets is None:
        import requests

        def get_targets(port):
            return requests.get(f"http://127.0.0.1:{port}/json/list", timeout=2).json()

    errors = []
    for attempt in range(1, attempts + 1):
        profile = tempfile.mkdtemp(prefix=f"sg-cdp-{role}-")
        proc = None
        try:
            port = touch_gate._free_port()
            argv, env = touch_gate.browser_launch(binary, port, profile)
            argv[-1:-1] = list(flags)
            proc = process_factory(argv, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, env=env)
            for _ in range(polls_per_attempt):
                if proc.poll() is not None:
                    break
                try:
                    targets = get_targets(port)
                    target = next((p.get("webSocketDebuggerUrl") for p in targets
                                   if p.get("type") == "page" and p.get("webSocketDebuggerUrl")), None)
                    if target:
                        return ChromeSession(proc, profile, target)
                except Exception:  # Browser may not yet have opened its CDP port.
                    pass
                await asyncio.sleep(poll_delay)
            errors.append(f"attempt {attempt}: Chrome did not offer a page CDP target")
        except (OSError, ValueError) as exc:
            errors.append(f"attempt {attempt}: {type(exc).__name__}")
        if proc is not None:
            ChromeSession(proc, profile, "").close()
        else:
            shutil.rmtree(profile, ignore_errors=True)
    raise RuntimeError(f"{role}: Chrome CDP startup failed after {attempts} attempts; " +
                       "; ".join(errors))
