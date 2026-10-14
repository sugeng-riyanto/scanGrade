"""Guards for `deploy/render_fixture_gate.py`, and for the two lags it found.

The gate renders every report document from every report fixture with
`StrictUndefined`, so a fixture that has fallen behind its reader is a refusal
instead of a blank on the sheet. These tests are the gate's own contract: that the
undefined policy really is strict (with the default `Undefined` the whole gate is a
page of empty strings that all pass), that the context is the fixture's own call
rather than a copy, that a lag *is* a finding and a cross pair is not, and that the
deploy's theme gate actually runs it.

It is also a regression target. The tree at the time of writing carried two real
lags — `print/report_card.html` and `student/result_detail_pdf.html` both read
`submission.submitted_at`, which `report_card_service` selects and both fixtures
omitted, so the sheets printed `-` where the submission time belongs and nothing
said so. Both are fixed in the fixtures that render them
(`tests/unit/test_principal_signature.py`, `tests/unit/test_subject_label.py`), and
`test_this_tree_is_green` is what keeps the next one from landing quietly.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = ROOT / "deploy" / "render_fixture_gate.py"
THEME_GATE = ROOT / "deploy" / "theme_gate.sh"
TEMPLATES = ROOT / "app" / "templates"


def gate():
    """Import `deploy/render_fixture_gate.py` without making `deploy/` a package."""
    if not GATE_PATH.exists():
        pytest.fail("deploy/render_fixture_gate.py does not exist — the report "
                    "documents have no render check")
    spec = importlib.util.spec_from_file_location("sg_render_fixture_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def reading(app):
    """The real matrix, measured once: 8 fixtures against every report document."""
    with app.test_request_context("/"):
        return gate().measure(app)


def theme_gate_code() -> str:
    """`deploy/theme_gate.sh` with its comment lines dropped.

    Whole-line comments only, so a `#` inside a string is not mistaken for prose —
    and so a guard about the call cannot be satisfied by the paragraph explaining it.
    """
    return "\n".join(line for line in THEME_GATE.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))


# ── the registry ─────────────────────────────────────────────────────────────

class TestTheRegistryNamesRealThings:
    def test_every_fixture_names_a_document_that_exists(self):
        for fixture in gate().FIXTURES:
            assert (TEMPLATES / fixture.template).is_file(), (
                f"{fixture.name} renders {fixture.template}, which is not in the tree")

    def test_every_fixture_names_the_test_file_it_came_from(self):
        for fixture in gate().FIXTURES:
            assert (ROOT / fixture.source).is_file(), (
                f"{fixture.name} claims to come from {fixture.source}, which is gone — "
                f"the context it measures is nobody's")

    def test_fixture_names_are_unique(self):
        names = [fixture.name for fixture in gate().FIXTURES]
        assert len(names) == len(set(names)), f"two fixtures share a name: {names}"

    def test_every_fixture_points_at_one_of_the_columns(self):
        columns = set(gate().report_documents())
        for fixture in gate().FIXTURES:
            assert fixture.template in columns, (
                f"{fixture.name} renders {fixture.template}, which is not a column of "
                f"the matrix, so its own document is judged by nothing")

    def test_the_column_list_covers_the_whole_sheet_family(self):
        """The `print/` directory is the sheet family: every document in it is a column."""
        columns = set(gate().report_documents())
        family = {path.relative_to(TEMPLATES).as_posix()
                  for path in (TEMPLATES / "print").rglob("*.html")}
        assert family, "there is no print/ family to cover"
        assert family <= columns, f"sheets outside the matrix: {sorted(family - columns)}"

    def test_every_column_is_a_document_a_fixture_renders(self):
        named = {fixture.template for fixture in gate().FIXTURES}
        unrendered = [name for name in gate().report_documents() if name not in named]
        assert not unrendered, (
            f"these report documents are columns no fixture renders: {unrendered} — the "
            f"gate would have nothing to render them with")

    def test_an_exemption_names_a_real_file_and_gives_a_reason(self):
        for name, reason in gate().EXEMPT.items():
            assert (TEMPLATES / name).is_file(), (
                f"{name} is exempted from the matrix and is not in the tree — the "
                f"exemption is protecting nothing")
            assert len(reason.strip()) > 40, (
                f"{name} is exempted with {reason!r}, which does not say why")


# ── the strict reading ───────────────────────────────────────────────────────

class TestStrictUndefinedIsThePoint:
    def test_a_missing_key_is_a_reading_and_not_an_empty_string(self, app):
        page = gate().strict_render(app, "print/report_card.html", {})
        assert page.startswith(gate().ERROR), (
            "an empty context rendered a report document — the gate is not strict, and "
            "every fixture in the suite would pass as a rendered page")
        key = gate().missing_key(TypeError(
            f"'{page[len(gate().ERROR):].strip(chr(39))}' is undefined"))
        assert key or page[len(gate().ERROR):], "the failure does not name what is missing"

    def test_no_column_renders_from_a_context_of_nothing(self, app):
        """The non-vacuity of the matrix, asked of every column at once."""
        rendered = [name for name in gate().report_documents()
                    if not gate().strict_render(app, name, {}).startswith(gate().ERROR)]
        assert not rendered, (
            f"these documents render with no context at all: {rendered} — a fixture that "
            f"supplies nothing would be measured as a success")

    def test_the_undefined_policy_is_put_back_afterwards(self, app):
        before = app.jinja_env.undefined
        gate().strict_render(app, "print/report_card.html", {})
        assert app.jinja_env.undefined is before, (
            "the strict policy leaked out of the render — every later page in the run "
            "would be judged by it")

    def test_a_context_that_does_supply_the_keys_still_renders(self, app, reading):
        context = reading.contexts["print_reports.report_card"]
        page = gate().strict_render(app, "print/report_card.html", context)
        assert not page.startswith(gate().ERROR), page


# ── the context is the fixture's own call ────────────────────────────────────

class TestTheContextIsCapturedNotCopied:
    def test_the_context_comes_from_the_call_the_fixture_makes(self, app):
        module = gate()

        def build(app_):
            app_.jinja_env.get_template("print/report_card.html").render(
                cards=[{"submission": {}}], printed_on="now", show_key=True)
            return ""

        fixture = module.Fixture("stub", "print/report_card.html", build, "tests/unit/x.py")
        context, error = module.capture_context(app, fixture)
        assert error == ""
        assert context == {"cards": [{"submission": {}}], "printed_on": "now",
                           "show_key": True}

    def test_a_builder_that_does_not_render_is_not_measured(self, app):
        module = gate()
        fixture = module.Fixture("stub", "print/report_card.html", lambda app_: "page",
                                 "tests/unit/x.py")
        context, error = module.capture_context(app, fixture)
        assert context is None, (
            "a builder that never rendered anything was measured as an empty context")
        assert "jinja_env.get_template" in error, error

    def test_a_builder_that_raises_is_not_measured(self, app):
        module = gate()

        def build(app_):
            raise RuntimeError("the fixture's own data is gone")

        context, error = module.capture_context(
            app, module.Fixture("stub", "print/report_card.html", build, "tests/unit/x.py"))
        assert context is None and "RuntimeError" in error

    def test_the_real_fixtures_captured_a_non_empty_context_each(self, reading):
        assert set(reading.contexts) == {fixture.name for fixture in gate().FIXTURES}, (
            f"fixtures that captured nothing: "
            f"{sorted(reading.capture_errors)} — {reading.capture_errors}")
        for name, context in reading.contexts.items():
            assert context, f"{name} captured an empty context"


# ── what is a finding, and what is only a report ─────────────────────────────

def _reading(module, **overrides):
    reading = module.Reading(documents=overrides.pop("documents",
                                                     ["print/report_card.html"]))
    reading.contexts = overrides.pop("contexts", {"print_reports.report_card": {"a": 1}})
    reading.capture_errors = overrides.pop("capture_errors", {})
    reading.rendered = overrides.pop("rendered", {})
    return reading


class TestTheReadingOfTheMatrix:
    def test_a_fixture_that_lags_its_reader_is_a_finding(self):
        module = gate()
        fixture = module.FIXTURES[0]
        reading = _reading(module, rendered={
            (fixture.name, fixture.template): module.ERROR + "'submitted_at' is undefined"})
        problems = reading.findings()
        assert any("can no longer render its own document" in p for p in problems), (
            "a fixture whose own document raises is not a finding")
        assert any(fixture.source in p for p in problems), (
            "the finding does not say which test file to open")

    def test_a_document_no_fixture_renders_is_a_finding(self):
        module = gate()
        fixture = module.FIXTURES[0]
        reading = _reading(module, documents=["print/report_card.html", "print/new.html"],
                           rendered={(fixture.name, fixture.template): "<html>"})
        problems = reading.findings()
        assert any("print/new.html" in p for p in problems), problems

    def test_a_capture_failure_is_a_finding_naming_the_file(self):
        module = gate()
        reading = _reading(module, capture_errors={module.FIXTURES[0].name: "ImportError: x"})
        problems = reading.findings()
        assert any(module.FIXTURES[0].source in p and "could not be measured" in p
                   for p in problems), problems

    def test_cross_pairs_are_reported_and_not_judged(self):
        """Two documents do not share a context: a miss between them is the matrix."""
        module = gate()
        rendered = {}
        for fixture in module.FIXTURES:
            for document in module.report_documents():
                rendered[(fixture.name, document)] = (
                    "<html>" if document == fixture.template
                    else module.ERROR + "'something_else' is undefined")
        reading = module.Reading(documents=module.report_documents(), rendered=rendered)
        assert reading.own_failures() == []
        assert reading.findings() == [], (
            "a cross pair was judged as a defect, so the gate would refuse every "
            "release for a document that was never meant to render from that fixture")

    def test_an_empty_document_list_cannot_pass_as_a_clean_matrix(self):
        module = gate()
        reading = module.Reading(documents=[])
        assert reading.rendered_pairs() == 0
        assert reading.own_failures() == []


# ── this tree, today ─────────────────────────────────────────────────────────

class TestThisTreeIsGreen:
    def test_every_fixture_renders_its_own_document(self, reading):
        for fixture in gate().FIXTURES:
            page = reading.cell(fixture.name, fixture.template)
            assert not page.startswith(gate().ERROR), (
                f"{fixture.name} ({fixture.source}) can no longer render "
                f"{fixture.template}: {page[len(gate().ERROR):]}")

    def test_this_tree_is_green(self, reading):
        assert reading.findings() == [], (
            "the report fixtures and the report documents disagree; run "
            "`python deploy/render_fixture_gate.py` for the matrix")

    def test_the_matrix_rendered_every_fixtures_own_document(self, reading):
        expected = len(gate().FIXTURES)
        assert reading.rendered_pairs() >= expected, (
            f"{reading.rendered_pairs()} pair(s) rendered strictly, and there are "
            f"{expected} fixtures — their own documents must render at least")


# ── the exit contract, and the wiring ────────────────────────────────────────

class TestTheExitContract:
    def test_a_lagging_fixture_exits_with_a_finding(self, app, monkeypatch):
        module = gate()
        fixture = module.FIXTURES[0]
        reading = _reading(module, rendered={
            (fixture.name, fixture.template): module.ERROR + "'x' is undefined"})
        monkeypatch.setattr(module, "_app", lambda: app)
        monkeypatch.setattr(module, "measure", lambda app_: reading)
        assert module.main(["--quiet"]) == module.EXIT_FINDING

    def test_a_cannot_measure_exits_two_and_names_nothing(self, monkeypatch):
        module = gate()
        monkeypatch.setattr(module, "FIXTURES", ())
        assert module.main(["--quiet"]) == module.EXIT_CANNOT_MEASURE

    def test_the_codes_are_the_repositorys_own(self):
        module = gate()
        assert (module.EXIT_OK, module.EXIT_FINDING, module.EXIT_CANNOT_MEASURE) == (0, 1, 2)


class TestTheThemeGateRunsIt:
    def test_it_is_run_by_an_executed_line(self):
        code = theme_gate_code()
        assert "deploy/render_fixture_gate.py" in code or "render_fixture_gate.py" in code, (
            "the theme gate no longer runs the render fixture gate, so a fixture can lag "
            "its reader on a release that passes")
        executed = [line for line in code.splitlines()
                    if "render_fixture_gate.py" in line and "$(" in line]
        assert executed, "the gate is named in the theme gate but never called"

    def test_it_is_part_of_the_armament(self):
        """Deleting the gate must refuse a release, not quietly stop checking it."""
        armament = re.search(r"^ARMAMENT=\"(.*)\"$", theme_gate_code(), re.M)
        assert armament, "the theme gate no longer declares its armament"
        assert "render_fixture_gate.py" in armament.group(1), (
            "the render fixture gate is not in the armament list, so a release could "
            "delete it and pass")
        assert "test_render_fixture_gate.py" in theme_gate_code().split("ARMAMENT=")[0], (
            "this guard file is not in the theme gate's TESTS, so the gate's own contract "
            "is not checked by the release that runs it")

    def test_a_finding_exits_one_and_the_verdict_is_relayed(self):
        code = theme_gate_code()
        assert 'FIXTURES_RC" -eq 1' in code, "the gate's finding does not refuse a release"
        assert "theme gate: FAILED — a report fixture and a report document no longer agree" \
            in code

    def test_the_success_line_only_claims_a_measurement_taken(self):
        code = theme_gate_code()
        assert "${FIXTURES_CLAIM}" in code, (
            "the success line claims the report documents were rendered without asking "
            "whether they were")
        setter = re.search(r'if \[ "\$FIXTURES_RC" -eq 0 \]; then\n\s*FIXTURES_CLAIM=', code)
        assert setter, "the claim is not conditional on the gate having measured anything"
