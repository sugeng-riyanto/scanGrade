"""Fullscreen is optional on every device and rotation is audit evidence."""

from pathlib import Path

from app.services.anti_cheat_service import _as_event

EXAM = Path(__file__).resolve().parents[2] / "app/templates/student/take_exam.html"


def test_exam_never_requests_or_enforces_fullscreen():
    page = EXAM.read_text(encoding="utf-8")
    for marker in ("requestFullscreen", "fullscreenchange", "fullscreenBlocked",
                   "fullscreen_required", "fullscreen_exit"):
        assert marker not in page
    assert "Layar penuh opsional." in page


def test_rotation_is_an_event_without_a_penalty():
    page = EXAM.read_text(encoding="utf-8")
    rotation = page.split("_noteOrientationShift() {", 1)[1].split("returnToExam()", 1)[0]
    assert "this._ev('orientation_shift');" in rotation
    assert "handleViolation" not in rotation
    assert not _as_event({"violation_type": "orientation_shift"})["charged"]


def test_fullscreen_exit_remains_visible_without_a_penalty():
    event = _as_event({"violation_type": "fullscreen_exit"})
    assert event["label"] == ("Keluar dari layar penuh", "Left fullscreen")
    assert not event["charged"]
