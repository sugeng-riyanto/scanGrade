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

Add ``--signoff`` to print the same thirteen rows as a Markdown sheet - the URL,
``--exam``, when it ran, the browser it drove, **the commit the host reported
serving on `/health`** and the verdict the run earned - ready to paste into the
staging ticket. The sheet carries the verdict it earned: a run that could not
measure a row prints ``CANNOT MEASURE`` rather than a table that merely looks
complete. ``--json-out <file>`` keeps the raw reading beside that sheet;
``--json`` and ``--signoff`` are refused together, because both write stdout and
the first version dropped the sheet in silence.

``--expect-commit <sha>`` is how a caller says *which* build the rows have to be
about. The host is asked what it is serving before a single browser is driven, and
a host answering on another commit is refused (exit 2, no sheet) rather than
measured: the thirteen rows say nothing about a release the host is not running,
and a green sheet for the wrong code is worse than no sheet. Without any
expectation the commit is still read and printed, so a reader can see what
answered.

The expectation has three sources, and `expected_commit()` is the one place that
resolves them: the flag (an explicitly empty one means *record, do not judge*),
then `$SCANMEET_EXPECT_COMMIT`, then **a GitHub runner's own `$GITHUB_SHA`**. The
third is the case this driver was silently wrong about -- the workflow ran on
`0967b040`, the host answered `77abe6f`, and the sheet said `SIGNED OFF` -- and on
a runner the dispatched commit *is* the commit the run is evidence for, so
following it is not a guess.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import served_commit_gate  # noqa: E402  (the one `/health` reader in this tree)
from deploy.safe_url import host_only
import touch_gate  # noqa: E402  (the one CDP client, launcher and sign-in here)

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_CANNOT_MEASURE = 2

#: The live site. This driver opens a Live room and presses a real teacher's
#: controls, so the one host it must never reach by a stale `$SCANMEET_STAGING_URL`
#: is the one pupils are sitting exams on. The same hosts
#: `deploy/scanmeet_staging_gate.py` refuses as a staging target, and a test holds
#: the two constants equal rather than trusting this copy to stay right.
#:
#: This list used to hold `scan-grade.app` and nothing else, which is the demo
#: *email* domain in `manage.py` -- the deployed site is `scangrade.web.id`
#: (`README.md`), and `lmsshb.scangrade.web.id` answers on the same box (both
#: names have a certificate in `/etc/letsencrypt/live`). So the guard named a host
#: that is not production and missed the one that is: pointed at the live URL, it
#: opened a room on a real exam. The apex and every subdomain are refused; a
#: deliberate pilot still has `--allow-production`.
PRODUCTION_DOMAIN = "scangrade.web.id"
PRODUCTION_HOSTS = (PRODUCTION_DOMAIN, "www." + PRODUCTION_DOMAIN,
                    "lmsshb." + PRODUCTION_DOMAIN,
                    "scan-grade.app", "www.scan-grade.app")


def production_target(url: str) -> bool:
    """True when `url` is the live site rather than a staging host.

    The suffix rule is what makes a new name under the live domain production
    too: `lmsshb.` already proves those names reach this box's app, so a driver
    cannot tell a future alias from the site itself.
    """
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return (host in PRODUCTION_HOSTS
            or host == PRODUCTION_DOMAIN
            or host.endswith("." + PRODUCTION_DOMAIN))


def host_of(url: str) -> str:
    """Credential-safe host-only display shared with deployment tooling."""
    return ascii_safe(host_only(url))


#: How long `/health` is given to answer. The app builds that body out of its own
#: process, so a host that needs longer than this is not answering at all.
HEALTH_TIMEOUT = 10.0

#: The shortest prefix that still identifies a commit rather than a coincidence.
#: `$GITHUB_SHA` is forty characters and `/health` publishes both spellings, so a
#: caller passing the short form is asking the same question as one passing the
#: full one; comparing the exact strings would refuse a run whose caller merely
#: spelled it the other way.
SHORT_SHA = 7


