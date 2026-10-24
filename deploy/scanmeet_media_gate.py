#!/usr/bin/env python3
"""ScanMeet's exam room, driven end to end by real browsers over CDP.

Why this exists
---------------
`docs/SCANMEET_BIZNET_STAGING.md` §2 asks a person to open two browsers, press
nine things and paste two console snippets into a sign-off. Three of those
readings are weaker than they look when a person takes them:

* **a microphone that is refused before a teacher approves it** is a permission
  held by the SFU, not a button that is hidden. A person records the button.
* **"frames are rising"** is the only evidence that decoded video arrived at
  all; a tile can be present, sized and completely empty.
* **per-pupil media isolation** is a thing a pupil's browser *did not receive*,
  which no screenshot of the page can show.

So this drives it. Three headless browsers over the Chrome DevTools Protocol,
signed in through the app's own login form, the page's own controls pressed with
real mouse events, and every reading taken twice: from the DOM, and from the
join token the server hands out — because the token's ``canPublishSources`` is
what LiveKit enforces, while the DOM is only what the page drew.

What each row proves
--------------------
``sdk-loaded``            the page reached the pinned LiveKit SDK (a page whose
                          script never loaded has no room to judge)
``session``               the teacher's own button opened a room, and the API
                          reports it active under that teacher
``pupil-camera``          the pupil's browser acquired a camera and is decoding
                          its own frames (two samples, FLOORS seconds apart)
``teacher-receives-camera``  the teacher's grid holds a tile per pupil, with
                          frames rising on it
``mic-starts-closed``     a fresh pupil token publishes the camera **only**, and
                          the page offers no microphone control
``hand-raise``            the request is a ``pending`` row in the API and a row
                          the teacher can see in the queue
``mic-approved``          after the teacher presses *Izinkan*, a fresh token
                          publishes camera + microphone, and the control appears
``audio-reaches-teacher`` after the pupil presses it, the teacher's page holds a
                          **live** audio track for the room
``mic-closed``            after *Akhiri bicara*, the token is back to camera only,
                          the control is gone and no live audio remains
``pupil-isolation``       one pupil's page never received another pupil's camera
                          (judged by participant identity, not by tile count)
``outsider-refused``      another school's pupil is refused the page and the token
``room-ended``            after *Akhiri Live*, a new join token is refused

Exit codes follow the other instruments here: 0 every row passed, 1 a row
disagreed, 2 could not measure — a missing account, a deployment with Live off,
a page that never loaded. An incomplete run is **2**, never 0: the three rows
this driver exists for are the ones that cannot be skipped.

Limits, stated rather than discovered
-------------------------------------
* The cameras are Chrome's synthetic device (``--use-fake-device-for-media-stream``):
  a real ``getUserMedia`` track, a real publish, a real SFU subscription and real
  decoded frames, with a test pattern where a face would be. ``--no-fake-devices``
  drops those flags for a box with hardware; the driver then pre-grants
  ``videoCapture``/``audioCapture`` through the Browser domain and says so when
  the grant failed.
* It cannot read the SFU's logs. It reads what a pupil's browser received, which
  is the claim that matters.
* A ~30 s network drop (§2 #09) is **not** driven: that is thirty seconds of an
  operator's reconnect, and a driver would be measuring
  ``Network.emulateNetworkConditions`` rather than LiveKit.
* The queue buttons are addressed by the labels ``app/static/js/scan-live-exam.js``
  renders (``Izinkan``, ``Akhiri bicara``). They are constants here with a test
  holding them to that script, because a reworded label must fail this file's own
  suite rather than make a press silently miss.

Usage (on the staging host, with the staging env exported):

    python -m deploy.scanmeet_media_gate \\
      --url https://staging.example.edu --exam 9b17e852-... \\
      --teacher guru@example.edu:pw --student murid1@example.edu:pw \\
      --student2 murid2@example.edu:pw --outsider murid@lain.example.edu:pw
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
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import touch_gate  # noqa: E402  (the one CDP client, launcher and sign-in here)

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_CANNOT_MEASURE = 2

PAGE = "/live/exam/{exam_id}"
STATUS = "/api/live/exams/{exam_id}"
START = "/api/live/exams/{exam_id}/sessions"
TOKEN = "/api/live/sessions/{sid}/token"
RAISES = "/api/live/sessions/{sid}/hand-raises"

CAMERA = "camera"
MICROPHONE = "microphone"

#: The moderation labels the page renders for the two presses this driver makes.
#: Held to `app/static/js/scan-live-exam.js` by the test suite: a press is
#: addressed by what the teacher sees, so a reworded button has to be a red test.
LABEL_APPROVE = "Izinkan"
LABEL_CLOSE = "Akhiri bicara"

#: How long decoded frames are watched before "is it moving?" is answered.
FLOORS = 3.0

#: The status the queue row carries for an open request, as the API spells it.
PENDING = "pending"

STATUS_RE = re.compile(r"[\u2014-]\s*(" + "|".join(
    ("pending", "approving", "approved", "closing", "rejected", "closed")) + r")\s*$")


class CannotMeasure(RuntimeError):
    """The deployment could not be asked — not the same as failing."""


# --------------------------------------------------------------------------- #
# The reading
# --------------------------------------------------------------------------- #
@dataclass
class Reading:
    """One page, read once. Everything here comes out of the browser."""

    path: str = ""
    status: str = ""
    warning: bool = False
    livekit: bool = False
    #: `[{identity, w, h, ready, frames, muted, paused}]`, one per tile video.
    videos: list = field(default_factory=list)
    #: `[{tracks, live}]`, one per hidden `<audio>` the page attached.
    audio: list = field(default_factory=list)
    mic_visible: bool = False
    mic_label: str = ""
    hand_enabled: bool = False
    #: `[{name, status}]` parsed out of the teacher's queue rows.
    queue: list = field(default_factory=list)
    events: list = field(default_factory=list)
    error: str = ""

    def video_for(self, identity: str):
        return next((v for v in self.videos if v.get("identity") == identity), None)

    def identities(self) -> list[str]:
        return [str(v.get("identity") or "") for v in self.videos]

    def audio_live(self) -> int:
        """Live, unmuted audio tracks the page is holding for other people."""
        return sum(int(a.get("live") or 0) for a in self.audio)

    def queue_states(self) -> list[str]:
        return [row["status"] for row in self.queue]

    def as_dict(self) -> dict:
        return {
            "path": self.path, "status": self.status, "warning": self.warning,
            "livekit": self.livekit, "videos": self.videos, "audio": self.audio,
            "mic_visible": self.mic_visible, "mic_label": self.mic_label,
            "hand_enabled": self.hand_enabled, "queue": self.queue,
            "events": self.events, "error": self.error,
        }


def snapshot_expression() -> str:
    """The JavaScript that reads one page, out of the page.

    Deliberately DOM-only: the room object lives inside the page's own closure,
    and a hook added to the shipped script so a driver could reach in would be a
    change to what is under test. Every claim here is one a person could take
    with the console snippet in §2 — the driver just takes it twice, at known
    moments, and remembers it.

    The queue's status is read from the row's **description**, not from the row:
    `textContent` of the whole row ends with the last label the page drew
    ("… — pendingIzinkan"), so a status parsed from it becomes the label's
    tail. Found by this file's own test, on the first run.
    """
    return (
        "(() => {"
        "  const root = document.getElementById('scan-live');"
        "  if (!root) return {error: 'no live page on this URL'};"
        "  const byId = id => document.getElementById(id);"
        "  const grid = byId('live-grid');"
        "  const videos = grid ? [...grid.querySelectorAll('video')].map(v => {"
        "    const tile = v.closest('[data-identity]');"
        "    return {"
        "      identity: tile ? (tile.dataset.identity || '') : '',"
        "      w: v.videoWidth, h: v.videoHeight, ready: v.readyState,"
        "      frames: v.getVideoPlaybackQuality"
        "        ? v.getVideoPlaybackQuality().totalVideoFrames : -1,"
        "      muted: !!v.muted, paused: !!v.paused,"
        "    };"
        "  }) : [];"
        "  const audio = [...root.querySelectorAll('audio')].map(a => {"
        "    const stream = a.srcObject;"
        "    const tracks = stream && stream.getAudioTracks ? stream.getAudioTracks() : [];"
        "    return {tracks: tracks.length,"
        "            live: tracks.filter(t => t.readyState === 'live' && !t.muted).length};"
        "  });"
        "  const queue = [...document.querySelectorAll('#live-queue > div')].map(row => {"
        "    const desc = [...row.childNodes]"
        "      .filter(n => n.nodeType !== 1 || n.tagName !== 'BUTTON')"
        "      .map(n => n.textContent || '').join('').trim();"
        "    const m = /([a-z]+)\\s*$/.exec(desc);"
        "    return {text: row.textContent.trim(), desc,"
        "            buttons: [...row.querySelectorAll('button')]"
        "              .map(b => b.textContent.trim()),"
        "            status: m ? m[1] : ''};"
        "  });"
        "  const events = [...document.querySelectorAll('#live-events > p')]"
        "    .map(p => p.textContent.trim());"
        "  const mic = byId('live-mic'), hand = byId('live-hand');"
        "  const status = byId('live-status'), warning = byId('live-warning');"
        "  const micVisible = !!mic && !mic.hidden && mic.getBoundingClientRect().width > 0;"
        "  return {"
        "    path: location.pathname,"
        "    status: status ? status.textContent.trim() : '',"
        "    warning: !!warning && !warning.hidden,"
        "    livekit: !!window.LivekitClient,"
        "    videos, audio, events, queue,"
        "    mic_visible: micVisible,"
        "    mic_label: mic ? mic.textContent.trim() : '',"
        "    hand_enabled: !!hand && !hand.disabled,"
        "  };"
        "})()"
    )


def reading_of(value, name: str = "") -> Reading:
    """A browser's `snapshot_expression()` answer, as a `Reading`."""
    if not isinstance(value, dict):
        return Reading(error=f"{name or 'the page'} answered {type(value).__name__}, "
                             f"not a reading")
    if value.get("error"):
        return Reading(error=str(value["error"]))
    known = {key: value.get(key) for key in Reading.__dataclass_fields__ if key in value}
    for item in known.get("queue") or []:
        if not item.get("status"):
            # The description, never the whole row: its tail is the last button the
            # page drew, which is a label and not a state.
            match = STATUS_RE.search(str(item.get("desc") or item.get("text") or ""))
            item["status"] = match.group(1) if match else ""
    return Reading(**known)


