#!/usr/bin/env python3
"""Every report template, rendered from every report fixture, with no key left out.

The defect this exists for is quiet. A report document reads a key it never read
before — a new column on the sheet, a school field, a flag — and the fixtures that
render it in `tests/unit` are not told. Jinja's default `Undefined` draws that hole
as an empty string, the page is still a 200, and every assertion those tests make
about the *other* parts of the sheet still passes: the fixture has lagged its reader
and nothing says so. The one place it shows is production, as a blank where a name
should be.

So the reading is a strict one. Each fixture is run with `jinja2.StrictUndefined` in
force, which turns "this key is missing" into an exception — the failure a page would
give if a key its reader asked for had never been passed. The context is not copied
into this file: it is *captured* from the call the fixture already makes (see
`capture_context`), so a fixture that gains a key is measured with it, a fixture that
loses one fails here, and there is one place a report context is written.

The matrix, and which half of it is a finding
---------------------------------------------
The columns are every report document in the tree and the rows are every report
fixture in `tests/unit` — the grid the two are asked to agree about.

* A fixture's **own** document failing is a finding: the reader has moved and the
  fixture has not. This is the sentence in the title.
* A report document **no** fixture renders is a finding: the document is new and
  needs a fixture, or it reads a key no fixture in the suite supplies at all.
* A **cross** pair (fixture A's context against document B) is *reported, not
  judged*. Two report documents do not share a context, so a miss there is expected;
  what is worth reading is which keys exist across the set, which is what `--show`
  prints.

A fixture whose *file* this interpreter cannot even parse is the third answer, and it
belongs to the box. `python_requires` declares the floor, and the production box's venv
is 3.10 while one fixture's source uses a f-string 3.12 added; refused as a finding,
that quarantined five commits on `main` over a file that is valid under the floor the
project declares — a site that keeps serving the release it already had and says
nothing. Below the floor it is `could not measure` (exit 2) and it names the file and
the floor; at or above the floor the same file stays the finding it is, because then
nothing but the release can explain it. A real finding still outranks it.

Usage::

    python deploy/render_fixture_gate.py               # the matrix and the verdict
    python deploy/render_fixture_gate.py --show print/report_card.html
"""
from __future__ import annotations

import argparse
import contextlib
import re
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_CANNOT_MEASURE = 2

#: What a rendered-anyway cell starts with when the render raised. Kept as a prefix
#: rather than an exception object so the matrix can hold both outcomes in one grid.
ERROR = "__error__"


# ── the fixtures ─────────────────────────────────────────────────────────────
#
# Each entry is the *test's own* rendering call, adapted as little as possible:
# `template` is what that call renders and `build(app)` is the call. The context is
# never written here — it is captured from the call — so there is exactly one place a
# report context lives and it is the suite. `tests/unit` is importable as a package
# from the repository root (the suite imports `tests.unit.git_env` the same way);
# importing these modules defines tests, it does not run one.

@dataclass(frozen=True)
class Fixture:
    name: str
    template: str
    build: Callable[[object], object]
    source: str


def _print_reports_card(app):
    from tests.unit import test_print_reports as t
    return t.TestTheReportCard().render(app)


def _print_reports_sheet(app):
    from tests.unit import test_print_reports as t
    return t.TestTheExamSheet().render(app)


def _class_report_cards(app):
    from tests.unit import test_class_report_cards as t
    return t._render(app, t._cards())


def _principal_signature_sheet(app):
    from tests.unit import test_principal_signature as t
    return t._render_exam_sheet(app, dict(t.EXAM, subject_name="Matematika"))


def _principal_signature_card(app):
    from tests.unit import test_principal_signature as t
    return t._render_report_card(app, dict(t.EXAM, subject_name="Matematika"))


def _reports_hub(app):
    from tests.unit import test_reports_hub as t
    return t._render(app)["html"]


def _learner_files(app):
    from tests.unit import test_learner_files as t
    return t._render(app, "/student/files/download", False)


def _subject_label_pupil_pdf(app):
    from tests.unit import test_subject_label as t
    return t._render_pupil_pdf(app, dict(t.EXAM, subject_name="Matematika"))