def served_commit(base_url: str, *, insecure: bool = False,
                  timeout: float = HEALTH_TIMEOUT):
    """What commit the host says it is serving: one read of `/health`.

    The thirteen rows are about the code that answered them, and the sheet this
    driver prints is filed as evidence about a *commit*. Nothing in the readings
    says which commit that was, and a host dozens of commits behind serves the
    same thirteen pages and passes the same thirteen rows: measured on this
    repository, the workflow ran on `0967b040`, the host answered `/health` with
    `77abe6f`, and the sheet it produced said `SIGNED OFF`.

    The reader is not written here. `deploy/served_commit_gate.py` owns the
    question — the production deploy gates on it after every reload — so this asks
    its `reading_of`, and the four states it keeps apart arrive unchanged: a
    readable commit, a host that cannot name itself, a release from before the
    reading, and an answer that is not JSON. Two readers of one endpoint is how
    they start disagreeing.

    A host that cannot be asked is `READING_UNREADABLE` rather than an exception:
    the caller decides what that means, and this driver's rule is that a reading it
    could not take is never a pass.
    """
    import requests

    url = base_url.rstrip("/") + served_commit_gate.DEFAULT_HEALTH_PATH
    try:
        answer = requests.get(url, timeout=timeout, verify=not insecure)
    except requests.RequestException as exc:
        return served_commit_gate.Reading(
            served_commit_gate.READING_UNREADABLE, None,
            f"the host did not answer {served_commit_gate.DEFAULT_HEALTH_PATH}: "
            f"{type(exc).__name__}")
    return served_commit_gate.reading_of(answer.text)


def expected_commit(flag, environ=None) -> tuple[str, str]:
    """The commit this run is evidence for, and where that claim came from.

    Three sources, in the order a caller would expect them to win:

    * **the flag**, including an explicitly empty one. Empty is not the same as
      absent: it is a person saying "record what this host serves, do not judge
      it", which is how a deliberate recording of an older build is taken.
    * **`$SCANMEET_EXPECT_COMMIT`**, for a box that exports it.
    * **a GitHub runner**, which is `GITHUB_ACTIONS=true`. A dispatched run is
      evidence about the commit it was dispatched on, and `GITHUB_SHA` is that
      commit -- not a guess, the caller's own statement. This is the case the
      workflow was wrong about: it ran on `0967b040`, the host answered
      `77abe6f`, and the sheet said `SIGNED OFF`.

    With none of the three the run only *records* what answered, which is what a
    workstation run against a shared staging box wants.
    """
    environ = os.environ if environ is None else environ
    if flag is not None:
        wanted = flag.strip()
        return wanted, ("--expect-commit" if wanted else
                        "--expect-commit '' (record only)")
    env = (environ.get("SCANMEET_EXPECT_COMMIT") or "").strip()
    if env:
        return env, "$SCANMEET_EXPECT_COMMIT"
    if (environ.get("GITHUB_ACTIONS") or "").lower() == "true":
        sha = (environ.get("GITHUB_SHA") or "").strip()
        if sha:
            return sha, "$GITHUB_SHA (this run is about the commit it ran on)"
    return "", ""


def commit_matches(reading, expected: str) -> bool:
    """Does the host's own answer name `expected`?"""
    wanted = (expected or "").strip().lower()
    served = (reading.commit or "").lower()
    if not wanted or not served:
        return False
    if min(len(served), len(wanted)) < SHORT_SHA:
        return False
    return served.startswith(wanted) or wanted.startswith(served)


def served_line(reading) -> str:
    """One line for the console and the sheet: what answered, or why nothing did."""
    if reading.state == served_commit_gate.READING_OK:
        return f"{reading.commit} (from {served_commit_gate.DEFAULT_HEALTH_PATH})"
    return f"could not be read - {ascii_safe(reading.why)}"


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


def token_claims(token: str) -> dict:
    """A join token's payload, unverified for the same reason `grant` is."""
    import jwt

    try:
        return jwt.decode(token, options={"verify_signature": False})
    except Exception as exc:  # noqa: BLE001 - unreadable is a finding, not a crash
        raise CannotMeasure(f"the join token is not a JWT ({type(exc).__name__})") from exc


def token_life(token: str) -> float | None:
    """`exp - iat`: how long the app decided this token is good for.

    Measured from `nbf` when there is no `iat`, which is the case here: LiveKit's
    own SDK stamps `exp`, `nbf`, `iss` and `sub` and **no** `iat`, so a reader that
    insisted on `iat` reported the issued token as carrying "no expiry pair" — the
    first run of this row did exactly that, as a SKIP rather than as a guess.

    `None` when the token carries neither, which is a token whose expiry cannot be
    reasoned about at all.
    """
    claims = token_claims(token)
    exp = claims.get("exp")
    issued = claims.get("iat", claims.get("nbf"))
    if not isinstance(exp, (int, float)) or not isinstance(issued, (int, float)):
        return None
    return float(exp) - float(issued)


def token_deadline(token: str) -> float | None:
    """The token's own `exp`, as a Unix second, or `None` when it has none."""
    exp = token_claims(token).get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


def url_host(url: str) -> str:
    """`scheme://host` only — the signalling URL with any path or userinfo gone.

    The room URL is what a page connects to, and it is written into the run's
    JSON so a reader can tell which SFU the refusal came from. It is not a
    credential, but a URL that carries one would be, so it is cut here rather
    than trusted to be clean.
    """
    parsed = urllib.parse.urlsplit(url or "")
    return f"{parsed.scheme}://{parsed.hostname}" if parsed.scheme and parsed.hostname else ""


