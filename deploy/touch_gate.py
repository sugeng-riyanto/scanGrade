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
floor is a finding — and so is a builder that laid out with no controls in it at
all, because the scope is the release's own page and an empty form is a regression
rather than a page this gate cannot judge. (A page *without* the scope is the other
answer — `could not measure`, because a renamed scope and a page that is not the
builder are the same reading.)

The floor is measured the way the rule can be enforced: a control whose computed
`display` is `inline` is **not** measured, because `min-height`/`min-width` do not
apply to a non-replaced inline box — the rule cannot fix one, so flagging it would
be a finding no release could ever clear. The stylesheet's own comment records the
same limit. Hidden controls (no box, `display: none`) are skipped for the same
reason.

Exit codes
----------
  0  every measured control is at least the floor, at every width
  1  a finding — at least one control is under the floor, or the builder rendered
     with no controls in it at all (a real regression)
  2  could not measure — no browser, no credentials, the page did not render, the
     builder scope is not on the page, a navigation timed out. **Not** a pass, and
     deliberately distinct from one.

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


def browser_launch(browser, port, profile):
    """The argv and environment to start a headless browser the gate can drive.

    Two things the box taught, both invisible in a developer checkout:

    * **Chrome needs a writable `HOME`.** It writes crash reports, its mimeapps
      list and its singleton lock under `$HOME` even when `--user-data-dir` is
      given. The gates run as the checkout's owner via `runuser`, and a service
      account's home need not exist — on the VPS `scangrade`'s does not — so
      Chrome died before offering a debug target and the gate answered "could
      not measure" on every release while looking armed. The profile the gate
      already creates is the one directory it knows is writable, so it becomes
      `HOME` too.
    * **The sandbox is unavailable to root.** A browser started as root refuses
      to run it (`Running as root without --no-sandbox is not supported`) and
      never reaches its debug port. It is turned off only when the gate is root,
      so an ordinary deploy user keeps the sandbox.
    * **`/dev/shm` need not be big enough for a renderer.** A container's is 64 MB
      and a CI runner's is whatever the guest gives it; Chrome puts its renderer's
      shared buffers there, and a renderer that cannot allocate one dies *before*
      the browser offers a page target — which reads downstream as "the browser
      never offered a debug target", with nothing on the page having been wrong.
      The flag moves those buffers to `/tmp` and costs nothing where `/dev/shm` is
      generous. It is one of three known ways a launch can fail silently; the
      other two are above. `start_browser()` is what handles the rest by trying
      again and keeping what Chrome said.

    Returns `(argv, env)`; `profile` is used for both the data dir and `HOME`.
    """
    argv = [
        browser,
        "--headless=new",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        "--disable-features=Translate",
        "about:blank",
    ]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        argv.append("--no-sandbox")
    env = dict(os.environ)
    env["HOME"] = profile
    return argv, env


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
    """The one Chrome DevTools Protocol client in this tree, over one websocket.

    Three gates drive a headless browser — this one, `exam_render_gate.py` and the
    client half of `seb_door_gate.py` — and they need two different things from the
    same socket: the reply to a call, and the **events** the browser emits while a
    page loads. A page error and a request the page's own script made both arrive
    *while* a navigation is in flight, so they cannot be read afterwards: a `send`
    whose reply has already landed would be waiting for the next message instead.
    `send` therefore keeps every message that is not its own reply, and `pump`
    waits without asking for anything.

    The copies this replaces had drifted into two classes — one that kept events
    and one that dropped them — which is how one gate ends up blind to the event
    the other one watches for. `events` is optional so a caller that only issues
    commands does not have to keep a list.
    """

    def __init__(self, ws, events=None):
        self.ws = ws
        self.n = 0
        self.events = events if events is not None else []

    def _keep(self, msg):
        """An event, kept. Anything with a `method` is one; a reply is not."""
        if msg.get("method"):
            self.events.append(msg)

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
            self._keep(msg)

    async def wait_event(self, name, timeout=45):
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                return None
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=left))
            if msg.get("method") == name:
                return msg
            self._keep(msg)

    async def pump(self, seconds):
        """Read whatever the browser emits for `seconds`, asking for nothing.

        A timeout is the normal end of this call, not a failure: it means the
        browser had nothing else to say within the window.
        """
        deadline = time.time() + seconds
        while True:
            left = deadline - time.time()
            if left <= 0:
                return
            try:
                msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=left))
            except asyncio.TimeoutError:
                return
            self._keep(msg)


def _free_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


#: What a launch that never came up answers. One string, because the retry below
#: asks whether the failure was the launch's or the page's by matching it, and the
#: deployed log, the suite and `deploy/browser_gate.py` all quote it.
NO_TARGET = "the browser never offered a debug target"

