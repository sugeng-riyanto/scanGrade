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
  * does the exception question's row exist **once**, look like the
    single-answer row beside it, and carry the "more than one answer" badge —
    with that badge on no other question?

Any of those is a finding. Passing means the exam actually appeared, and that the
row the teacher's exception promises is the row the pupil is given.

The last one is the half a *template read* cannot make, which is why it is here.
Every other statement about that row is a statement about one template, and the
row a pupil meets is the one Alpine laid out. Two sibling `x-if` templates whose
conditions both match — `qKind(i) === 'choice'` and
`qKind(i) === 'choice' && qIsMulti(i)`, and `qKind` is `'choice'` for the
exception too — render **two** rows for the *same* question; that shipped, and was
measured here before it was fixed: `Soal 1: A B C D E` twice, a row that replaces
a letter above a row that adds one. The same reading is what keeps the promise
"the exception's row is the single-answer row": the letters, the computed font,
padding, border and display, the child elements of each button and the number of
rows the question rendered are compared against a plain question's row — plus the
measured size when both rows are on screen — so a second control (the checkbox the
first version of this row carried, with its icon inside the button) fails here
rather than on a pupil's screen.

Where the exception comes from, in two directions, because "the row is missing"
has two very different causes. The **data** is read out of the served document
(`type: ({...})[i]`, which the template writes from `question_types`), and the
**spec** is read out of `deploy/demo_exam_fixture.py`, this release's own file. A
paper whose data carries no exception question is *could not measure* — that is
stale demo data, a box fact, and it must not refuse a release. But a fixture that
no longer declares one is a **finding**: the release removed the only subject this
half of the gate has, which is the same shape as deleting the check.

Exit codes
----------
  0  the exam page rendered, with questions and no page error
  1  a finding — blank exam (a real regression)
  2  could not measure — no browser, no credentials, no sittable demo exam, or
     the page did not load. **Not** a pass, and deliberately distinct from one:
     the runner refuses it on a box that enforces this gate (`RENDER_ENFORCE`),
     so a page nobody looked at is never shipped as a page that was verified.

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
import sys
import time
import urllib.parse

# The gate reuses the touch gate's browser locator, sign-in and CDP client — the
# same box, the same account shape, the same socket plumbing, so there is one copy.
# Starting the browser is `start_browser`'s job rather than a second copy of the
# launch-wait here: it owns the debug-target wait, the retry for a launch that never
# came up, and the browser's own last words when it did not. The CDP client keeps
# the *events* (`send` used to drop everything but its own reply), which is what
# makes a page error visible here at all.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from touch_gate import (  # noqa: E402
    _CDP,
    _stop,
    locate_browser,
    sign_in,
    start_browser,
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
#: A card's title, the way `deploy/smoke_test.py` reads the same list — the gate
#: opens the *fixture*, not whatever paper happens to be first, because a pupil's
#: list can legitimately hold a teacher's exam with no exception question on it.
CARD_TITLE_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
#: The paper's own question types, out of the served document. The template writes
#: them into the page as `type: ({"0": "mcq_multi", ...})[i] || 'mcq'`, so the
#: data half of this reading needs no second request and no credentials beyond the
#: session the gate already has.
# The group is not narrowed to an object literal: the *shape* the paper writes is a
# JSON object, and a document whose blob is anything else is one this gate cannot
# read — which `declared_exceptions` answers with `None` rather than with "no
# exception question", the answer that would quietly disarm the check it feeds.
QUESTION_TYPES_RE = re.compile(r"type:\s*\((.*?)\)\[i\]", re.S)

#: The exception's type, as it is *stored*. `question_types.MCQ_MULTI`, the
#: template's `SG_QT.multi` and this name are one string; a unit test holds them
#: together, because a gate that looks for the wrong one answers "no exception
#: question" about a paper that has two.
MULTI_TYPE = "mcq_multi"

#: The badge's two halves, as the pupil's page writes the pair. Held equal to the
#: template's own `t(...)` call by `tests/unit/test_exam_render_gate.py`, so a
#: reworded badge is a test failure rather than a gate that cannot find it.
BADGE_COPY = ("Boleh lebih dari satu", "More than one allowed")

