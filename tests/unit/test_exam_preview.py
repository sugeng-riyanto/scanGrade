"""The teacher's preview of a paper: the pupil's page, and nothing but the page.

The requirement this file exists for is a *negative*: opening the preview must
create no attempt, enrol nobody, and start no anti-cheat. A negative is only worth
asserting against a surface that records what happened, so the route is driven here
with a supabase that keeps every call, and the template is **rendered** rather than
grepped — "no anti-cheat ran" is a property of the page that comes out, not of the
source it came from.

Three things are pinned:

* **The paper door writes nothing.** No `submissions` row, no `exam_target_student`,
  no update of any kind. The route reads and renders; the fake records writes and
  the test asserts there were none. It also asserts the door does not name the three
  things the *pupil's* door does on the way in — `open_sitting`, `issue_code`,
  `ensure_page_thumbs` — because a copy of that flow is how a preview becomes a
  sitting six months from now.
* **The preview page arms nothing.** The rendered HTML carries the preview marker
  and the banner, and does *not* carry `armAntiCheat`, the tab watch, the heartbeat
  interval, the countdown interval, or the `sync-draft` beacon. The same template
  rendered without `preview` still carries all of them, so these assertions fail if
  the gates are ever "cleaned up".
* **The frames are the sizes they claim.** A laptop, a tablet and a phone, with the
  tablet and the phone offered in both orientations, exactly as the page labels them.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routes import teacher as t
from app.utils.exam_access import EXAM_OK

ROOT = Path(__file__).resolve().parents[2]
FORM = ROOT / "app" / "templates" / "teacher" / "exam_preview.html"
EXAMS = ROOT / "app" / "templates" / "teacher" / "exams.html"
PAPER = ROOT / "app" / "templates" / "student" / "take_exam.html"


# ── a supabase that remembers ────────────────────────────────────────────────

class _RecordingSb:
    """Answers reads and records every call, so "it wrote nothing" is checkable.

    The write methods return the object rather than raising: a route that *did*
    write would then finish, and the assertion about the recording is the failure —
    which reads as "the preview wrote an attempt" instead of "the preview 500ed".
    """

    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []
        self.reads = []
        self.writes = []

    def table(self, name):
        self.reads.append(("table", name))
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    def single(self):
        return self

    def insert(self, *a, **k):
        self.writes.append(("insert", a))
        return self

    def update(self, *a, **k):
        self.writes.append(("update", a))
        return self

    def upsert(self, *a, **k):
        self.writes.append(("upsert", a))
        return self

    def delete(self, *a, **k):
        self.writes.append(("delete", a))
        return self

    def rpc(self, *a, **k):
        self.writes.append(("rpc", a))
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)

    #: The tables this run touched at all, reads included.
    @property
    def tables(self):
        return {c[1] for c in self.reads if c[0] == "table"}


EXAM = {
    "id": "e1", "title": "Fisika Akhir", "teacher_id": "t-1", "school_id": "sch-1",
    "total_questions": 2, "duration_minutes": 60, "status": "active", "is_published": True,
    "question_types": {"0": "choice", "1": "essay"},
    "answer_key": {"0": "B"},
    "question_audio": {}, "question_pages": {}, "question_weights": {},
    "pdf_page_urls": [], "class_ids": ["c1"], "anti_cheat_enabled": True,
}


def _raw(view):
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


@pytest.fixture
def paper_door(monkeypatch):
    """Run the preview's paper door with everything around it recorded.

    Returns ``(captured, sb)``: the kwargs the route handed `render_template`, and
    the recording supabase.
    """
    sb = _RecordingSb(EXAM)
    captured = {}

    def _render(template, **kwargs):
        captured["template"] = template
        captured.update(kwargs)
        return "RENDERED"

    monkeypatch.setattr(t, "get_supabase", lambda: sb)
    monkeypatch.setattr(t, "managed_exam",
                        lambda *a, **k: (dict(EXAM), EXAM_OK))
    monkeypatch.setattr(t, "render_template", _render)
    # A preview signs media URLs for this teacher; the signing itself is not what
    # this file is about, and it needs bucket configuration to run.
    monkeypatch.setattr(t.exam_media, "with_media_urls", lambda rows, **k: rows)

    import importlib
    return importlib, _raw(t.exam_preview_paper), captured, sb


def _call_paper(app, door):
    _importlib, view, _captured, _sb = door
    with app.test_request_context("/teacher/exams/e1/preview/paper"):
        from flask import g
        g.user_id, g.user_name, g.user_role = "t-1", "Guru Uji", "guru"
        g.user_email, g.tz_offset, g.show = "g@example.test", 7, {}
        g.user_school_id, g.user_class_id = "sch-1", None
        return view("e1")


# ── 1. the paper door creates nothing ────────────────────────────────────────

class TestThePreviewCreatesNothing:
    def test_the_paper_door_writes_nothing_at_all(self, app, paper_door):
        importlib, view, captured, sb = paper_door
        with app.test_request_context("/teacher/exams/e1/preview/paper"):
            from flask import g
            g.user_id, g.user_name, g.user_role = "t-1", "Guru Uji", "guru"
            g.user_email, g.tz_offset, g.show = "g@example.test", 7, {}
            g.user_school_id, g.user_class_id = "sch-1", None
            view("e1")
        assert sb.writes == [], (
            "the preview wrote to the database; it must read and render only: "
            f"{sb.writes}")

    def test_no_sitting_row_is_even_looked_for(self, app, paper_door):
        """`submissions` is the table an attempt lives in. The preview has no
        business asking about it — the pupil's door does, and that is the door that
        makes a sitting."""
        importlib, view, captured, sb = paper_door
        _call_paper(app, paper_door)
        assert "submissions" not in sb.tables, (
            f"the preview touched submissions: {sorted(sb.tables)}")

    def test_the_preview_path_does_not_make_a_sitting(self):
        """The three calls the pupil's door makes on the way in. A preview that
        grew one of them would be a preview that enrols a pupil."""
        src = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")
        start = src.index("def exam_preview_paper(")
        end = src.index("\ndef ", start + 1)
        body = src[start:end]
        for forbidden in ("open_sitting(", "issue_code(", "ensure_page_thumbs("):
            assert forbidden not in body, (
                f"the preview calls {forbidden}; that is how a preview becomes a sitting")

    def test_it_hands_the_template_preview_mode(self, app, paper_door):
        importlib, view, captured, sb = paper_door
        _call_paper(app, paper_door)
        assert captured["template"] == "student/take_exam.html", (
            "the preview must render the pupil's own page, not a mock-up of it")
        assert captured["preview"] is True, "the preview flag never reached the page"

    def test_the_answer_key_never_reaches_the_page(self, app, paper_door):
        """The same rule the pupil's door follows, asked in the same place — a
        preview is not a licence to send the key to a browser."""
        importlib, view, captured, sb = paper_door
        _call_paper(app, paper_door)
        assert "answer_key" not in captured["exam"]
        # `choice` is a multiple choice question, so its key must not travel as a
        # public option either.
        assert captured["question_options"].get("0") in (None, {}, []), (
            "a multiple choice question's public options carry its key")


# ── 2. the rendered page ─────────────────────────────────────────────────────

def _render_paper(app, *, preview):
    """The paper, rendered — the same context shape the pupil's door sends."""
    ctx = {
        "exam": {"id": "e1", "title": "Fisika Akhir", "total_questions": 2,
                 "duration_minutes": 60, "question_types": {"0": "choice", "1": "essay"},
                 "pdf_page_urls": []},
        "anti_cheat_config": '{"anti_cheat_enabled": true}',
        "exam_started_at": None, "recovery_code": "", "question_options": {},
        "deadline": None, "deadline_reason": "preview", "seconds_left": None,
        "window_end": None, "away_grace_seconds": 15, "away_grace_chances": 2,
        "rotation_grace_seconds": 1, "student_name": "Guru Uji",
        "student_class_label": "", "student_key": "t-1", "media_used": {},
    }
    if preview:
        ctx["preview"] = True
    with app.test_request_context("/student/exams/e1"):
        from flask import g
        g.user_id, g.user_name, g.user_role = "t-1", "Guru Uji", "guru"
        g.tz_offset, g.show = 7, {}
        return app.jinja_env.get_template("student/take_exam.html").render(**ctx)