#: One connection attempt, made from a page where this app already loaded the SDK.
#: `%s` twice: the room URL, then the token, both JSON-encoded so a value cannot
#: end the expression. The shape is the page's own (`new lib.Room(...)` then
#: `room.connect(url, token)`, as `app/static/js/scan-live-exam.js` writes it) — a
#: first version called `LivekitClient.connect`, which is not a function, and the
#: resulting `TypeError` was read as a refusal. That is the failure this row has
#: to be incapable of: an error is only evidence once the *same* page, at the same
#: moment, has been shown to connect with a token that should work.
#: The promise is answered rather than thrown, because `Runtime.evaluate` turns a
#: rejected promise into a driver error and this row needs the *reason* instead.
CONNECT_WITH = """(async () => {
  const lib = window.LivekitClient;
  if (!lib || typeof lib.Room !== 'function') return {missing: true};
  let room = null;
  try {
    room = new lib.Room({adaptiveStream: true, dynacast: true});
    await room.connect(%s, %s);
    return {connected: true};
  } catch (e) {
    return {connected: false, why: String((e && (e.name + ': ' + e.message)) || e)};
  } finally {
    try { if (room) await room.disconnect(); } catch (e) {}
  }
})()"""


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
def media_flags(*, fake: bool = True, video_file: str = "",
                size=(1280, 900)) -> list:
    """What a media page needs on top of the launcher `touch_gate` builds.

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

    ``--disable-dev-shm-usage`` is **not** here: it is `browser_launch`'s now,
    because every gate that starts a browser needed it (two browsers decoding
    video at once is what this driver adds, and a renderer that cannot get shared
    memory dies before the browser offers a debug target). One copy, in the one
    place that builds argv.
    """
    extra = [
        f"--window-size={int(size[0])},{int(size[1])}",
        "--autoplay-policy=no-user-gesture-required",
        "--use-fake-ui-for-media-stream",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
    ]
    if fake:
        extra.append("--use-fake-device-for-media-stream")
        if video_file:
            extra.append(f"--use-file-for-fake-video-capture={video_file}")
    return extra


