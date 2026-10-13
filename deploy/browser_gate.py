#!/usr/bin/env python3
"""The shipped pages, measured in a headless browser — on a laptop and on every push.

Why this exists
---------------
Three measurements in this tree point a real browser at this app:

  * the exam builder's **44px finger floor** (`deploy/touch_gate.py`),
  * the pupil's **exam page**, questions rendered and the exception question's row
    drawn as the single-answer row (`deploy/exam_render_gate.py`), and
  * the **handshake page's own script** — the client half of the SEB door
    (`deploy/seb_door_gate.py`): a browser with no `SafeExamBrowser` API must report
    its refusal with the CSRF token, post no claim, and be sent back to the handshake
    page if it tries the paper — and the **retry the refusal panel offers** must, when
    pressed with a real mouse event, ask the client again, file the second answer and
    not leave the page (the SEB gate's C7; `seb_findings` judges it here, where there
    is no database to count rows in).

All three ran **only on the deploy box**, against the live database, after a release
had already been fetched and merged. That is the worst possible place to find a DOM
regression: the release is already on the branch the box reads, a finding there is a
rollback, and a developer could not run any of it against their own work.

This file is the missing half. It serves the app from *the checkout it lives in*
(`deploy/fixture_app.py`: the real `create_app`, the real routes, the real templates
and static assets, on `127.0.0.1`, with the two Supabase clients replaced by
in-memory stand-ins seeded from `deploy/demo_exam_fixture.py`), signs in through the
app's own login POST, and then drives the three measurements above against it. It is
the same browser plumbing the box uses — `touch_gate._CDP`, its launcher and its
locator — and the same page readings, because a second copy of a measurement is a
second thing to keep in step (see AGENTS.md on the one CDP client).

What it does *not* do
---------------------
It does not replace the box's runs. `touch_gate`, `exam_render_gate` and
`seb_door_gate` still run in the deploy runner against the served release, because a
green fixture is not evidence about the box's database, its `.env`, its accounts or
its TLS. This is the earlier, cheaper question — "did this release break the pages a
teacher and a pupil actually use" — answered where the change is written.

A part whose subject this release does not ship is *named* and left out of the
verdict (a branch without the SEB door has no handshake page to measure). A part that
simply could not be measured — no browser, a page that would not render, a server
that would not answer — is exit 2, never a pass: the distinction matters more here
than on the box, because a CI job that cannot look must not go green for looking.

Exit codes
----------
  0  every part this release ships was measured, and none of them found anything
  1  a finding — a page this gate measured is broken (a real regression)
  2  nothing could be measured (no browser, the fixture would not serve the pages,
     the app under test would not answer)

Options
-------
  --base-url URL      measure an app already serving somewhere, instead of the
                      fixture (debugging against a dev box; CI uses the fixture)
  --teacher email:pw  the account whose builder page is measured (fixture default)
  --student email:pw  the pupil whose exam page is opened (fixture default)
  --seb-exam ID       the *gated* paper the handshake half is measured on
  --only touch,render,seb   run some of the parts (debugging)
  --port N            bind the fixture to N instead of a free port
  --json              print the whole reading as JSON on stdout
  SG_CHROME           the browser binary, as for `deploy/touch_gate.py`
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import exam_render_gate  # noqa: E402  (the pupil's page, unchanged)
import fixture_app  # noqa: E402  (the pages, served from this checkout)
import touch_gate  # noqa: E402  (the builder's floor, unchanged)

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_CANNOT_MEASURE = 2

#: The three parts, in the order they run, each named so `--only` and every printed
#: line use the same word a failure summary does.
PARTS = ("touch", "render", "seb")

#: What one part answered. `not_shipped` is deliberately its own answer and not a
#: pass: a part with no subject in this release is reported by name, so a release
#: that *drops* the page reads as a release that dropped it rather than as a release
#: that is fine.
STATE_OK = "ok"
STATE_FINDING = "finding"
STATE_UNMEASURED = "unmeasured"
STATE_NOT_SHIPPED = "not-shipped"

#: The refusal the handshake page's script settles into when it finds no
#: `SafeExamBrowser` API — the branch an ordinary browser takes, and the one this
#: gate's population is.
REFUSED = "refused"

#: The reason the page sends in that report. Deliberately a literal here with
#: `tests/unit/test_browser_gate.py` holding it equal to the SEB gate's own constant:
#: the page's `no_client` branch is the one this browser takes, so a reworded reason
#: has to be a red test rather than a reading that quietly never matches. (Read
#: through the module at call time as well, so the two cannot disagree at runtime.)
NO_CLIENT_REASON = "no_client"

#: The reason the *press* has to leave on the wire, and it is the page's own second
#: answer: the SEB gate hands the page a client whose API is there and whose
#: `configKey` is not populated yet, so its retry ends in `no_key` — a different
#: finding from the `no_client` the first load filed, which is exactly what makes the
#: press something to judge rather than a repeat of the load above it.
NO_KEY_REASON = "no_key"


def _seb_module():
    """`deploy/seb_door_gate.py`, or `None` on a release that has no SEB door.

    Loaded by name rather than imported at module level, because this file has to be
    usable on a branch where the door does not exist yet: a top-level import would
    make the whole gate — including the two DOM measurements that have nothing to do
    with SEB — unimportable there, which is the shape that gets a gate deleted.
    """
    try:
        import seb_door_gate

        return seb_door_gate
    except Exception:  # noqa: BLE001 - a release without the door is a fact, not a crash
        return None


def seb_findings(reading, *, no_client: str | None = None,
                 no_key: str | None = None) -> list[str]:
    """The handshake page's own visit, judged. Pure: no network, no browser.

    Each sentence is a different fact, because they have different remedies: a script
    that never ran is a template/script-order defect, a report without the token is a
    CSRF defect, and a paper that admitted the browser is the *door* being unenforced
    — the one failure a page-local reading would otherwise miss entirely.

    The last four are the retry the refusal panel offers, pressed (the SEB gate's C7):
    a control a refused pupil cannot press at all, one that asks nothing, one that
    leaves the page instead of re-asking in it, and one that asks and files nothing.
    The *meaning* of those three words is not decided here — it is derived on the
    reading (`ClientReading.retry_dead` and its neighbours), so the box's verdict and
    this one cannot be about two different presses — and the sentences stop at the
    first that fits, because one press is one act.

    `no_client` and `no_key` are the reasons the SEB gate's module spells, passed in
    when that module is there so the reading is judged against the shipped contract
    rather than against this file's copies of it.
    """
    no_client = no_client or NO_CLIENT_REASON
    no_key = no_key or NO_KEY_REASON
    problems = []
    if not reading.initialized:
        problems.append(
            "the handshake page's script never ran: no Alpine scope with a `state`, so "
            "nothing on the page was asked and nothing was reported")
    if reading.seb_api:
        problems.append(
            "the browser answered with a `SafeExamBrowser` API, so this was not the "
            "population the refusal branch exists for and the reading means nothing")
    if reading.state != REFUSED:
        problems.append(
            f"the page settled on {reading.state!r}, not on {REFUSED!r} — an ordinary "
            f"browser was not told to open the school's .seb file")
    if not reading.refused_shown:
        problems.append(
            "the refusal panel is not the one the page laid out (the pupil is told "
            "nothing about what to do next)")
    if reading.reports < 1:
        problems.append(
            "the page posted no refusal report, so the school's panel has no record "
            "that this browser was turned away")
    if reading.reason != no_client:
        problems.append(
            f"the refusal report says {reading.reason!r} where a browser with no SEB "
            f"API sends {no_client!r}")
    if not reading.token:
        problems.append(
            "the refusal was posted without the CSRF header the route reads (on a real "
            "iPad that is a 403, and the row is never written)")
    if reading.claims:
        problems.append(
            f"the page posted {reading.claims} claim(s) with no client to prove — the "
            f"bypass this page exists to prevent")
    if reading.status and reading.status != 200:
        problems.append(
            f"the refusal route answered HTTP {reading.status} — the page reported a "
            f"refusal the server would not take")
    if not reading.relocked:
        problems.append(
            "the paper admitted this browser instead of sending it back to the "
            "handshake page: the door is not enforced, whatever the page said")
    if not reading.retry_control:
        problems.append(
            "the refusal panel offers no retry the gate could press: the two commonest "
            "refusals here — SEB answering after the page has already asked, and a "
            "pupil returning from the file — are asks that succeed on a second try, "
            "and this control is the only thing that takes them")
    elif reading.retry_dead:
        problems.append(
            "the retry control is dead: pressing it put no request on the wire, so a "
            "pupil whose key is available now stays on the refusal panel however many "
            "times they press")
    elif reading.retry_away:
        problems.append(
            f"the retry control navigates away: pressing it loaded "
            f"{reading.retry_navigations} document(s) and left the pupil on "
            f"{reading.retry_landed or '(nowhere)'} — the panel's retry is an ask in "
            f"the page the pupil is on")
    elif reading.retry_unfiled:
        problems.append(
            f"the retry filed nothing: the press sent {reading.retry_posts} report(s) "
            f"to the refusal route (HTTP {reading.retry_status}, reason "
            f"{reading.retry_reason!r}) where one accepted report carrying "
            f"{no_key!r} is the row a teacher's panel counts")
    return problems


def _seb_shipped(base_url, session, claim_url) -> bool:
    """Does this release serve the handshake page at all?

    A 404 is the answer a release without the SEB door gives, and it is the only
    moment this gate can tell "the page is broken" from "the page is not here" — the
    two must not print the same line.
    """
    try:
        answer = session.get(claim_url, timeout=25, allow_redirects=False)
    except Exception:  # noqa: BLE001 - unreachable is answered by the caller
        return False
    return answer.status_code != 404


def seb_part(base_url, student, exam_id, *, insecure=False, module=None):
    """C6, in CI: the handshake page's own script, driven in a real browser.

    The browser plumbing, the page expression and the reading are the SEB gate's
    (`client_half`), so the two cannot drift; the *verdict* is here, because the
    box's verdict is entangled with the live database (it counts the rows the
    refusal left behind) and this one is about the page. Both halves are real
    measurements of the same shipped script.
    """
    module = module if module is not None else _seb_module()
    if module is None:
        return _part_result(STATE_UNMEASURED, [
            "seb half: could not measure — this release has no deploy/seb_door_gate.py",
            "    (the handshake page's script is measured by the browser plumbing there)",
        ], {"reason": "no-seb-gate"})

    session, reason = touch_gate.sign_in(base_url, student, insecure)
    if session is None:
        return _part_result(STATE_UNMEASURED, [
            "seb half: could not measure — could not sign in", f"    {reason}"
        ], {"reason": reason})

    paper = f"{base_url}{module.PAPER_PATH.format(exam_id=exam_id)}"
    claim_url = f"{paper}{module.CLAIM_SUFFIX}"
    if not _seb_shipped(base_url, session, claim_url):
        return _part_result(STATE_NOT_SHIPPED, [
            f"seb half: not in this release — {module.CLAIM_SUFFIX} is not a route "
            f"this app serves"
        ], {"reason": "not-shipped"})

    reading, reason = module.client_half(base_url, session, claim_url, paper,
                                         insecure=insecure)
    if reading is None:
        return _part_result(STATE_UNMEASURED, [
            "seb half: could not measure — the handshake page was not driven",
            f"    {reason}",
        ], {"reason": reason or "no-reading"})

    # A driver without the press is *unmeasured*, not clean: the two files ship in the
    # same release, so this is a release whose SEB gate stopped pressing the control —
    # and "the panel works" is a claim about both halves of it.
    if not hasattr(reading, "retry_control"):
        return _part_result(STATE_UNMEASURED, [
            "seb half: could not measure — the SEB gate this release ships does not "
            "press the refusal panel's retry control, so only the first load was "
            "measured, and 'the panel works' is a claim about both halves of it",
        ], {"reason": "no-retry-press",
            "reading": reading.__dict__ if hasattr(reading, "__dict__") else {}})

    problems = seb_findings(reading,
                            no_client=getattr(module, "NO_CLIENT_REASON", None),
                            no_key=getattr(module, "NO_KEY_REASON", None))
    payload = {"reading": reading.__dict__ if hasattr(reading, "__dict__") else {}}
    if problems:
        return _part_result(STATE_FINDING, [
            "seb half: FAILED — the handshake page's own script, in a browser with no "
            f"SEB API ({exam_id}):"
        ] + [f"    - {problem}" for problem in problems], {**payload, "problems": problems})

    return _part_result(STATE_OK, [
        "seb half: OK — the script ran, settled on the refusal, posted it once with "
        "its CSRF token and the `no_client` reason, posted no claim, and the paper "
        "still sent that browser back to the handshake page; and the panel's retry, "
        "pressed with a real mouse event, asked the client again and filed the second "
        "answer (`no_key`) without leaving the page"
    ], payload)


def _part_result(state, lines, payload):
    return {"state": state, "lines": list(lines), "payload": payload or {}}


def _verdict(parts: dict):
    """The run's exit code, out of the parts that answered.

    A finding outranks a gap on purpose: "this page is broken" is actionable, and
    hiding it behind "another part could not be measured" would send the reader to
    the wrong problem.
    """
    states = [part["state"] for part in parts.values()]
    if STATE_FINDING in states:
        return EXIT_FINDING
    if STATE_UNMEASURED in states:
        return EXIT_CANNOT_MEASURE
    return EXIT_OK


def run(*, base_url=None, teacher=None, student=None, seb_exam=None, browser=None,
        only=PARTS, port=None, insecure=False):
    """Measure the pages. Returns `(exit_code, lines, payload)`."""
    lines: list[str] = []
    unknown = [name for name in only if name not in PARTS]
    if unknown or not only:
        # A typo in `--only` must not measure a subset and call it a run: the parts
        # that were skipped would be missing from a verdict that reads like a verdict.
        return EXIT_CANNOT_MEASURE, [
            "browser gate: could not measure — unknown part(s): "
            f"{', '.join(unknown) or '(none)'}",
            f"    known parts: {', '.join(PARTS)}",
        ], {"reason": "unknown-part", "unknown": unknown}
    browser = browser or touch_gate.locate_browser()
    if not browser:
        return EXIT_CANNOT_MEASURE, [
            "browser gate: could not measure — no browser found",
            "    set SG_CHROME to a Chrome/Chromium binary, or install one",
        ], {"reason": "no-browser"}

    server = None
    if base_url is None:
        # The fixture: this checkout's pages, served on loopback with a database that
        # is a dict. Only the two clients are replaced; the app is the app.
        try:
            server = fixture_app.serve(port=port)
        except Exception as exc:  # noqa: BLE001 - a fixture that will not start is "cannot measure"
            return EXIT_CANNOT_MEASURE, [
                "browser gate: could not measure — the fixture server did not start",
                f"    {type(exc).__name__}: {exc}",
            ], {"reason": f"{type(exc).__name__}: {exc}"}
        base_url = server.base_url
        teacher = teacher or fixture_app.credentials("guru")
        student = student or fixture_app.credentials("murid")
        seb_exam = seb_exam or fixture_app.GATED_EXAM_ID

    base_url = base_url.rstrip("/")
    lines.append(f"browser gate: measuring {base_url} with {browser}")
    parts: dict[str, dict] = {}
    try:
        if "touch" in only:
            parts["touch"] = _driven(
                "touch gate", lambda: touch_gate.run(base_url, teacher, insecure))
        if "render" in only:
            parts["render"] = _driven(
                "exam render gate",
                lambda: exam_render_gate.run(base_url, student, insecure))
        if "seb" in only:
            if not seb_exam:
                parts["seb"] = _part_result(STATE_UNMEASURED, [
                    "seb half: could not measure — no gated paper to measure",
                    "    pass --seb-exam <id> when pointing this at a running app",
                ], {"reason": "no-exam"})
            else:
                parts["seb"] = _guarded(
                    "seb half",
                    lambda: seb_part(base_url, student, seb_exam, insecure=insecure))
        for part in parts.values():
            lines.extend(part["lines"])
    finally:
        if server is not None:
            server.stop()

    code = _verdict(parts)
    measured = [name for name, part in parts.items()
                if part["state"] in (STATE_OK, STATE_FINDING)]
    lines.append(
        f"browser gate: {'FAILED' if code == EXIT_FINDING else 'OK' if code == EXIT_OK else 'NOT MEASURED'}"
        f" — {len(measured)} part(s) measured ({', '.join(measured) or 'none'})"
        f", {len(only)} asked for"
    )
    return code, lines, {
        "base_url": base_url,
        "browser": browser,
        "parts": {name: {"state": part["state"], "payload": part["payload"]}
                  for name, part in parts.items()},
    }


def _state_for(code: int) -> str:
    """A driven gate's exit code, in this file's own vocabulary."""
    if code == 0:
        return STATE_OK
    if code == 1:
        return STATE_FINDING
    return STATE_UNMEASURED


def _driven(name: str, call):
    """One of the two DOM gates, run: `(code, lines, payload)` → this file's shape."""
    code, lines, payload = call()
    said = list(lines or [])
    if not said:
        said = [f"{name}: no reading (the gate returned {code} without a line)"]
    return _part_result(_state_for(code), said, payload)


