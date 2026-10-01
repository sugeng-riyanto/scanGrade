#!/usr/bin/env python3
"""The exam builder's finger floor, measured on a real browser.

The floor is a stylesheet rule: `app/static/css/theme.css` has a
`@media (pointer: coarse)` block that hands every control inside `.sg-exam-builder`
a 44px minimum in both axes. It was written after a measurement — **42 controls
under 40px** at every tablet width the page was opened at, **0** after the rule —
and that measurement was made once, by hand, in headless Chrome. A rule about
*laid-out* geometry cannot be kept by a grep, so this gate lays the page out for
every release and measures it again.

What it does
------------
Signs in over HTTPS as the teacher account the smoke test already uses, opens the
exam builder (`/teacher/exams/new`), emulates a finger (`pointer: coarse`, the
media query the rule is asked as), and at each documented tablet width — portrait
*and* landscape — measures every control the rule names. Any control under the
floor is a finding.

The floor is measured the way the rule can be enforced: a control whose computed
`display` is `inline` is **not** measured, because `min-height`/`min-width` do not
apply to a non-replaced inline box — the rule cannot fix one, so flagging it would
be a finding no release could ever clear. The stylesheet's own comment records the
same limit. Hidden controls (no box, `display: none`) are skipped for the same
reason.

Exit codes
----------
  0  every measured control is at least the floor, at every width
  1  a finding — at least one control is under the floor (a real regression)
  2  could not measure — no browser, no credentials, the page did not render,
     a navigation timed out. **Not** a pass, and deliberately distinct from one.

The distinction is the whole point: exit 2 is "we could not look", and the deploy
keeps a release it could not look at (loudly) rather than rejecting a good one over
a missing browser. Exit 1 is the regression the gate exists for.

Options
-------
  --base-url URL          where the app is serving (no trailing slash)
  --teacher email:pass    the account whose builder page is measured
  --insecure              skip TLS verification (a self-signed local box)
  --json                  print the raw measurement as JSON on stdout

Environment (the deploy passes these; flags win):
  TOUCH_BASE_URL, TOUCH_TEACHER, TOUCH_INSECURE
  SG_CHROME    the browser binary. Set — even empty — it is the *only* place
               looked, so `SG_CHROME=""` measures nothing instead of guessing.
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

FLOOR = 44
SCOPE = "sg-exam-builder"
PAGE = "/teacher/exams/new"

#: The rule's own selector list, minus the scope prefix. Held equal to the
#: stylesheet block by `tests/unit/test_touch_gate.py`, so the two cannot drift.
SELECTORS = (
    "select",
    'input[type="text"]',
    'input[type="url"]',
    'input[type="number"]',
    'input[type="search"]',
    "textarea",
    "button",
    "a[href]",
)

#: The tablet viewports the floor was measured at — portrait and landscape.
WIDTHS = ((820, 1180), (1180, 820), (800, 1280), (1280, 800), (1024, 768), (768, 1024))

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_CANNOT_MEASURE = 2

REQUEST_TIMEOUT = 25
CSRF_RE = re.compile(r'name="csrf-token"\s+content="([^"]+)"')

_OS_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)

_BROWSER_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
)


def locate_browser(env=None, which=shutil.which, exists=None):
    """The browser to drive, or `None`.

    `SG_CHROME` is authoritative: when the variable is present it is the only
    place looked, so an operator can point the gate at one binary and an empty
    value means "there is none here". Without it, the usual names on `PATH` and
    then the well-known install paths are tried.
    """
    env = os.environ if env is None else env
    exists = os.path.exists if exists is None else exists
    if "SG_CHROME" in env:
        candidate = (env.get("SG_CHROME") or "").strip()
        return candidate if candidate and exists(candidate) else None
    for name in _BROWSER_NAMES:
        found = which(name)
        if found:
            return found
    for candidate in _OS_CANDIDATES:
        if exists(candidate):
            return candidate
    return None


def findings(controls):
    """The controls under the floor, each naming the axis that failed.

    The same rule the stylesheet can actually enforce:

    * `display: none`, or an unlaid-out box (0x0), is not on the page and is not
      measured.
    * `display: inline` is skipped — `min-height`/`min-width` do not apply to a
      non-replaced inline box, so the rule cannot raise one and a finding would
      be unfixable.
    * width is checked before height, so an icon-only control (narrow, tall)
      reports the axis that is actually wrong.
    """
    under = []
    for control in controls:
        display = (control.get("display") or "").strip().lower()
        if display in ("none", "") or display == "inline":
            continue
        w, h = int(control.get("w") or 0), int(control.get("h") or 0)
        if w <= 0 or h <= 0:
            continue
        if w < FLOOR:
            under.append({**control, "axis": "width"})
        elif h < FLOOR:
            under.append({**control, "axis": "height"})
    return under


def measure_expression() -> str:
    """The JavaScript that reads every control the rule names, out of the page."""
    return (
        "(() => {"
        f"  const root = document.querySelector({json.dumps('.' + SCOPE)});"
        "  if (!root) return {error: 'no builder root on the page'};"
        f"  const sels = {json.dumps(list(SELECTORS))};"
        "  const out = [];"
        "  for (const sel of sels) {"
        "    for (const el of root.querySelectorAll(sel)) {"
        "      const cs = getComputedStyle(el);"
        "      const r = el.getBoundingClientRect();"
        "      out.push({selector: sel, display: cs.display,"
        "                w: Math.round(r.width), h: Math.round(r.height),"
        "                name: el.getAttribute('name') || el.id || '',"
        "                text: (el.value || el.textContent || '').trim().slice(0, 40)});"
        "    }"
        "  }"
        "  return {path: location.pathname, controls: out};"
        "})()"
    )


def sign_in(base_url, teacher, insecure, session=None):
    """A requests session with the teacher signed in, or a reason string.

    Returns `(session, None)` on success and `(None, reason)` otherwise. The
    login path and CSRF handling mirror `deploy/smoke_test.py`, because it is the
    same account and the same form.
    """
    import requests

    email, _, password = (teacher or "").partition(":")
    if not email or not password:
        return None, "no teacher credential (want email:password)"
    s = session or requests.Session()
    verify = not insecure
    try:
        page = s.get(
            f"{base_url}/auth/login-user", timeout=REQUEST_TIMEOUT, verify=verify
        )
    except Exception as exc:  # noqa: BLE001 - any failure here is "cannot measure"
        return None, f"cannot reach {base_url}/auth/login-user — {type(exc).__name__}"
    if page.status_code != 200:
        return None, f"GET /auth/login-user -> {page.status_code}"
    match = CSRF_RE.search(page.text)
    if not match:
        return None, "/auth/login-user carries no csrf-token meta tag"
    try:
        response = s.post(
            f"{base_url}/auth/login-user",
            data={"_csrf_token": match.group(1), "email": email, "password": password},
            timeout=REQUEST_TIMEOUT,
            verify=verify,
            allow_redirects=False,
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"POST /auth/login-user failed — {type(exc).__name__}"
    if response.status_code in (301, 302, 303, 307, 308):
        return s, None
    return None, f"credentials refused for {email} (POST -> {response.status_code})"


class _CDP:
    """A minimal Chrome DevTools Protocol client over one websocket."""

    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    async def send(self, method, **params):
        self.n += 1
        ident = self.n
        await self.ws.send(
            json.dumps({"id": ident, "method": method, "params": params})
        )
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == ident:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def wait_event(self, name, timeout=45):
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                return None
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=left))
            if msg.get("method") == name:
                return msg


def _free_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _measure(base_url, browser, cookies, widths):
    """Drive one browser through every width and return the per-width readings."""
    import requests
    import websockets

    port = _free_port()
    profile = tempfile.mkdtemp(prefix="sg-touch-")
    proc = subprocess.Popen(
        [
            browser,
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--disable-features=Translate",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
            return None, "the browser never offered a debug target"

        host = urllib.parse.urlparse(base_url).hostname or "127.0.0.1"
        readings = []
        async with websockets.connect(target, max_size=64 * 1024 * 1024) as ws:
            c = _CDP(ws)
            await c.send("Network.enable")
            await c.send("Page.enable")
            for name, value in cookies.items():
                await c.send(
                    "Network.setCookie", name=name, value=value, domain=host, path="/"
                )
            # Touch emulation is what makes `pointer: coarse` answer true; the
            # stylesheet rule is asked as a pointer, not a width.
            await c.send(
                "Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=5
            )
            width, height = widths[0]
            await c.send(
                "Emulation.setDeviceMetricsOverride",
                width=width,
                height=height,
                deviceScaleFactor=1,
                mobile=True,
            )
            await c.send("Page.navigate", url=f"{base_url}{PAGE}")
            await c.wait_event("Page.loadEventFired", timeout=60)
            await asyncio.sleep(4)

            expression = measure_expression()
            for width, height in widths:
                await c.send(
                    "Emulation.setDeviceMetricsOverride",
                    width=width,
                    height=height,
                    deviceScaleFactor=1,
                    mobile=True,
                )
                await asyncio.sleep(0.6)
                out = await c.send(
                    "Runtime.evaluate",
                    expression=expression,
                    returnByValue=True,
                    awaitPromise=True,
                    userGesture=True,
                )
                if out.get("exceptionDetails"):
                    return (
                        None,
                        f"the page threw during measurement: "
                        f"{str(out['exceptionDetails'])[:200]}",
                    )
                readings.append(
                    {
                        **(out.get("result", {}).get("value") or {}),
                        "width": width,
                        "height": height,
                    }
                )
        return readings, None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def run(base_url, teacher, insecure=False, browser=None, widths=WIDTHS):
    """Measure the builder. Returns `(exit_code, lines, payload)`."""
    lines: list[str] = []
    browser = browser or locate_browser()
    if not browser:
        return (
            EXIT_CANNOT_MEASURE,
            [
                "touch gate: could not measure — no browser found",
                "    set SG_CHROME to a Chrome/Chromium binary, or install one",
            ],
            {"reason": "no-browser"},
        )

    session, reason = sign_in(base_url, teacher, insecure)
    if session is None:
        return (
            EXIT_CANNOT_MEASURE,
            [
                "touch gate: could not measure — could not sign in",
                f"    {reason}",
            ],
            {"reason": reason},
        )

    try:
        readings, reason = asyncio.run(
            _measure(base_url, browser, session.cookies.get_dict(), tuple(widths))
        )
    except Exception as exc:  # noqa: BLE001 - any browser failure is "cannot measure"
        return (
            EXIT_CANNOT_MEASURE,
            [
                "touch gate: could not measure — the browser run failed",
                f"    {type(exc).__name__}: {exc}",
            ],
            {"reason": f"{type(exc).__name__}: {exc}"},
        )
    if readings is None:
        return (
            EXIT_CANNOT_MEASURE,
            [
                "touch gate: could not measure — the builder page did not render",
                f"    {reason}",
            ],
            {"reason": reason},
        )

    all_findings = []
    measured = 0
    for reading in readings:
        if reading.get("error"):
            return (
                EXIT_CANNOT_MEASURE,
                [
                    "touch gate: could not measure — the page is not the exam builder",
                    f"    {reading['error']}",
                ],
                {"reason": reading["error"]},
            )
        controls = reading.get("controls") or []
        measured += len(controls)
        for item in findings(controls):
            all_findings.append(
                {**item, "width": reading.get("width"), "height": reading.get("height")}
            )

    if all_findings:
        lines.append(
            f"touch gate: FAILED — {len(all_findings)} control(s) under "
            f"the {FLOOR}px finger floor on the exam builder:"
        )
        for item in all_findings[:12]:
            where = f"{item.get('width')}x{item.get('height')}"
            label = item.get("name") or item.get("text") or item.get("selector")
            lines.append(
                f"    - {item.get('selector')} ({label!r}) "
                f"{item.get('w')}x{item.get('h')} at {where} "
                f"— under on {item.get('axis')}"
            )
        if len(all_findings) > 12:
            lines.append(f"    ... and {len(all_findings) - 12} more")
        return EXIT_FINDING, lines, {"findings": all_findings, "measured": measured}

    lines.append(
        f"touch gate: OK — {measured} controls measured, none under "
        f"{FLOOR}px, at {len(readings)} tablet widths"
    )
    return EXIT_OK, lines, {"findings": [], "measured": measured}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("TOUCH_BASE_URL", ""))
    parser.add_argument("--teacher", default=os.environ.get("TOUCH_TEACHER", ""))
    parser.add_argument(
        "--insecure",
        action="store_true",
        default=os.environ.get("TOUCH_INSECURE", "").lower() == "true",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the raw measurement as JSON on stdout",
    )
    args = parser.parse_args(argv)

    if not args.base_url:
        print(
            "touch gate: could not measure — no base URL (--base-url or TOUCH_BASE_URL)"
        )
        return EXIT_CANNOT_MEASURE

    code, lines, payload = run(args.base_url.rstrip("/"), args.teacher, args.insecure)
    if args.json:
        print(json.dumps(payload, indent=1, ensure_ascii=False))
    else:
        for line in lines:
            print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