# --------------------------------------------------------------------------- #
# The token: what the SFU is told to allow
# --------------------------------------------------------------------------- #
def grant(token: str) -> dict:
    """A join token's `video` grant, read **without** verifying the signature.

    Not verifying is the point: the token was just issued by the deployment under
    test over TLS, and the question is what it says, not whether this script
    could have forged it. `--json` never carries the token itself — only the
    decoded grants — so a run's output stays safe to paste into a sign-off.
    """
    import jwt

    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except Exception as exc:  # noqa: BLE001 - a token this script cannot read is a finding
        raise CannotMeasure(f"the join token is not a JWT ({type(exc).__name__})") from exc
    return payload.get("video") or {}


def publish_sources(token: str) -> list[str]:
    """The publish sources a token grants, lower-cased and sorted."""
    return sorted(str(source).strip().lower()
                  for source in (grant(token).get("canPublishSources") or []))


def may_publish_data(token: str) -> bool:
    return bool(grant(token).get("canPublishData"))


# --------------------------------------------------------------------------- #
# The judgements (pure: no browser, no network — so they can be driven by a test)
# --------------------------------------------------------------------------- #
def camera_finding(first: Reading, second: Reading, identity: str, who: str) -> str:
    """Is `who`'s camera actually arriving and decoding, over two samples?"""
    if first.error:
        return f"{who} did not answer: {first.error}"
    tile = first.video_for(identity)
    if tile is None:
        return (f"{who}'s page has no camera tile for that participant "
                f"({len(first.videos)} video(s) on it)")
    if not int(tile.get("w") or 0) or not int(tile.get("h") or 0):
        return (f"{who}'s camera element never received a frame of any size "
                f"(videoWidth 0, readyState {tile.get('ready')})")
    if tile.get("paused"):
        return f"{who}'s camera element is paused, so nothing is being decoded"
    later = second.video_for(identity) if not second.error else None
    if later is None:
        return f"{who}'s camera element disappeared between the two samples"
    before_frames = int(tile.get("frames") or 0)
    after_frames = int(later.get("frames") or 0)
    if after_frames <= before_frames:
        return (f"{who}'s decoded frames did not advance ({before_frames} -> "
                f"{after_frames} in {FLOORS:g}s): the track is published but not "
                f"arriving")
    return ""