def _guarded(name: str, call):
    """A part as one answer, with a crash reported as *not measured*.

    A gate that raises is not a gate that found something, and the difference matters
    to the exit contract: a traceback out of CI reads as a defect in the release, and
    the reader then goes looking for one that is not there. Named and answered —
    which is how the box's own gates treat every failure they can foresee.
    """
    try:
        return call()
    except Exception as exc:  # noqa: BLE001 - any failure here is "could not measure"
        return _part_result(STATE_UNMEASURED, [
            f"{name}: could not measure — the measurement raised",
            f"    {type(exc).__name__}: {exc}",
        ], {"reason": f"{type(exc).__name__}: {exc}"})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.environ.get("BROWSER_GATE_BASE_URL", ""),
                        help="an app already serving somewhere, instead of the fixture")
    parser.add_argument("--teacher", default=os.environ.get("BROWSER_GATE_TEACHER", ""))
    parser.add_argument("--student", default=os.environ.get("BROWSER_GATE_STUDENT", ""))
    parser.add_argument("--seb-exam", default=os.environ.get("BROWSER_GATE_SEB_EXAM", ""))
    parser.add_argument("--only", default=",".join(PARTS),
                        help=f"a comma-separated subset of {','.join(PARTS)}")
    parser.add_argument("--port", type=int, default=None,
                        help="bind the fixture to this port instead of a free one")
    parser.add_argument("--insecure", action="store_true",
                        default=os.environ.get("BROWSER_GATE_INSECURE", "").lower() == "true")
    parser.add_argument("--json", action="store_true",
                        help="print the whole reading as JSON on stdout")
    parser.add_argument("--json-out", default="",
                        help="also write the whole reading to this file, so a CI run "
                             "keeps the reading it failed on")
    args = parser.parse_args(argv)

    only = tuple(name.strip() for name in args.only.split(",") if name.strip())

    code, lines, payload = run(
        base_url=(args.base_url or "").strip() or None,
        teacher=args.teacher.strip() or None,
        student=args.student.strip() or None,
        seb_exam=args.seb_exam.strip() or None,
        only=only,
        port=args.port,
        insecure=args.insecure,
    )
    if args.json_out:
        # Written whatever the verdict is, and for the same reason the box prints its
        # readings: the run that failed is the run whose measurement someone needs.
        Path(args.json_out).write_text(
            json.dumps(payload, indent=1, ensure_ascii=False, default=str),
            encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=1, ensure_ascii=False, default=str))
    else:
        for line in lines:
            print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
