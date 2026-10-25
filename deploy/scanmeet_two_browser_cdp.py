"""Staging-only ScanMeet end-to-end evidence collector (two Chromium processes).

Install: pip install playwright && python -m playwright install chromium
Never run against production, or with real student accounts.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

INSTRUMENT = r"""
(() => {
  const tracks = [];
  Object.defineProperty(window, '__scanmeetE2ETracks', {value: tracks});
  const media = navigator.mediaDevices;
  if (media && media.getUserMedia) {
    const original = media.getUserMedia.bind(media);
    media.getUserMedia = async (...args) => {
      const stream = await original(...args);
      for (const t of stream.getTracks()) tracks.push(t);
      return stream;
    };
  }
})();
"""

SAMPLE = r"""(() => {
  const videos = [...document.querySelectorAll('#live-grid video')];
  const quality = v => {
    try { return v.getVideoPlaybackQuality().totalVideoFrames; }
    catch (_) { return null; }
  };
  const local = window.__scanmeetE2ETracks || [];
  return {
    url: location.origin + location.pathname,
    count: videos.length,
    videos: videos.map(v => ({
      frames: quality(v),
      width: v.videoWidth,
      height: v.videoHeight,
      paused: v.paused,
      readyState: v.readyState,
      tracks: v.srcObject && v.srcObject.getTracks
        ? v.srcObject.getTracks().map(t => ({kind: t.kind, readyState: t.readyState, enabled: t.enabled})) : []
    })),
    local: local.map(t => ({kind: t.kind, readyState: t.readyState, enabled: t.enabled}))
  };
})()"""

def check_staging(url: str) -> None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or not host or host in
        {"scangrade.web.id", "www.scangrade.web.id", "scan-grade.app",
         "www.scan-grade.app", "localhost", "127.0.0.1"}):
        raise ValueError("refusing non-HTTPS or known production/local URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("URL must not contain credentials or fragments")


async def read_cdp(page):
    session = await page.context.new_cdp_session(page)
    try:
        response = await session.send("Runtime.evaluate", {
            "expression": SAMPLE, "returnByValue": True, "awaitPromise": True
        })
        if "exceptionDetails" in response:
            raise AssertionError("CDP Runtime.evaluate failed")
        return response["result"]["value"]
    finally:
        await session.detach()


def assert_sample(label, before, after, *, exact_videos=1):
    if before["count"] != exact_videos or after["count"] != exact_videos:
        raise AssertionError(f"{label}: expected exactly {exact_videos} grid video(s), got {before['count']} -> {after['count']}")
    for index, (a, b) in enumerate(zip(before["videos"], after["videos"])):
        if a["frames"] is None or b["frames"] is None or b["frames"] <= a["frames"]:
            raise AssertionError(f"{label}: video[{index}] frame counter did not rise: {a['frames']} -> {b['frames']}")
        if not b["width"] or not b["height"]:
            raise AssertionError(f"{label}: video[{index}] has no decoded dimensions")


def microphone_closed(sample):
    audio = [t for t in sample["local"] if t["kind"] == "audio" and t["readyState"] == "live"]
    return all(not t["enabled"] for t in audio)


async def main_async(args):
    for url in (args.teacher_url, args.pupil1_url, args.pupil2_url):
        check_staging(url)
    if not args.ask_selector or not args.approve_selector:
        raise ValueError("mic gating acceptance requires --ask-selector AND --approve-selector")
    if len({urlsplit(u).hostname for u in (args.teacher_url, args.pupil1_url, args.pupil2_url)}) != 1:
        raise ValueError("all actors must be on the same isolated staging host")
    for file in (args.teacher_state, args.pupil1_state, args.pupil2_state):
        if not Path(file).is_file():
            raise ValueError(f"missing authentication storage state: {file}")
    results = {"status": "FAIL", "staging_origin": urlsplit(args.pupil1_url).scheme + "://" + urlsplit(args.pupil1_url).netloc,
               "measurements": {}, "checks": []}
    def passed(name):
        results["checks"].append({"name": name, "status": "PASS"})
        print("PASS:", name, flush=True)
    async with async_playwright() as pw:
        # Two separate actual Chromium browser processes. Teacher and first pupil
        # have separate authentication contexts even though they share process A.
        a = await pw.chromium.launch(headless=not args.headed, args=["--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream"])
        b = await pw.chromium.launch(headless=not args.headed, args=["--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream"])
        try:
            teacher_ctx = await a.new_context(storage_state=args.teacher_state)
            pupil1_ctx = await a.new_context(storage_state=args.pupil1_state, permissions=["camera", "microphone"])
            pupil2_ctx = await b.new_context(storage_state=args.pupil2_state, permissions=["camera", "microphone"])
            for ctx in (pupil1_ctx, pupil2_ctx):
                await ctx.add_init_script(script=INSTRUMENT)
            teacher = await teacher_ctx.new_page()
            p1 = await pupil1_ctx.new_page()
            p2 = await pupil2_ctx.new_page()
            for page, url in ((teacher, args.teacher_url), (p1, args.pupil1_url), (p2, args.pupil2_url)):
                await page.goto(url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
            # Explicit joining controls must be provided if pages do not auto-join.
            if args.teacher_join:
                await teacher.locator(args.teacher_join).click(timeout=args.timeout * 1000)
            for p in (p1, p2):
                if args.pupil_join:
                    await p.locator(args.pupil_join).click(timeout=args.timeout * 1000)
            for p in (p1, p2):
                await p.locator("#live-grid video").first.wait_for(state="attached", timeout=args.timeout * 1000)
            await asyncio.sleep(args.warmup)
            first1, first2 = await asyncio.gather(read_cdp(p1), read_cdp(p2))
            await asyncio.sleep(3)
            last1, last2 = await asyncio.gather(read_cdp(p1), read_cdp(p2))
            results["measurements"] = {"pupil1_before": first1, "pupil1_after": last1,
                                        "pupil2_before": first2, "pupil2_after": last2,
                                        "sample_interval_seconds": 3}
            assert_sample("pupil1 isolation and decoded video", first1, last1)
            passed("pupil1 single video and rising decoded frames")
            assert_sample("pupil2 isolation and decoded video", first2, last2)
            passed("pupil2 single video and rising decoded frames")
            for label, sample in (("pupil1", last1), ("pupil2", last2)):
                camera = [t for t in sample["local"] if t["kind"] == "video" and t["readyState"] == "live"]
                if not camera or not any(t["enabled"] for t in camera):
                    raise AssertionError(f"{label}: camera track not live and enabled (getUserMedia instrumentation)")
                passed(f"{label} live enabled camera capture")
                if not microphone_closed(sample):
                    raise AssertionError(f"{label}: microphone live and enabled before approval")
                passed(f"{label} microphone gated before approval")
            if args.ask_selector and args.approve_selector:
                await p1.locator(args.ask_selector).click(timeout=args.timeout * 1000)
                await teacher.locator(args.approve_selector).click(timeout=args.timeout * 1000)
                deadline = asyncio.get_event_loop().time() + args.timeout
                approved = None
                while asyncio.get_event_loop().time() < deadline:
                    approved = await read_cdp(p1)
                    audio = [t for t in approved["local"] if t["kind"] == "audio" and t["readyState"] == "live" and t["enabled"]]
                    if audio:
                        break
                    await asyncio.sleep(0.5)
                else:
                    raise AssertionError("pupil1 mic never enabled after teacher approval")
                if not microphone_closed(await read_cdp(p2)):
                    raise AssertionError("pupil2 mic opened after pupil1 approval")
                results["measurements"]["pupil1_approved"] = approved
                passed("teacher approves pupil1 microphone only")
                passed("pupil2 microphone stays gated")
            else:
                raise AssertionError("microphone approval transition was not exercised")
            results["status"] = "PASS"
        finally:
            await b.close()
            await a.close()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for role in ("teacher", "pupil1", "pupil2"):
        parser.add_argument(f"--{role}-url", required=True)
        parser.add_argument(f"--{role}-state", required=True, help="Playwright storage_state JSON for staging test actor")
    parser.add_argument("--teacher-join", default="")
    parser.add_argument("--pupil-join", default="")
    parser.add_argument("--ask-selector", default="")
    parser.add_argument("--approve-selector", default="")
    parser.add_argument("--warmup", type=float, default=8)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--out", default="scanmeet-e2e-result.json")
    args = parser.parse_args()
    result = {"status": "FAIL", "checks": []}
    try:
        result = asyncio.run(main_async(args))
    except Exception as exc:
        result["error"] = str(exc)
        print("FAIL:", exc, file=sys.stderr)
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("Evidence:", args.out)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