def isolation_finding(own: Reading, *, own_identity: str, supervisor: str,
                      other_pupil: str) -> str:
    """Did one pupil's page receive another pupil's camera?

    Judged by **participant identity**, not by how many tiles are on the page. A
    count is the wrong question twice over: a pupil legitimately holds their own
    tile plus the supervisor's, so "one video" is only true while the teacher has
    no camera — and a leaked tile would still be one video if the pupil's own had
    not arrived. The identity is what the SFU's subscription permission is about.
    """
    if own.error:
        return f"the pupil's page did not answer: {own.error}"
    if not own.videos:
        return ("the pupil's page holds no camera at all, so this reading cannot "
                "say whether another pupil's media was withheld")
    seen = own.identities()
    if own_identity not in seen:
        return ("the pupil's own camera is not on their page, so the page is not the "
                "one this reading is about")
    if other_pupil and other_pupil in seen:
        return ("this pupil is receiving another pupil's camera: the room's "
                "subscription permission is not being applied")
    foreign = [i for i in seen if i and i not in (own_identity, supervisor)]
    if foreign:
        return (f"this pupil is receiving {len(foreign)} camera(s) from a participant "
                f"they may not see ({', '.join(sorted(foreign))})")
    return ""


@dataclass
class Row:
    """One line of the verdict."""

    name: str
    state: str  # "pass" | "fail" | "skipped"
    detail: str = ""


def row(name: str, finding: str = "", *, skip: str = "", ok: str = "") -> Row:
    """A row out of a judgement: a finding fails, a skip is unmeasured."""
    if skip:
        return Row(name, "skipped", skip)
    if finding:
        return Row(name, "fail", finding)
    return Row(name, "pass", ok)


def verdict(rows: list[Row]) -> int:
    """A finding outranks a gap: a broken room is actionable, a gap is not."""
    if any(item.state == "fail" for item in rows):
        return EXIT_FINDING
    if any(item.state == "skipped" for item in rows):
        return EXIT_CANNOT_MEASURE
    return EXIT_OK


# --------------------------------------------------------------------------- #
# HTTP: the API's own answers, and the CSRF token the page gives its script
# --------------------------------------------------------------------------- #
def api(session, base_url: str, path: str, *, method: str = "GET", csrf: str = "",
        insecure: bool = False, timeout: int = 30):
    """`(status, body)` for one API call, with transport failure as "cannot measure"."""
    import requests

    headers = {"Accept": "application/json"}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    try:
        answer = session.request(method, base_url + path, headers=headers,
                                 timeout=timeout, verify=not insecure)
    except requests.RequestException as exc:
        raise CannotMeasure(f"{method} {path} - {type(exc).__name__}") from exc
    try:
        body = answer.json()
    except ValueError:
        body = None
    return answer.status_code, body


def csrf_for(session, base_url: str, exam_id: str, *, insecure: bool = False):
    """The page's own CSRF token, as its script reads it off `data-csrf`."""
    import requests

    url = base_url + PAGE.format(exam_id=exam_id)
    try:
        answer = session.get(url, timeout=30, verify=not insecure)
    except requests.RequestException as exc:
        raise CannotMeasure(f"GET the live page - {type(exc).__name__}") from exc
    if answer.status_code != 200:
        return None, answer.status_code
    match = re.search(r'data-csrf="([^"]+)"', answer.text)
    return (match.group(1) if match else None), answer.status_code


def split_credentials(spec: str) -> tuple[str, str]:
    email, _, password = (spec or "").partition(":")
    return email.strip(), password


#: What printed output folds, and why: a row's detail can carry a string this
#: file did not write (`touch_gate.sign_in`'s reason, the page's own Indonesian),
#: and an em dash that cannot be encoded once crashed a guard on a cp1252 console.
#: The report is ASCII on purpose; the payload keeps the page's own text for a
#: reader who wants it.
FOLD = {"\u2014": "-", "\u2013": "-", "\u2026": "...", "\u2192": "->",
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
        "\u00a7": "S", "\u00d7": "x"}


def ascii_safe(text) -> str:
    """A printed line that cannot fail on an unhelpful console."""
    return "".join(FOLD.get(ch, ch if ord(ch) < 128 else "?") for ch in str(text))


# --------------------------------------------------------------------------- #
# One browser: launched, driven and read over the tree's own CDP client
# --------------------------------------------------------------------------- #
def launch_argv(binary: str, port: int, profile: str, *, fake: bool = True,
                video_file: str = "", size=(1280, 900)) -> tuple[list, dict]:
    """The launcher `touch_gate` builds, plus what a media page needs from it.

    Four additions matter and each was learned the hard way:

    * ``--autoplay-policy=no-user-gesture-required`` — the page attaches a remote
      camera **unmuted**, and Chrome refuses to autoplay that without a gesture,
      so the element stays paused and a frame counter never moves. A driver that
      did not pass this would report every remote camera as dead.
    * ``--use-fake-ui-for-media-stream`` — accepts the camera/microphone prompt,
      so a headless browser can publish without a person clicking Allow.
    * the three backgrounding flags — a headless tab that Chrome decides is
      hidden stops its media loop, and a frame counter is exactly what stops
      with it.
    * ``--disable-dev-shm-usage`` — a container's `/dev/shm` is 64 MB, and two
      browsers decoding video at once is what this driver adds. A renderer that
      cannot get shared memory dies before the browser offers a debug target,
      which reads as "the browser never started" rather than as the resource it
      is. Harmless on a box with a real `/dev/shm`.
    """
    argv, env = touch_gate.browser_launch(binary, port, profile)
    extra = [
        f"--window-size={int(size[0])},{int(size[1])}",
        "--autoplay-policy=no-user-gesture-required",
        "--use-fake-ui-for-media-stream",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        "--disable-dev-shm-usage",
    ]
    if fake:
        extra.append("--use-fake-device-for-media-stream")
        if video_file:
            extra.append(f"--use-file-for-fake-video-capture={video_file}")
    return argv[:-1] + extra + argv[-1:], env