FIXTURES: tuple[Fixture, ...] = (
    Fixture("print_reports.report_card", "print/report_card.html", _print_reports_card,
            "tests/unit/test_print_reports.py"),
    Fixture("print_reports.exam_sheet", "teacher/print_exam_report.html",
            _print_reports_sheet, "tests/unit/test_print_reports.py"),
    Fixture("class_report_cards.class_set", "print/report_card.html", _class_report_cards,
            "tests/unit/test_class_report_cards.py"),
    Fixture("principal_signature.exam_sheet", "teacher/print_exam_report.html",
            _principal_signature_sheet, "tests/unit/test_principal_signature.py"),
    Fixture("principal_signature.report_card", "print/report_card.html",
            _principal_signature_card, "tests/unit/test_principal_signature.py"),
    Fixture("reports_hub.hub", "teacher/reports.html", _reports_hub,
            "tests/unit/test_reports_hub.py"),
    Fixture("learner_files.analysis_student", "teacher/analysis_student.html",
            _learner_files, "tests/unit/test_learner_files.py"),
    Fixture("subject_label.pupil_pdf", "student/result_detail_pdf.html",
            _subject_label_pupil_pdf, "tests/unit/test_subject_label.py"),
)

#: The report documents that are not in the `print/` family: the pages a school files,
#: hands out or prints. Every one of them needs a fixture, so a new sheet added to the
#: tree without one is a finding rather than a quiet gap in the columns.
STANDALONE_DOCUMENTS = (
    "teacher/print_exam_report.html",
    "student/result_detail_pdf.html",
    "teacher/analysis_student.html",
    "teacher/reports.html",
    "monitor.html",
)

#: Documents that are deliberately not columns, each with the reason it needs no
#: fixture. An exemption is a sentence rather than a name in a list: the guard test
#: reads this mapping, so `monitor.html` cannot quietly become an unmeasured file.
EXEMPT = {
    "monitor.html": (
        "a live screen, not a filed document: it reads the rows of a sitting in "
        "progress, and a fixture would have to fabricate one. Its own contract is "
        "tests/unit/test_analysis_page.py"),
}

UNDEFINED_RE = re.compile(r"'([^']+)' is undefined")


def report_documents() -> list[str]:
    """The columns: the whole `print/` family, then the standalone documents."""
    found = sorted(path.relative_to(TEMPLATES).as_posix()
                   for path in (TEMPLATES / "print").rglob("*.html"))
    for name in STANDALONE_DOCUMENTS:
        if (TEMPLATES / name).is_file() and name not in found:
            found.append(name)
    return [name for name in found if name not in EXEMPT]


# ── capturing a fixture's context ────────────────────────────────────────────

class _Recorder:
    """A stand-in for a Jinja template that keeps the render call's own kwargs."""

    def __init__(self, seen: dict, name: str):
        self._seen = seen
        self._name = name

    def render(self, *args, **kwargs):
        self._seen["context"] = kwargs
        self._seen["template"] = self._name
        return ""


@contextlib.contextmanager
def _recording(app):
    seen: dict = {}
    original = app.jinja_env.get_template
    app.jinja_env.get_template = lambda name, *a, **kw: _Recorder(seen, name)
    try:
        yield seen
    finally:
        app.jinja_env.get_template = original


class FixtureUnparseable(Exception):
    """The fixture's own module cannot be parsed by the interpreter running here.

    Kept apart from every other capture failure because the answer is different, and
    the difference cost five quarantined releases: the box's venv is Python 3.10,
    `tests/unit/test_reports_hub.py` uses a PEP 701 f-string that 3.12 added, and the
    gate answered "the fixture no longer renders through the Jinja environment this
    gate watches" — a finding about a commit — for a file that renders perfectly under
    the floor the project declares.
    """


