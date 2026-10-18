"""Who can see the code that lets a locked pupil back into their paper?

A pupil who is locked out reads a six-digit code off their own screen. The staff
member on the floor has to be able to compare it — otherwise the code is a secret
only the person asking can see, and the invigilator's only option is to believe
whatever number they were read.

Three roles hold sittings and three pages show the codes: the teacher who owns or
invigilates the exam, the school admin, and the principal/vice principal. Each is
a different route with a different authority, and the failure this guards against
is one of them silently losing the section — a page that renders a heading and an
empty table reads as "no codes yet", not as "this reader can never see one".
"""
from __future__ import annotations

from pathlib import Path

import pytest

TEACHER = Path("app/routes/teacher.py")
ADMIN = Path("app/routes/admin_sekolah.py")
PRINCIPAL = Path("app/routes/principal.py")

SURFACES = [
    (TEACHER, "teacher/invigilation.html", "the teacher who invigilates or owns the exam"),
    (ADMIN, "principal/invigilation.html", "the school admin (whole school)"),
    (PRINCIPAL, "principal/invigilation.html", "the principal / vice principal (read-only)"),
]


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _route_block(src: str, needle: str) -> str:
    start = src.index(needle)
    rest = src[start:]
    nxt = rest.find("\n@", 1)
    return rest if nxt == -1 else rest[:nxt]


class TestEveryStaffPageCarriesTheCodes:
    @pytest.mark.parametrize("path,template,who", SURFACES)
    def test_the_page_reads_the_codes(self, path, template, who):
        block = src = _src(path)
        assert "exam_codes.codes_for_exams(" in src, f"{who} has no code read at all"
        if path is TEACHER:
            block = _route_block(src, 'def invigilation_duties(')
            assert "codes = exam_codes.codes_for_exams(" in block
            assert "codes=codes" in block
        else:
            assert "codes = exam_codes.codes_for_exams(" in src
            assert "codes=codes" in src

    @pytest.mark.parametrize("path,template,who", SURFACES)
    def test_the_template_renders_a_code_column(self, path, template, who):
        tpl = Path("app/templates") / template
        src = tpl.read_text(encoding="utf-8")
        assert "codes" in src
        assert "{{ c.code }}" in src, f"{who}'s page does not print the code itself"
        assert 'data-code=' in src, "no stable hook for a reader/test to find a row"


class TestEveryDoorThatReopensALockedSittingExists:
    def test_all_three_roles_have_an_unlock_route(self):
        # Each blueprints its own prefix, so the URLs are three different strings
        # that all end in the same three segments.
        # The teacher's exam id sits before the segment, the officials' after it;
        # what the three share is the last two segments and the verb.
        for path in (TEACHER, ADMIN, PRINCIPAL):
            src = _src(path)
            assert "/locked/" in src and "<student_id>/unlock" in src, (
                f"{path} lost its unlock door")

    def test_the_three_doors_share_one_implementation(self):
        """Three routes, one rule about the clock — a second copy is a second answer."""
        for path in (TEACHER, ADMIN, PRINCIPAL):
            assert "sitting_unlock.unlock_sitting(" in _src(path)

    def test_the_teacher_door_is_scoped_to_the_sittings_they_hold(self):
        block = _route_block(_src(TEACHER), "def unlock_locked_sitting(")
        assert "_teacher_code_exam_ids(" in block, (
            "the teacher unlock door does not check that this teacher holds the exam")