class Browser:
    """One headless browser, kept open across the steps of a run.

    `touch_gate._measure` launches its browser for a single measurement and quits;
    a room needs three of them to stay joined while the others act, so this owns
    the process and the socket for the length of the run and closes both in
    `close()` whatever happened.
    """

    def __init__(self, name: str, base_url: str, cookies: dict, *, binary: str,
                 fake: bool = True, video_file: str = "", size=(1280, 900)):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.cookies = dict(cookies or {})
        self.binary = binary
        self.fake = fake
        self.video_file = video_file
        self.size = size
        self.notes: list[str] = []
        self.proc = None
        self.profile = ""
        self.ws = None
        self.cdp = None
        self.port = 0
        #: Ids for presses written without awaiting a reply, in a range no
        #: awaited command reaches, so a stray reply can never be taken for one.
        self._tap_id = 10 ** 6

    # -- lifecycle --------------------------------------------------------- #
    async def open(self):
        import websockets

        self.port = touch_gate._free_port()
        self.profile = tempfile.mkdtemp(prefix=f"sg-media-{self.name}-")
        argv, env = launch_argv(self.binary, self.port, self.profile,
                                fake=self.fake, video_file=self.video_file,
                                size=self.size)
        self.proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, env=env)
        target = await self._target()
        if not target:
            raise CannotMeasure("the browser never offered a debug target")
        self.ws = await websockets.connect(target, max_size=64 * 1024 * 1024)
        self.cdp = touch_gate._CDP(self.ws)
        await self.cdp.send("Network.enable")
        await self.cdp.send("Page.enable")
        await self.cdp.send("Runtime.enable")
        host = urllib.parse.urlparse(self.base_url).hostname or "127.0.0.1"
        for name, value in self.cookies.items():
            await self.cdp.send("Network.setCookie", name=name, value=value,
                                domain=host, path="/")
        await self._grant_media()

    async def _target(self):
        import requests

        for _ in range(80):
            try:
                pages = [
                    item for item in requests.get(
                        f"http://127.0.0.1:{self.port}/json/list", timeout=2
                    ).json()
                    if item.get("type") == "page"
                ]
                if pages:
                    return pages[0]["webSocketDebuggerUrl"]
            except Exception:  # noqa: BLE001 - the browser is still starting
                pass
            await asyncio.sleep(0.25)
        return None

    async def _grant_media(self):
        """Pre-grant the camera and microphone to this origin, if Chrome allows.

        `--use-fake-ui-for-media-stream` already answers the prompt, so this is
        belt and braces on the fake device and the only grant that exists on a
        box running with `--no-fake-devices`. A browser that refuses the call is
        noted, not fatal: the flags above are what the run depends on.
        """
        origin = self.base_url
        try:
            await self.cdp.send("Browser.grantPermissions", origin=origin,
                                permissions=["videoCapture", "audioCapture"])
        except Exception as exc:  # noqa: BLE001 - an older or locked-down browser
            self.notes.append(f"camera/microphone pre-grant refused ({exc})")

    async def close(self):
        try:
            if self.ws is not None:
                await self.ws.close()
        except Exception:  # noqa: BLE001 - the socket may already be gone
            pass
        self.ws = None
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                self.proc.kill()
            self.proc = None
        if self.profile:
            shutil.rmtree(self.profile, ignore_errors=True)
            self.profile = ""

    # -- driving ----------------------------------------------------------- #
    async def goto(self, path: str, *, timeout: int = 90):
        await self.cdp.send("Page.navigate", url=self.base_url + path)
        await self.cdp.wait_event("Page.loadEventFired", timeout=timeout)

    async def evaluate(self, expression: str):
        out = await self.cdp.send("Runtime.evaluate", expression=expression,
                                  returnByValue=True, awaitPromise=True,
                                  userGesture=True)
        if out.get("exceptionDetails"):
            raise CannotMeasure(
                f"{self.name}: the page threw - "
                f"{str(out['exceptionDetails'])[:300]}")
        return (out.get("result") or {}).get("value")

    async def read(self) -> Reading:
        return reading_of(await self.evaluate(snapshot_expression()), self.name)

    async def _box(self, finder: str):
        """Where a control is on the page, or `None` when it is not pressable.

        A hidden control is the point: the driver must not press the coordinates
        where a control *would* be and report success for a click on nothing.
        """
        return await self.evaluate(
            "(() => {"
            f"  const el = ({finder});"
            "  if (!el) return null;"
            "  const r = el.getBoundingClientRect();"
            "  if (!r.width || !r.height) return null;"
            "  return {x: r.left + r.width / 2, y: r.top + r.height / 2};"
            "})()"
        )

    async def press(self, finder: str) -> bool:
        """Press whatever `finder` resolves to, with real mouse events.

        Addressed by the element's own box, so a hidden control (or one that is
        not there) is a `False` rather than a click at the top-left of the page:
        a control the driver cannot see is a control a pupil cannot press.
        """
        box = await self._box(finder)
        if not box:
            return False
        for kind in ("mousePressed", "mouseReleased"):
            await self.cdp.send("Input.dispatchMouseEvent", type=kind,
                                x=box["x"], y=box["y"], button="left",
                                clickCount=1)
        return True

    async def tap(self, finder: str) -> bool:
        """Press without waiting for the browser to answer the press.

        For a control that opens `confirm()`: the page's handler blocks on the
        modal, and a command that waits for its reply waits for a page that is
        waiting for the dialog — a deadlock this file hit on its first harness
        run. The events are written straight to the socket; the replies are
        discarded by the next `send`, which matches on its own id.
        """
        box = await self._box(finder)
        if not box:
            return False
        for kind in ("mousePressed", "mouseReleased"):
            self._tap_id += 1
            await self.ws.send(json.dumps({
                "id": self._tap_id,
                "method": "Input.dispatchMouseEvent",
                "params": {"type": kind, "x": box["x"], "y": box["y"],
                           "button": "left", "clickCount": 1},
            }))
        return True

    async def click(self, selector: str) -> bool:
        return await self.press(f"document.querySelector({json.dumps(selector)})")

    async def press_label(self, label: str, container: str = "#live-queue") -> bool:
        """Press the queue button the page labels `label`."""
        finder = (f"[...document.querySelectorAll({json.dumps(container + ' button')})]"
                  f".find(b => b.textContent.trim() === {json.dumps(label)})")
        return await self.press(finder)

    async def wait(self, expression: str, timeout: float, interval: float = 0.4):
        """Poll an expression until it is truthy; the value, or `None`."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = await self.evaluate(expression)
            if value:
                return value
            await asyncio.sleep(interval)
        return None

    async def accept_dialog(self, timeout: float = 6.0) -> bool:
        """Accept the `confirm()` a control opens, if it opened one.

        `Akhiri Live` asks before it ends the room; a press with no answer leaves
        the page waiting on a modal and every later reading returns the page it
        was already showing. The dialog arrives as an *event*, which is why the
        client keeps them.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            event = await self.cdp.wait_event("Page.javascriptDialogOpening",
                                              timeout=max(1.0, deadline - time.time()))
            if event is not None:
                await self.cdp.send("Page.handleJavaScriptDialog", accept=True)
                return True
            await asyncio.sleep(0.2)
        return False


