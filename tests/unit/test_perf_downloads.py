"""The perf gate's own files, over HTTP, for a super admin without a shell.

`/super-admin/deploy-status` already renders the gate's *last* judgement and a bounded
tail of its history, and that page exists because a quarantined box has no symptom: the
previous release serves, the site is healthy, and the reason lives in a file on the box.
The bounded tail is the same problem one layer down — the judgement that refused a commit
can be older than the window the page reads, and the numbers an operator wants to check
are all in the file. So the page hands the files over: the history (the gate's own
evidence) and the baseline (what it compared against), resolved through the same
environment variables the page reads, so the download is the file that was judged rather
than a second guess at the path.

These tests hold four things:

1. **Nothing is invented.** A file that is absent and a file that cannot be read are two
   different answers, each reported as itself; neither is served as an empty download an
   operator would act on.
2. **Nothing is parsed.** The gate's own bytes are the evidence, so the body is the file
   byte for byte.
3. **It is super-admin only, and read-only.**
4. **It is bounded.** The history grows for the life of the box; past the cap the newest
   bytes are served, cut back to a line boundary, and the answer says so in a header.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services import deploy_status_service as status  # noqa: E402

TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "deploy_status.html"
ROUTE_SOURCE = ROOT / "app" / "routes" / "super_admin.py"


def _view(name):
    """The route's own function, decorators peeled off, so a test can call it.

    `_sa_required` and `login_required` both use `functools.wraps`, so the original is
    reachable through the `__wrapped__` chain the same way the test client reaches the
    registered view — calling it inside a request context is the only way to exercise
    the body without a live Supabase session.
    """
    from app.routes import super_admin as sa
    view = getattr(sa, name)
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


def _call(app, which):
    from flask import g
    view = _view("deploy_status_perf_file")
    with app.test_request_context(f"/super-admin/deploy-status/perf/{which}"):
        g.user_id = "a-super-admin"
        g.user_role = "super_admin"
        return view(which)


def _history(tmp_path: Path, lines: int) -> Path:
    path = tmp_path / "history.jsonl"
    path.write_bytes(b"".join(b'{"n": %d}\n' % i for i in range(lines)))
    return path


# ── what is served ───────────────────────────────────────────────────────────

class TestWhatIsServed:
    def test_a_present_history_is_served_byte_for_byte(self, tmp_path):
        path = tmp_path / "history.jsonl"
        raw = b'{"commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "verdict": "regressed"}\n'
        path.write_bytes(raw)
        got = status.perf_download("evidence", path=str(path))
        assert got["key"] == "present"
        assert got["data"] == raw, (
            "the gate's own line is the evidence; a re-serialised copy is a different "
            "document")
        assert got["content_type"] == "application/x-ndjson"
        assert got["name"] == "history.jsonl"
        assert got["truncated"] is False

    def test_the_baseline_is_json_and_named_for_it(self, tmp_path):
        path = tmp_path / "baseline.json"
        path.write_bytes(b'{"commit": "abc"}\n')
        got = status.perf_download("baseline", path=str(path))
        assert got["content_type"] == "application/json"
        assert got["name"] == "baseline.json"

    def test_an_absent_file_is_reported_not_served_empty(self, tmp_path):
        got = status.perf_download("evidence", path=str(tmp_path / "nope.jsonl"))
        assert got["key"] == "absent" and got["data"] == b""

    def test_a_file_that_cannot_be_read_is_a_different_answer(self, tmp_path):
        blocked = tmp_path / "blocked.jsonl"
        blocked.mkdir()  # a directory where a file is expected: readable as neither
        got = status.perf_download("evidence", path=str(blocked))
        assert got["key"] == "unreadable" and got["reason"], (
            "\"the gate judged nothing\" and \"this page cannot read the file\" are "
            "different states with different remedies")

    def test_the_path_comes_from_the_environment_the_page_reads(self, tmp_path, monkeypatch):
        path = tmp_path / "elsewhere.jsonl"
        path.write_bytes(b"x\n")
        monkeypatch.setenv("SCANGRADE_PERF_HISTORY_FILE", str(path))
        got = status.perf_download("evidence")
        assert got["path"] == str(path) and got["data"] == b"x\n"

    def test_the_defaults_are_the_files_the_report_reads(self):
        assert status.PERF_DOWNLOADS["evidence"][0] == status.DEFAULT_PERF_HISTORY_FILE
        assert status.PERF_DOWNLOADS["baseline"][0] == status.DEFAULT_PERF_BASELINE_FILE


# ── it is bounded, and the cut is honest ─────────────────────────────────────

class TestTheBound:
    def test_a_small_history_is_never_truncated(self, tmp_path):
        path = _history(tmp_path, 3)
        got = status.perf_download("evidence", path=str(path), limit=4096)
        assert got["truncated"] is False and got["data"] == path.read_bytes()

    def test_past_the_cap_the_newest_bytes_are_served_at_a_line_boundary(self, tmp_path):
        path = _history(tmp_path, 500)
        got = status.perf_download("evidence", path=str(path), limit=100)
        assert got["truncated"] is True
        assert got["size"] == path.stat().st_size, (
            "the answer has to say how big the file really is, or a truncated download "
            "reads as the whole history")
        assert len(got["data"]) <= 100
        assert got["data"].endswith(b"}\n"), "the tail is cut at a line boundary"
        for line in got["data"].splitlines():
            json.loads(line)  # every line is whole JSON, not half of one

    def test_the_newest_line_is_the_one_kept(self, tmp_path):
        path = _history(tmp_path, 500)
        got = status.perf_download("evidence", path=str(path), limit=100)
        assert b'{"n": 499}' in got["data"], (
            "a tail serves the end of the file; the head is the part that is already "
            "outside every read window")


# ── the route ────────────────────────────────────────────────────────────────

class TestTheRoute:
    def test_it_is_get_only_and_super_admin_only(self):
        source = ROUTE_SOURCE.read_text(encoding="utf-8")
        assert re.search(
            r'@super_bp\.route\("/deploy-status/perf/<which>"\)\s*\n@_sa_required',
            source), "the download must carry the super-admin guard, and no methods= list"
        assert "methods=" not in source.split('"/deploy-status/perf/<which>"')[1][:40]

    def test_an_anonymous_visitor_is_sent_to_the_door(self, app):
        client = app.test_client()
        for which in ("evidence", "baseline"):
            response = client.get(f"/super-admin/deploy-status/perf/{which}")
            assert response.status_code in (301, 302), response.status_code
            assert "/auth/login" in response.headers.get("Location", "")

    def test_an_absent_file_is_a_404_that_names_which_answer_it_is(self, app, tmp_path,
                                                                  monkeypatch):
        monkeypatch.setenv("SCANGRADE_PERF_HISTORY_FILE", str(tmp_path / "nope.jsonl"))
        response = _call(app, "evidence")
        assert response.status_code == 404, (
            "an empty 200 is a download an operator would read as an empty history")
        body = json.loads(response.get_data(as_text=True))
        assert body["error"] == "absent" and str(tmp_path) in body["path"]

    def test_an_unreadable_file_is_a_different_404(self, app, tmp_path, monkeypatch):
        blocked = tmp_path / "blocked.jsonl"
        blocked.mkdir()
        monkeypatch.setenv("SCANGRADE_PERF_HISTORY_FILE", str(blocked))
        response = _call(app, "evidence")
        assert response.status_code == 404
        assert json.loads(response.get_data(as_text=True))["error"] == "unreadable"

    def test_the_download_is_the_file_and_carries_its_type(self, app, tmp_path, monkeypatch):
        path = tmp_path / "history.jsonl"
        raw = b'{"n": 1}\n{"n": 2}\n'
        path.write_bytes(raw)
        monkeypatch.setenv("SCANGRADE_PERF_HISTORY_FILE", str(path))
        response = _call(app, "evidence")
        assert response.status_code == 200
        assert response.get_data() == raw
        assert response.headers["Content-Type"].startswith("application/x-ndjson")
        assert "history.jsonl" in response.headers["Content-Disposition"]
        assert response.headers["Cache-Control"] == "no-store", (
            "this is a diagnostic: a cached copy from before the refusal is the wrong "
            "answer to the question that made somebody open it")

    def test_the_baseline_route_serves_the_baseline(self, app, tmp_path, monkeypatch):
        path = tmp_path / "baseline.json"
        path.write_bytes(b'{"commit": "abc"}\n')
        monkeypatch.setenv("SCANGRADE_PERF_BASELINE_FILE", str(path))
        response = _call(app, "baseline")
        assert response.status_code == 200
        assert json.loads(response.get_data(as_text=True))["commit"] == "abc"

    def test_a_truncated_history_says_so_in_a_header(self, app, tmp_path, monkeypatch):
        monkeypatch.setattr(status, "PERF_DOWNLOAD_MAX_BYTES", 50)
        path = _history(tmp_path, 500)
        monkeypatch.setenv("SCANGRADE_PERF_HISTORY_FILE", str(path))
        response = _call(app, "evidence")
        assert response.status_code == 200
        assert response.headers.get("X-Perf-Truncated") == "true"
        assert response.headers.get("X-Perf-File-Bytes") == str(path.stat().st_size)

    def test_an_unknown_name_is_not_a_file_to_serve(self, app):
        from werkzeug.exceptions import NotFound
        with pytest.raises(NotFound):
            _call(app, "secrets")


# ── the page offers them ─────────────────────────────────────────────────────

class TestThePageOffersThem:
    def test_the_two_downloads_are_linked(self):
        html = TEMPLATE.read_text(encoding="utf-8")
        assert 'href="/super-admin/deploy-status/perf/evidence"' in html
        assert 'href="/super-admin/deploy-status/perf/baseline"' in html

    def test_the_links_are_labelled_in_both_languages(self):
        html = TEMPLATE.read_text(encoding="utf-8")
        for want in ("Unduh riwayat penilaian", "Download the judgement history",
                     "Unduh pembanding", "Download the baseline"):
            assert want in html, f"{want!r} is missing, so the toggle cannot reach it"

    def test_the_page_says_why_the_downloads_exist(self):
        html = TEMPLATE.read_text(encoding="utf-8")
        assert "tanpa shell" in html or "without a shell" in html, (
            "the links are for a box somebody cannot SSH into; saying so is how the "
            "next reader knows what they are for")