def capture_context(app, fixture: Fixture) -> tuple[dict | None, str]:
    """The context a fixture hands Jinja, taken from the call it already makes.

    Copied from the fixture rather than imported *as a dict*, for the reason the
    fixture is the suite's own: a second copy of a report context is exactly the drift
    this gate is about. A builder that renders through something else (a route, say)
    captures nothing, which is answered as "could not measure" rather than as an empty
    context — an empty context renders everything and would be a gate that cannot see.
    """
    with _recording(app) as seen:
        try:
            fixture.build(app)
        except SyntaxError as exc:
            # The fixture's *module* did not parse: a page that does not parse raises
            # TemplateSyntaxError, so this is the interpreter and the file, not the
            # document. Answered by the caller against the floor. (read on: 3.12 here)
            raise FixtureUnparseable(f"{type(exc).__name__}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - answered as a finding by the caller
            return None, f"{type(exc).__name__}: {exc}"
    if "context" not in seen:
        return None, "it does not render through jinja_env.get_template"
    return seen["context"], ""


# ── rendering, strictly ──────────────────────────────────────────────────────

@contextlib.contextmanager
def _strict(app):
    """`StrictUndefined` for the duration, put back whatever happens."""
    from jinja2 import StrictUndefined

    original = app.jinja_env.undefined
    app.jinja_env.undefined = StrictUndefined
    try:
        yield
    finally:
        app.jinja_env.undefined = original


def missing_key(exc: BaseException) -> str:
    """The key Jinja could not find, from the exception it raised."""
    match = UNDEFINED_RE.search(str(exc))
    return match.group(1) if match else ""


def strict_render(app, template: str, context: dict) -> str:
    """The rendered page, or `ERROR` + the first key the document could not find."""
    with _strict(app):
        try:
            return app.jinja_env.get_template(template).render(**context)
        except Exception as exc:  # noqa: BLE001 - the finding *is* the exception
            key = missing_key(exc)
            detail = f"'{key}' is undefined" if key else \
                (str(exc).strip().splitlines() or [type(exc).__name__])[0]
            return ERROR + detail


# ── the matrix, and the reading of it ────────────────────────────────────────

@dataclass
class Reading:
    documents: list[str]
    contexts: dict = field(default_factory=dict)        # fixture -> captured context
    capture_errors: dict = field(default_factory=dict)  # fixture -> why not measurable
    unparseable: dict = field(default_factory=dict)     # fixture -> its file will not parse
    rendered: dict = field(default_factory=dict)        # (fixture, document) -> page

    def cell(self, fixture: str, document: str) -> str:
        return self.rendered.get((fixture, document), "")

    def rendered_pairs(self) -> int:
        return sum(1 for page in self.rendered.values() if not page.startswith(ERROR))

    def own_failures(self) -> list[tuple[str, str, str]]:
        out = []
        for fixture in FIXTURES:
            page = self.cell(fixture.name, fixture.template)
            if page.startswith(ERROR):
                out.append((fixture.name, fixture.template, page[len(ERROR):]))
        return out

    def orphan_documents(self) -> list[str]:
        """Report documents no fixture names at all — the missing-context case."""
        named = {fixture.template for fixture in FIXTURES}
        return [name for name in self.documents if name not in named]

    def findings(self, skip: set[str] = frozenset()) -> list[str]:
        """The release's defects. *skip* names fixtures whose entry is about the box.

        Only the caller may decide which those are: it needs the floor, and the floor
        is `python_requires`'. The default is the strict reading — nothing skipped.
        """
        lines = []
        for name, reason in sorted(self.capture_errors.items()):
            if name in skip:
                continue
            source = next(f.source for f in FIXTURES if f.name == name)
            lines.append(
                f"the fixture {name!r} ({source}) could not be measured: {reason} — it "
                f"no longer renders through the Jinja environment this gate watches")
        for name, template, detail in self.own_failures():
            source = next(f.source for f in FIXTURES if f.name == name)
            lines.append(
                f"the fixture {name!r} ({source}) can no longer render its own document "
                f"{template}: {detail}. The document reads a key the fixture does not "
                f"supply, and a page renders that as an empty hole with a 200 and no "
                f"error")
        for template in self.orphan_documents():
            lines.append(
                f"no fixture names the report document {template}, so what it reads is "
                f"measured nowhere — give it a fixture, or add it to STANDALONE_DOCUMENTS "
                f"with the reason it needs none (an exemption is a sentence, see EXEMPT)")
        return lines


def measure(app) -> Reading:
    reading = Reading(documents=report_documents())
    for fixture in FIXTURES:
        try:
            context, error = capture_context(app, fixture)
        except FixtureUnparseable as exc:
            # Recorded twice on purpose: once as the reason this fixture has no
            # context (which the matrix and `--show` print), and once as a file this
            # interpreter cannot parse, which `answer` weighs against the floor.
            reason = str(exc)
            reading.capture_errors[fixture.name] = reason
            reading.unparseable[fixture.name] = reason
            continue
        if context is None:
            reading.capture_errors[fixture.name] = error
        else:
            reading.contexts[fixture.name] = context
    for fixture in FIXTURES:
        if fixture.name not in reading.contexts:
            continue
        for document in reading.documents:
            reading.rendered[(fixture.name, document)] = strict_render(
                app, document, reading.contexts[fixture.name])
    return reading


def matrix_lines(reading: Reading) -> list[str]:
    """The grid: one row per fixture, one column per document, `.` rendered, `x` not."""
    width = max(len(fixture.name) for fixture in FIXTURES) + 2
    lines = ["render fixture gate: the matrix (`.` rendered, `x` a missing key)", ""]
    lines.append("    " + "fixture".ljust(width) + "  ".join(_column(document)
                                                            for document in reading.documents))
    for fixture in FIXTURES:
        if fixture.name in reading.capture_errors:
            lines.append("    " + fixture.name.ljust(width) + "(not measurable)")
            continue
        cells = ["  .  " if not reading.cell(fixture.name, document).startswith(ERROR)
                 else "  x  " for document in reading.documents]
        lines.append("    " + fixture.name.ljust(width) + "".join(cells))
    lines.append("")
    for index, document in enumerate(reading.documents, 1):
        lines.append(f"      {index}. {document}")
    return lines


def _column(document: str) -> str:
    return Path(document).stem[:5].center(5)


def _app():
    """The app the suite builds, from the config the suite builds it with."""
    sys.path.insert(0, str(ROOT))
    from app import create_app

    return create_app("app.config.TestingConfig")


def below_the_floor() -> tuple[bool, str]:
    """Whether this interpreter is older than the floor, and the floor's own text.

    Asked of `python_requires` rather than spelled out again: that module owns the
    comparison and the message, and this gate is the second caller that needs the
    answer. The import is local because `deploy/` is run as a script, with the
    checkout's root put on the path by `_app`.
    """
    sys.path.insert(0, str(ROOT))
    import python_requires

    return python_requires.below_floor(), ".".join(str(p) for p in python_requires.MINIMUM)


def answer(reading: Reading, below: bool, floor: str) -> tuple[int, list[str]]:
    """The gate's answer and the lines that carry it, decided in one place.

    A finding wins: something in the release is wrong, and the inability to look at
    one file does not excuse it. Only when the *only* thing in the way is a fixture
    file this interpreter cannot parse — below the floor — is the answer ``could not
    measure``, because then the release is not the thing that failed. Split out of
    `main` so a test asks what the gate would answer without building an app again.
    """
    problems = reading.findings(skip=set(reading.unparseable) if below else set())
    if problems:
        lines = ["render fixture gate: FAILED — a report fixture and a report document "
                 "no longer agree, and the page draws the gap as a blank:"]
        lines += [f"    - {problem}" for problem in problems]
        return EXIT_FINDING, lines
    if reading.unparseable and below:
        here = f"{sys.version_info.major}.{sys.version_info.minor}"
        lines = [f"render fixture gate: could not measure — this interpreter is {here}, "
                 f"below the {floor} floor, so the files these fixtures come from do "
                 f"not parse here and their documents are NOT checked:"]
        for name, reason in sorted(reading.unparseable.items()):
            source = next(f.source for f in FIXTURES if f.name == name)
            lines.append(f"    - the fixture {name!r} ({source}): {reason}")
        lines.append(f"    Nothing about the release is wrong; recreate the venv with a "
                     f"{floor} interpreter to have it measured. That is a property of "
                     f"this box, not of the release, so nothing is refused.")
        return EXIT_CANNOT_MEASURE, lines
    return EXIT_OK, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", metavar="DOCUMENT",
                        help="the keys each fixture supplies, and whether it renders it")
    parser.add_argument("--quiet", action="store_true", help="the verdict only")
    args = parser.parse_args(argv)

    if not FIXTURES:
        print("render fixture gate: could not measure — no fixtures are registered, so "
              "every document would look rendered and none of them is")
        return EXIT_CANNOT_MEASURE

    try:
        app = _app()
        # Inside a request context, because these are pages: `url_for`, `csrf_token`
        # and the session are Jinja globals that only exist with a request, and a
        # report page rendered without one fails for a reason that is not a key.
        with app.test_request_context("/"):
            reading = measure(app)
    except Exception as exc:  # noqa: BLE001 - a gate that cannot measure says so
        print(f"render fixture gate: could not measure — {type(exc).__name__}: {exc}")
        traceback.print_exc(limit=3)
        return EXIT_CANNOT_MEASURE

    if args.show:
        print(f"render fixture gate: {args.show}")
        for fixture in FIXTURES:
            context = reading.contexts.get(fixture.name)
            if context is None:
                print(f"    {fixture.name}: not measurable "
                      f"({reading.capture_errors.get(fixture.name)})")
                continue
            page = reading.cell(fixture.name, args.show)
            verdict = "renders" if not page.startswith(ERROR) else page[len(ERROR):]
            print(f"    {fixture.name} ({verdict}): {', '.join(sorted(context))}")
        return EXIT_OK

    if not args.quiet:
        print("\n".join(matrix_lines(reading)))
        print(f"render fixture gate: {len(FIXTURES)} fixture(s), {len(reading.documents)} "
              f"report document(s), {reading.rendered_pairs()} of "
              f"{len(reading.rendered)} pair(s) rendered strictly")

    code, lines = answer(reading, *below_the_floor())
    if lines:
        print("\n".join(lines))
    if code != EXIT_OK:
        return code

    print("render fixture gate: OK — every report document is rendered by at least one "
          "fixture under StrictUndefined, and every fixture supplies every key its own "
          "document reads, so no page can pass as an empty one")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