#: How long a step may take before it is a finding rather than a wait. The page
#: polls its own queue every 4 s and LiveKit's SFU needs a round trip, so a
#: reading that arrives late is normal; a reading that never arrives is not.
STEP_TIMEOUT = 30.0
CONNECT_TIMEOUT = 90.0

#: The whole room, from sign-in to the last token probe. Generous enough for a
#: slow staging box and short enough that a stalled page is reported by name.
TOTAL_TIMEOUT = 300.0

MIC_VISIBLE = ("(() => { const m = document.getElementById('live-mic');"
               " return !!m && !m.hidden && m.getBoundingClientRect().width > 0; })()")
MIC_GONE = ("(() => { const m = document.getElementById('live-mic');"
            " return !m || m.hidden || !m.getBoundingClientRect().width; })()")
AUDIO_LIVE = ("[...document.querySelectorAll('#scan-live audio')].some(a => {"
              " const s = a.srcObject; const t = s && s.getAudioTracks"
              " ? s.getAudioTracks() : [];"
              " return t.some(x => x.readyState === 'live' && !x.muted); })")
AUDIO_SILENT = ("![...document.querySelectorAll('#scan-live audio')].some(a => {"
                " const s = a.srcObject; const t = s && s.getAudioTracks"
                " ? s.getAudioTracks() : [];"
                " return t.some(x => x.readyState === 'live' && !x.muted); })")
HAND_READY = ("(() => { const h = document.getElementById('live-hand');"
              " return !!h && !h.disabled; })()")
TEACHER_READY = ("(() => { const e = document.getElementById('live-end');"
                 " return !!e && !e.hidden; })()")
QUEUE_PENDING = ("[...document.querySelectorAll('#live-queue > div')]"
                 ".some(d => /pending/.test(d.textContent))")
QUEUE_HAS_APPROVED = ("[...document.querySelectorAll('#live-queue > div')]"
                      ".some(d => /approved/.test(d.textContent))")


