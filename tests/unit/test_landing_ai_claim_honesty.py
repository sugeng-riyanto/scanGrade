"""Guard against misleading autonomous-AI claims on the public landing page."""
from pathlib import Path

LANDING = Path(__file__).resolve().parents[2] / "app/templates/landing.html"


def test_landing_ai_claims_are_teacher_controlled():
    page = LANDING.read_text(encoding="utf-8")
    assert "AI-Powered Essay Grading" not in page
    assert "AI grading for text essays. The teacher just reviews." not in page
    assert "Guru Menentukan Nilai Akhir" in page
    assert "Teachers Decide Final Grades" in page
    assert "guru meninjau dan menentukan nilai akhir" in page
    assert "teachers review and decide final grades" in page


def test_landing_capacity_claims_remain_present():
    page = LANDING.read_text(encoding="utf-8")
    for phrase in ("p95 4,9 detik", "p95 8,5 detik", "11,4 detik"):
        assert phrase in page
