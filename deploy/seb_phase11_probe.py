#!/usr/bin/env python3
"""Phase 11 Step 0 and Step 5, executed: what does a real SEB client actually send?

`deploy/seb_phase11.py` judges values a *person* read out of SEB's log or settings
window. This instrument is the other half: it **asks the client directly**, by
serving the config and recording what comes back, so the two open questions in
`docs/features/SEB_PHASE11.md` have a machine-readable answer.

**Question A** — does the Config Key the server computes equal the one the client
computes? The probe builds the installation-test config with the same functions the
app uses (`seb_service.settings_for` + `seb_service.seb_file_bytes`), so the file a
real client opens is byte-for-byte what `/panduan/seb/uji.seb` hands out, and then
recomputes `SHA256(absolute URL + Config Key)` for every request it receives.

Why the header is the strongest available evidence for A: the key is a hash over the
SEB-JSON of the file's settings. If the client loaded *our* file and computed a
**different** key, the header it sends — `SHA256(URL + that key)` — could not equal
the value we compute from *our* key. So an equality here is a proof that the client's
key equals ours, for this file, on this platform. A `MATCH` is not "the door let it
in": it is two independent implementations of rule 7 agreeing, and SHA-256 makes the
alternative (matching headers, different keys) a collision. It is also why this probe
does not need SEB's verbose log, which SEB for Windows does not write by default.

**Question B** — does the header ride on an **XHR**, or only on navigations? The
served page fires one `fetch()` at this probe and the probe records whether that
request carried the header. That is Step 5's experiment, done without adding a logging
line to the app's sync route: what is being asked is a property of the client, and a
client can be asked directly.

The page also reports whether this platform exposes the SEB **JavaScript API**
(`SafeExamBrowser.security`), calling `updateKeys` exactly the way
`student/seb_claim.html` calls it — so the probe measures what that page would see,
and on a platform where the API is present its own `configKey` is captured too.

Judged per URL, and recomputed when the report is read
------------------------------------------------------
A client hashes **the URL it is fetching**, so `SHA256(favicon + key)` is only
comparable against the favicon's own address. The first version of this probe
compared every request against the *start URL's* hash and printed `MISMATCH` for a
favicon request that was in fact correct — and a false finding is the one thing an
instrument may not produce. So the judgement is per path, it is recomputed from the
recorded raw values every time the report is read, and the raw capture is never
rewritten: re-judging a run is a re-analysis, not a second opinion.

What it does NOT prove, stated here rather than in a report
----------------------------------------------------------
The comparison covers the *installation-test* config, which has no `startURL` of an
exam, no exam row and no credential. It proves the serialiser and the header name; it
does not prove an exam file read out of a database, which is `seb_phase11.py --exam`
and needs credentials this checkout may not carry.

Usage
-----
    python deploy/seb_phase11_probe.py            # serve and wait
    python deploy/seb_phase11_probe.py --report   # print what was captured

Artifacts (the generated `.seb` and the capture) are written to a **temporary
directory**, never into the checkout: a measurement must not be able to leave the
tree dirty for the next `git pull`. The report is rewritten after **every** request,
so it can be read while the client is still on screen — SEB holds the desktop in
kiosk mode, and the shell does not stop.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import seb_config_key as ck                            # noqa: E402
from app.services import seb_crypto, seb_service                         # noqa: E402

HOST, PORT = "127.0.0.1", 8765
BASE = f"http://{HOST}:{PORT}"
START_URL = f"{BASE}/panduan/seb/berhasil"

#: Where the generated config and the capture go. A temp directory by default, and
#: never the checkout: see the module docstring.
OUT = Path(tempfile.gettempdir())

#: The published test passwords — the same two `/panduan/seb/uji.seb` uses. They
#: guard a page whose whole purpose is to be reachable, which is why this config is
#: never used for an exam.
QUIT_PASSWORD = "SEB-TEST-QUIT"
ADMIN_PASSWORD = "SEB-TEST-ADMIN"

SETTINGS = seb_service.settings_for(
    None, start_url=START_URL, quit_hash=seb_crypto.sha256_hex(QUIT_PASSWORD),
    admin_hash=seb_crypto.sha256_hex(ADMIN_PASSWORD))
KEY = ck.config_key(SETTINGS)
SEB_JSON = ck.seb_json(SETTINGS)
SEB_BYTES = seb_service.seb_file_bytes(SETTINGS)

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>ScanGrade — SEB Phase 11 probe</title></head>
<body style="font-family:system-ui;max-width:44rem;margin:3rem auto;line-height:1.5">
<h1>{verdict}</h1>
<p><strong>Request path:</strong> <code>{path}</code></p>
<p><strong>Header the client sent:</strong><br><code>{sent}</code></p>
<p><strong>This server computes over that path:</strong><br><code>{expected}</code></p>
<p><strong>User-Agent:</strong><br><code>{agent}</code></p>
<p><strong>Question B, an XHR from this page:</strong> <span id="xhr">firing…</span></p>
<p><strong>SEB JavaScript API on this platform:</strong> <code id="jsapi">reading…</code></p>
<p style="color:#555">The verdict above is the whole of question A for this config:
a matching header means the client's Config Key equals the one computed here.
Close SEB with <code>{quitpw}</code> when you are done.</p>
<script>
// The two questions the runbook leaves open, measured from inside the client:
// whether an XHR carries the Config Key header (question B), and whether this
// platform exposes the SEB JavaScript API at all — the path an iPad must use
// because WKWebView cannot send the header. `updateKeys` is called exactly the way
// `student/seb_claim.html` calls it, so what is measured here is what that page
// would see. Nothing is assembled in this script beyond a payload for the probe.
(function () {{
  var sec = (window.SafeExamBrowser && window.SafeExamBrowser.security) || null;
  var api = sec ? 'present' : 'absent';
  document.getElementById('jsapi').textContent =
      api + (sec && sec.configKey ? ' — configKey is populated' : '');
  var report = function (value) {{
    fetch('/probe-xhr', {{method: 'POST', headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{jsapi: api, configKey: value || '',
                              userAgent: navigator.userAgent}})}})
      .then(function (r) {{ document.getElementById('xhr').textContent = 'sent, HTTP ' + r.status; }})
      .catch(function (e) {{ document.getElementById('xhr').textContent = 'failed: ' + e; }});
  }};
  if (sec && typeof sec.updateKeys === 'function') {{
    sec.updateKeys(function () {{ report(sec.configKey); }});
  }} else {{
    report(sec ? sec.configKey : '');
  }}
}})();
</script>
</body></html>"""