#: The things that must not be in a preview. Each one is where a preview would
#: otherwise start behaving like a sitting: the ladder, the tab watch, the liveness
#: ping, the countdown, and the upload of a draft on the way out.
NOT_IN_A_PREVIEW = (
    "this.armAntiCheat();",
    "this.watchOtherTabs();",
    "this.heartbeatTimer = setInterval",
    "this.timer = setInterval",
    "sendBeacon('/api/student/sync-draft'",
)


class TestTheRenderedPreviewArmsNothing:
    @pytest.mark.parametrize("marker", NOT_IN_A_PREVIEW)
    def test_the_marker_is_absent(self, app, marker):
        html = _render_paper(app, preview=True)
        assert marker not in html, (
            f"the preview page still starts '{marker}' — a preview must not arm the "
            "machinery a sitting runs on")

    def test_the_page_says_it_is_a_preview(self, app):
        html = _render_paper(app, preview=True)
        assert 'data-preview="1"' in html
        assert "window.SG_EXAM_PREVIEW = true" in html, (
            "the components outside examApp read this flag to stay quiet")

    def test_the_banner_explains_the_stopped_clock(self, app):
        """A clock that does not move has to say so, or it reads as a fault."""
        html = _render_paper(app, preview=True)
        assert 'data-preview-banner="1"' in html
        assert "bukan ujian sungguhan" in html and "not a real sitting" in html

    def test_submitting_is_switched_off_on_the_page(self, app):
        html = _render_paper(app, preview=True)
        assert "previewMode" in html, (
            "the submit button must know it is a preview and disable itself")


