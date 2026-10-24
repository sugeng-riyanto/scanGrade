"""Real ScanMeet media acceptance via three independently authenticated Chrome/CDP sessions.

A teacher + TWO pupils are required: one pupil alone cannot prove per-pupil media
isolation. This is an opt-in STAGING test; it creates and later ends a real LiveKit
room. It sends Chrome's synthetic camera/audio frames through the real WebRTC/SFU
and measures decoded inbound frames, never claiming physical webcam validation.

Run from the repository checkout on a QA workstation, not the 1 GB production VPS:
    SCANMEET_STAGING_URL=https://staging.example.edu \
    SCANMEET_EXAM_ID=<staging-exam-id> \
    SCANMEET_TEACHER_LOGIN='email:password' \
    SCANMEET_PUPIL_A_LOGIN='email:password' \
    SCANMEET_PUPIL_B_LOGIN='email:password' \
      python -m deploy.scanmeet_cdp_e2e --confirm-staging --json-out scanmeet-e2e.json

Use a PRIVATE, non-committed env file for credentials. Do not put secrets on
command-line flags or in GitHub Actions logs. Exit: 0 verified, 1 observed
violation, 2 could not measure. No test claim is made until real staging runs.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

from deploy import touch_gate

EXIT_PASS = 0
EXIT_FINDING = 1
EXIT_UNMEASURED = 2
ROLES = ("teacher", "pupil_a", "pupil_b")
LOGIN_KEYS = {
    "teacher": "SCANMEET_TEACHER_LOGIN",
    "pupil_a": "SCANMEET_PUPIL_A_LOGIN",
    "pupil_b": "SCANMEET_PUPIL_B_LOGIN",
}

# Instrument WebRTC *before* LiveKit creates any PeerConnection. The page's
# normal room variable stays private; no token or identity is inspected.
PC_INSTRUMENT = r"""
(() => {
  const Native = window.RTCPeerConnection;
  window.__scanmeetE2EPCs = [];
  if (!Native) return;
  class MonitoredPeerConnection extends Native {
    constructor(...args) {
      super(...args);
      window.__scanmeetE2EPCs.push(this);
    }
  }
  window.RTCPeerConnection = MonitoredPeerConnection;
})();
"""

# Do NOT base an isolation verdict only on visible tiles. A malicious or buggy
# subscriber can receive streams without rendering them in the page's grid.
# getStats(inbound-rtp) sees video decoded by Chrome, including hidden tracks.
MEDIA_SAMPLE = r"""
(async () => {
  const pcs = window.__scanmeetE2EPCs || [];
  let incomingVideo = 0, videoFrames = 0, videoBytes = 0;
  let incomingAudio = 0, audioPackets = 0, audioBytes = 0;
  let sendingAudio = 0, sendingVideo = 0, localCamera = 0;
  for (const pc of pcs) {
    for (const sender of pc.getSenders()) {
      const track = sender.track;
      if (!track || track.readyState !== 'live' || !track.enabled) continue;
      if (track.kind === 'audio') sendingAudio++;
      if (track.kind === 'video') sendingVideo++;
    }
    const stats = await pc.getStats();
    for (const s of stats.values()) {
      if (s.type !== 'inbound-rtp' || s.isRemote) continue;
      const kind = s.kind || s.mediaType;
      if (kind === 'video' && ((s.bytesReceived || 0) > 0 || (s.framesDecoded || 0) > 0)) {
        incomingVideo++;
        videoFrames += s.framesDecoded || 0;
        videoBytes += s.bytesReceived || 0;
      }
      if (kind === 'audio' && (s.packetsReceived || 0) > 0) {
        incomingAudio++;
        audioPackets += s.packetsReceived || 0;
        audioBytes += s.bytesReceived || 0;
      }
    }
  }
  const videos = [...document.querySelectorAll('#live-grid video')].map(v => ({
    width: v.videoWidth, height: v.videoHeight,
    frames: v.getVideoPlaybackQuality?.().totalVideoFrames || 0,
    ready: v.readyState,
    local: v.muted && v.closest('[data-identity]') !== null
  }));
  localCamera = videos.filter(v => v.local && v.width > 0).length;
  const status = document.getElementById('live-status')?.textContent || '';
  const mic = document.getElementById('live-mic');
  const hand = document.getElementById('live-hand');
  return {
    pcCount: pcs.length,
    incomingVideo, videoFrames, videoBytes,
    incomingAudio, audioPackets, audioBytes,
    sendingAudio, sendingVideo, localCamera, videos,
    status, micVisible: !!mic && !mic.hidden,
    handEnabled: !!hand && !hand.disabled,
    tiles: document.querySelectorAll('#live-grid video').length,
    queueText: document.getElementById('live-queue')?.textContent || '',
    connectedUI: !!document.querySelector('#live-grid') &&
      (document.getElementById('live-end')?.hidden === false ||
       document.getElementById('live-hand')?.disabled === false)
  };
})()
"""


class Finding(RuntimeError):
    """An observed staging behavior violated the expected ScanMeet policy."""


class Unmeasured(RuntimeError):
    """The prerequisites were not ready, so the E2E assertion was not made."""


def safe_staging_url(raw: str, expected: str) -> str:
    """Require an exact separate HTTPS staging origin (no prod or localhost)."""
    candidate = (raw or "").rstrip("/")
    expected = (expected or "").rstrip("/")
    try:
        u = urlsplit(candidate)
        e = urlsplit(expected)
    except ValueError as exc:
        raise Unmeasured("invalid staging URL") from exc
    if not candidate or not expected or candidate != expected:
        raise Unmeasured("URL must exactly match SCANMEET_STAGING_URL")
    host = (u.hostname or "").lower()
    if (u.scheme != "https" or not host or host in (
        "scan-grade.app", "www.scan-grade.app", "localhost", "127.0.0.1", "::1"
    ) or host.endswith((".invalid", ".example", ".test")) or
            u.username or u.password or u.query or u.fragment or
            u.path not in ("", "/") or u.port not in (None, 443) or
            e.scheme != "https"):
        raise Unmeasured("refusing non-staging or non-HTTPS endpoint")
    return candidate


def check_preconditions(base_url: str, exam_id: str, logins: dict,
                        *, confirmed: bool, expected_url: str) -> str:
    if not confirmed:
        raise Unmeasured("--confirm-staging is required: this test creates a real room")
    origin = safe_staging_url(base_url, expected_url)
    if not exam_id or any(x in exam_id for x in "/?#"):
        raise Unmeasured("SCANMEET_EXAM_ID must identify a real staging exam")
    if any(not logins.get(role) or ":" not in logins[role] for role in ROLES):
        raise Unmeasured("staging teacher and two pupil login env vars are required")
    if len({logins[r].partition(":")[0].casefold() for r in ROLES}) != 3:
        raise Unmeasured("teacher and pupils must be three distinct accounts")
    return origin


def assert_baseline(teacher: dict, a: dict, b: dict):
    """Judge observed media after all three users joined the real SFU."""
    for label, pupil in (("pupil A", a), ("pupil B", b)):
        if pupil["pcCount"] == 0:
            raise Unmeasured(f"{label}: WebRTC peer connection not instrumented")
        if pupil["tiles"] != 1:
            raise Finding(f"{label} renders {pupil['tiles']} video tiles, expected own camera only")
        if pupil["incomingVideo"] != 0 or pupil["videoFrames"] != 0:
            raise Finding(f"{label} receives remote video (privacy breach)")
        if pupil["sendingAudio"] != 0 or pupil["micVisible"]:
            raise Finding(f"{label} has a microphone publication before teacher approval")
        if not pupil["handEnabled"]:
            raise Finding(f"{label}: hand-raise control not enabled")
        if not any(v["width"] > 0 and v["height"] > 0 for v in pupil["videos"]):
            raise Finding(f"{label}: own camera has no decoded video dimensions")
    if teacher["pcCount"] == 0:
        raise Unmeasured("teacher: WebRTC peer connection not instrumented")
    if teacher["incomingVideo"] < 2:
        raise Finding("teacher does not receive both independent pupil video streams")
    if len([v for v in teacher["videos"] if v["width"] > 0 and v["height"] > 0]) < 2:
        raise Finding("teacher renders fewer than two pupil cameras")


def assert_frames_advance(before: dict, after: dict, label: str):
    if after["videoFrames"] <= before["videoFrames"]:
        raise Finding(f"{label}: no new decoded WebRTC video frames between samples")


def assert_tile_frames_advance(before: dict, after: dict, label: str):
    frames_before = sum(v["frames"] for v in before["videos"])
    frames_after = sum(v["frames"] for v in after["videos"])
    if frames_after <= frames_before:
        raise Finding(f"{label}: no new locally displayed camera frames")


def assert_audio_gating(before: dict, approved: dict, revoked: dict,
                        teacher_start: dict, teacher_audio: dict):
    if before["sendingAudio"] or before["micVisible"]:
        raise Finding("student microphone accessible before approval")
    if not approved["micVisible"] or approved["sendingAudio"] == 0:
        raise Finding("approved student cannot publish microphone")
    if teacher_audio["audioPackets"] <= teacher_start["audioPackets"]:
        raise Finding("teacher did not receive approved microphone packets")
    if revoked["micVisible"] or revoked["sendingAudio"] > 0:
        raise Finding("student microphone continued after teacher revocation")


def assess_response(payload: dict) -> dict:
    """Unwrap Runtime.evaluate returnByValue and refuse JS exceptions."""
    if payload.get("exceptionDetails"):
        raise Unmeasured("browser script threw while measuring staging page")
    value = (payload.get("result") or {}).get("value")
    if not isinstance(value, dict):
        raise Unmeasured("browser returned no measurement object")
    return value


async def evaluate(cdp, expression: str):
    return assess_response(await cdp.send(
        "Runtime.evaluate", expression=expression, returnByValue=True,
        awaitPromise=True, userGesture=True,
    ))


async def page_status(cdp):
    return await evaluate(cdp, "(() => ({path:location.pathname, live:!!document.getElementById('scan-live')}))()")


async def click(cdp, element: str):
    # Values come exclusively from static IDs controlled by this module.
    return await evaluate(cdp, f"(() => (document.getElementById({json.dumps(element)})?.click(), {{clicked: true}}))()")


async def click_queue(cdp, caption: str):
    expression = """(() => {
      const target = [...document.querySelectorAll('#live-queue button')]
        .find(b => b.textContent.trim() === %s);
      if (!target) return {clicked:false};
      target.click(); return {clicked:true};
    })()""" % json.dumps(caption)
    return (await evaluate(cdp, expression))["clicked"]


async def wait_measure(cdp, predicate, *, seconds=60, reason="expected state"):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        last = await evaluate(cdp, MEDIA_SAMPLE)
        if predicate(last):
            return last
        await asyncio.sleep(1.3)
    raise Finding(f"timeout verifying {reason}")


async def start_chrome(role: str, binary: str, origin: str, exam_id: str,
                       cookies, stack: AsyncExitStack):
    """Own Chrome process & profile for each account; reuse repository CDP."""
    import requests
    import websockets

    port = touch_gate._free_port()
    profile = tempfile.mkdtemp(prefix=f"sg-cdp-{role}-")
    argv, env = touch_gate.browser_launch(binary, port, profile)
    argv[-1:-1] = [
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        "--autoplay-policy=no-user-gesture-required",
        "--enable-features=WebRtcHideLocalIpsWithMdns",
    ]
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=env)

    def cleanup():
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        shutil.rmtree(profile, ignore_errors=True)

    stack.callback(cleanup)
    target = None
    for _ in range(90):
        try:
            pages = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=2).json()
            target = next((t.get("webSocketDebuggerUrl") for t in pages
                           if t.get("type") == "page"), None)
            if target:
                break
        except (requests.RequestException, ValueError):
            pass
        await asyncio.sleep(0.3)
    if not target:
        raise Unmeasured(f"{role}: Chrome CDP port did not become available")
    ws = await stack.enter_async_context(websockets.connect(target, max_size=8 * 1024 * 1024))
    cdp = touch_gate._CDP(ws)
    await cdp.send("Network.enable")
    await cdp.send("Page.enable")
    await cdp.send("Runtime.enable")
    await cdp.send("Page.addScriptToEvaluateOnNewDocument", source=PC_INSTRUMENT)
    for cookie in cookies:
        await cdp.send("Network.setCookie", name=cookie.name, value=cookie.value,
                       url=origin)
    await cdp.send("Page.navigate", url=origin + "/live/exam/" + exam_id)
    await cdp.wait_event("Page.loadEventFired", timeout=45)
    path = await page_status(cdp)
    if not path.get("live"):
        raise Unmeasured(f"{role}: authenticated live exam page not rendered")
    return cdp


async def run_live(origin: str, exam_id: str, binary: str, cookies: dict):
    results = {"checks": [], "mode": "synthetic-camera-over-real-webrtc", "exit": EXIT_UNMEASURED}
    created_room = False
    teacher = None
    async with AsyncExitStack() as stack:
        chrome = {}
        for role in ROLES:
            chrome[role] = await start_chrome(role, binary, origin, exam_id, cookies[role], stack)
        teacher = chrome["teacher"]
        pupil_a, pupil_b = chrome["pupil_a"], chrome["pupil_b"]
        try:
            # Refuse to take over a room that a staging teacher already started.
            state = await evaluate(teacher, """(async () => {
              const id = document.getElementById('scan-live')?.dataset.examId;
              const r = await fetch('/api/live/exams/' + encodeURIComponent(id),
                {credentials:'same-origin', cache:'no-store'});
              if (!r.ok) return {ok:false, status:r.status};
              const v = await r.json(); return {ok:true, active:!!v.session};
            })()""")
            if not state.get("ok"):
                raise Unmeasured("teacher cannot read staging session state")
            if state["active"]:
                raise Unmeasured("staging exam already has an active Live room; not taking it over")
            await click(teacher, "live-start")
            created_room = True
            await wait_measure(teacher, lambda s: s["connectedUI"],
                               seconds=70, reason="teacher WebRTC session")
            await wait_measure(pupil_a, lambda s: s["connectedUI"],
                               seconds=75, reason="pupil A WebRTC session")
            await wait_measure(pupil_b, lambda s: s["connectedUI"],
                               seconds=75, reason="pupil B WebRTC session")
            t1 = await wait_measure(teacher, lambda s: s["incomingVideo"] >= 2,
                                    seconds=45, reason="two remote pupil camera tracks")
            a1 = await evaluate(pupil_a, MEDIA_SAMPLE)
            b1 = await evaluate(pupil_b, MEDIA_SAMPLE)
            assert_baseline(t1, a1, b1)
            results["checks"].append("three-isolated-sessions-with-teacher-camera-grid")
            await asyncio.sleep(3)
            t2 = await evaluate(teacher, MEDIA_SAMPLE)
            assert_frames_advance(t1, t2, "teacher inbound pupil video")
            a2 = await evaluate(pupil_a, MEDIA_SAMPLE)
            b2 = await evaluate(pupil_b, MEDIA_SAMPLE)
            assert_tile_frames_advance(a1, a2, "pupil A own camera")
            assert_tile_frames_advance(b1, b2, "pupil B own camera")
            results["checks"].append("live-video-decoded-frames-increase")
            # The room is new; request pupil A specifically, never approve an
            # unrelated/leftover queue item as a shortcut.
            await click(pupil_a, "live-hand")
            await wait_measure(teacher, lambda s: "pending" in s["queueText"],
                               seconds=25, reason="pupil A hand raise in teacher queue")
            if not await click_queue(teacher, "Izinkan"):
                raise Finding("teacher approve button missing")
            await wait_measure(pupil_a, lambda s: s["micVisible"],
                               seconds=30, reason="pupil microphone approval")
            teacher_audio_start = await evaluate(teacher, MEDIA_SAMPLE)
            await click(pupil_a, "live-mic")
            approved = await wait_measure(pupil_a, lambda s: s["sendingAudio"] > 0,
                                          seconds=30, reason="approved pupil audio publication")
            await asyncio.sleep(4)
            teacher_audio = await evaluate(teacher, MEDIA_SAMPLE)
            if not await click_queue(teacher, "Akhiri bicara"):
                raise Finding("teacher revoke microphone button missing")
            revoked = await wait_measure(pupil_a, lambda s: not s["micVisible"],
                                         seconds=30, reason="mic revocation acknowledgement")
            # There is a separate *real media* requirement: disabling the button
            # is not enough if Chrome still publishes the audio RTP track.
            await asyncio.sleep(3)
            revoked = await evaluate(pupil_a, MEDIA_SAMPLE)
            assert_audio_gating(a1, approved, revoked, teacher_audio_start, teacher_audio)
            results["checks"].append("microphone-gated-approved-revoked-over-real-sfu")
            results["checks"].append("no-pupil-inbound-video-rtp")
            results["exit"] = EXIT_PASS
            return results
        finally:
            if created_room and teacher is not None:
                # Cleanup is deliberately best-effort; report when not confirmed.
                try:
                    await evaluate(teacher, "(() => {window.confirm = () => true; return {armed:true};})()")
                    await click(teacher, "live-end")
                    await asyncio.sleep(1.0)
                    end_state = await evaluate(teacher, "(() => ({ended:document.getElementById('live-start')?.hidden===false}))()")
                    results["cleanup"] = "confirmed-ui" if end_state["ended"] else "manual-review-required"
                except Exception:
                    results["cleanup"] = "manual-review-required"


def sanitized_result(code: int, problem: str, checks=None) -> dict:
    """Public report never contains emails, passwords, tokens or JWT payloads."""
    return {"status": ("PASS" if code == 0 else "FINDING" if code == 1 else "UNMEASURED"),
            "exit": code, "reason": problem, "checks": checks or []}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=os.environ.get("SCANMEET_STAGING_URL", ""),
                        help="HTTPS staging URL; must match SCANMEET_STAGING_URL")
    parser.add_argument("--exam-id", default=os.environ.get("SCANMEET_EXAM_ID", ""),
                        help="staging exam ID (not production)")
    parser.add_argument("--confirm-staging", action="store_true",
                        help="confirm isolated staging: driver creates and ends one Live room")
    parser.add_argument("--json-out", type=Path,
                        help="optional sanitized results (no secrets/media/screenshots)")
    args = parser.parse_args(argv)
    logins = {role: os.getenv(key, "") for role, key in LOGIN_KEYS.items()}
    try:
        origin = check_preconditions(
            args.base_url, args.exam_id, logins,
            confirmed=args.confirm_staging,
            expected_url=os.environ.get("SCANMEET_STAGING_URL", ""),
        )
        browser = touch_gate.locate_browser()
        if not browser:
            raise Unmeasured("Chrome not installed; set SG_CHROME on QA workstation")
        cookie_sets = {}
        for role in ROLES:
            session, _ = touch_gate.sign_in(origin, logins[role], insecure=False)
            if session is None:
                raise Unmeasured(f"{role}: staging login refused or unavailable")
            cookie_sets[role] = list(session.cookies)
        outcome = asyncio.run(run_live(origin, args.exam_id, browser, cookie_sets))
        report = sanitized_result(outcome["exit"], "", outcome["checks"])
        report["cleanup"] = outcome.get("cleanup", "not-requested")
    except Finding as exc:
        report = sanitized_result(EXIT_FINDING, str(exc))
    except Unmeasured as exc:
        report = sanitized_result(EXIT_UNMEASURED, str(exc))
    except Exception:
        report = sanitized_result(EXIT_UNMEASURED,
                                  "unexpected browser/staging error; inspect local private logs")
    print(f"{report['status']}: ScanMeet CDP media E2E")
    if report["reason"]:
        print("  " + report["reason"])
    if report["checks"]:
        print("  verified: " + ", ".join(report["checks"]))
    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report["exit"]


if __name__ == "__main__":
    raise SystemExit(main())