#: How many times a browser is started before the gate reports that it could not
#: look. Two, because the failure this exists for is a *launch* that lost a race on
#: a loaded runner: in CI the same test passed in one job and failed in two others
#: on one commit (`test_browser_gates_can_fail`, aa3cd7e), with 4 of its 5 sibling
#: launches succeeding in the same job seconds either side. A retry is honest here
#: and a relaxation would not be: nothing about the page is retried, and two failed
#: launches are still exit 2 — loudly, because a box where Chrome cannot start must
#: never report green about a page it did not see.
LAUNCH_ATTEMPTS = 2


def _chrome_words(log_path, keep=3, limit=600):
    """The last words Chrome wrote, for a reason its reader can act on.

    The gates used to send the browser's stderr to `DEVNULL`, so every launch
    failure arrived as "never offered a debug target" — the one thing the message
    could not tell you was *why*, and the CI log above carried no more than that.
    The lines are joined rather than indented because they go into a `(chrome
    said: ...)` parenthetical on one line of a two-line reason.
    """
    try:
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return " | ".join(lines[-keep:])[:limit]


def _stop(proc):
    """End the browser process, politely and then not. Idempotent by nature."""
    try:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
    except Exception:  # noqa: BLE001 - a browser that died on its own is not an error
        pass


def start_browser(browser, attempts=LAUNCH_ATTEMPTS, seconds=20.0, poll_every=0.25,
                  extra_args=()):
    """Start a headless browser and wait for the page target it offers.

    Returns ``(proc, target, profile, reason)``: the running browser, the WebSocket
    URL of its first page and the profile directory it owns — or
    ``(None, None, None, reason)`` when no launch came up, the reason carrying
    Chrome's own last words and how many launches it took. The caller owns both the
    process and the directory and must end them (`_stop`, `shutil.rmtree`).

    `extra_args` is for a flag one gate needs on top of the shared launch — the SEB
    door accepts a self-signed certificate — and is inserted after the binary so it
    cannot displace the debugging port the wait below polls.

    This is the one place a gate starts a browser: `deploy/exam_render_gate.py` and
    `deploy/seb_door_gate.py` import it for the same reason they import
    `browser_launch` — there is one copy of how this box starts Chrome, so a fix
    reaches every gate that drives one.
    """
    import requests

    attempted = max(1, int(attempts))
    polls = max(1, int(seconds / poll_every))
    reason = ""
    for _launch in range(attempted):
        port = _free_port()
        profile = tempfile.mkdtemp(prefix="sg-browser-")
        argv, env = browser_launch(browser, port, profile)
        if extra_args:
            argv = [argv[0], *extra_args, *argv[1:]]
        log_path = os.path.join(profile, "chrome.log")
        # The child inherits the descriptor, so the parent's copy closes at the end
        # of this block and the file is complete by the time the browser is stopped.
        with open(log_path, "wb") as log:
            proc = subprocess.Popen(
                argv, stdout=subprocess.DEVNULL, stderr=log, env=env
            )
        target = None
        for _ in range(polls):
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
            time.sleep(poll_every)
        if target:
            return proc, target, profile, None
        _stop(proc)
        words = _chrome_words(log_path)
        shutil.rmtree(profile, ignore_errors=True)
        reason = NO_TARGET + (
            f" (chrome said: {words})"
            if words
            else " (chrome wrote nothing before it exited)"
        )
    if attempted > 1:
        reason = f"{reason} — after {attempted} launches"
    return None, None, None, reason


async def _measure(base_url, browser, cookies, widths):
    """Drive one browser through every width and return the per-width readings.

    A launch that never came up is tried again inside `start_browser`; a page that
    threw, redirected or did not render is **not** retried, because that is the
    page's answer and asking twice would only take longer to report the same thing.
    """
    import websockets

    proc, target, profile, reason = start_browser(browser)
    if target is None:
        return None, reason
    try:
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
        _stop(proc)
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

    if measured == 0:
        # The scope is on the page and **not one control the rule names is inside it**.
        # That is a builder whose form did not render — and counting it as zero
        # findings would be a clean answer about a page with nothing on it, which is
        # how a blank builder ships past a gate whose whole subject is the controls in
        # it. A page *without* the scope is a different answer and is handled above
        # (`not the exam builder`, exit 2): this one is the release's own page, so it
        # is a finding. Found by `tests/unit/test_browser_gates_can_fail.py`, which
        # runs this gate against a deliberately empty builder.
        lines.append(
            f"touch gate: FAILED — the exam builder rendered {SCOPE} with no controls "
            f"in it:"
        )
        lines.append(
            f"    - {len(SELECTORS)} control kinds are measured and not one of them is "
            f"on the page, at {len(readings)} tablet width(s)"
        )
        return EXIT_FINDING, lines, {"findings": [], "measured": 0,
                                     "reason": "empty-builder"}

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