#: The fields of an option button whose equality *is* row parity. Computed style and
#: structure rather than measured size, because this paper lays out **one question at
#: a time** — the others are `display: none` — so a hidden row measures 0x0 while its
#: computed font, padding, border, display and children are exactly what the cascade
#: gave it. Those are the fields a different control moves: the checkbox this row
#: first shipped had a different child list and an icon inside the button, not a
#: different font size. The selection state is deliberately not among them either:
#: `.opt-btn.selected` paints a tint, and a pupil who has answered one question and
#: not the other must not read as a finding.
ROW_KEYS = ("font", "pad", "border", "display", "kids")

#: The measured size of an option button, compared only when **both** rows are
#: actually on screen. On this paper that is true only when the question being read
#: is the plain one and the exception is the laid-out question beside it — so this is
#: a bonus reading, and the claim that survives every navigation is the computed one.
SIZE_KEYS = ("w", "h")


def demo_exam_id(session, base_url):
    """The demo exam on the pupil's list, or `None`.

    The same fixture `deploy/smoke_test.py` reads, and chosen the same way: the
    *fixture's* own card when the list carries one. This gate measures the exception
    question on the paper it opens, and a pupil's list can legitimately offer a
    teacher's paper first — one with no exception on it, which would read as "could
    not measure" while a perfectly measurable paper sat below it. The first link is
    still the fallback, so a list without the fixture is opened exactly as before.
    """
    try:
        listing = session.get(f"{base_url}/student/exams", timeout=REQUEST_TIMEOUT)
    except Exception:  # noqa: BLE001 - any failure here is "cannot measure"
        return None
    if listing.status_code != 200:
        return None
    cards = sittable_exams(listing.text)
    if not cards:
        return None
    spec = fixture_spec()
    if spec is not None:
        for exam_id, title in cards:
            if spec.is_fixture(title):
                return exam_id
    return cards[0][0]


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
        # gate answered "could not measure" (exit 2) — and a run that answers
        # could-not-measure is a release in which nothing looked at the page, which
        # the runner now refuses on an enforcing box — so the read must survive a
        # document that is not built yet.
        "    bodyText: (d.body && d.body.innerText || '').slice(0, 120),"
        "  };"
        "})()"
    )


def paper_expression() -> str:
    """The JavaScript that reads the answer rows out of the laid-out paper.

    Per question block: how many `.opt-row`s it contains (the number this gate
    exists for), the tick state of each option button, and — per button — the
    geometry and computed style a parity claim is made of. It returns what it
    found and never judges: the verdicts are Python's, so they are testable
    without a browser.
    """
    return (
        "(() => {"
        "  const d = document;"
        "  const paper = d.querySelector('.exam-answers');"
        "  if (!paper) return {error: 'no .exam-answers on the page'};"
        "  const norm = s => (s || '').replace(/\\s+/g, ' ').trim();"
        "  const oneRow = (r) => ({"
        "    buttons: [...r.querySelectorAll('button.opt-btn')].map(b => {"
        "      const cs = getComputedStyle(b), box = b.getBoundingClientRect();"
        "      return {"
        "        letter: norm(b.textContent),"
        "        pressed: b.hasAttribute('aria-pressed'),"
        "        w: Math.round(box.width), h: Math.round(box.height),"
        "        font: cs.fontSize + '/' + cs.fontWeight,"
        "        pad: [cs.paddingTop, cs.paddingRight, cs.paddingBottom, cs.paddingLeft].join(' '),"
        "        border: [cs.borderTopWidth, cs.borderTopStyle, cs.borderTopLeftRadius].join(' '),"
        "        display: cs.display,"
        "        kids: [...b.children].map(c => c.tagName.toLowerCase()).join(','),"
        "        icons: b.querySelectorAll('i, svg, input, img').length,"
        "      };"
        "    }),"
        "    other: [...r.children].filter(c => !c.matches('button'))"
        "             .map(c => c.tagName.toLowerCase()).join(','),"
        "  });"
        "  const blocks = [];"
        "  [...paper.children].forEach((b, i) => {"
        "    const rows = [...b.querySelectorAll('.opt-row')];"
        "    const text = norm(b.innerText);"
        "    if (!rows.length && !/Soal|Question/.test(text)) return;"
        "    const label = [...b.querySelectorAll('span')].find("
        "      s => /^(Soal|Question)\\s*\\d+\\s*:/.test(norm(s.textContent)));"
        "    blocks.push({"
        "      index: i,"
        "      label: label ? norm(label.textContent) : '',"
        "      chips: [...b.querySelectorAll('span.inline-flex')].map(s => norm(s.textContent)),"
        "      rows: rows.map(oneRow),"
        "    });"
        "  });"
        "  return {blocks: blocks};"
        "})()"
    )


