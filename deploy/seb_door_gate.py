#!/usr/bin/env python3
"""Is the SEB door *enforced* on the release this box is serving? Measured, not asserted.

The claim, and the four observations that make it mean something
----------------------------------------------------------------
**One paper's page door refuses a plain browser, admits a header that carries the
exam's own Config Key, and admits a client that proves itself through the handshake
page's JavaScript API**, on the live database, against the app that is *answering
right now* — with the exam row's `require_seb` column the only difference between the
two requests:

* **C0 — the control.** With `require_seb` off, the same pupil opens the same paper:
  HTTP 200 with the paper's own title in the body. Without this, "refused" could be
  any other guard in `take_exam` — the window, the school, the class, the attempt cap —
  and the run would prove nothing about SEB. Nothing else here is worth reading if C0
  is not 200.
* **C1 — a plain browser.** With `require_seb` on and no header, the door answers
  **302 to `/student/exams/<id>/seb-claim`** — the handshake page, not a bare 403,
  because the likely reader is a pupil who opened the link in the wrong thing, and
  because a client that cannot attach a header at all (SEB on iOS runs on WKWebView)
  has that road.
* **C1b — the road is real.** The claim page itself answers 200. A door that sends a
  pupil to a 404 has refused them with a detour.
* **C2 — a matching header.** The same URL with
  `X-SafeExamBrowser-ConfigKeyHash = SHA256(the exam's start URL + the stored key)`,
  computed by the app's own `seb_config_key.request_hash`, answers 200 *and* the
  paper comes back. A header that merely exists is not enough, which C3 shows.
* **C3 — the falsification.** The same header shape computed over a *different* key
  does not open the paper. Without it, C1 and C2 are equally explained by "any header
  passes", which is the failure this whole check exists to catch.
* **C4a–C4c — the three claims that must be refused.** The handshake page posts the
  value the client's own JavaScript API reports, and that value is
  `SHA256(the URL of the page the script ran on + the Config Key)` — the *handshake
  page's* address, which is the opposite of C2's paper address, and the single most
  likely thing for this path to get backwards. So three shapes are posted and each
  must answer 403: a value hashed over the paper's address, a value over another
  key, and a value that was honest for the key stored *before* the key was re-issued
  (the stale file a pupil is still holding).
* **C4d — none of them left the door ajar.** With the same session, the paper still
  answers 302 to the handshake: nothing was minted by a claim that was refused.
* **C5/C5b — the admission.** The honest value answers 200 with `ok: true`, and then
  the paper itself answers 200 *with no header at all* — which is the only way an
  iPad ever sits a gated paper. A handshake that refuses an honest client leaves the
toggle reading ON and every WKWebView client locked out, with no symptom on the box.
* **C6 — the page's own script, in a real browser.** C4 proves the *protocol* (this
  file composes the POST itself); it cannot see whether the shipped page ever makes
  it. So a headless browser is pointed at the handshake page with **no
  `SafeExamBrowser` API at all** — the ordinary-browser population `no_client` exists
  for — and the run reads what the page did on its own: its state and the panel it
  laid out, the requests it made (exactly one POST to the refusal route, with the
  reason the route validates and the CSRF header the route reads, and **no** claim),
  what the route answered, and the row the server recorded for this pupil carrying
  this run's own browser marker. Then the paper is asked again, in that same browser,
  and still hands it the handshake. This is the half `tests/unit/test_seb_js_api.py`
  drives under node; here it is measured on the box, against the release being
  served, which is what the other gates in this file are for.
  The browser half is part of what "enforced" means, so a box that cannot take it —
  no browser, or a database that predates the refusal record — is reported as **not
  measured** rather than passed: exit 0 is a claim about both transports and both
  halves of the client, and a gate that says otherwise is the silent symptom this
  whole file exists to remove.

Why this runs over HTTP against the served app, and not in a test client
-----------------------------------------------------------------------
The probe this replaces drove the Flask test client over the *checkout's* code, and
said so in its own header: it proved that code against that data, not that the
deployed release enforces SEB. This runs at the end of a deploy, after the reload,
against `--base` — so the door being measured is the door the box is answering with,
and the release is the one the deploy just merged (the served-commit gate has already
established that). The **data** is still production: the row is written to the live
project the checkout's `.env` names, and deleted again before this exits.

Where the two addresses come from
---------------------------------
`PAPER_PATH` and `CLAIM_SUFFIX` are the *interface*, not a second implementation of
the door: `seb_service.start_url` builds the paper's address as
`request.url_root + url_for("student.take_exam")`, and this file composes the same
string from the base it dials, for the same reason a real SEB client does — the hash
covers the address the client was sent to. The hashing itself is imported
(`seb_config_key.request_hash`), so there is one implementation of it and this is not
where a second one could drift.

Why a throwaway row, and what it costs
--------------------------------------
Turning `require_seb` on for a paper a school is actually using would refuse every
pupil who opens it in an ordinary browser, so the row is **created for this run**
(cloned from the demo fixture, which the deploy refreshes immediately before the smoke
test) and deleted afterwards — along with any sitting it left, and any row an earlier
run was killed before removing. The title is a marker the sweep looks for by name, so
a crash leaves something the *next* run deletes rather than something a school has to
find. The pupil is the smoke config's own `murid` account: no new credential, and the
paper is created in that pupil's own school and class.

Exit codes
----------
  0  enforced — every observation above
  3  **not enforced**: a plain browser opened a gated paper, a matching header was
     refused, a foreign header opened it, the handshake road is missing, a
     JavaScript claim that must be refused was not, a refused claim still opened the
     paper, or an honest claim was refused and the paper stayed shut. A property of
     the release: the caller refuses it.
  2  could not measure — no credentials, no such pupil, no fixture paper to clone, the
     control did not open, the box cannot reach the app, **this release carries no
     SEB door at all** (the paper opened and the claim route does not exist), or the
     client half could not be taken (no browser, or a database with no refusal table
     yet). A property of the box or of an older release, and never a rollback on its
     own — but also never a pass.

What this must never do
-----------------------
Read `exam_seb_credential`. The row here carries a key this run invents and nothing a
pupil holds; the reversible passwords of a real paper are none of a smoke check's
business. `tests/unit/test_seb_door_gate.py` fails if that table is named anywhere in
this file. It also must never leave the database changed: the cleanup runs in a
`finally`, is verified by reading the rows back, and a leftover is reported as a
`seb door: LEFTOVER` line rather than passed over.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# The browser plumbing is the touch gate's, imported rather than copied: the locator,
# the launcher (its `HOME` and `--no-sandbox` lessons), and the CDP client that keeps
# the events a page emits while it loads. `locate_browser` reads `SG_CHROME`, which is
# the conf key the two DOM gates already use, so pointing one of the three at a
# browser points all three.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from touch_gate import _CDP, _free_port, browser_launch, locate_browser  # noqa: E402

#: The paper's own address, and the handshake page's suffix. The door composes both
#: from `request.url_root` + these same route paths (`seb_service.start_url`,
#: `seb_service.claim_url`); this file composes them from the base it dials, which is
#: the address the client was sent to and therefore the one the hash covers.
PAPER_PATH = "/student/exams/{exam_id}"
CLAIM_SUFFIX = "/seb-claim"

#: The JSON field the handshake page posts, and the header it posts it with. Named
#: here rather than spelled at each call site, and asserted against the page's own
#: script and the app's CSRF reader by `tests/unit/test_seb_door_gate.py`: a smoke
#: check that posts the wrong field measures a refusal it caused itself, and one that
#: omits the token measures the CSRF guard instead of the door.
JS_VALUE_FIELD = "config_key"
CSRF_HEADER = "X-CSRF-Token"

#: The refusal route the handshake page reports to — the page's own script posts to
#: it, and C6 reads the request back out of the browser. Derived from
#: `CLAIM_SUFFIX` so a moved handshake page cannot leave this dialling the old one.
REFUSAL_SUFFIX = CLAIM_SUFFIX + "/refused"

#: The reason the page sends when it found no SafeExamBrowser API at all — the one
#: `seb_door_log.CLIENT_REASONS` accepts, and the branch an ordinary browser takes.
#: Asserted against the page's own script and against the record module.
NO_CLIENT_REASON = "no_client"

#: The marker this run puts in the browser's `User-Agent`. It is how the row that
#: comes back is known to be *this* browser's page, and not something else that
#: happened to report a refusal for the same paper — the one property a count of
#: rows cannot establish on its own. Only this gate ever sends it.
BROWSER_UA = "ScanGrade-SebDoorGate-browser/1"

#: How long the browser is given to settle on the handshake page, and how often it is
#: asked. The page posts its report from `x-init`, and this box is a 1 vCPU VPS
#: running the other gates at the same time: a fixed sleep is the flake the render
#: gate already had to fix once.
CLIENT_BUDGET = 20.0
POLL_EVERY = 0.5

#: The throwaway row's title. It *is* the sweep's key, so a run that is killed leaves
#: something the next run deletes rather than something a school has to find.
TITLE = "ZZ SEB DOOR SMOKE - throwaway, safe to delete"

#: The pupil signs in where every pupil signs in. Read from the smoke test's own
#: mapping rather than restated, so a moved login route cannot leave this gate
#: signing in at an address no pupil uses.
LOGIN_PATH = "/auth/login-user"
CSRF_RE = re.compile(r'name="csrf-token"\s+content="([^"]+)"')
#: A slow link must not read as a finding: the box's own Supabase round trips are
#: the slowest thing here, and `--timeout` is what a spotty deploy box raises.
REQUEST_TIMEOUT = 45

EXIT_OK = 0
EXIT_NOT_ENFORCED = 3
EXIT_UNMEASURED = 2


# ── the verdict, as a decision table rather than a chain of prints ─────────────


@dataclass
class Seen:
    """What the requests answered. No network in here, on purpose."""

    control: int = 0                     # C0: require_seb off
    control_title: bool = False          # ...and the paper's own title came back
    gated: int = 0                       # C1: require_seb on, no header
    gated_location: str = ""             # ...and where it sent the browser
    claim: int = 0                       # C1b: the handshake page itself
    matched: int = 0                     # C2: a header over the stored key
    matched_title: bool = False
    foreign: int = 0                     # C3: a header over another key
    foreign_location: str = ""
    # ── the JavaScript transport (C4–C5), for the clients that cannot send a header
    js_wrong_address: int = 0            # C4a: a value hashed over the *paper's* URL
    js_foreign: int = 0                  # C4b: a value over another key
    js_stale: int = 0                    # C4c: a value from before the key was re-issued
    js_closed: int = 0                   # C4d: the paper after all three
    js_closed_location: str = ""
    js_accepted: int = 0                 # C5: the honest value
    js_accepted_ok: bool = False
    js_opened: int = 0                   # C5b: the paper, with no header at all
    js_opened_title: bool = False
    # ── C6: the handshake page's own script, in a real headless browser ──────
    client_measured: bool = False        # a browser reading was taken at all
    client_seb_api: bool = False         # ...and the browser really had no SEB API
    client_state: str = ""               # what the page's own state ended on
    client_refused_shown: bool = False   # the refusal panel is the one laid out
    client_reports: int = 0              # POSTs the page made to the refusal route, first load
    client_reports_total: int = 0        # ...and across the whole visit, which loads it twice
    client_reason: str = ""              # the reason in that POST's body
    client_token: bool = False           # ...carried with the CSRF header
    client_claims: int = 0               # POSTs to the claim route, of which there must be none
    client_status: int = 0               # what the refusal route answered
    client_rows_total: int = -1          # rows the server holds for this exam (-1: not read)
    client_rows_marked: int = 0          # ...of which this run's browser left
    client_rows_foreign: int = 0         # ...marked rows that are not this pupil's
    client_relocked: bool = False        # the paper still sends that browser to the handshake
    client_record_missing: bool = False  # this database has no refusal table yet


def _client_verdict(seen: Seen, lines: list[str]) -> None:
    """C6, appended to `lines`: the handshake page's own script, in a real browser.

    C4 above proves the *protocol* — this file composes the claim POSTs itself, so a
    page whose script never fires one is invisible to it. That page is the whole
    population the refusal path exists for, though: a client with no
    `SafeExamBrowser` API at all. So a headless browser is pointed at the handshake
    page and this reads what the page did on its own, and then whether the server
    holds the row only that page could have caused.

    Three ways the client half is *not measured*, and every one of them is a property
    of the box or of an older release rather than a finding: no browser to drive; a
    browser that reported a SEB API (so it is not this population and the reading
    would be about a different client); and a database whose refusal table does not
    exist yet, which is a release from before migration 064. What is left is a
    finding when any of it is wrong, because "the page half the clients depend on
    never reported" is the failure this whole gate exists to make visible.
    """
    if seen.client_seb_api:
        lines.append(
            "seb door: NOT MEASURED — the browser this gate drives reported a "
            "SafeExamBrowser API, so it is not the client the `no_client` branch "
            "exists for and the reading would be about a different client."
        )
        return
    if not seen.client_measured:
        lines.append(
            "seb door: NOT MEASURED — the handshake page's own script was not "
            "measured: no browser to drive (set SG_CHROME to a Chrome/Chromium "
            "binary, the same key the DOM gates use) or the run could not be driven. "
            "The header transport and the claim protocol above were measured; the "
            "page's script was not, and 'enforced' is a claim about both."
        )
        return
    if seen.client_record_missing:
        lines.append(
            "seb door: NOT MEASURED — the handshake page reported its refusal to a "
            "route whose record this database does not have yet (migration 064 is not "
            "applied here), so the row the client half is judged by cannot exist. "
            "This database is older than the record the release writes."
        )
        return

    if seen.client_state != "refused" or not seen.client_refused_shown:
        lines.append(
            f"seb door: FAILED — the handshake page, opened in a browser with no SEB "
            f"API, ended in state {seen.client_state!r} with the refusal panel laid "
            f"out: {seen.client_refused_shown}. A pupil who opens the link in an "
            f"ordinary browser is shown the refusal — or nothing at all."
        )
    if seen.client_reports != 1:
        lines.append(
            f"seb door: FAILED — the handshake page made {seen.client_reports} POST(s) "
            f"to {REFUSAL_SUFFIX}; exactly one locked-out visit must leave exactly one "
            f"report, or the number a teacher reads is not the number of pupils who "
            f"could not get in."
        )
    if seen.client_reason != NO_CLIENT_REASON:
        lines.append(
            f"seb door: FAILED — the handshake page reported {seen.client_reason!r} "
            f"instead of {NO_CLIENT_REASON!r} for a browser with no SEB API, so the "
            f"route either refused it (and no row exists) or recorded the wrong cause."
        )
    if not seen.client_token:
        lines.append(
            f"seb door: FAILED — the handshake page's report carried no "
            f"{CSRF_HEADER} header, so the app's own guard answered instead of the "
            f"door and the refusal is never recorded."
        )
    if seen.client_claims:
        lines.append(
            f"seb door: FAILED — the handshake page posted {seen.client_claims} "
            f"claim(s) to {CLAIM_SUFFIX} from a browser with no SEB API, so a client "
            f"with nothing to prove itself with is asking anyway."
        )
    if seen.client_status != 200:
        lines.append(
            f"seb door: FAILED — the refusal route answered HTTP {seen.client_status} "
            f"to the page's own report; only a 200 means the attempt was taken and "
            f"recorded."
        )
    # The rows are attributed by **this run's marker**, not counted wholesale. Two
    # things make a raw count wrong, and both were measured on a live database before
    # this sentence was written. The three refused claims above (C4a–C4d) leave rows
    # of their own, deliberately — the claim route records a mismatch — and they carry
    # the gate's own `requests` User-Agent, not the browser's. And the browser loads
    # the handshake page **twice**: the paper refuses it and sends it back, and a new
    # load is a new visit, so it reports twice. "One row" was therefore never the
    # right number; "as many rows carrying this run's marker as the page made reports"
    # is, and it catches a lost and a duplicated write alike.
    if seen.client_reports_total < 1:
        lines.append(
            "seb door: FAILED — the handshake page never reported a refusal at all, "
            "so a pupil who opens the link in an ordinary browser is turned away with "
            "no record and the teacher's panel cannot say it happened."
        )
    if seen.client_rows_marked != seen.client_reports_total:
        lines.append(
            f"seb door: FAILED — the page made {seen.client_reports_total} report(s) "
            f"and the server holds {seen.client_rows_marked} row(s) carrying this "
            f"run's browser marker ({BROWSER_UA!r}), so what the page sent is not what "
            f"the teacher's count is built from. {seen.client_rows_total} row(s) exist "
            f"for this paper in total; the refused claims above leave their own, and "
            f"those are deliberate."
        )
    if seen.client_rows_foreign:
        lines.append(
            f"seb door: FAILED — {seen.client_rows_foreign} row(s) carrying this "
            f"run's browser marker belong to another pupil, so the page's own session "
            f"is not the one being recorded."
        )
    if not seen.client_relocked:
        lines.append(
            "seb door: FAILED — a browser that reported a refusal was then admitted "
            "to the paper: the report opened something, which is exactly what a "
            "refusal must never do."
        )


def judge(seen: Seen) -> tuple[int, list[str]]:
    """(exit code, the `seb door:` lines) for what was observed.

    The order is deliberate and each step says why it is where it is. A control that
    did not open makes every later answer meaningless, so it is asked first; "this
    release has no door" has to be separated from "the door let a browser in" or a
    release that merely *predates* the feature would be refused as a security
    regression.
    """
    lines: list[str] = []

    if seen.control != 200 or not seen.control_title:
        return EXIT_UNMEASURED, [
            f"seb door: NOT MEASURED — the control did not open the paper "
            f"(HTTP {seen.control}, title in page: {seen.control_title}), so a refusal "
            f"on the gated request would prove nothing about SEB. The pupil, the class, "
            f"the window or the fixture are the places to look, not the door."
        ]

    claim_missing = seen.claim in (404, 405)
    if seen.gated == 200 and claim_missing:
        return EXIT_UNMEASURED, [
            f"seb door: NOT MEASURED — the paper opened with `require_seb` on and its "
            f"claim route does not exist (HTTP {seen.claim}), which is what a release "
            f"that predates the SEB door answers. Nothing about this release was judged."
        ]

    if seen.gated == 200:
        lines.append(
            f"seb door: FAILED — a plain browser opened a paper with `require_seb` on: "
            f"HTTP 200 on {PAPER_PATH.format(exam_id='<id>')}. The toggle is stored and "
            f"is not being enforced, which is a paper any pupil can open in anything."
        )
    elif seen.gated == 302 and seen.gated_location.endswith(CLAIM_SUFFIX):
        lines.append(
            f"seb door: a plain browser is handed the handshake — "
            f"HTTP 302 -> {seen.gated_location}"
        )
    else:
        lines.append(
            f"seb door: FAILED — a gated paper answered HTTP {seen.gated} "
            f"(Location: {seen.gated_location or '(none)'}), which is neither the paper "
            f"(enforcement is off) nor the handshake page (a refused pupil is given no "
            f"way in)."
        )

    if seen.claim != 200 and not claim_missing:
        lines.append(
            f"seb door: FAILED — the handshake page the door sends a plain browser to "
            f"answers HTTP {seen.claim}, so the road iOS and WKWebView clients depend on "
            f"is not there."
        )

    if seen.matched != 200 or not seen.matched_title:
        lines.append(
            f"seb door: FAILED — a header computed over the exam's own Config Key was "
            f"not admitted (HTTP {seen.matched}, title in page: {seen.matched_title}). "
            f"The door fails closed on an honest client, so nobody can open this paper."
        )

    if seen.foreign == 200:
        lines.append(
            f"seb door: FAILED — a header computed over a *different* key opened the "
            f"paper, so the header is being accepted for existing rather than for "
            f"matching. That is the failure a check without this step cannot see."
        )

    # ── the JavaScript transport: the handshake page's own claim ─────────────
    #
    # The other half of the same door. A client that cannot attach a header at all
    # (SEB on iOS and modern macOS runs on WKWebView) has to prove itself the other
    # way: the page asks the client's own JavaScript API and posts the value back.
    # The header test above cannot see whether *that* road works, and a release where
    # it does not is a release on which every iPad is locked out of every gated
    # paper — with the toggle reading ON, the file downloading, and no symptom
    # anywhere except the pupils' screens.
    #
    # Four shapes, and the three refusals are deliberately different defects: a value
    # hashed over the paper's address (the binding this path is most likely to get
    # backwards), a value over another key, and a value that was honest for the file
    # issued *before* a re-issue. The fourth request is the honest one.
    if seen.js_wrong_address in (404, 405):
        return EXIT_UNMEASURED, [
            f"seb door: NOT MEASURED — the claim route answered HTTP "
            f"{seen.js_wrong_address} to a POST, which is what a release that predates "
            f"the JavaScript handshake answers. The door was judged, the handshake was "
            f"not."
        ]

    for label, value, why in (
        ("a value hashed over the paper's own address", seen.js_wrong_address,
         "The client hashes the URL of the page it ran on, which is the handshake "
         "page; the paper's address is the header transport's string. Accepting it "
         "means the binding is not being checked at all."),
        ("a value computed over another key", seen.js_foreign,
         "That is the falsification: without it, 'a value is posted' and 'the right "
         "value is posted' are indistinguishable."),
        ("a value from before the key was re-issued", seen.js_stale,
         "The value was honest for the key stored a moment earlier, so accepting it "
         "means the door honours a value once it has been right rather than the key "
         "that is stored now — a file that should be dead still opens the paper."),
    ):
        if value != 403:
            lines.append(
                f"seb door: FAILED — the JavaScript handshake answered HTTP {value} to "
                f"{label}; only a 403 refuses it. {why}"
            )

    if not (seen.js_closed == 302
            and seen.js_closed_location.endswith(CLAIM_SUFFIX)):
        lines.append(
            f"seb door: FAILED — after three refused claims the paper answered HTTP "
            f"{seen.js_closed} (Location: "
            f"{seen.js_closed_location or '(none)'}), so something other than a "
            f"matching key opened it. A refused claim must leave the door exactly "
            f"where it found it."
        )

    if seen.js_accepted != 200 or not seen.js_accepted_ok:
        lines.append(
            f"seb door: FAILED — the JavaScript handshake refused a client reporting "
            f"the exam's own Config Key value (HTTP {seen.js_accepted}, `ok` in the "
            f"body: {seen.js_accepted_ok}). Every WKWebView client — SEB on iOS and "
            f"modern macOS — is locked out of this paper, and nothing else on this box "
            f"would say so."
        )

    if seen.js_opened != 200 or not seen.js_opened_title:
        lines.append(
            f"seb door: FAILED — a client that proved itself through the JavaScript "
            f"handshake was not admitted to the paper (HTTP {seen.js_opened}, the "
            f"paper's title in the body: {seen.js_opened_title}). The claim route "
            f"accepted it, so the door is not reading the claim it was given."
        )

    _client_verdict(seen, lines)

    # The tail, and it is one table for both halves: a finding outranks a reading that
    # could not be taken, and a reading that could not be taken is never a pass — exit
    # 0 claims the header transport, the claim protocol *and* the page's own script.
    if any(line.startswith("seb door: FAILED") for line in lines):
        return EXIT_NOT_ENFORCED, lines
    if any(line.startswith("seb door: NOT MEASURED") for line in lines):
        return EXIT_UNMEASURED, lines
    lines.insert(0, "seb door: OK — the SEB door is enforced on the served release")
    return EXIT_OK, lines


# ── the live database, over the API the app itself uses ──────────────────────


class Rest:
    """PostgREST with the checkout's service key. Reads nothing it does not need."""

    def __init__(self, url: str, key: str) -> None:
        self.url = url.rstrip("/")
        self.key = key

    def call(self, method: str, path: str, payload=None, prefer: str | None = None,
             missing_ok: bool = False):
        """One PostgREST call. `missing_ok` answers `None` for an absent object.

        PostgREST answers 404 (`PGRST205`) for a table it does not have, and there is
        exactly one table here whose absence is a *reading* rather than a failure:
        the refusal record, which a database older than migration 064 does not have.
        A release from before it is not a release whose door is broken, so that
        absence has to be a different answer from every other 4xx — which stays an
        exception, because a mistyped column must never read as "nothing to see".
        """
        headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}",
                   "Accept": "application/json", "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        response = requests.request(
            method, f"{self.url}/rest/v1/{path}",
            data=json.dumps(payload) if payload is not None else None,
            headers=headers, timeout=REQUEST_TIMEOUT)
        if response.status_code >= 400:
            if missing_ok and response.status_code in (400, 404):
                return None
            raise RuntimeError(f"{method} {path.split('?')[0]} -> "
                               f"HTTP {response.status_code}: {response.text[:200]}")
        text = response.text
        return json.loads(text) if text.strip() else None

    # -- the pupil ------------------------------------------------------------

    def pupil(self, email: str) -> dict | None:
        """The account's row, by the address the smoke config names.

        `profiles.email` is migration 040's mirror of the address in `auth.users`;
        it is read here rather than the auth admin API because that API pages fifty
        users at a time and cannot be filtered server-side.
        """
        rows = self.call("GET", f"profiles?select=id,role,class_id,school_id"
                                f"&email=eq.{requests.utils.quote(email)}") or []
        return rows[0] if rows else None

    # -- the throwaway row ----------------------------------------------------

    def leftovers(self) -> list[dict]:
        return self.call("GET", f"exams?select=id,title&title=eq.{requests.utils.quote(TITLE)}") or []

    def sweep(self) -> int:
        """Delete anything an earlier run was killed before removing. Idempotent."""
        removed = 0
        for row in self.leftovers():
            exam_id = row.get("id")
            if not exam_id:
                continue
            self.call("DELETE", f"submissions?exam_id=eq.{exam_id}")
            self.call("DELETE", f"exams?id=eq.{exam_id}")
            removed += 1
        return removed

    def create(self, template: dict, *, pupil: dict) -> str:
        """Clone a real paper into a throwaway one the pupil can actually open."""
        payload = {key: value for key, value in template.items()
                   if key not in ("id", "created_at", "updated_at")}
        payload.update({
            "title": TITLE,
            "school_id": pupil["school_id"],
            "class_ids": [pupil["class_id"]],
            "target_mode": "class",
            "status": "active",
            "is_published": True,
            "publish_mode": "manual",
            "start_at": None,
            "end_at": None,
            "max_attempts": 5,
            "require_seb": False,          # C0 runs ungated on purpose
            "seb_config_key": None,
        })
        created = self.call("POST", "exams", payload, prefer="return=representation")
        return str((created or [{}])[0].get("id") or "")

    def gate(self, exam_id: str, key: str) -> None:
        """The one difference between C0 and everything after it."""
        self.call("PATCH", f"exams?id=eq.{exam_id}",
                  {"require_seb": True, "seb_config_key": key},
                  prefer="return=representation")

    def refusals(self, exam_id: str) -> list[dict] | None:
        """The refusal rows this exam holds, or `None` when the table does not exist.

        The only reader the client half has: the handshake page's own script posts a
        report, and the question is whether a row exists afterwards. `None` says the
        *database* is older than the record (migration 064) — a release from before
        it, and not a door that stopped working.
        """
        return self.call(
            "GET", "seb_door_refusal?select=reason,student_id,user_agent"
                   f"&exam_id=eq.{exam_id}", missing_ok=True)

    def forget(self, exam_id: str) -> tuple[int, int, int | None]:
        """Delete the row, its sittings and its refusals; return what is left.

        The refusal count is `None` on a database with no refusal table at all — the
        one reading that means "nothing was recorded here", as against "the cleanup
        left rows behind". Both are read back rather than assumed: a throwaway exam
        in a live school is the one side effect this check cannot hand back.
        """
        self.call("DELETE", f"submissions?exam_id=eq.{exam_id}")
        self.call("DELETE", f"exams?id=eq.{exam_id}")
        exams = self.call("GET", f"exams?select=id&id=eq.{exam_id}") or []
        sittings = self.call("GET", f"submissions?select=id&exam_id=eq.{exam_id}") or []
        rows = self.refusals(exam_id)
        return len(exams), len(sittings), (None if rows is None else len(rows))


