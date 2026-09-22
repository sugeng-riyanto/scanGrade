"""The capacity status page may not say anything its files do not say.

The landing page's capacity block is held to `docs/measurements/` by
`test_landing_claims.py`. This is the same contract for `/capacity`, with one
addition that is the whole point of the page: **adding a measurement adds a row**
— no code change, no figure to edit — so a school reads the numbers that were
actually taken rather than the numbers somebody remembered to update.

Three things are checked here and nothing else:

1. every figure on the page equals the figure in the artifact it names;
2. the page keeps working, honestly, when the evidence is missing (an empty
   directory, a machine with no deploy-gate history) — a page that 500s is a page
   nobody can check, and a page that invents a date is worse;
3. the evidence route hands over exactly the committed files, and nothing else.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services import capacity_service as capacity  # noqa: E402

MEASUREMENTS = ROOT / "docs" / "measurements"
LANDING = ROOT / "app" / "templates" / "landing.html"
PAGE = ROOT / "app" / "templates" / "public" / "capacity.html"


@pytest.fixture()
def read(monkeypatch):
    """Point the reader at a fixture directory, for tests about the report itself."""

    def point(directory: Path, state: Path | None = None) -> Path:
        monkeypatch.setenv("SCANGRADE_MEASUREMENTS_DIR", str(directory))
        if state is not None:
            monkeypatch.setenv("SCANGRADE_DEPLOY_STATE_DIR", str(state))
        capacity.invalidate()
        return directory

    yield point
    capacity.invalidate()


@pytest.fixture()
def client(app, read):
    """A client whose capacity reader is pointed at a fixture directory."""

    def build(directory: Path | None = None, state: Path | None = None):
        read(directory or MEASUREMENTS, state)
        return app.test_client()

    return build


def page_text(client):
    response = client.get("/capacity")
    assert response.status_code == 200, response.status_code
    return response.get_data(as_text=True)


def a_measurement(students: int, p50: float, p95: float, errors: float = 0.0,
                  tool: str = "loadtest_concurrent.py", measured_at: str = ""):
    """A minimal artifact of the shape both harnesses really write."""
    payload = {
        "base": "https://scangrade.web.id",
        "duration_s": 60.0,
        "error_rate_pct": errors,
        "identity_checked": students,
        "identity_ok": students,
        "identity_wrong": {},
        "latency_ms": {"p50": p50, "p95": p95, "p99": p95 * 2},
        "logins_failed": 0,
        "logins_ok": students,
        "per_endpoint": {
            "GET /student/dashboard": {"n": 10, "p50": p50, "p95": p95, "p99": p95},
            "GET /student/exams": {"n": 10, "p50": p50 * 2, "p95": p95, "p99": p95},
        },
        "rate_limited_429": 0,
        "requests_total": 100,
        "server_errors_5xx": 0,
        "tool": "locust" if tool == "locust" else "",
    }
    if measured_at:
        payload["measured_at"] = measured_at
    return payload


# ── 1. every figure comes from the file it names ─────────────────────────────

class TestThePageMatchesItsEvidence:

    def test_every_row_on_the_page_is_the_row_in_the_artifacts(self, client):
        body = page_text(client(MEASUREMENTS))
        for rung in capacity.report()["rungs"]:
            for text in (rung["p50_text"], rung["p95_text"], rung["error_text"]):
                assert text in body, (
                    f"the {rung['students']}-student row does not carry {text!r}, "
                    "which is what its artifacts measured")
            for name in rung["artifacts"]:
                assert name in body, (
                    f"{name} backs the {rung['students']}-student row but the page "
                    "does not show the file a reader would check it against")

    def test_the_two_public_pages_agree_about_the_same_files(self, client):
        """The marketing block and the status page read the same artifacts.

        They are allowed to differ in *shape* — the landing page prints a table,
        this one prints a table plus every run — but not in figures. A number that
        differs between two pages backed by one file is the original defect.
        """
        spec = importlib.util.spec_from_file_location(
            "landing_claims_for_capacity", ROOT / "tests" / "unit" / "test_landing_claims.py")
        claims = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(claims)
        client(MEASUREMENTS)
        assert claims._published_rows() == {k: v for k, v in capacity.published_rungs().items()
                                            if k in claims._published_rows()}

    def test_the_withdrawn_figures_are_not_here_either(self, client):
        body = page_text(client(MEASUREMENTS))
        for fig in ("46,698", "46.698", "74,923", "74.923", "1.000 concurrent"):
            assert fig not in body, (
                f"{fig} is back, on a page whose whole purpose is that no figure "
                "appears without an artifact")


# ── 2. a new measurement is the only thing an update needs ──────────────────

class TestANewArtifactBecomesARow:

    def test_a_dropped_in_artifact_appears_without_any_code_change(self, client, tmp_path):
        """The page is the report. There is no number in the template to edit."""
        (tmp_path / "rung-200.json").write_text(
            json.dumps(a_measurement(200, p50=1234.0, p95=5678.0)), encoding="utf-8")
        body = page_text(client(tmp_path))
        assert "200" in body
        assert capacity.fmt_ms_range([1234.0, 2468.0]) in body
        assert capacity.fmt_p95(5678.0) in body
        assert "rung-200.json" in body

    def test_a_second_harness_on_a_rung_moves_the_published_bound_to_the_worse_one(
            self, client, read, tmp_path):
        """Two runs of the same load do not agree; the page shows the worse one.

        Publishing the better of two measurements advertises a number the box has
        not been shown to hold, and the deploy gate holds this page to 2x of what
        it says — so the flattering choice is the one that manufactures a failed
        release later.
        """
        (tmp_path / "rung-100.json").write_text(
            json.dumps(a_measurement(100, p50=500.0, p95=2000.0)), encoding="utf-8")
        (tmp_path / "locust-100.json").write_text(
            json.dumps(a_measurement(100, p50=900.0, p95=6000.0, tool="locust")),
            encoding="utf-8")
        read(tmp_path)
        body = page_text(client(tmp_path))
        row = capacity.report()["rungs"][0]
        assert row["p95_text"] == capacity.fmt_p95(6000.0), row

        # The published row, isolated: each run's own card below it is *supposed*
        # to show that run's figures, so the one place the flattering number may
        # not appear is the bound a school reads first.
        table = body.split('id="capacity-curve"', 1)[1].split("</table>", 1)[0]
        assert capacity.fmt_p95(6000.0) in table
        assert capacity.fmt_p95(2000.0) not in table, (
            "the published row took the better of the two measurements of the same load")

    def test_a_file_that_is_not_a_measurement_is_listed_but_not_a_row(self, client, read, tmp_path):
        (tmp_path / "rung-100.json").write_text(
            json.dumps(a_measurement(100, 500.0, 2000.0)), encoding="utf-8")
        (tmp_path / "README.md").write_text("# notes\n", encoding="utf-8")
        (tmp_path / "rung-100-vps-samples.txt").write_text("load average 3.1\n", encoding="utf-8")
        read(tmp_path)
        report = capacity.build()
        assert [m["name"] for m in report["measurements"]] == ["rung-100.json"]
        assert {s["name"] for s in report["supporting"]} == {"README.md",
                                                           "rung-100-vps-samples.txt"}
        body = page_text(client(tmp_path))
        for name in ("README.md", "rung-100-vps-samples.txt"):
            assert name in body, (
                f"{name} is in the directory and the page does not mention it: the "
                "set of files a reader can check has to be the set that is there")


# ── 3. the date is the artifact's, or the repository's, or unknown ──────────

class TestTheDateIsNeverInvented:

    def test_an_artifact_that_carries_its_own_date_is_used_as_is(self, read, tmp_path):
        (tmp_path / "rung-100.json").write_text(
            json.dumps(a_measurement(100, 500.0, 2000.0, measured_at="2026-09-14T22:21:44")),
            encoding="utf-8")
        read(tmp_path)
        first = capacity.build()["measurements"][0]
        assert (first["at"], first["at_source"]) == ("2026-09-14", "artifact")

    def test_an_artifact_without_one_falls_back_to_its_commit_date(self, read):
        """`rung-050.json` predates the field, so it is dated by `git log`."""
        read(MEASUREMENTS)
        first = next(m for m in capacity.build()["measurements"] if m["name"] == "rung-050.json")
        assert first["at_source"] == "git", first
        assert re.match(r"\d{4}-\d{2}-\d{2}", first["at"]), first

    def test_the_file_mtime_is_never_the_date(self, client, read, tmp_path):
        """A fresh clone stamps every file with today, so a mtime is not evidence.

        An artifact outside the repository has no commit date either, which is the
        case that used to be answered by the mtime: the page must say the date is
        unknown instead.
        """
        (tmp_path / "rung-100.json").write_text(
            json.dumps(a_measurement(100, 500.0, 2000.0)), encoding="utf-8")
        read(tmp_path)
        first = capacity.build()["measurements"][0]
        assert first["at_source"] == "unknown", first
        assert first["at"] == ""
        body = page_text(client(tmp_path))
        assert "date not recorded" in body, (
            "an undated measurement has to say so rather than borrow today's date")


# ── 4. the recommendation is derived from a row, and stated once ────────────

class TestTheRecommendationIsARow:

    def test_it_is_the_landing_page_s_limit_and_a_measured_row(self):
        body = LANDING.read_text(encoding="utf-8")
        stated = re.search(r"~(\d+) concurrent students per exam session", body)
        assert stated, "the landing page no longer states a comfortable limit"
        assert int(stated.group(1)) == capacity.COMFORTABLE_STUDENTS, (
            f"the landing page recommends {stated.group(1)} and the status page "
            f"recommends {capacity.COMFORTABLE_STUDENTS}: one number, two pages")

        row = capacity.report()["comfortable_row"]
        assert row, (f"{capacity.COMFORTABLE_STUDENTS} students is recommended but no "
                     "artifact measured that rung")
        assert row["error_rate_pct"] == 0, (
            "the recommended rung is not a 0% error row any more; re-measure before "
            "recommending it")

    def test_the_configuration_is_stated_on_both_pages(self, client):
        body = page_text(client(MEASUREMENTS))
        machine = capacity.MACHINE
        assert f"{machine['vcpu']} vCPU" in body
        assert f"{machine['memory_mb']} MB" in body
        assert f"{machine['workers']} {machine['worker_class']}" in body
        assert f"{machine['vcpu']} vCPU" in LANDING.read_text(encoding="utf-8"), (
            "the two pages must not disagree about what the numbers were measured on")


# ── 5. the evidence route serves the committed files and nothing else ───────

class TestTheEvidenceIsInspectable:

    @pytest.mark.parametrize("name", ["rung-050.json", "rung-500-endurance.txt",
                                      "locust-050.json", "README.md"])
    def test_a_listed_file_is_served_verbatim_as_text(self, client, name):
        response = client(MEASUREMENTS).get(f"/capacity/evidence/{name}")
        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("text/plain"), (
            "an artifact is a document to read, not to execute in a visitor's browser")
        assert response.get_data() == (MEASUREMENTS / name).read_bytes(), (
            f"the bytes served for {name} are not the bytes in the repository")

    @pytest.mark.parametrize("name", ["../../.env", "..%2f..%2f.env", "app/config.py",
                                      "nope.json", "rung-999.json", ""])
    def test_anything_not_in_the_report_is_a_404(self, client, name):
        """Through the URL. A hostile name mostly dies at Werkzeug before this.

        Which is exactly why the boundary is also tested below, against the
        function: a framework that normalises `../` out of the path cannot be the
        only thing standing between a visitor and `.env`, and a test that only
        goes through the URL would keep passing if this route's own guard were
        deleted. It did, until the mutation run said so.
        """
        response = client(MEASUREMENTS).get(f"/capacity/evidence/{name}")
        assert response.status_code == 404, (
            f"{name!r} was served. The whitelist is the report's own file list, so a "
            "name that is not a committed measurement cannot be requested at all")

    def test_the_boundary_holds_when_the_url_layer_is_not_in_the_way(self, read, tmp_path):
        """The route's own two locks, called directly with a hostile name.

        A measurement-shaped file sits one directory above the evidence directory,
        so a reader that concatenates the name onto that directory will serve it.
        """
        measurements = tmp_path / "measurements"
        measurements.mkdir()
        (measurements / "rung-100.json").write_text(
            json.dumps(a_measurement(100, 500.0, 2000.0)), encoding="utf-8")
        outside = tmp_path / "rung-777.json"
        outside.write_text(json.dumps(a_measurement(777, 500.0, 2000.0)), encoding="utf-8")
        read(measurements)

        served = capacity.artifact_path("rung-100.json")
        assert served == measurements / "rung-100.json", served
        assert capacity.artifact_path("../rung-777.json") is None, (
            "a relative traversal served a file from outside the evidence directory")
        assert capacity.artifact_path("rung-777.json") is None, (
            "a file that is not in the report was served")
        assert capacity.artifact_path("../../.env") is None


# ── 6. missing evidence is said out loud, not rendered as zeros ─────────────

class TestMissingEvidenceIsHonest:

    def test_an_empty_directory_renders_and_says_so(self, client, tmp_path):
        body = page_text(client(tmp_path))
        assert "No readable measurement file on this server yet" in body
        assert capacity.build()["rungs"] == []

    def test_an_unreadable_directory_does_not_break_the_page(self, client, tmp_path):
        body = page_text(client(tmp_path / "does-not-exist"))
        assert "No readable measurement file on this server yet" in body

    def test_a_machine_with_no_gate_history_says_so(self, client, tmp_path):
        body = page_text(client(MEASUREMENTS, state=tmp_path))
        assert "No gate record on this server yet" in body

    def test_the_gates_own_measurements_are_shown_when_they_exist(self, client, tmp_path):
        perf = tmp_path / "perf"
        perf.mkdir()
        (perf / "history.jsonl").write_text(json.dumps({
            "commit": "abcdef1234567890",
            "measured_at": "2026-09-15T10:00:00+00:00",
            "shape": {"sessions": 20, "duration": 20},
            "latency": {"p50_ms": 374.2, "p95_ms": 903.7, "endpoints": 9, "samples": 180},
            "error_pct": 0.0, "server_errors_5xx": 0, "requests_total": 512,
            "throughput_rps": 25.6,
        }) + "\n", encoding="utf-8")
        claims = tmp_path / "claims"
        claims.mkdir()
        (claims / "history.jsonl").write_text(json.dumps({
            "at": "2026-09-15T10:05:00+00:00",
            "verdict": "ok",
            "reason": "within 2x of what the page advertises",
            "advertised": {"students": 50, "p50_ms": 962, "p95_ms": 3700, "error_pct": 0},
            "sessions_probed": 30,
            "base": "https://scangrade.web.id",
            "measured": {"error_rate_pct": 0.0, "latency_ms": {"p50": 380.0, "p95": 910.0}},
            "divergences": [],
        }) + "\n", encoding="utf-8")

        body = page_text(client(MEASUREMENTS, state=tmp_path))
        assert "abcdef1" in body, "the last release the perf gate measured is not shown"
        assert "374 ms" in body and "904 ms" in body
        assert "Claims gate" in body and "within 2x of what the page advertises" in body
        assert "No gate record on this server yet" not in body

    def test_a_malformed_history_line_does_not_break_the_page(self, client, tmp_path):
        perf = tmp_path / "perf"
        perf.mkdir()
        (perf / "history.jsonl").write_text("{not json\n", encoding="utf-8")
        body = page_text(client(MEASUREMENTS, state=tmp_path))
        assert "No gate record on this server yet" in body

    def test_an_unreachable_state_directory_is_not_reported_as_no_record(
            self, client, tmp_path):
        """The distinction an operator needs, and the one `is_file()` destroys.

        On the VPS this was live: `/var/lib/scangrade-deploy` was root:root 0750
        with no group, so the app could not traverse it, `claims/history.jsonl`
        answered "not a file", and this page said *no release has been through the
        gates* — a claim about the box, and false: both gates were recording on
        every release.

        The path is stood in for by a *directory* where the history file belongs.
        Opening it raises an OSError that is not FileNotFoundError on every
        platform (IsADirectoryError on POSIX, PermissionError on Windows), which is
        the property the code under test has to branch on — a file-shaped parent, by
        contrast, reads as ENOENT on Windows and would pass vacuously.
        """
        state = tmp_path / "state"
        (state / "claims" / "history.jsonl").mkdir(parents=True)

        body = page_text(client(MEASUREMENTS, state=state))
        assert "No gate record on this server yet" not in body, (
            "an unreadable state directory is still being reported as a box whose "
            "gates never ran — the reassuring blank this page is not allowed to print")
        assert "cannot be read by the application" in body
        assert "install-auto-deploy.sh" in body, (
            "the empty state names no remedy, so the reader is left with the symptom")
        assert "claims:" in body, (
            "the captured error is not shown, so the operator cannot tell which of "
            "the two histories it was")

    def test_the_empty_state_is_still_the_old_sentence_when_nothing_is_there(
            self, client, tmp_path):
        """The other half of the pair: an empty directory is not an error."""
        empty = tmp_path / "empty-state"
        empty.mkdir()
        body = page_text(client(MEASUREMENTS, state=empty))
        assert "No gate record on this server yet" in body
        assert "cannot be read by the application" not in body


# ── 7. the page is a page, not an Alpine-only shell ─────────────────────────

class TestThePageIsReadableWithoutJavaScript:

    def test_the_figures_are_in_the_html_not_only_in_bindings(self, client):
        """A school may open this before any script runs (or with it blocked).

        The numbers are server-rendered; only the sentences are bound, so what is
        measured survives a page whose JavaScript never executed. Checked by
        removing every x-text binding and looking again.
        """
        body = page_text(client(MEASUREMENTS))
        rendered = re.sub(r'x-text="[^"]*"', "", body)
        for rung in capacity.report()["rungs"]:
            assert rung["p50_text"] in rendered, (
                f"{rung['p50_text']} appears only inside a binding")

    def test_both_sides_of_the_authenticated_split_render_it(self):
        text = PAGE.read_text(encoding="utf-8")
        assert "{% block content %}" in text and "{% block content_noauth %}" in text, (
            "the page has to render for a signed-in teacher and for a stranger")

    def test_a_stranger_can_actually_switch_the_language(self, client):
        """Bilingual copy with no way to switch it is copy for one reader only.

        The anonymous path renders no navbar, so base.html's toggle is not on the
        page at all — which is why the landing page carries its own button, and why
        this one has to as well. Only the page's own button counts here: the client
        has no session, so nothing else on the rendered page can call setLang.
        """
        body = page_text(client(MEASUREMENTS))
        assert '@click="setLang(' in body, (
            "a stranger cannot switch this page to Indonesian: it renders no language "
            "control of its own, and base.html's lives in the chrome they never see")