class TestThePupilsPageIsUnchanged:
    @pytest.mark.parametrize("marker", NOT_IN_A_PREVIEW)
    def test_the_sitting_still_starts_everything(self, app, marker):
        """The other half of the contract, and the reason the assertions above are
        worth anything: rendered without `preview`, the page still does all of it."""
        html = _render_paper(app, preview=False)
        assert marker in html, (
            f"the pupil's page lost '{marker}' — the preview's gates have leaked "
            "into the sitting")

    def test_the_pupil_is_not_told_it_is_a_preview(self, app):
        html = _render_paper(app, preview=False)
        assert 'data-preview="1"' not in html
        assert 'data-preview-banner="1"' not in html
        assert "window.SG_EXAM_PREVIEW = false" in html


# ── 3. the frames are the sizes they claim ───────────────────────────────────

DEVICES = {d["key"]: d for d in t.PREVIEW_DEVICES}


class TestTheFramesAreTheSizesTheyClaim:
    def test_three_devices_because_three_are_supported(self):
        assert set(DEVICES) == {"laptop", "tablet", "phone"}

    def test_a_laptop_is_offered_in_one_shape(self):
        laptop = DEVICES["laptop"]
        assert len(set(laptop["portrait"])) == 2, "a laptop frame is not a square"
        assert laptop["rotatable"] is False, "a laptop is not rotated"
        assert laptop["portrait"] == (1366, 768)

    def test_the_tablet_is_the_breakpoint_it_is_looking_for(self):
        tablet = DEVICES["tablet"]
        assert tablet["portrait"] == (768, 1024)
        assert tablet["landscape"] == (1024, 768)
        assert tablet["rotatable"] is True

    def test_the_phone_is_a_phone(self):
        phone = DEVICES["phone"]
        assert phone["portrait"] == (375, 812)
        assert phone["landscape"] == (812, 375)
        assert phone["rotatable"] is True

    def test_the_page_binds_the_frame_to_the_spec(self):
        """Not a hard-coded width anywhere: the frame's size comes from the device
        table, so a size that is not the size is a change to `PREVIEW_DEVICES`."""
        html = FORM.read_text(encoding="utf-8")
        assert ':width="frameW"' in html and ':height="frameH"' in html
        assert ':data-frame-w="frameW"' in html and ':data-frame-h="frameH"' in html
        assert not re.search(r'width="\d{3,4}"', html), (
            "the frame has a hand-written width again")

    def test_every_orientation_is_reachable_from_the_page(self):
        html = FORM.read_text(encoding="utf-8")
        assert "data-device-btn" in html and "data-orientation-btn" in html
        assert "@click=\"flip()\"" in html

    def test_the_page_names_the_frame_it_is_showing(self):
        """A teacher has to be able to say *which* size they were looking at."""
        html = FORM.read_text(encoding="utf-8")
        assert "data-frame-size" in html


# ── 4. the way in ────────────────────────────────────────────────────────────

class TestTheWayIn:
    def test_every_exam_card_offers_the_preview(self):
        html = EXAMS.read_text(encoding="utf-8")
        assert 'data-preview-link="{{ exam.id }}"' in html
        assert '/teacher/exams/{{ exam.id }}/preview' in html

    def test_the_preview_doors_are_guarded_like_the_rest_of_the_paper(self):
        src = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")
        for name in ("exam_preview", "exam_preview_paper"):
            body = src[src.index(f"def {name}("):]
            body = body[:body.index("\ndef ", 1)]
            assert "teacher_or_admin_required" in src[max(0, src.index(f"def {name}(") - 200):src.index(f"def {name}(")], (
                f"{name} is not behind the teacher gate")
            assert "_guard_exam(" in body, (
                f"{name} does not ask whether this teacher may open this paper")