# ── the served app ───────────────────────────────────────────────────────────


def sign_in(base: str, email: str, password: str, *, verify_tls: bool):
    """A pupil session, through the app's own login page.

    The CSRF token is read out of the page rather than skipped: the login route is a
    write, and a check that turns the protection off to get in is not measuring the
    release a pupil meets.
    """
    session = requests.Session()
    session.headers["User-Agent"] = "ScanGrade-SebDoorGate/1"
    page = session.get(f"{base}{LOGIN_PATH}", timeout=REQUEST_TIMEOUT, verify=verify_tls)
    if page.status_code != 200:
        raise RuntimeError(f"GET {LOGIN_PATH} -> HTTP {page.status_code}")
    found = CSRF_RE.search(page.text)
    if not found:
        raise RuntimeError(f"{LOGIN_PATH} carries no csrf-token meta tag")
    response = session.post(
        f"{base}{LOGIN_PATH}",
        data={"_csrf_token": found.group(1), "email": email, "password": password},
        timeout=REQUEST_TIMEOUT, verify=verify_tls, allow_redirects=False)
    if response.status_code in (301, 302, 303, 307, 308):
        return session
    raise RuntimeError(f"POST {LOGIN_PATH} -> HTTP {response.status_code} "
                       f"(the credentials were not accepted)")