class Browser:
    """One headless browser, kept open across the steps of a run.

    `touch_gate._measure` launches its browser for a single measurement and quits;
    a room needs three of them to stay joined while the others act, so this owns
    the process and the socket for the length of the run and closes both in
    `close()` whatever happened. What it does **not** own is the launch: starting
    the browser, waiting for its debug target, retrying a launch that never came
    up and remembering what Chrome said are `touch_gate.start_browser`'s, so this
    driver cannot drift away from the three gates on any of them.
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
        #: Ids for presses written without awaiting a reply, in a range no
        #: awaited command reaches, so a stray reply can never be taken for one.
        self._tap_id = 10 ** 6

    # -- lifecycle --------------------------------------------------------- #
    async def open(self):
        import websockets

        # Through the shared launcher, in a thread: it polls in blocking
        # `time.sleep`, and three of these open at once — a blocking wait on this
        # loop would serialize the launches and stall the two browsers already
        # talking over the socket. The process and the profile are taken before
        # anything can raise, because `close()` is what ends them.
        proc, target, profile, reason = await asyncio.to_thread(
            touch_gate.start_browser,
            self.binary,
            extra_args=tuple(
                media_flags(fake=self.fake, video_file=self.video_file, size=self.size)
            ),
        )
        self.proc, self.profile = proc, profile
        if target is None:
            raise CannotMeasure(reason)
        # `ping_interval`/`ping_timeout` are the *library's* liveness check, and its
        # 20 s default is a deadline three starved Chrome processes on a two-core
        # runner can miss: the hosted run of this gate lost the second pupil's
        # socket 46 s into the expiry wait with `sent 1011 (internal error)
        # keepalive ping timeout; no close frame received` — a pong that arrived
        # more than 20 s late, not a browser that had gone. The connection was
        # still writable (a crashed process gives 1006, not 1011), so the reading
        # was refused for the runner's clock rather than for anything about the
        # token. Two minutes keeps the check — a peer that never answers is still
        # closed and still surfaces — without charging a busy renderer for it.
        self.ws = await websockets.connect(target, **DEVTOOLS_SOCKET)
        self.cdp = touch_gate._CDP(self.ws)
        await self.cdp.send("Network.enable")
        await self.cdp.send("Page.enable")
        await self.cdp.send("Runtime.enable")
        host = urllib.parse.urlparse(self.base_url).hostname or "127.0.0.1"
        for name, value in self.cookies.items():
            await self.cdp.send("Network.setCookie", name=name, value=value,
                                domain=host, path="/")
        await self._grant_media()

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
            touch_gate._stop(self.proc)
            self.proc = None
        if self.profile:
            shutil.rmtree(self.profile, ignore_errors=True)
            self.profile = ""

    # -- driving ----------------------------------------------------------- #
    async def goto(self, path: str, *, timeout: int = 90):
        await self.cdp.send("Page.navigate", url=self.base_url + path)
        await self.cdp.wait_event("Page.loadEventFired", timeout=timeout)

    async def ping(self) -> None:
        """One round trip on the same DevTools socket, to keep it open.

        Not a no-op: the answer is read and discarded, but the *command* is what
        resets Chrome's idle timer on that connection. Any failure propagates, and
        the caller turns it into could-not-measure — a wait whose socket died
        measured nothing, and must never read as a pass.
        """
        await self.cdp.send("Runtime.evaluate", expression="1", returnByValue=True)

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

#: The life the app stamps on a join token (`live_policy.TOKEN_TTL`, five
#: minutes). The expiry row waits this out for real, so it is a wall-clock
#: constant here rather than a value the driver can substitute for waiting.
TOKEN_TTL_SECONDS = 300.0

#: How far past `exp` the attempt is made. **Measured, not guessed.** Forging
#: tokens with a chosen `exp` against the staging project (the same key that mints
#: the app's own) put the SFU's tolerance between 40 and 60 seconds: `exp - 20s` and
#: `exp - 40s` were admitted, `exp - 60s` was refused with `invalid token`, as were
#: every larger offset up to 30 minutes. A margin of 10 s sat *inside* that window,
#: so the row reported "the SFU admitted a token 10 s past its expiry" about a
#: token the server was still entitled to accept.
#:
#: 75 s clears the observed tolerance with room for the clock this driver reads
#: versus the one the SFU does (measured server offset: 0.2-0.7 s). The row still
#: fails if the SFU admits a token older than this — which is the claim worth
#: making: the five-minute life holds to within a minute of clock tolerance, and
#: no further.
EXPIRY_MARGIN = 75.0

#: How often the driver answers Chrome while it waits. Chrome closes an idle
#: DevTools socket after about a minute of silence (`1011 keepalive ping
#: timeout`), which is exactly what a five-minute wait for a token looks like from
#: the driver's side: the first run of the expiry row lost the socket and reported
#: two rows as SKIP — a measurement that did not happen, for a reason that had
#: nothing to do with the token.
EXPIRY_PING_SECONDS = 20.0

#: How this driver opens the DevTools socket. A 64 MB frame limit because a
#: `Runtime.evaluate` reply can carry a page's whole state, and a pong deadline of
#: two minutes because Chrome on a two-core runner with three instances publishing
#: video answers late often enough that the library's 20 s default closed a live
#: socket (see `Browser.open`). Not a "disable the keepalive": a peer that never
#: answers is still closed, in 120 s rather than in 20.
DEVTOOLS_SOCKET = {
    "max_size": 64 * 1024 * 1024,
    "ping_interval": 20,
    "ping_timeout": 120,
}

#: How long one of those liveness calls may go unanswered before the wait is
#: declared unmeasurable rather than merely slow. Longer than the socket's own
#: `ping_timeout` (120 s), so it is the outer guard for a connection that is open
#: and writable but has stopped replying: without it the reply is awaited with no
#: deadline at all and the run ends on its whole budget, naming no cause.
PING_ANSWER_SECONDS = 150.0

assert PING_ANSWER_SECONDS > DEVTOOLS_SOCKET["ping_timeout"], (
    "the outer deadline must sit past the socket's own, or the run ends on its "
    "budget instead of naming the browser that stopped answering")

#: The whole room, from sign-in to the last token probe. Generous enough for a
#: slow staging box and short enough that a stalled page is reported by name.
#: The whole run, and it carries the expiry row's real wait: a budget sized
#: without it would turn a measured refusal into "the run exceeded 300s".
TOTAL_TIMEOUT = 300.0 + TOKEN_TTL_SECONDS + 60.0

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
#: `#live-start` starts `hidden` and is revealed by the page's own `discover()`
#: request — so a press issued the moment the document loads reads as "the page
#: offers no control", which is a reading the driver cannot take yet rather than a
#: finding about the product. Measured on staging from a remote runner: the control
#: appears within about a second of load.
START_READY = ("(() => { const b = document.getElementById('live-start');"
               " return !!b && !b.hidden && b.getBoundingClientRect().width > 0; })()")


def tiles_expression(identities: list[str]) -> str:
    """True once every wanted identity's tile holds a *decoded* frame.

    The teacher's grid fills from `TrackSubscribed`, which lands after the pupil's
    first published frame. Measured on staging, twice:

    * a reading taken the instant both pupils were publishing found two videos —
      the teacher's own and the first pupil's — and judged the second as "no
      camera tile for that participant";
    * after waiting for the *element*, a second run found the tile with
      `videoWidth 0`, because the element is appended before its first frame
      arrives. Both were statements about the snapshot, not about the room.

    So the wait is for a tile whose video has decoded something, which is the
    claim row 4 makes.
    """
    wanted = json.dumps(list(identities))
    return ("(() => { const want = " + wanted + ";"
            " const grid = document.getElementById('live-grid');"
            " if (!grid) return false;"
            " const have = new Set([...grid.querySelectorAll('video')]"
            "   .filter(v => v.videoWidth > 0).map(v => {"
            "     const tile = v.closest('[data-identity]');"
            "     return tile ? (tile.dataset.identity || '') : ''; }));"
            " return want.every(x => have.has(x)); })()")
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
            offered = await teacher.wait(START_READY, STEP_TIMEOUT)
            if not offered or not await teacher.click("#live-start"):
                said = (await teacher.read()).status
                self.rows.append(Row("session", "fail",
                                     "the teacher's page offered no pressable "
                                     "*Mulai pengawasan* control "
                                     f"(`{said}` after {STEP_TIMEOUT:.0f}s)"))
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
        self.step = "waiting out a join token"
        self.rows.append(await self._expiry_row())
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
                 "token-expiry", "room-ended")
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

        expected = [self.identities.get(role) for role in pupils
                    if self.identities.get(role)]
        if expected:
            await teacher.wait(tiles_expression(expected), CONNECT_TIMEOUT)

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

        # A pass has a sentence too: `camera_finding` returns "" when it finds
        # nothing, so this row used to reach the sign-off sheet as a cell of
        # evidence for every row and one empty cell -- which reads as a row
        # nobody took.
        rows.append(row(
            "pupil-camera",
            camera_finding(first["student"], second["student"],
                           self.identities["student"], "the pupil's page"),
            ok=f"the pupil's page is decoding its own camera, and the frames were "
               f"still rising {FLOORS:g}s later"))

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

    async def _wait_past_expiry(self, browsers, deadline: float) -> None:
        """Sleep past a token's own `exp`, answering the browser on the way.

        Two clocks, for two different jobs. The distance to wait is measured from
        the token's `exp` — a wall-clock instant minted by the deployment under
        test — while the *elapsed* time is counted on `time.monotonic()`, which
        an NTP step or a daylight change cannot move. A loop that trusted the wall
        clock alone would either wake early (and connect with a live token) or
        sleep far too long after a backwards step.

        The interval is the other half: every `EXPIRY_PING_SECONDS` the driver
        sends one cheap command over the same DevTools socket, because Chrome
        closes a socket nobody speaks on after about a minute. The first run of
        this row died that way — `keepalive ping timeout`, and rows 12 and 13
        skipped for a reason that had nothing to do with the token.
        """
        target = deadline + EXPIRY_MARGIN
        remaining = target - time.time()
        self.step = f"waiting {max(0.0, remaining):.0f}s for a join token to expire"
        # Every browser still open, not just the pupil's: the rows after the wait
        # need the teacher's page too, and the first repaired run lost *that*
        # socket instead — `room-ended` skipped seconds after the expiry row
        # passed, for the same silence.
        open_pages = [page for page in browsers if page is not None]
        started = time.monotonic()
        announced = False
        while True:
            spent = time.monotonic() - started
            left = remaining - spent
            if left <= 0:
                return
            if announced:
                for page in open_pages:
                    try:
                        # Bounded, because `send` awaits the reply with no deadline
                        # of its own: an unanswered call would otherwise sit there
                        # until the run's whole budget expired.
                        await asyncio.wait_for(page.ping(), PING_ANSWER_SECONDS)
                    except Exception as exc:  # noqa: BLE001 - see below
                        # The socket, not the token. Could-not-measure, said out
                        # loud: a wait that lost its connection measured nothing,
                        # and the one thing it must never do is leave a green row.
                        # Whether the browser is still running separates a starved
                        # renderer from one that had gone — the two failures look
                        # identical from the exception alone, and only one of them
                        # is about this host's size.
                        proc = getattr(page, "proc", None)
                        alive = proc is not None and proc.poll() is None
                        raise CannotMeasure(
                            f"the DevTools connection for {page.name} closed while "
                            f"waiting for the token to expire "
                            f"({type(exc).__name__}: {exc}; the browser process "
                            f"{'was still running' if alive else 'had exited'}) "
                            f"— the expiry was not measured") from exc
            announced = True
            await asyncio.sleep(min(EXPIRY_PING_SECONDS, left))

    async def _expiry_row(self) -> Row:
        """A token the app issued, refused by the SFU once its five minutes pass.

        Two authorities, so two readings. **The app's** is the life it stamps on
        the token: `exp - iat` must be the policy's five minutes, and a token is
        only obtainable by a fresh issue — no route in this app extends one, so a
        page left open past expiry cannot silently renew what it holds. **The
        SFU's** is the one that needs the clock: the same token, offered by the
        same page through `LivekitClient.connect`, is refused after `exp`.

        Waiting is the measurement. A token minted to look expired would prove
        that LiveKit checks a signature, not that the token this app handed to a
        browser dies when the app said it would.
        """
        minted = self._token("student")
        token, url = minted.get("token") or "", minted.get("url") or ""
        life = token_life(token)
        deadline = token_deadline(token)
        self.payload["expiry"] = {"life": life, "deadline": deadline,
                                  "url": url_host(url)}
        if life is None or deadline is None:
            return Row("token-expiry", "skipped",
                       "the issued token carries no `iat`/`exp` pair to wait out")
        if abs(life - TOKEN_TTL_SECONDS) > 1.0:
            return Row("token-expiry", "fail",
                       f"the app stamped a {life:.0f}s life on the token; the policy "
                       f"is {TOKEN_TTL_SECONDS:.0f}s")

        pupil = self.browsers.get("student")
        if pupil is None:
            return Row("token-expiry", "skipped", "the pupil is not in the room")
        control_page = self.browsers.get("student2")
        if control_page is None:
            return Row("token-expiry", "skipped",
                       "needs a second pupil to show the room is reachable at the "
                       "moment the stale token is offered (--student2)")

        await self._wait_past_expiry(list(self.browsers.values()), deadline)
        before = await pupil.read()

        # The control first: a *fresh* token, from a page in the same room, right
        # now. Without it a rejection is an observation about the network or a
        # broken SDK call rather than about the token — and the first version of
        # this row reported PASS on `TypeError: … is not a function`.
        fresh = self._token("student2")
        self.step = "proving the room is reachable with a fresh token"
        control = await control_page.evaluate(CONNECT_WITH % (
            json.dumps(fresh.get("url") or url), json.dumps(fresh.get("token") or "")))
        self.payload["expiry"]["control"] = control
        if not isinstance(control, dict) or control.get("missing"):
            return Row("token-expiry", "skipped",
                       "the page has no usable LiveKit SDK, so a rejection could "
                       "not be attributed to the token")
        if not control.get("connected"):
            return Row("token-expiry", "skipped",
                       "the room could not be reached even with a fresh token "
                       f"(`{_cell(control.get('why') or '')}`), so the stale "
                       "token's refusal proves nothing")

        self.step = "offering the expired token"
        attempt = await pupil.evaluate(CONNECT_WITH % (json.dumps(url),
                                                       json.dumps(token)))
        # A moment after the attempt: an admitted token would have replaced this
        # pupil's own connection, and their frames would stop rising.
        await asyncio.sleep(FLOORS)
        after = await pupil.read()
        self.payload["expiry"].update({"attempt": attempt, "undisturbed":
                                       camera_finding(before, after,
                                                      self.identities.get("student", ""),
                                                      "the pupil's page")})
        if not isinstance(attempt, dict):
            return Row("token-expiry", "fail",
                       f"the page answered {attempt!r}, not a connection result")
        if attempt.get("missing"):
            return Row("token-expiry", "skipped",
                       "the pupil's page has no LiveKit SDK to attempt a connection with")
        if attempt.get("connected"):
            return Row("token-expiry", "fail",
                       f"the SFU admitted a token {EXPIRY_MARGIN:.0f}s past its "
                       f"expiry, beyond its own measured tolerance (40-60s) — the "
                       f"five-minute life is not a life")
        disturbed = self.payload["expiry"]["undisturbed"]
        if disturbed:
            return Row("token-expiry", "fail",
                       f"the refusal disturbed the pupil already in the room: {disturbed}")
        return row("token-expiry", "",
                   ok=f"a token with a {life:.0f}s life was refused "
                      f"{EXPIRY_MARGIN:.0f}s after it expired while a fresh token "
                      f"connected from the same room, and the pupil already in "
                      f"it was not disturbed (`{_cell(attempt.get('why') or '')}`)")

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
    "audio-reaches-teacher", "mic-closed", "outsider-refused", "token-expiry",
    "room-ended",
)


#: What the sheet calls each exit code. The word matters more than the number:
#: a person pastes this into a ticket, and "CANNOT MEASURE" beside a table of
#: thirteen rows is the difference between a pilot's evidence and a table nobody
#: filled in.
VERDICTS = {
    EXIT_OK: "SIGNED OFF",
    EXIT_FINDING: "NOT SIGNED OFF",
    EXIT_CANNOT_MEASURE: "CANNOT MEASURE",
}


def _cell(text) -> str:
    """One table cell: a single line, no column break, and ASCII.

    A detail here is a sentence this driver wrote (`*Akhiri Live* is confirmed, and
    the next join token is refused`) and may carry a pipe or a newline from a page
    error; either one would split the row into the wrong columns, which is how a
    table starts lying without anyone editing it.
    """
    flat = " ".join(str(text).split())
    return ascii_safe(flat).replace("|", "\\|")


def signoff_sheet(base_url: str, exam_id: str, rows: list, code: int, *,
                  browser: str = "", devices: str = "", run_at: str = "",
                  served=None) -> str:
    """The thirteen rows as a Markdown sign-off sheet a person can paste.

    `docs/SCANMEET_BIZNET_STAGING.md` §2 ends with a sheet a person fills in by
    hand, and the gate's own console table is not that: it has no verdict line, no
    exam, no run time, and a reader cannot tell a `SKIP` in a two-column dump from
    one in a signed page. This prints the same thirteen readings **with the verdict
    they earned** — including the verdicts that are not a sign-off, because a run
    that could not measure two rows must not leave a table that looks complete.

    Everything is ASCII for the same reason the rest of this file is: a Windows
    console and a cp1252 log are both places this gets copied to.
    """
    passed = sum(1 for item in rows if item.state == "pass")
    failed = [item for item in rows if item.state == "fail"]
    skipped = [item for item in rows if item.state == "skipped"]

    host = host_of(base_url)
    lines = [
        f"## ScanMeet media sign-off - {host}",
        "",
        f"- **Verdict:** `{VERDICTS.get(code, 'UNKNOWN')}` (exit {code}) - "
        f"{passed}/{len(rows)} row(s) passed",
        f"- **Exam:** `{_cell(exam_id)}`",
    ]
    # Which code answered is part of the reading, not a footnote to it: a sheet a
    # person files is evidence about a commit, and every row above was taken
    # against whatever the host was running. It is printed even when it could not
    # be read, because a blank beside `13/13 passed` reads as a match.
    if served is not None:
        if served.state == served_commit_gate.READING_OK:
            lines.append(f"- **Served commit:** `{_cell(served.commit)}` "
                         f"(from {served_commit_gate.DEFAULT_HEALTH_PATH})")
        else:
            lines.append(f"- **Served commit:** could not be read - "
                         f"{_cell(served.why)}")
    if run_at:
        lines.append(f"- **Run at:** {_cell(run_at)}")
    if browser:
        lines.append(f"- **Browser:** `{_cell(browser)}`"
                     + (f" ({_cell(devices)})" if devices else ""))
    lines += [
        "",
        "| # | Row | State | What this run observed |",
        "| --- | --- | --- | --- |",
    ]
    for index, item in enumerate(rows, start=1):
        # A gap is emphasised exactly as a finding is: in a sheet that is mostly
        # PASS, the two rows that are not evidence are the two a reader must see.
        state = {"pass": "PASS", "fail": "**FAIL**",
                 "skipped": "**SKIP**"}.get(item.state, "**? **")
        lines.append(
            f"| {index} | `{_cell(item.name)}` | {state} | {_cell(item.detail)} |")
    lines.append("")
    if code == EXIT_OK:
        lines.append(
            "Every row passed: cameras decode, the microphone is refused until a "
            "teacher approves it and revoked after, no pupil receives another's "
            "media, a token the app issued stops working when its five minutes "
            "run out, and the ended room admits nobody.")
    elif failed:
        lines.append(
            f"{len(failed)} row(s) disagreed and {len(skipped)} could not be "
            "measured. A room is not ready for pupils until every row passes: "
            "the rows above are the pilot's own evidence, not a checklist.")
    else:
        reasons = {ascii_safe(item.detail) for item in skipped}
        lines.append(
            f"{len(skipped)} of {len(rows)} row(s) were never taken, so this is "
            "**not** a sign-off and must not be filed as one."
            + (f" Every gap: {_cell(reasons.pop())}" if len(reasons) == 1 else ""))
    return "\n".join(lines)


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
    parser.add_argument("--json-out", default="", metavar="PATH",
                        help="write the whole reading as JSON to this file — the "
                             "way to keep it beside a --signoff sheet")
    parser.add_argument("--signoff", action="store_true",
                        help="print the thirteen rows as a Markdown sign-off sheet "
                             "carrying the run's URL, exam and verdict")
    parser.add_argument("--expect-commit", default=None,
                        help="the commit this host must report serving on /health; "
                             "a host answering on another commit is refused before "
                             "a room is opened, because the rows would be about the "
                             "code that answered (short or full sha). Defaults to "
                             "$SCANMEET_EXPECT_COMMIT, then to $GITHUB_SHA on a "
                             "runner; pass an empty value to record without "
                             "judging")
    parser.add_argument("--allow-production", action="store_true",
                        help="let the driver run against the live site (a deliberate "
                             "pilot, never an accident)")
    args = parser.parse_args(argv)

    missing = [name for name, value in (
        ("--url", args.url), ("--exam", args.exam),
        ("--teacher", args.teacher), ("--student", args.student)) if not value]
    if missing:
        print("COULD NOT MEASURE: " + ", ".join(missing) + " is empty.")
        print("Pass the staging URL, the exam id and the teacher and pupil "
              "accounts as email:password; nothing is printed back.")
        return EXIT_CANNOT_MEASURE

    if production_target(args.url) and not args.allow_production:
        print(f"COULD NOT MEASURE: {ascii_safe(host_only(args.url))} is the live site.")
        print("This driver opens a Live room and presses a teacher's controls, so it "
              "runs against staging. Pass --allow-production for a deliberate pilot "
              "on the live site.")
        return EXIT_CANNOT_MEASURE

    # Two flags, one stream. The first version let `--json` win and dropped the
    # sheet in silence — a run asked for a sign-off, printed a JSON blob and
    # exited 0, so nothing anywhere said the sheet was never produced. Refused
    # here, before three browsers are driven, rather than papered over later.
    if args.json and args.signoff:
        print("COULD NOT MEASURE: --json and --signoff both write stdout.")
        print("Pass one of them, or add --json-out <file> to keep the reading "
              "beside the sheet.")
        return EXIT_CANNOT_MEASURE

    # Which code is answering, asked once and before three browsers are driven.
    # Refusing here rather than filing a sheet is the whole point: the rows are
    # about the commit that serves them, and a host on another commit can pass
    # every one of them while a release it is not running is signed off.
    served = served_commit(args.url, insecure=args.insecure)
    expected, expected_from = expected_commit(args.expect_commit)
    if expected and not commit_matches(served, expected):
        print(f"COULD NOT MEASURE: {host_of(args.url)} is not known to be serving "
              f"the commit this run asked for.")
        print(f"  asked for:  {ascii_safe(expected)}  ({ascii_safe(expected_from)})")
        print(f"  the host:   {_cell(served_line(served))}")
        print("The thirteen rows are about the code that answered them, so no room "
              "was opened and no sheet was produced. Deploy this commit to that "
              "host, or drop --expect-commit to record whatever is serving.")
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

    # Kept beside the rows so a reader of the JSON can tell a reading about the
    # commit they asked for from one about whatever happened to be deployed.
    payload["served_commit"] = {"state": served.state, "commit": served.commit,
                                "why": served.why,
                                "expected": expected or None,
                                "expected_from": expected_from or None}

    if args.json_out:
        try:
            with open(args.json_out, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, indent=1, ensure_ascii=True, default=str)
                handle.write("\n")
        except OSError as exc:
            # The rows were measured; the record of them was not kept. That is a
            # reading nobody can file, so it is not a pass.
            print(f"COULD NOT MEASURE: could not write {args.json_out}: {exc}")
            return EXIT_CANNOT_MEASURE

    if args.json:
        print(json.dumps(payload, indent=1, ensure_ascii=True, default=str))
        return code

    if args.signoff:
        from datetime import datetime, timezone

        print(signoff_sheet(
            args.url, args.exam.strip(), rows, code, browser=binary,
            devices="synthetic devices" if not args.no_fake_devices else "real devices",
            run_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            served=served))
        return code

    print(ascii_safe(
        f"scanmeet media gate: driving {host_of(args.url)} with {binary}"
        f" ({'synthetic devices' if not args.no_fake_devices else 'real devices'})"))
    print(ascii_safe(f"  serving:  {served_line(served)}"))
    print(ascii_safe("  expecting: " + (f"{expected}  ({expected_from})" if expected
                                         else "nothing - recording what this host serves")))
    for item in rows:
        mark = {"pass": "PASS", "fail": "FAIL"}.get(item.state, "SKIP")
        print(ascii_safe(f"  {mark}  {item.name:24} {item.detail}"))
    passed = sum(1 for item in rows if item.state == "pass")
    failed = [item for item in rows if item.state == "fail"]
    skipped = [item for item in rows if item.state == "skipped"]
    if failed:
        print(f"\nFAILED: {len(failed)} row(s) disagreed - {len(passed)}/{len(rows)} "
              f"passed on {host_only(args.url)}")
        print("A room is not ready for pupils until every row here passes: the "
              "media claims are the pilot's own evidence.")
    elif skipped:
        print(f"\nINCOMPLETE: {passed}/{len(rows)} rows passed and "
              f"{len(skipped)} could not be measured - this is not a pass.")
        reasons = {ascii_safe(item.detail) for item in skipped}
        if len(reasons) == 1:
            # One reason for every gap is the common case (no room, no account, a
            # driver that stopped): said once, because a page of identical lines is
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