def sittable_exams(html):
    """``[(exam_id, title)]`` — the cards on the pupil's exam list.

    The same reading `deploy/smoke_test.py` makes of the same page, so the two
    cannot disagree about which paper the demo fixture is.
    """
    from html import unescape

    found = []
    marks = list(EXAM_LINK_RE.finditer(html or ""))
    for index, mark in enumerate(marks):
        start = marks[index - 1].end() if index else 0
        titles = CARD_TITLE_RE.findall(html[start:mark.start()])
        title = unescape(TAG_RE.sub("", titles[-1])).strip() if titles else ""
        found.append((mark.group(1), title))
    return found


def fixture_spec():
    """This release's demo-exam fixture, loaded by path, or `None`.

    Loaded rather than imported: `deploy/` is not a package and the gate runs from
    the box's checkout with no app environment. It is stdlib-only by design, so
    this is the same object `manage.py demo-exam` writes.
    """
    import importlib.util

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_exam_fixture.py")
    if not os.path.exists(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location("sg_render_gate_fixture", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - an unreadable spec is "cannot measure"
        return None
    return module


def declares_exception(question_types):
    """How many exception questions this `{index: type}` mapping carries."""
    return len([1 for value in (question_types or {}).values()
                if str(value).strip().lower() == MULTI_TYPE])


def declared_exceptions(html):
    """What the *served* paper says it carries, or `None` if it cannot be read.

    `None` is the important answer: it is "the document did not carry the question
    types", which is a page this gate cannot judge rather than a paper with no
    exception question on it — and the caller answers those two differently.
    """
    match = QUESTION_TYPES_RE.search(html or "")
    if not match:
        return None
    try:
        types = json.loads(match.group(1))
    except ValueError:
        return None
    if not isinstance(types, dict):
        # A blob of another shape is a template this gate does not recognise, not a
        # paper without the exception: `{"0": …}` is the only thing the page writes.
        return None
    return declares_exception(types)


def _choice_blocks(paper):
    """The rendered question blocks that hold an answer row."""
    return [b for b in ((paper or {}).get("blocks") or []) if b.get("rows")]


def _is_exception(block):
    """Does any of this question's rows carry the exception's control?

    `aria-pressed` is on the exception's buttons and on nothing else in the paper,
    which is what makes it readable from the lay-out — and "any row" rather than
    "the first row" is deliberate: a duplicated row is a finding this reading has
    to see *and* classify, not one that hides behind the row above it.
    """
    for row in block.get("rows") or []:
        for button in row.get("buttons") or []:
            if button.get("pressed"):
                return True
    return False


def _label_prefix(label):
    """``Soal `` / ``Question `` — the part of the number label both rows share."""
    return re.sub(r"\d.*$", "", str(label or "")).strip()


def _letters(buttons):
    return [b.get("letter") for b in buttons or []]


def row_differences(reference, exception_blocks):
    """How each exception row fails to be the plain row beside it.

    Compared field by field rather than as a whole, because "the rows differ" sends
    the reader to two templates; "option B is 34px wide where the plain row's is
    44px" sends them to the width that changed.
    """
    problems = []
    ref_buttons = (reference.get("rows") or [{}])[0].get("buttons") or []
    ref_icons = sum(int(b.get("icons") or 0) for b in ref_buttons)
    if ref_icons:
        problems.append(
            f"the single-answer row {reference.get('label')!r} carries {ref_icons} "
            f"icon/control element(s) inside its option buttons")
    ref_prefix = _label_prefix(reference.get("label"))
    for block in exception_blocks:
        where = block.get("label") or "the exception question"
        rows = [r for r in block.get("rows") or []
                if (r.get("buttons") or []) and r["buttons"][0].get("pressed")] \
            or (block.get("rows") or [{}])
        if _label_prefix(block.get("label")) != ref_prefix:
            problems.append(
                f"{where}'s number label does not read like {reference.get('label')!r}, "
                f"so the exception is not drawn as the same kind of block")
        buttons = rows[0].get("buttons") or []
        if len(buttons) != len(ref_buttons):
            problems.append(
                f"{where} offers {len(buttons)} option(s) where the single-answer row "
                f"{reference.get('label')!r} offers {len(ref_buttons)}")
            continue
        if _letters(buttons) != _letters(ref_buttons):
            problems.append(
                f"{where} offers the letters {_letters(buttons)} where the "
                f"single-answer row offers {_letters(ref_buttons)}")
        for mine, theirs in zip(buttons, ref_buttons):
            differing = [key for key in ROW_KEYS if mine.get(key) != theirs.get(key)]
            both_up = all(int(mine.get(key) or 0) > 0 and int(theirs.get(key) or 0) > 0
                          for key in SIZE_KEYS)
            if both_up:
                differing += [key for key in SIZE_KEYS if mine.get(key) != theirs.get(key)]
            if differing:
                problems.append(
                    f"{where}'s option {mine.get('letter')} differs from the "
                    f"single-answer row's in {', '.join(differing)} "
                    f"({', '.join(f'{k}={mine.get(k)!r}' for k in differing)} vs "
                    f"{', '.join(f'{k}={theirs.get(k)!r}' for k in differing)})")
        icons = sum(int(b.get("icons") or 0) for b in buttons)
        if icons:
            problems.append(
                f"{where}'s option row carries {icons} icon/control element(s) "
                f"inside its buttons — the row is letters only, so the pupil is not "
                f"handed a second way to draw an answer")
        # On screen, the row's own buttons must agree about their size: one option
        # wider than its siblings is a control that wrapped, which no comparison
        # with another row can see because the other row is hidden.
        laid_out = [(b.get("w"), b.get("h")) for b in buttons if int(b.get("w") or 0) > 0]
        if laid_out and len(set(laid_out)) > 1:
            problems.append(
                f"{where}'s option buttons are not one size ({sorted(set(laid_out))}) "
                f"— the five letters are drawn as five different controls")
    return problems


def parity_problems(paper, declared):
    """The findings only a laid-out page can give, as sentences.

    `declared` is how many exception questions the *served* document's data carries,
    or `None` when that could not be read (the caller answers that case with "could
    not measure" before reaching here).
    """
    problems = []
    choice = _choice_blocks(paper)
    if not choice:
        return ["no multiple-choice answer row rendered on the page"]

    for block in [b for b in choice if len(b.get("rows") or []) > 1]:
        problems.append(
            f"{block.get('label') or 'a question'} rendered "
            f"{len(block.get('rows'))} answer rows — one question shown twice, once "
            f"as a row that replaces a letter and once as a row that adds one")

    exceptions = [b for b in choice if _is_exception(b)]
    plain = [b for b in choice if not _is_exception(b)]

    if declared is not None and len(exceptions) != declared:
        problems.append(
            f"the paper's data carries {declared} exception question(s) and the page "
            f"rendered {len(exceptions)} of them")

    for block in exceptions:
        found = [c for c in (block.get("chips") or []) if c in BADGE_COPY]
        if len(found) != 1:
            problems.append(
                f"{block.get('label') or 'the exception question'} carries "
                f"{len(found)} badge(s) saying more than one answer is allowed, and "
                f"it must carry exactly one beside its number")
    for block in plain:
        if [c for c in (block.get("chips") or []) if c in BADGE_COPY]:
            problems.append(
                f"{block.get('label') or 'a single-answer question'} carries the "
                f"\"more than one answer\" badge although its data is one answer")

    if exceptions and plain:
        # The reference is a plain row that is *on screen* when one is: this paper
        # lays out one question at a time, so the current question's row is the only
        # one with a measured size, and comparing against that one is what adds the
        # size half. The computed half is the same either way.
        on_screen = [b for b in plain
                     if any(int(button.get("w") or 0) > 0
                            for row in b.get("rows") or []
                            for button in row.get("buttons") or [])]
        problems.extend(row_differences((on_screen or plain)[0], exceptions))
    return problems


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


def readiness(value):
    """Is there a page *and* its answer rows to judge yet?

    Named rather than written into the loop because it is the one decision the poll
    is: the blocks and the question controls arrive together, but the rows inside
    them come from Alpine's own `x-if`/`x-for`, so "there is a block" is not yet "there
    is a row" — and a parity claim about an empty row is a finding about the gate's
    timing rather than about the page.
    """
    return (int(value.get("qbtns") or 0) >= 1
            and int(value.get("answers") or 0) >= 1
            and bool(value.get("paper")))


async def read_once(cdp, value):
    """One poll: the page's own reading, then the answer rows, into `value`.

    Returns the page read's `exceptionDetails` when it threw — which is *not* a
    verdict, and the caller keeps looking until the budget runs out — or `None` when
    it read. The rows are read here, in the same pass, because the two readings have
    to describe one layout: a row read before Alpine finished is a row that is not
    there yet.
    """
    out = await cdp.send(
        "Runtime.evaluate",
        expression=render_expression(),
        returnByValue=True,
        awaitPromise=True,
    )
    if out.get("exceptionDetails"):
        return out["exceptionDetails"]
    value.update((out.get("result") or {}).get("value") or {})
    paper = await cdp.send(
        "Runtime.evaluate",
        expression=paper_expression(),
        returnByValue=True,
        awaitPromise=True,
    )
    if not paper.get("exceptionDetails"):
        found = (paper.get("result") or {}).get("value") or {}
        if found.get("blocks"):
            value["paper"] = found
        elif found.get("error"):
            # Named rather than swallowed: a reading that could not be taken must be
            # reportable as "could not measure" and not as an absence of rows.
            value["paper_error"] = found["error"]
    return None


async def _render(base_url, browser, cookies, exam_path):
    """Drive one headless browser to the exam page. Returns `(reading, events, reason)`.

    Starting the browser, waiting for its debug target and retrying a *launch* that
    never came up are `start_browser`'s job — one copy of how this box starts
    Chrome, so the retry and Chrome's own last words reach this gate too. What is
    not retried is the page: a reading that came back empty is the page's answer.
    """
    import websockets

    proc, target, profile, reason = start_browser(browser)
    if target is None:
        return None, [], reason
    try:
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
                threw = await read_once(c, value)
                if threw:
                    # A read that fails mid-navigation is not a verdict — keep
                    # looking, and only give up when the budget is spent.
                    if time.time() >= deadline:
                        return None, events, (
                            f"the page threw while reading it: {threw}")
                    continue
                if readiness(value):
                    break
                if time.time() >= deadline:
                    # One more short settle so an error thrown late is captured
                    # before the finding is reported.
                    await c.pump(POLL_EVERY)
                    break
        return value, events, None
    finally:
        _stop(proc)
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

    # A page that did not appear is judged by the three checks above, and nothing
    # below can make that verdict stronger — so the blank-page finding is returned
    # from exactly where it always was, before the exception half is asked. A row
    # that cannot be read on a page that *did* render is the other case, and it is
    # answered below rather than folded in here: "we could not look at the row" and
    # "the page is blank" are different sentences to the operator.
    if problems:
        payload = {
            "exam_id": exam_id,
            "reading": reading,
            "errors": errors,
            "problems": problems,
        }
        lines.append(
            f"exam render gate: FAILED — the pupil's exam page did not render "
            f"({exam_id}):"
        )
        for problem in problems:
            lines.append(f"    - {problem}")
        for err in errors[:6]:
            lines.append(f"    - {err}")
        return EXIT_FINDING, lines, payload

    # ── the exception question: the data, then the fixture spec, then the rows ──
    #
    # Three questions in a fixed order, because each answer is a different kind of
    # fact and only one of them is about this release's rendering:
    #
    #   the spec (`demo_exam_fixture.py`, this release's file) — a fixture that no
    #     longer declares an exception question is a release that removed the only
    #     subject this half of the gate has. A finding.
    #   the data (the served document's own `question_types`) — a paper with none is
    #     stale demo data on this box: the seeder did not run, or ran before the
    #     fixture carried one. Could not measure, and loud, because an unmeasured
    #     release is not a bad one.
    #   the rows (the laid-out DOM) — once both are known to carry one, a render that
    #     does not show it, or shows it twice, or shows it without its badge, is a
    #     finding.
    spec = fixture_spec()
    if spec is None:
        return (
            EXIT_CANNOT_MEASURE,
            [
                "exam render gate: could not measure — deploy/demo_exam_fixture.py",
                "    could not be read, so nothing says whether the exception question",
                "    this gate measures is part of the release it is measuring",
            ],
            {"reason": "unreadable-fixture-spec"},
        )
    fixture_exceptions = declares_exception(getattr(spec, "QUESTION_TYPES", {}))
    if not fixture_exceptions:
        return (
            EXIT_FINDING,
            [
                "exam render gate: FAILED — this release's demo fixture declares no",
                f"    {MULTI_TYPE} question, so the exception's row and its badge have no",
                "    paper to be measured on. The fixture is the one exam the browser",
                "    gates are allowed to sit (deploy/demo_exam_fixture.py), and an",
                "    exception question on it is what makes the pupil's row measurable",
                "    here rather than only in a template read.",
            ],
            {"exam_id": exam_id, "reason": "fixture-declares-no-exception"},
        )

    try:
        served = session.get(f"{base_url}{exam_path}", timeout=REQUEST_TIMEOUT)
        served_html = served.text if served.status_code == 200 else ""
        served_note = "" if served.status_code == 200 else f"HTTP {served.status_code}"
    except Exception as exc:  # noqa: BLE001 - any failure here is "cannot measure"
        served_html, served_note = "", f"{type(exc).__name__}: {exc}"
    declared = declared_exceptions(served_html)
    if declared is None:
        return (
            EXIT_CANNOT_MEASURE,
            [
                "exam render gate: could not measure — the served paper did not carry",
                "    its own question types, so this gate cannot say how many exception",
                f"    questions it was asked to look for{f' ({served_note})' if served_note else ''}.",
            ],
            {"exam_id": exam_id, "reason": "no-question-types"},
        )
    if declared == 0:
        return (
            EXIT_CANNOT_MEASURE,
            [
                "exam render gate: could not measure — the paper this pupil may sit",
                f"    carries no exception question ({MULTI_TYPE}), so the row and its",
                "    badge were not measured. That is this box's demo data, not a",
                "    property of the release: re-seed it with",
                "        python manage.py demo-exam",
            ],
            {"exam_id": exam_id, "reason": "no-exception-in-the-paper"},
        )

    paper = reading.get("paper")
    if not paper:
        return (
            EXIT_CANNOT_MEASURE,
            [
                "exam render gate: could not measure — the answer rows could not be",
                "    read out of the laid-out paper",
                f"    {reading.get('paper_error') or 'the reading returned no question block'}",
            ],
            {"exam_id": exam_id, "reason": "no-row-reading"},
        )

    problems = parity_problems(paper, declared)
    payload = {
        "exam_id": exam_id,
        "reading": reading,
        "errors": errors,
        "problems": problems,
        "declared_exceptions": declared,
        "paper": paper,
    }
    if problems:
        lines.append(
            f"exam render gate: FAILED — the pupil's exam page rendered, and the "
            f"exception question's row is not the row it promises ({exam_id}):"
        )
        for problem in problems:
            lines.append(f"    - {problem}")
        return EXIT_FINDING, lines, payload

    lines.append(
        f"exam render gate: OK — the exam page rendered "
        f"{reading['qbtns']} question control(s) and {reading['answers']} answer "
        f"block(s), the {declared} exception question(s) each show one option row "
        f"with their badge, that row is the single-answer row, and no page error "
        f"({exam_id})"
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