def hit(session, url: str, *, headers: dict | None, verify_tls: bool):
    """One paper request: (status, location, body) with redirects left alone."""
    response = session.get(url, headers=headers or {}, timeout=REQUEST_TIMEOUT,
                           verify=verify_tls, allow_redirects=False)
    return response.status_code, response.headers.get("Location", ""), response.text


def post_claim(session, url: str, value: str, *, token: str, verify_tls: bool):
    """One claim, posted the way the handshake page posts it: (status, `ok`).

    The token travels in the header the page sends it in, because the route is a
    state-changing POST and the app's guard reads it there; a check that skipped the
    token would measure the CSRF guard and report it as a door that refuses honest
    clients. Nothing is redirected: a 302 here is itself an observation.
    """
    response = session.post(
        url, json={JS_VALUE_FIELD: value},
        headers={CSRF_HEADER: token, "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT, verify=verify_tls, allow_redirects=False)
    try:
        body = response.json() or {}
    except ValueError:
        body = {}
    return response.status_code, bool(body.get("ok"))


# ── C6: the handshake page, driven in a real browser ─────────────────────────


@dataclass
class ClientReading:
    """What the browser's page did on its own. No verdict in here, on purpose."""

    state: str = ""                  # the page's own state when it settled
    refused_shown: bool = False      # the refusal panel is the one it laid out
    seb_api: bool = False            # ...and whether the browser had a SEB API
    reports: int = 0                 # POSTs the page made to the refusal route, first load
    reports_total: int = 0           # ...and across both loads (the paper sends it back)
    reason: str = ""                 # the reason in that POST's body
    token: bool = False              # ...posted with the CSRF header the route reads
    claims: int = 0                  # POSTs to the claim route (must be none)
    status: int = 0                  # what the refusal route answered
    landed: str = ""                 # where the paper sent that browser afterwards

    @property
    def relocked(self) -> bool:
        """The paper still hands this browser the handshake — the report opened nothing.

        Compared against the *handshake* suffix and not the refusal one: a browser
        parked on `/seb-claim/refused` is not a browser that was let into the paper.
        """
        return self.landed.endswith(CLAIM_SUFFIX)


def client_expression() -> str:
    """The JavaScript that reads what the page's own script decided.

    Read as *layout*, not as Alpine's internals: each `x-show` panel is the browser's
    own answer to "is this on the screen", which is the same question a pupil
    answers by looking. The Alpine state is read beside it as a second witness, and
    the state names are the page's own contract — `student/seb_claim.html` writes
    `state === 'refused'` and the test file fails if that string leaves the page.

    `seb` is the gate's own honesty check about its population: an empty
    `SafeExamBrowser` object would mean this is not the client the `no_client` branch
    exists for, and the reading would be about something else.
    """
    return (
        "(() => {"
        f"  const names = {json.dumps(['checking', 'refused', 'error'])};"
        "  const panels = {};"
        "  for (const el of document.querySelectorAll('[x-show]')) {"
        "    const attr = el.getAttribute('x-show') || '';"
        "    const shown = getComputedStyle(el).display !== 'none';"
        "    for (const name of names) {"
        "      if (attr.indexOf(name) !== -1) panels[name] = shown;"
        "    }"
        "  }"
        # The *scope that has a `state`*, not the first `[x-data]` on the page. The
        # layout is full of them (the app's own shell carries one), and the first
        # match is the outermost: reading `[0].state` off it answered `null` on a page
        # whose panel was unmistakably refused. Measured, not deduced — this read was
        # wrong until a real browser was pointed at it.
        "  let state = null;"
        "  for (const el of document.querySelectorAll('[x-data]')) {"
        "    for (const scope of (el._x_dataStack || [])) {"
        "      if (scope && typeof scope.state === 'string') state = scope.state;"
        "    }"
        "  }"
        "  return {"
        "    path: location.pathname,"
        "    state: state,"
        "    panels: panels,"
        "    seb: !!(window.SafeExamBrowser && window.SafeExamBrowser.security),"
        "  };"
        "})()"
    )


def _client_from(value: dict, landed: str, events: list, first_load: int) -> ClientReading:
    """The browser's own account of the visit, out of the events `send` kept.

    `Network.requestWillBeSent` is the page's request **as the browser made it**,
    which is the only place the page — and not this file — is the author. Counting
    the POSTs is therefore a different observation from C4, which composes its own.
    The status comes from `Network.responseReceived` matched by `requestId`, because
    a POST the page fired and a POST that reached the route are different findings.

    Two counts, because the visit has two loads and they answer different questions:
    `reports` is the first load's ("one locked-out visit reports once") and
    `reports_total` is both ("one row per report", which is what C6c checks).
    """
    value = value or {}
    panels = value.get("panels") or {}
    reading = ClientReading(
        state=str(value.get("state") or ""),
        refused_shown=bool(panels.get("refused")),
        seb_api=bool(value.get("seb")),
        landed=landed or str(value.get("path") or ""),
    )
    reports: dict = {}
    for ev in events:
        if ev.get("method") != "Network.requestWillBeSent":
            continue
        params = ev.get("params") or {}
        request = params.get("request") or {}
        if str(request.get("method") or "").upper() != "POST":
            continue
        url = str(request.get("url") or "")
        if url.endswith(REFUSAL_SUFFIX):
            reading.reports_total += 1
            reports[params.get("requestId")] = True
            try:
                body = json.loads(request.get("postData") or "") or {}
            except ValueError:
                body = {}
            reading.reason = str(body.get("reason") or "")
            headers = {str(k).lower(): v
                       for k, v in (request.get("headers") or {}).items()}
            reading.token = bool(headers.get(CSRF_HEADER.lower()))
        elif url.endswith(CLAIM_SUFFIX):
            reading.claims += 1
    for ev in events:
        if ev.get("method") != "Network.responseReceived":
            continue
        params = ev.get("params") or {}
        if params.get("requestId") in reports:
            reading.status = int((params.get("response") or {}).get("status") or 0)
    # The first load's share, counted from the same events: `first_load` is the index
    # `_drive_client` cut the phase at, and the paper navigation comes after it.
    for ev in events[:first_load]:
        if ev.get("method") != "Network.requestWillBeSent":
            continue
        params = ev.get("params") or {}
        request = params.get("request") or {}
        if (str(request.get("method") or "").upper() == "POST"
                and str(request.get("url") or "").endswith(REFUSAL_SUFFIX)):
            reading.reports += 1
    return reading


async def _drive_client(base_url, browser, cookies, claim_url, paper, insecure):
    """Load the handshake page in a headless browser and watch what it does.

    Returns `(reading, reason)`; `reading` is `None` when the browser could not be
    driven at all. The events are collected rather than asked after the fact: the
    report is fired from the same handler that sets the state, so its request is on
    the wire *while* the page is still settling.
    """
    import requests
    import websockets

    port = _free_port()
    profile = tempfile.mkdtemp(prefix="sg-seb-door-")
    argv, env = browser_launch(browser, port, profile)
    if insecure:
        # The certificate the *browser* has to accept. `--insecure` is about the
        # requests session otherwise, and on a self-signed box the browser would
        # answer "the page did not render" while the door was fine.
        argv.insert(1, "--ignore-certificate-errors")
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=env)
    try:
        target = None
        for _ in range(80):
            try:
                pages = [t for t in requests.get(
                    f"http://127.0.0.1:{port}/json/list", timeout=2).json()
                    if t.get("type") == "page"]
                if pages:
                    target = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:                                   # noqa: BLE001
                pass
            time.sleep(0.25)
        if not target:
            return None, "the browser never offered a debug target"

        host = urllib.parse.urlparse(base_url).hostname or "127.0.0.1"
        secure = base_url.startswith("https")
        events: list = []
        value: dict = {}
        landed = ""
        async with websockets.connect(target, max_size=64 * 1024 * 1024) as ws:
            c = _CDP(ws, events)
            await c.send("Network.enable")
            await c.send("Page.enable")
            await c.send("Runtime.enable")
            # The marker the row comes back with: a row this gate did not cause
            # cannot be read as this browser's page having caused it.
            await c.send("Network.setUserAgentOverride", userAgent=BROWSER_UA)
            for name, cookie in cookies.items():
                await c.send("Network.setCookie", name=name, value=cookie,
                             domain=host, path="/", secure=secure)
            await c.send("Emulation.setDeviceMetricsOverride", width=1280, height=900,
                         deviceScaleFactor=1, mobile=False)
            await c.send("Page.navigate", url=claim_url)
            deadline = time.time() + CLIENT_BUDGET
            while True:
                await c.pump(POLL_EVERY)
                out = await c.send("Runtime.evaluate", expression=client_expression(),
                                   returnByValue=True, awaitPromise=True)
                if not out.get("exceptionDetails"):
                    value = (out.get("result") or {}).get("value") or {}
                if str(value.get("state") or "") == "refused" \
                        or (value.get("panels") or {}).get("refused"):
                    # Settle a touch longer: the report leaves from the same handler
                    # that sets the state, so the request has to be on the wire before
                    # the socket closes or the count would read zero on a page that
                    # worked.
                    await c.pump(POLL_EVERY * 2)
                    break
                if time.time() >= deadline:
                    break
            # The report belongs to the *first* load, and the events are cut here for
            # exactly that reason: the navigation below hands this browser the
            # handshake page a second time (the paper refuses it again), and a second
            # visit legitimately reports a second time. Counting both would make every
            # run a finding, which is what the first version of this did.
            await c.pump(POLL_EVERY)
            first_load = len(events)
            # And then the paper, in the same browser: a report must open nothing.
            await c.send("Page.navigate", url=paper)
            await c.pump(POLL_EVERY * 2)
            out = await c.send("Runtime.evaluate", expression="location.pathname",
                               returnByValue=True)
            if not out.get("exceptionDetails"):
                landed = str((out.get("result") or {}).get("value") or "")
        return _client_from(value, landed, events, first_load), None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:                                       # noqa: BLE001
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def client_half(base_url, session, claim_url, paper, *, insecure):
    """Drive the handshake page in a real browser. Returns `(reading, reason)`.

    A box with no browser is neither a failure nor a pass: it is a reading that was
    not taken, and `judge` says so in as many words. The browser is located the way
    the other two gates locate it — `SG_CHROME` first, then the usual names — so one
    conf key arms all three.
    """
    browser = locate_browser()
    if not browser:
        return None, "no browser found (set SG_CHROME to a Chrome/Chromium binary)"
    try:
        reading, reason = asyncio.run(_drive_client(
            base_url, browser, session.cookies.get_dict(), claim_url, paper, insecure))
    except Exception as exc:                                    # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    return reading, reason


def fixture_title() -> str:
    """The demo fixture's own title, loaded from the spec rather than restated.

    `deploy/demo_exam_fixture.py` is the one object `manage.py demo-exam` writes the
    paper from, and the deploy refreshes that paper immediately before this runs — so
    cloning it is cloning a row that exists on every release by construction.
    """
    path = Path(__file__).resolve().parent / "demo_exam_fixture.py"
    spec = importlib.util.spec_from_file_location("sg_seb_door_fixture", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.TITLE


# ── the run ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    global REQUEST_TIMEOUT
    parser = argparse.ArgumentParser(
        description="Measure the SEB door on the release this box is serving.")
    parser.add_argument("--base", default="",
                        help="the app to measure (loopback on the deploy box)")
    parser.add_argument("--repo", default=str(ROOT), help="checkout holding .env")
    parser.add_argument("--student", default="",
                        help="email:password — the smoke config's own murid account")
    parser.add_argument("--template-exam", default="",
                        help="exam id to clone (default: the demo fixture in the pupil's school)")
    parser.add_argument("--insecure", action="store_true", help="do not verify TLS")
    parser.add_argument("--timeout", type=int, default=REQUEST_TIMEOUT,
                        help="per-request timeout in seconds")
    parser.add_argument("--keep", action="store_true",
                        help="leave the throwaway row behind (debugging only)")
    args = parser.parse_args(argv)

    base = (args.base or "").rstrip("/")
    credentials = (args.student or "").strip()
    email, _, password = credentials.partition(":")
    if not base or not email or not password:
        print("seb door: NOT MEASURED — --base and --student email:password are both "
              "required (the deploy passes the smoke config's own murid account)")
        return EXIT_UNMEASURED
    REQUEST_TIMEOUT = max(5, int(args.timeout))
    verify_tls = not args.insecure
    if not verify_tls:
        requests.packages.urllib3.disable_warnings()  # noqa: S001 - explicit opt-in

    from deploy import db_snapshot                                       # noqa: E402
    rest = None
    exam_id = ""
    code = EXIT_UNMEASURED
    lines: list[str] = []
    #: Named at every step, because "could not measure" without saying *where* is a
    #: line an operator cannot act on, and a timeout on a spotty link is the likeliest
    #: way this ever refuses to answer.
    step = "reading the credentials"
    try:
        url, key = db_snapshot.load_credentials(Path(args.repo))
        if not url or not key:
            raise RuntimeError(f"no SUPABASE_URL / SUPABASE_SERVICE_KEY in {args.repo}/.env")
        rest = Rest(url, key)

        step = "resolving the pupil and the fixture paper"
        pupil = rest.pupil(email)
        if not pupil or pupil.get("role") != "murid" or not pupil.get("class_id") \
                or not pupil.get("school_id"):
            print(f"seb door: NOT MEASURED — {email} is not a pupil with a class and a "
                  f"school in this database (role={((pupil or {}).get('role'))!r}), so "
                  f"there is no paper to put in front of them")
            return EXIT_UNMEASURED

        template_id = args.template_exam
        if not template_id:
            wanted = fixture_title()
            rows = rest.call(
                "GET", "exams?select=id,title"
                       f"&school_id=eq.{pupil['school_id']}"
                       f"&title=eq.{requests.utils.quote(wanted)}") or []
            template_id = str((rows or [{}])[0].get("id") or "")
        if not template_id:
            print(f"seb door: NOT MEASURED — no paper to clone in this school: the demo "
                  f"fixture is what `manage.py demo-exam` writes, and the deploy "
                  f"refreshes it before this runs")
            return EXIT_UNMEASURED
        template = (rest.call("GET", f"exams?select=*&id=eq.{template_id}") or [{}])[0]
        if not template.get("id"):
            print(f"seb door: NOT MEASURED — no exam row with id {template_id}")
            return EXIT_UNMEASURED

        swept = rest.sweep()
        if swept:
            print(f"seb door: swept {swept} throwaway row(s) an earlier run left behind")

        run_key = secrets.token_hex(32)
        #: What a re-issue writes. One extra key, so the stale case is a *real* change
        #: of the stored value rather than a request the door could not have matched.
        fresh_key = secrets.token_hex(32)
        step = "creating the throwaway row"
        exam_id = rest.create(template, pupil=pupil)
        if not exam_id:
            print("seb door: NOT MEASURED — the throwaway row was not created")
            return EXIT_UNMEASURED
        paper = f"{base}{PAPER_PATH.format(exam_id=exam_id)}"
        claim_url = f"{paper}{CLAIM_SUFFIX}"
        # The hash covers the address the client was *sent to*, and the door builds
        # that address from the request's own root — so this string is the same one
        # the door recomputes for the request below. The hashing is the app's.
        from app.services import seb_config_key as ck                            # noqa: E402
        good = ck.request_hash(paper, run_key)
        foreign = ck.request_hash(paper, secrets.token_hex(32))
        print(f"seb door: measuring {base} against exam {exam_id} (throwaway)")

        seen = Seen()
        step = "signing in through the app's own login page"
        session = sign_in(base, email, password, verify_tls=verify_tls)

        step = "C0, the control"
        seen.control, _, body = hit(session, paper, headers=None, verify_tls=verify_tls)
        seen.control_title = TITLE in body
        print(f"   C0  require_seb=off       -> HTTP {seen.control} "
              f"(title in page: {seen.control_title})")

        step = "turning require_seb on"
        rest.gate(exam_id, run_key)
        step = "C1, a plain browser"
        seen.gated, seen.gated_location, _ = hit(session, paper, headers=None,
                                                 verify_tls=verify_tls)
        print(f"   C1  no header             -> HTTP {seen.gated} "
              f"Location={seen.gated_location or '(none)'}")

        step = "C1b, the handshake page"
        seen.claim, _, claim_body = hit(session, claim_url, headers=None,
                                        verify_tls=verify_tls)
        print(f"   C1b the handshake page    -> HTTP {seen.claim}")

        step = "C2, a matching header"
        seen.matched, _, body = hit(session, paper,
                                    headers={ck.CONFIG_KEY_HEADER: good},
                                    verify_tls=verify_tls)
        seen.matched_title = TITLE in body
        print(f"   C2  matching header       -> HTTP {seen.matched} "
              f"(title in page: {seen.matched_title})")

        step = "C3, another key's header"
        seen.foreign, seen.foreign_location, _ = hit(
            session, paper, headers={ck.CONFIG_KEY_HEADER: foreign}, verify_tls=verify_tls)
        print(f"   C3  another key's header  -> HTTP {seen.foreign} "
              f"Location={seen.foreign_location or '(none)'}")

        # ── the JavaScript transport, on the same session ────────────────────
        #
        # The three refusals run *before* the honest claim, because a claim is kept
        # in the session: once one is minted the paper opens for this session however
        # the later requests answer, and C4d would then prove nothing.
        step = "C4, reading the handshake page's CSRF token"
        token = CSRF_RE.search(claim_body)
        if not token:
            raise RuntimeError(
                f"{claim_url} carries no csrf-token meta tag, so the page's own script "
                f"cannot post a claim from a browser either")

        step = "C4a, a JavaScript value over the paper's own address"
        seen.js_wrong_address, _ = post_claim(session, claim_url, ck.request_hash(paper, run_key),
                                              token=token.group(1), verify_tls=verify_tls)
        print(f"   C4a value over the paper  -> HTTP {seen.js_wrong_address} "
              f"(must be 403)")

        step = "C4b, a JavaScript value over another key"
        seen.js_foreign, _ = post_claim(session, claim_url,
                                        ck.request_hash(claim_url, secrets.token_hex(32)),
                                        token=token.group(1), verify_tls=verify_tls)
        print(f"   C4b value over a key      -> HTTP {seen.js_foreign} (must be 403)")

        step = "C4c, a JavaScript value from before the re-issue"
        # Honest for the key stored one line ago; the re-issue is what makes it stale.
        stale_value = ck.request_hash(claim_url, run_key)
        rest.gate(exam_id, fresh_key)
        seen.js_stale, _ = post_claim(session, claim_url, stale_value,
                                      token=token.group(1), verify_tls=verify_tls)
        print(f"   C4c value before re-issue -> HTTP {seen.js_stale} (must be 403)")

        step = "C4d, the paper after three refused claims"
        seen.js_closed, seen.js_closed_location, _ = hit(session, paper, headers=None,
                                                         verify_tls=verify_tls)
        print(f"   C4d paper, no claim yet   -> HTTP {seen.js_closed} "
              f"Location={seen.js_closed_location or '(none)'}")

        # ── C6: the same visit, made by the shipped page's own script ────────
        #
        # Everything above is this file speaking the protocol. This is the page
        # speaking it — the half the node-driven suite covers inside a checkout and
        # nothing covered on the box, on the one client population the refusal
        # report exists for.
        step = "C6, the handshake page's own script in a real browser"
        reading, why = client_half(base, session, claim_url, paper,
                                   insecure=not verify_tls)
        if reading is None:
            print(f"   C6  browser               -> NOT MEASURED ({why})")
        else:
            seen.client_measured = True
            seen.client_seb_api = reading.seb_api
            seen.client_state = reading.state
            seen.client_refused_shown = reading.refused_shown
            seen.client_reports = reading.reports
            seen.client_reason = reading.reason
            seen.client_token = reading.token
            seen.client_claims = reading.claims
            seen.client_status = reading.status
            seen.client_relocked = reading.relocked
            seen.client_reports_total = reading.reports_total
            print(f"   C6  browser               -> state={reading.state!r} "
                  f"refusal shown={reading.refused_shown} "
                  f"reports={reading.reports} of {reading.reports_total} "
                  f"({reading.reason!r}) claims={reading.claims} -> "
                  f"HTTP {reading.status} (landed on {reading.landed or '(nowhere)'})")
            step = "C6c, what the server recorded for that page's report"
            rows = rest.refusals(exam_id)
            if rows is None:
                # No refusal table in this database at all: a release from before
                # migration 064, where the row cannot exist whatever the page does.
                seen.client_record_missing = True
                print("   C6c refusal record        -> this database has no "
                      "seb_door_refusal table (migration 064 not applied)")
            else:
                seen.client_rows_total = len(rows)
                # Attributed by the marker this run alone sets: the rows the refused
                # claims above left are the same pupil's and are nobody's evidence
                # about what the page did.
                marked = [row for row in rows
                          if BROWSER_UA in str(row.get("user_agent") or "")]
                seen.client_rows_marked = len(marked)
                seen.client_rows_foreign = sum(
                    1 for row in marked
                    if str(row.get("student_id")) != str(pupil["id"]))
                print(f"   C6c refusal rows recorded -> {len(rows)} total, "
                      f"{len(marked)} this browser's "
                      f"({len(marked) - seen.client_rows_foreign} of them this "
                      f"pupil's) for {reading.reports_total} report(s)")

        step = "C5, the honest JavaScript claim"
        seen.js_accepted, seen.js_accepted_ok = post_claim(
            session, claim_url, ck.request_hash(claim_url, fresh_key),
            token=token.group(1), verify_tls=verify_tls)
        print(f"   C5  value over the key    -> HTTP {seen.js_accepted} "
              f"(ok in body: {seen.js_accepted_ok})")

        step = "C5b, the paper after the claim"
        seen.js_opened, _, body = hit(session, paper, headers=None, verify_tls=verify_tls)
        seen.js_opened_title = TITLE in body
        print(f"   C5b paper, no header      -> HTTP {seen.js_opened} "
              f"(title in page: {seen.js_opened_title})")

        code, lines = judge(seen)
    except Exception as exc:                                    # noqa: BLE001
        # Not a verdict: the box could not make the measurement, which is never a
        # refusal on its own. The cleanup below still runs.
        lines = [f"seb door: NOT MEASURED — while {step}: {type(exc).__name__}: {exc}"]
        code = EXIT_UNMEASURED
    finally:
        # The cleanup, whatever happened above — and it is *verified*, because a
        # throwaway exam left in a live school is the one side effect this check
        # cannot hand back. A crash before the row exists has nothing to undo.
        if rest is not None and exam_id:
            if args.keep:
                print(f"seb door: kept the throwaway row {exam_id} (--keep)")
            else:
                try:
                    exams_left, sittings_left, refusals_left = rest.forget(exam_id)
                    if exams_left or sittings_left or refusals_left:
                        refusals_note = ("no refusal table in this database"
                                         if refusals_left is None
                                         else f"{refusals_left} refusal row(s)")
                        print(f"seb door: LEFTOVER — {exams_left} exam row(s), "
                              f"{sittings_left} sitting(s) and {refusals_note} for "
                              f"{exam_id} are still in the database; delete them by "
                              f"hand")
                        # A real finding outranks left-over bookkeeping: the refusal
                        # stays a refusal, and the leftover is an extra line.
                        code = EXIT_NOT_ENFORCED if code == EXIT_NOT_ENFORCED \
                            else EXIT_UNMEASURED
                    else:
                        print("seb door: cleaned up (0 rows left)")
                except Exception as exc:                        # noqa: BLE001
                    print(f"seb door: LEFTOVER — the cleanup failed "
                          f"({type(exc).__name__}: {exc}); delete exam {exam_id} by hand")
                    code = EXIT_NOT_ENFORCED if code == EXIT_NOT_ENFORCED else EXIT_UNMEASURED

    print()
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    started = time.time()
    try:
        result = main()
    except KeyboardInterrupt:
        result = EXIT_UNMEASURED
    print(f"({time.time() - started:.1f}s)")
    sys.exit(result)
