"""Regression checks for teacher-controlled ScanMeet sessions."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ScanMeetTeacherControls(unittest.TestCase):
    def test_teacher_controls_are_role_scoped(self):
        page = (ROOT / "app/templates/live/exam.html").read_text(encoding="utf-8")
        teacher_part = page.split('{% if live_role == "teacher" %}', 1)[1].split("{% else %}", 1)[0]
        self.assertIn('id="live-start"', teacher_part)
        self.assertIn('id="live-end"', teacher_part)

    def test_start_and_end_enforce_staff_access(self):
        source = (ROOT / "app/routes/live.py").read_text(encoding="utf-8")
        start = source.split("def create_session(", 1)[1].split("\n@live_bp.route", 1)[0]
        stop = source.split("def end_session(", 1)[1].split("\n@live_bp.route", 1)[0]
        self.assertIn("staff_only=True", start)
        self.assertIn("staff_only=True", stop)
        self.assertIn('"status": "ended"', stop)

    def test_global_live_switches_default_off(self):
        source = (ROOT / "app/services/live_policy.py").read_text(encoding="utf-8")
        self.assertIn('SCANGRADE_LIVE_ENABLED', source)
        self.assertIn('SCANGRADE_LIVE_EXAM_ENABLED', source)
        self.assertIn('os.environ.get(key, "false")', source)


if __name__ == "__main__":
    unittest.main()