class Driver:
    """One run: sign in, open three browsers, take the readings, judge them.

    Kept as a class so a failure part-way through still closes every browser
    (`finally`) and still returns the rows taken before it — a run that stopped
    at the microphone is evidence about the camera, and throwing it away is how a
    half-answer reads as no answer.
    """

    def __init__(self, base_url: str, exam_id: str, accounts: dict, *,
                 insecure: bool = False, binary: str = "", fake: bool = True,
                 video_file: str = "", keep_room: bool = False):
        self.base_url = base_url.rstrip("/")
        self.exam_id = exam_id
        self.accounts = accounts
        self.insecure = insecure
        self.binary = binary
        self.fake = fake
        self.video_file = video_file
        self.keep_room = keep_room
        self.sessions: dict[str, object] = {}
        self.csrf: dict[str, str] = {}
        self.identities: dict[str, str] = {}
        self.sid = ""
        self.rows: list[Row] = []
        self.cannot = ""
        #: What the run is in the middle of, for a budget that runs out.
        self.step = ""
        self.payload: dict = {"base_url": self.base_url, "exam_id": exam_id,
                              "fake_devices": fake, "readings": {}}
        self.browsers: dict[str, Browser] = {}

    # -- plumbing ---------------------------------------------------------- #
    def _signed_in(self, role: str) -> bool:
        return role in self.sessions

    async def _browser(self, role: str) -> Browser:
        if role in self.browsers:
            return self.browsers[role]
        browser = Browser(role, self.base_url,
                          self.sessions[role].cookies.get_dict(),
                          binary=self.binary, fake=self.fake,
                          video_file=self.video_file)
        await browser.open()
        self.browsers[role] = browser
        return browser

    def _page(self) -> str:
        return PAGE.format(exam_id=self.exam_id)

    def _token(self, role: str) -> dict:
        """A fresh join token for a role, as the page's own script would get one."""
        code, body = api(self.sessions[role], self.base_url,
                         TOKEN.format(sid=self.sid), method="POST",
                         csrf=self.csrf.get(role, ""), insecure=self.insecure)
        if code != 200 or not isinstance(body, dict) or not body.get("token"):
            raise CannotMeasure(f"the token route answered HTTP {code} for {role}")
        return body

    def _room(self) -> dict:
        code, body = api(self.sessions["teacher"], self.base_url,
                         STATUS.format(exam_id=self.exam_id),
                         insecure=self.insecure)
        if code != 200 or not isinstance(body, dict):
            raise CannotMeasure(f"the live status route answered HTTP {code}")
        return body

    async def _close_all(self):
        for browser in list(self.browsers.values()):
            await browser.close()
        self.browsers.clear()

    # -- the run ----------------------------------------------------------- #
    async def run(self) -> None:
        self.step = "signing in"
        self._sign_in()
        room = self._room()
        session = room.get("session") or None
        self.payload["role"] = room.get("role")

        self.step = "the teacher's page"
        teacher = await self._browser("teacher")
        await teacher.goto(self._page())
        opened = await teacher.read()
        self.rows.append(row(
            "sdk-loaded",
            skip="" if opened.path else "the live page did not render",
            finding="" if opened.livekit else
            f"the page never loaded the pinned LiveKit SDK (`{opened.status}`), so "
            f"there is no room to judge",
            ok="the page reached the pinned SDK"))
        if not opened.livekit:
            self.rows.extend(self._unmeasured("the SDK did not load"))
            return

        if session is None:
            self.step = "opening the room"
            if not await teacher.click("#live-start"):
                self.rows.append(Row("session", "fail",
                                     "the teacher's page offers no *Mulai pengawasan* "
                                     "control to press"))
                self.rows.extend(self._unmeasured("no room was opened"))
                return
            session = await self._wait_session(teacher)
        if not session or not session.get("id"):
            reading = await teacher.read()
            self.rows.append(Row("session", "fail",
                                 "pressing *Mulai pengawasan* opened no room "
                                 f"(the page says `{reading.status}`)"))
            self.rows.extend(self._unmeasured("no room was opened"))
            return
        self.sid = session["id"]
        self.payload["session"] = {"id": self.sid, "status": session.get("status")}

        connected = await teacher.wait(TEACHER_READY, CONNECT_TIMEOUT)
        self.rows.append(row(
            "session",
            skip="" if connected else "the room opened but the teacher's browser "
                                      "never joined it",
            finding="" if connected else
            f"the room is {session.get('status')!r} but the teacher's page never "
            f"joined it (`{(await teacher.read()).status}`)",
            ok=f"the room opened ({self.sid[:8]}...) and the teacher joined it"))
        if not connected:
            self.rows.extend(self._unmeasured("the teacher never joined the room"))
            return

        teacher_identity = self._identities("teacher")
        self.step = "the camera readings"
        self.rows.extend(await self._media_rows(teacher, teacher_identity))
        self.step = "the microphone"
        self.rows.extend(await self._microphone_rows(teacher))
        self.step = "the other school's door"
        self.rows.extend(self._outsider_rows())
        self.step = "ending the room"
        self.rows.append(await self._end_row(teacher))

    async def _wait_session(self, teacher: Browser):
        deadline = time.time() + STEP_TIMEOUT
        while time.time() < deadline:
            await asyncio.sleep(1.0)
            code, body = api(self.sessions["teacher"], self.base_url,
                             STATUS.format(exam_id=self.exam_id),
                             insecure=self.insecure)
            if code == 200 and isinstance(body, dict) and body.get("session"):
                return body["session"]
        return None

    def _identities(self, role: str) -> str:
        """The room pseudonym for a role, from the token the server issues.

        The driver never assumes an identity: it is the pseudonym the deployment
        minted for that account in this room, which is what the isolation reading
        has to be about.
        """
        body = self._token(role)
        self.identities[role] = body.get("identity") or ""
        self.payload.setdefault("identities", {})[role] = self.identities[role]
        return self.identities[role]

    def _sign_in(self) -> None:
        for role in ("teacher", "student", "student2", "outsider"):
            spec = self.accounts.get(role) or ""
            if not spec:
                continue
            session, reason = touch_gate.sign_in(self.base_url, spec, self.insecure)
            if session is None:
                raise CannotMeasure(f"could not sign in the {role}: {reason}")
            self.sessions[role] = session
            token, status = csrf_for(session, self.base_url, self.exam_id,
                                     insecure=self.insecure)
            if not token:
                if role == "outsider" and status == 403:
                    self.csrf["outsider"] = ""
                    continue
                raise CannotMeasure(
                    f"the live page answered HTTP {status} for the {role}")
            self.csrf[role] = token

    def _unmeasured(self, why: str) -> list[Row]:
        """Every later row, unanswered — named rather than silently absent.

        A run that stopped early must not read as a short list of passes: the
        rows this driver exists for are the ones that would be missing.
        """
        names = ("pupil-camera", "teacher-receives-camera", "pupil-isolation",
                 "mic-starts-closed", "hand-raise", "mic-approved",
                 "audio-reaches-teacher", "mic-closed", "outsider-refused",
                 "room-ended")
        return [Row(name, "skipped", why) for name in names]

    # -- the rows ---------------------------------------------------------- #
    async def _media_rows(self, teacher: Browser, teacher_identity: str) -> list[Row]:
        """The cameras: both pupils in the room, and each one's own page."""
        rows: list[Row] = []
        pupils: dict[str, Browser] = {}
        for role in ("student", "student2"):
            if not self._signed_in(role):
                continue
            browser = await self._browser(role)
            await browser.goto(self._page())
            joined = await browser.wait(HAND_READY, CONNECT_TIMEOUT)
            if not joined:
                reading = await browser.read()
                rows.append(Row("pupil-camera" if role == "student" else
                                "teacher-receives-camera", "skipped",
                                f"the {role} never joined the room "
                                f"(`{reading.status}`)"))
                continue
            pupils[role] = browser
            self._identities(role)

        if "student" not in pupils:
            return rows or [Row("pupil-camera", "skipped",
                                "no pupil browser joined the room")]

        first = {role: await browser.read() for role, browser in pupils.items()}
        teacher_first = await teacher.read()
        await asyncio.sleep(FLOORS)
        second = {role: await browser.read() for role, browser in pupils.items()}
        teacher_second = await teacher.read()
        self.payload["readings"] = {
            "classroom": {role: value.as_dict() for role, value in first.items()},
            **{f"classroom_after_{int(FLOORS)}s":
               {role: value.as_dict() for role, value in second.items()}},
            "teacher": teacher_first.as_dict(),
            "teacher_after": teacher_second.as_dict(),
        }

        rows.append(row(
            "pupil-camera",
            camera_finding(first["student"], second["student"],
                           self.identities["student"], "the pupil's page")))

        pupils_seen = [self.identities.get(role) for role in pupils
                       if self.identities.get(role)]
        problems = [camera_finding(teacher_first, teacher_second, identity,
                                   "the teacher's grid")
                    for identity in pupils_seen]
        problems = [problem for problem in problems if problem]
        rows.append(row(
            "teacher-receives-camera", "; ".join(problems),
            skip="" if pupils_seen else "no pupil joined the room",
            ok=f"the teacher's grid is decoding {len(pupils_seen)} pupil camera(s)"))

        if len(pupils) < 2:
            rows.append(Row("pupil-isolation", "skipped",
                            "needs a second pupil in the room (--student2): isolation is "
                            "a claim about what one pupil did *not* receive from another"))
        else:
            leaked = []
            for role, other in (("student", "student2"), ("student2", "student")):
                problem = isolation_finding(
                    first[role], own_identity=self.identities[role],
                    supervisor=teacher_identity,
                    other_pupil=self.identities[other])
                if problem:
                    leaked.append(f"{role}: {problem}")
            rows.append(row(
                "pupil-isolation", "; ".join(leaked),
                ok="neither pupil's page holds any camera but their own and the "
                   "supervisor's, while the other is in the room"))
        return rows

    async def _microphone_rows(self, teacher: Browser) -> list[Row]:
        """The microphone, from refused to approved to closed — token and page."""
        rows: list[Row] = []
        pupil = self.browsers.get("student")
        if pupil is None:
            return [Row(name, "skipped", "the pupil is not in the room")
                    for name in ("mic-starts-closed", "hand-raise", "mic-approved",
                                 "audio-reaches-teacher", "mic-closed")]
        before = publish_sources(self._token("student")["token"])
        page = await pupil.read()
        self.payload.setdefault("sources", {})["before"] = before
        problem = ""
        if before != [CAMERA]:
            problem = (f"a pupil who has asked for nothing is granted {before}, not "
                       f"the camera alone")
        elif page.mic_visible:
            problem = ("the page offers *Aktifkan mikrofon* before any teacher "
                       "approved it")
        rows.append(row(
            "mic-starts-closed", problem,
            ok="a fresh pupil token grants the camera only, and the page offers no "
               "microphone control"))

        pressed = await pupil.press("document.getElementById('live-hand')")
        seen = await teacher.wait(QUEUE_PENDING, STEP_TIMEOUT)
        _, body = api(self.sessions["teacher"], self.base_url,
                      RAISES.format(sid=self.sid), insecure=self.insecure)
        states = [item.get("status") for item in (body or {}).get("requests", [])]
        problem = ""
        if not pressed:
            problem = "the pupil's page offers no *Angkat tangan* control to press"
        elif PENDING not in states:
            problem = (f"the room has no {PENDING} request for that pupil "
                       f"(the API lists {states})")
        elif not seen:
            problem = "no pending row reached the teacher's queue"
        rows.append(row("hand-raise", problem,
                        ok="the request is pending in the API and a row the teacher "
                           "can see in the queue"))
        if problem:
            return rows + [Row(name, "skipped", "the hand raise did not arrive")
                           for name in ("mic-approved", "audio-reaches-teacher",
                                        "mic-closed")]

        pressed = await teacher.press_label(LABEL_APPROVE)
        shown = await pupil.wait(MIC_VISIBLE, STEP_TIMEOUT) if pressed else None
        approved = publish_sources(self._token("student")["token"])
        self.payload["sources"]["approved"] = approved
        problem = ""
        if not pressed:
            problem = (f"the teacher's queue offers no *{LABEL_APPROVE}* control to "
                       f"press")
        elif not shown:
            problem = "the pupil's page never offered the microphone after approval"
        elif approved != sorted([CAMERA, MICROPHONE]):
            problem = (f"approval grants {approved}, so the SFU would still refuse the "
                       f"microphone")
        rows.append(row("mic-approved", problem,
                        ok="approval grants camera + microphone and the pupil's control "
                           "appears"))

        pressed = await pupil.press("document.getElementById('live-mic')")
        heard = await teacher.wait(AUDIO_LIVE, STEP_TIMEOUT) if pressed else None
        problem = ""
        if not pressed:
            problem = "the microphone control could not be pressed"
        elif not heard:
            problem = ("the teacher's page never held a live audio track: the pupil's "
                       "microphone was permitted but not published")
        rows.append(row("audio-reaches-teacher", problem,
                        ok="the teacher's page holds a live audio track for the room"))

        closed = await teacher.press_label(LABEL_CLOSE)
        gone = await pupil.wait(MIC_GONE, STEP_TIMEOUT) if closed else None
        silent = await teacher.wait(AUDIO_SILENT, STEP_TIMEOUT) if closed else None
        after = publish_sources(self._token("student")["token"])
        self.payload["sources"]["closed"] = after
        problem = ""
        if not closed:
            problem = (f"the teacher's queue offers no *{LABEL_CLOSE}* control to "
                       f"press")
        elif after != [CAMERA]:
            problem = (f"the room still grants {after} after the close, so the "
                       f"microphone is not actually revoked")
        elif not gone:
            problem = "the pupil's microphone control is still offered after the close"
        elif not silent:
            problem = ("the teacher's page still holds a live audio track after the "
                       "close: the microphone was not revoked at the SFU")
        rows.append(row("mic-closed", problem,
                        ok="the close takes the grant back to the camera, the control "
                           "away and the teacher's live audio with it"))
        return rows

    def _outsider_rows(self) -> list[Row]:
        """Another school's pupil, at both doors."""
        if not self._signed_in("outsider"):
            return [Row("outsider-refused", "skipped",
                        "no account from another school (--outsider)")]
        session = self.sessions["outsider"]
        page_code, _ = api(session, self.base_url, self._page(),
                           insecure=self.insecure)
        token_code, _ = api(session, self.base_url, TOKEN.format(sid=self.sid),
                            method="POST", csrf=self.csrf.get("outsider", ""),
                            insecure=self.insecure)
        self.payload["outsider"] = {"page": page_code, "token": token_code}
        problem = ""
        if page_code != 403:
            problem = f"the live page answered HTTP {page_code} to another school"
        elif token_code != 403:
            problem = (f"the join token route answered HTTP {token_code} to another "
                       f"school: the room would admit them")
        return [row("outsider-refused", problem,
                    ok="another school's pupil is refused both the page and the token")]

    async def _end_row(self, teacher: Browser) -> Row:
        """Ending the room, judged by whether a *new* join is still possible."""
        if self.keep_room:
            return Row("room-ended", "skipped",
                       "--keep-room: the room was left open for the pilot")
        # Tapped, not clicked: the handler opens `confirm()`, and the page cannot
        # answer a press while the modal is up.
        if not await teacher.tap("document.querySelector('#live-end')"):
            return Row("room-ended", "fail",
                       "the teacher's page offers no *Akhiri Live* control to press")
        confirmed = await teacher.accept_dialog()
        ended = await teacher.wait(
            "(() => { const s = document.getElementById('live-status');"
            " return !!s && /Live selesai/.test(s.textContent); })()", STEP_TIMEOUT)
        code, _ = api(self.sessions["student"], self.base_url,
                      TOKEN.format(sid=self.sid), method="POST",
                      csrf=self.csrf.get("student", ""), insecure=self.insecure)
        self.payload["after_end"] = {"token": code, "confirmed": confirmed,
                                     "page_said": bool(ended)}
        if code != 403:
            return Row("room-ended", "fail",
                       f"a new join token was still issued (HTTP {code}) after "
                       f"*Akhiri Live*: the room outlives the button")
        return row("room-ended", "",
                   ok="*Akhiri Live* is confirmed, and the next join token is refused")

    # -- the answer -------------------------------------------------------- #
    async def execute(self) -> None:
        """Run every step under one budget, then close every browser.

        A driver that waits on a page forever is a driver an operator kills, and
        a killed run reports nothing. The budget is generous (the page polls its
        own queue every 4 s) and the message names the step it stalled in, which
        is the one thing a Ctrl-C cannot tell anyone.
        """
        try:
            await asyncio.wait_for(self.run(), TOTAL_TIMEOUT)
        except asyncio.TimeoutError:
            self.cannot = (f"the run exceeded {TOTAL_TIMEOUT:g}s during "
                           f"{self.step or 'the opening steps'}")
        except CannotMeasure as exc:
            self.cannot = str(exc)
        except Exception as exc:  # noqa: BLE001 - a driver crash is a gap, not a finding
            self.cannot = f"{type(exc).__name__}: {exc}"
        finally:
            await self._close_all()

    def finish(self) -> tuple[int, list[Row], dict]:
        """The rows in their fixed order, with the ones never reached named."""
        why = self.cannot or "the run stopped before this reading"
        seen = {item.name for item in self.rows}
        for name in ROW_ORDER:
            if name not in seen:
                self.rows.append(Row(name, "skipped", why))
        self.rows.sort(key=lambda item: ROW_ORDER.index(item.name))
        self.payload["rows"] = [{"name": item.name, "state": item.state,
                                 "detail": item.detail} for item in self.rows]
        if self.cannot:
            self.payload["reason"] = self.cannot
        return verdict(self.rows), self.rows, self.payload


