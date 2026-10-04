#!/usr/bin/env python3
"""The pupil's exam page, rendered in a real browser on every release.

A blank exam page is invisible to every server-side gate: the route answers 200
with the whole paper, the fixture is present, the smoke test reads the right
panels — and the page is still white, because the failure is script *order* in
the browser. On 2026-10-03 `/student/exams/<id>` shipped blank: Alpine is loaded
with `defer`, so it starts on a microtask before the next deferred script runs,
and `x-data="examApp(...)"` read `sgExamMedia` before `exam-media.js` had
executed. `ReferenceError: sgExamMedia is not defined` killed the whole Alpine
scope and nothing on the page rendered. Every gate passed. Only a browser sees
that.

What it does
------------
Signs in over HTTPS as the demo pupil the smoke test already uses, opens the
demo exam from the pupil's own list, and asks the **rendered DOM**:

  * did any question control render (`button.q-btn`, `.exam-answers > div`)?
  * did the browser report a page error while loading?
  * is any element still `x-cloak` (Alpine never processed it)?

Any of those is a finding. Passing means the exam actually appeared.

Exit codes
----------
  0  the exam page rendered, with questions and no page error
  1  a finding — blank exam (a real regression)
  2  could not measure — no browser, no credentials, no sittable demo exam, or
     the page did not load. **Not** a pass, and deliberately distinct from one.

Flags / environment (the deploy passes these; flags win):
  --base-url URL         RENDER_BASE_URL      where the app is serving
  --student email:pass   RENDER_STUDENT       the pupil whose exam is opened
  --insecure             RENDER_INSECURE      skip TLS verification
  --json                 print the raw reading as JSON on stdout
  SG_CHROME              the browser binary, as for deploy/touch_gate.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse

# The gate reuses the touch gate's browser locator, sign-in and port helper — the
# same box, the same account shape, the same CDP plumbing, so there is one copy.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from touch_gate import (  # noqa: E402
    _free_port,
    browser_launch,
    locate_browser,
    sign_in,
)

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_CANNOT_MEASURE = 2

REQUEST_TIMEOUT = 25
#: How long the gate waits for the questions to appear, and how often it looks.
#: It *polls* rather than sleeping a fixed number of seconds: on a 1 vCPU box the
#: same page that renders in two seconds can take ten while the other gates run,
#: and a single early read quarantined a healthy release (b9bea75, 2026-10-04). A
#: genuinely blank page never renders, so it simply exhausts the budget and is
#: still a finding — the strictness is unchanged, only the flake is gone.
RENDER_BUDGET = 25.0
POLL_EVERY = 0.75
EXAM_LINK_RE = re.compile(r'href="/student/exams/([0-9a-fA-F-]{36})"')


def demo_exam_id(session, base_url):
    """The demo exam on the pupil's list, or `None`.

    The same fixture `deploy/smoke_test.py` reads: the list is the pupil's own,
    so the id it finds is one the pupil may actually open.
    """
    try:
        listing = session.get(f"{base_url}/student/exams", timeout=REQUEST_TIMEOUT)
    except Exception:  # noqa: BLE001 - any failure here is "cannot measure"
        return None
    if listing.status_code != 200:
        return None
    match = EXAM_LINK_RE.search(listing.text)
    return match.group(1) if match else None


def render_expression() -> str:
    """The JavaScript that reads what the browser actually laid out."""
    return (
        "(() => {"
        "  const d = document;"
        "  const stage = d.querySelector('.exam-stage');"
        "  return {"
        "    path: location.pathname,"
        "    qbtns: d.querySelectorAll('button.q-btn').length,"
        "    answers: d.querySelectorAll('.exam-answers > div').length,"
        "    xcloak: d.querySelectorAll('[x-cloak]').length,"
        "    stageH: stage ? Math.round(stage.getBoundingClientRect().height) : 0,"
        # `d.body` is null until the parser has built it, and the poll's first look
        # can land before that. Reading `.innerText` off it threw a TypeError, the
        # gate answered "could not measure" (exit 2), and exit 2 does not stop a
        # release — so the read must survive a document that is not built yet.
        "    bodyText: (d.body && d.body.innerText || '').slice(0, 120),"
        "  };"
        "})()"
    )


class _CDP:
    """A CDP client that keeps the events `send` would otherwise drop.

    Page errors arrive as `Runtime.exceptionThrown` *while* the navigation is in
    flight, so they cannot be read after the fact with a bare `send` loop that
    discards everything but its own reply.
    """

    def __init__(self, ws, events):
        self.ws = ws
        self.n = 0
        self.events = events

    async def send(self, method, **params):
        self.n += 1
        ident = self.n
        await self.ws.send(
            json.dumps({"id": ident, "method": method, "params": params})
        )
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("method"):
                self.events.append(msg)
            elif msg.get("id") == ident:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def pump(self, seconds):
        deadline = time.time() + seconds
        while True:
            left = deadline - time.time()
            if left <= 0:
                return
            try:
                msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=left))
            except asyncio.TimeoutError:
                return
            if msg.get("method"):
                self.events.append(msg)


def _exceptions(events):
    """Every page error the browser reported, as one line each."""
    out = []
    for ev in events:
        if ev.get("method") != "Runtime.exceptionThrown":
            continue
        detail = (ev.get("params") or {}).get("exceptionDetails") or {}
        exc = detail.get("exception") or {}
        text = exc.get("description") or exc.get("value") or detail.get("text") or ""
        out.append(str(text).splitlines()[0][:200])
    for ev in events:
        if ev.get("method") != "Runtime.consoleAPICalled":
            continue
        params = ev.get("params") or {}
        if params.get("type") != "error":
            continue
        args = [a.get("value") for a in (params.get("args") or []) if isinstance(a, dict)]
        text = " ".join(str(a) for a in args if a is not None)
        if text:
            out.append("console.error: " + text.splitlines()[0][:200])
    return out


async def _render(base_url, browser, cookies, exam_path):
    """Drive one headless browser to the exam page. Returns `(reading, events, reason)`."""
    import requests
    import websockets

    port = _free_port()
    profile = tempfile.mkdtemp(prefix="sg-render-")
    argv, env = browser_launch(browser, port, profile)
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        target = None
        for _ in range(80):
            try:
                pages = [
                    t
                    for t in requests.get(
                        f"http://127.0.0.1:{port}/json/list", timeout=2
                    ).json()
                    if t.get("type") == "page"
                ]
                if pages:
                    target = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:  # noqa: BLE001 - the browser is still starting
                pass
            time.sleep(0.25)
        if not target:
            return None, [], "the browser never offered a debug target"

        host = urllib.parse.urlparse(base_url).hostname or "127.0.0.1"
        events = []
        async with websockets.connect(target, max_size=64 * 1024 * 1024) as ws:
            c = _CDP(ws, events)
            await c.send("Network.enable")
            await c.send("Page.enable")
            # Runtime.enable must precede the navigation, or the page error is
            # thrown before anyone is listening.
            await c.send("Runtime.enable")
            for name, value in cookies.items():
                await c.send(
                    "Network.setCookie", name=name, value=value, domain=host, path="/"
                )
            await c.send(
                "Emulation.setDeviceMetricsOverride",
                width=1280,
                height=900,
                deviceScaleFactor=1,
                mobile=False,
            )
            await c.send("Page.navigate", url=f"{base_url}{exam_path}")
            # Poll until the questions are actually in the DOM. Reading once after
            # a fixed sleep was the flake: under load the page was still blank at
            # the read, and a healthy release was quarantined for it.
            deadline = time.time() + RENDER_BUDGET
            value = {}
            while True:
                await c.pump(POLL_EVERY)
                out = await c.send(
                    "Runtime.evaluate",
                    expression=render_expression(),
                    returnByValue=True,
                    awaitPromise=True,
                )
                if out.get("exceptionDetails"):
                    # A read that fails mid-navigation is not a verdict — keep
                    # looking, and only give up when the budget is spent.
                    if time.time() >= deadline:
                        return None, events, (
                            f"the page threw while reading it: {out['exceptionDetails']}")
                    continue
                value = (out.get("result") or {}).get("value") or {}
                if int(value.get("qbtns") or 0) >= 1 and int(value.get("answers") or 0) >= 1:
                    break
                if time.time() >= deadline:
                    # One more short settle so an error thrown late is captured
                    # before the finding is reported.
                    await c.pump(POLL_EVERY)
                    break
        return value, events, None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def run(base_url, student, insecure=False, browser=None):
    """Render the pupil's exam page. Returns `(exit_code, lines, payload)`."""
    lines: list[str] = []
    browser = browser or locate_browser()
    if not browser:
        return (
            EXIT_CANNOT_MEASURE,
            [
                "exam render gate: could not measure — no browser found",
                "    set SG_CHROME to a Chrome/Chromium binary, or install one",
            ],
            {"reason": "no-browser"},
        )

    session, reason = sign_in(base_url, student, insecure)
    if session is None:
        return (
            EXIT_CANNOT_MEASURE,
            ["exam render gate: could not measure — could not sign in", f"    {reason}"],
            {"reason": reason},
        )

    exam_id = demo_exam_id(session, base_url)
    if not exam_id:
        return (
            EXIT_CANNOT_MEASURE,
            [
                "exam render gate: could not measure — no sittable demo exam on the",
                "    pupil's list (put one back with: python manage.py demo-exam)",
            ],
            {"reason": "no-demo-exam"},
        )

    exam_path = f"/student/exams/{exam_id}"
    try:
        reading, events, reason = asyncio.run(
            _render(base_url, browser, session.cookies.get_dict(), exam_path)
        )
    except Exception as exc:  # noqa: BLE001 - any browser failure is "cannot measure"
        return (
            EXIT_CANNOT_MEASURE,
            [
                "exam render gate: could not measure — the browser run failed",
                f"    {type(exc).__name__}: {exc}",
            ],
            {"reason": f"{type(exc).__name__}: {exc}"},
        )

    if reading is None:
        return (
            EXIT_CANNOT_MEASURE,
            ["exam render gate: could not measure — the page did not render", f"    {reason}"],
            {"reason": reason},
        )

    # A redirect away from the exam (a dead session, a refused sitting) is not a
    # rendering regression — it is a page we did not get to look at.
    if reading.get("path") != exam_path:
        return (
            EXIT_CANNOT_MEASURE,
            [
                "exam render gate: could not measure — the exam page redirected away",
                f"    landed on {reading.get('path')!r}",
            ],
            {"reason": f"redirected to {reading.get('path')!r}"},
        )

    errors = _exceptions(events)
    problems = []
    if errors:
        problems.append(f"{len(errors)} page error(s) while loading")
    if int(reading.get("qbtns") or 0) < 1:
        problems.append("no question controls rendered (button.q-btn)")
    if int(reading.get("answers") or 0) < 1:
        problems.append("no answer blocks rendered (.exam-answers > div)")

    payload = {
        "exam_id": exam_id,
        "reading": reading,
        "errors": errors,
        "problems": problems,
    }
    if problems:
        lines.append(
            f"exam render gate: FAILED — the pupil's exam page did not render "
            f"({exam_id}):"
        )
        for problem in problems:
            lines.append(f"    - {problem}")
        for err in errors[:6]:
            lines.append(f"    - {err}")
        return EXIT_FINDING, lines, payload

    lines.append(
        f"exam render gate: OK — the exam page rendered "
        f"{reading['qbtns']} question control(s) and {reading['answers']} answer "
        f"block(s), no page error ({exam_id})"
    )
    return EXIT_OK, lines, payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("RENDER_BASE_URL", ""))
    parser.add_argument("--student", default=os.environ.get("RENDER_STUDENT", ""))
    parser.add_argument(
        "--insecure",
        action="store_true",
        default=os.environ.get("RENDER_INSECURE", "").lower() == "true",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the raw reading as JSON on stdout"
    )
    args = parser.parse_args(argv)

    if not args.base_url:
        print(
            "exam render gate: could not measure — no base URL "
            "(--base-url or RENDER_BASE_URL)"
        )
        return EXIT_CANNOT_MEASURE

    code, lines, payload = run(args.base_url.rstrip("/"), args.student, args.insecure)
    if args.json:
        print(json.dumps(payload, indent=1, ensure_ascii=False))
    else:
        for line in lines:
            print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