def _normalise(value: str) -> str:
    """What clients actually send: trailing whitespace and a trailing `;`."""
    return (value or "").strip().rstrip(";").strip().lower()


def expected_for(path: str) -> str:
    """The header an honest client must send for *this* path — its own URL, hashed."""
    return ck.request_hash(BASE + (path or "/"), KEY)


def judge(path: str, sent: str) -> str:
    """`match` / `mismatch` / `no_header`, for one request, against its own URL."""
    if not sent:
        return "no_header"
    return "match" if _normalise(sent) == expected_for(path) else "mismatch"


def _paths(out: Path) -> tuple[Path, Path]:
    """(the config file, the capture) for one run — one place, so they cannot drift."""
    return out / "ScanGrade-SEB-Uji.seb", out / "seb_phase11_report.json"


def _record(report_path: Path, entry: dict) -> None:
    report = []
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = []
    report.append(entry)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "ScanGradeSEBProbe"

    #: Set by :func:`serve` so the handler writes where the run said to.
    report_path: Path = OUT / "seb_phase11_report.json"

    def _headers_of_interest(self) -> dict:
        return {name: value for name, value in self.headers.items()
                if "safeexambrowser" in name.lower() or name.lower() == "user-agent"}

    def _body(self) -> str:
        """A request body, for the probe page's own report. Never executed."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return ""
        if length <= 0 or length > 8192:
            return ""
        return self.rfile.read(length).decode("utf-8", "replace")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _handle(self) -> None:
        sent = self.headers.get(ck.CONFIG_KEY_HEADER) or ""
        path = self.path.split("?")[0]
        _record(self.report_path, {
            "at": datetime.now(timezone.utc).isoformat(),
            "method": self.command,
            "path": path,
            "has_header": bool(sent),
            "sent": sent,
            "body": self._body()[:600],
            "headers": self._headers_of_interest(),
        })
        if path == "/uji.seb":
            self._send(200, SEB_BYTES, "application/octet-stream")
            return
        if path == "/probe-xhr":
            self._send(200, b'{"ok":true}', "application/json")
            return
        verdict = judge(path, sent)
        body = PAGE.format(
            verdict={"match": "MATCH — the client's Config Key equals ours",
                     "mismatch": "MISMATCH — the values differ; see the report",
                     "no_header": "NO HEADER — the client sent no Config Key here"}[verdict],
            path=self.path, sent=sent or "(none)",
            expected=expected_for(path), agent=self.headers.get("User-Agent") or "(none)",
            quitpw=QUIT_PASSWORD).encode("utf-8")
        self._send(200, body, "text/html; charset=utf-8")

    do_GET = _handle
    do_HEAD = _handle
    do_POST = _handle

    def log_message(self, fmt, *args):                                   # noqa: A003
        sys.stderr.write(f"  probe: {self.address_string()} {fmt % args}\n")


def show_report(report_path: Path) -> int:
    if not report_path.exists():
        print(f"no report yet — nothing has hit the probe ({report_path})")
        return 2
    entries = json.loads(report_path.read_text(encoding="utf-8"))
    print(f"{len(entries)} request(s) captured; judged per URL, from the raw capture")
    for entry in entries:
        path = entry["path"].split("?")[0]
        print(f"  {entry['method']:5} {path:36} {judge(path, entry.get('sent', '')):9} "
              f"ua={(entry['headers'].get('User-Agent') or '')[:44]}")
    print()
    print(f"  our Config Key : {KEY}")
    print(f"  our SEB-JSON   : {SEB_JSON}")
    start = [e for e in entries if e["path"].split("?")[0] == "/panduan/seb/berhasil"]
    if not start:
        print("\nVERDICT: the start URL was never requested, so nothing was measured.")
        return 2
    matched = [e for e in start
               if judge("/panduan/seb/berhasil", e.get("sent", "")) == "match"]
    if not matched:
        print("\nVERDICT: no matching header on the start URL. Question A is NOT answered "
              "by this run; keep the capture and read the first difference.")
        return 1
    print(f"\nVERDICT: {len(matched)} of {len(start)} request(s) to the start URL carried "
          "\n  SHA256(startURL + our Config Key).\n  -> the Config Key the client computed "
          "equals the one computed here.")
    others = [e for e in entries
              if e["path"].split("?")[0] not in {"/panduan/seb/berhasil", "/uji.seb"}
              and e.get("has_header")]
    if others:
        print(f"  also: {len(others)} non-navigation request(s) carried a header of their "
              "own —\n        evidence for question B, though a favicon is a subresource "
              "load, not an XHR.")

    # Question B, and the JavaScript API, from the page's own report.
    xhr = [e for e in entries if e["path"].split("?")[0] == "/probe-xhr"]
    if not xhr:
        print("\nQuestion B: no XHR reached the probe — the page never fired one, so it "
              "is NOT measured by this run.")
        return 0
    verdicts = sorted({judge("/probe-xhr", e.get("sent", "")) for e in xhr})
    print("\nQuestion B: an XHR from inside the client")
    print(f"  {len(xhr)} request(s); header verdict(s): {verdicts}")
    for entry in xhr:
        if entry.get("body"):
            print(f"  page reported: {entry['body']}")
    if verdicts == ["match"]:
        print("  -> YES: the Config Key header rides on an XHR, so a check on the sync "
              "\n     or submit path would have a header to read.")
    else:
        print("  -> NOT confirmed: the header did not match on the XHR. Do not put a "
              "\n     Config Key check on the sync or submit route on this run's "
              "authority.")
    return 0


def serve(out: Path) -> int:
    seb_path, report_path = _paths(out)
    seb_path.write_bytes(SEB_BYTES)
    report_path.unlink(missing_ok=True)
    Handler.report_path = report_path
    print("=" * 74)
    print("PHASE 11 probe — a real SEB client, measured")
    print("=" * 74)
    print(f"  our Config Key : {KEY}")
    print(f"  our SEB-JSON   : {SEB_JSON}")
    print(f"  startURL       : {START_URL}")
    print(f"  expected header: {expected_for('/panduan/seb/berhasil')}")
    print(f"  config file    : {seb_path}  ({len(SEB_BYTES)} bytes)")
    print(f"  quit password  : {QUIT_PASSWORD}")
    print(f"  capture        : {report_path}")
    print()
    print(f"  serving on {BASE} — open the .seb above with SEB, then run:")
    print(f"      python deploy/seb_phase11_probe.py --report --out {out}")
    print()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", action="store_true", help="print what was captured")
    parser.add_argument("--out", default=str(OUT),
                        help="where the generated .seb and the capture go "
                             "(default: a temporary directory)")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.report:
        return show_report(_paths(out)[1])
    return serve(out)


if __name__ == "__main__":
    sys.exit(main())