#: Every row this driver can print, in the order it prints them. A row missing
#: from a run is named and skipped rather than left out, because a short list of
#: passes is how an unfinished run reads as a clean one.
ROW_ORDER = (
    "sdk-loaded", "session", "pupil-camera", "teacher-receives-camera",
    "pupil-isolation", "mic-starts-closed", "hand-raise", "mic-approved",
    "audio-reaches-teacher", "mic-closed", "outsider-refused", "room-ended",
)


def run(*, base_url: str, exam_id: str, accounts: dict, insecure: bool = False,
        binary: str = "", fake: bool = True, video_file: str = "",
        keep_room: bool = False):
    """Drive one room. Returns `(exit_code, rows, payload)`."""
    driver = Driver(base_url, exam_id, accounts, insecure=insecure, binary=binary,
                    fake=fake, video_file=video_file, keep_room=keep_room)
    asyncio.run(driver.execute())
    return driver.finish()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("SCANMEET_STAGING_URL", ""),
                        help="base URL of the staging deployment")
    parser.add_argument("--exam", default=os.environ.get("SCANMEET_EXAM_ID", ""),
                        help="the exam id whose Live room is opened")
    parser.add_argument("--teacher", default=os.environ.get("SCANMEET_TEACHER", ""),
                        help="email:password of the supervising teacher")
    parser.add_argument("--student", default=os.environ.get("SCANMEET_STUDENT", ""),
                        help="email:password of an assigned pupil")
    parser.add_argument("--student2", default=os.environ.get("SCANMEET_STUDENT2", ""),
                        help="email:password of a second assigned pupil (isolation)")
    parser.add_argument("--outsider", default=os.environ.get("SCANMEET_OUTSIDER", ""),
                        help="email:password of a pupil of another school")
    parser.add_argument("--insecure", action="store_true",
                        default=os.environ.get("SCANMEET_INSECURE", "").lower() == "true",
                        help="skip TLS verification (a self-signed staging box)")
    parser.add_argument("--no-fake-devices", action="store_true",
                        help="use the box's real camera/microphone instead of Chrome's "
                             "synthetic device")
    parser.add_argument("--video-file", default="",
                        help="a .y4m file to feed the synthetic camera")
    parser.add_argument("--keep-room", action="store_true",
                        help="leave the room open for a human pilot afterwards")
    parser.add_argument("--json", action="store_true",
                        help="print the whole reading as JSON on stdout")
    args = parser.parse_args(argv)

    missing = [name for name, value in (
        ("--url", args.url), ("--exam", args.exam),
        ("--teacher", args.teacher), ("--student", args.student)) if not value]
    if missing:
        print("COULD NOT MEASURE: " + ", ".join(missing) + " is empty.")
        print("Pass the staging URL, the exam id and the teacher and pupil "
              "accounts as email:password; nothing is printed back.")
        return EXIT_CANNOT_MEASURE

    binary = touch_gate.locate_browser()
    if not binary:
        print("COULD NOT MEASURE: no Chrome/Chromium found (set SG_CHROME).")
        return EXIT_CANNOT_MEASURE

    accounts = {role: getattr(args, role) for role in
                ("teacher", "student")}
    accounts["student2"] = args.student2
    accounts["outsider"] = args.outsider

    code, rows, payload = run(
        base_url=args.url.rstrip("/"), exam_id=args.exam.strip(),
        accounts=accounts, insecure=args.insecure, binary=binary,
        fake=not args.no_fake_devices, video_file=args.video_file,
        keep_room=args.keep_room)

    if args.json:
        print(json.dumps(payload, indent=1, ensure_ascii=True, default=str))
        return code

    print(ascii_safe(
        f"scanmeet media gate: driving {args.url.rstrip('/')} with {binary}"
        f" ({'synthetic devices' if not args.no_fake_devices else 'real devices'})"))
    for item in rows:
        mark = {"pass": "PASS", "fail": "FAIL"}.get(item.state, "SKIP")
        print(ascii_safe(f"  {mark}  {item.name:24} {item.detail}"))
    passed = sum(1 for item in rows if item.state == "pass")
    failed = [item for item in rows if item.state == "fail"]
    skipped = [item for item in rows if item.state == "skipped"]
    if failed:
        print(f"\nFAILED: {len(failed)} row(s) disagreed - {len(passed)}/{len(rows)} "
              f"passed on {args.url.rstrip('/')}")
        print("A room is not ready for pupils until every row here passes: the "
              "media claims are the pilot's own evidence.")
    elif skipped:
        print(f"\nINCOMPLETE: {passed}/{len(rows)} rows passed and "
              f"{len(skipped)} could not be measured - this is not a pass.")
        reasons = {ascii_safe(item.detail) for item in skipped}
        if len(reasons) == 1:
            # One reason for every gap is the common case (no room, no account, a
            # driver that stopped): said once, because twelve identical lines is
            # how a reader stops reading them.
            print(f"    every gap: {reasons.pop()}")
        else:
            for item in skipped:
                print(f"    - {item.name}: {item.detail}")
    else:
        print(f"\nPASS: {len(rows)}/{len(rows)} rows - cameras decode, the "
              f"microphone is refused until approved and revoked after, no pupil "
              f"receives another's media, and the ended room admits nobody.")
    return code


if __name__ == "__main__":  # pragma: no cover - exercised as a script
    sys.exit(main())
