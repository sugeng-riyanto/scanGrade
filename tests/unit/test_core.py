"""Unit tests for ScanGrade core logic."""
import json
import io
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


# ─── Test: Anti-Cheat Penalty Calculation ───

def test_calculate_graduated_penalty_first_violation():
    """First violation should be a warning (0 penalty cumulative)."""
    from app.services.anti_cheat_service import calculate_graduated_penalty
    exam = {"penalty_per_violation": 5}
    result = calculate_graduated_penalty(1, exam)
    assert result["penalty"] == 0
    assert result["warning"] is True


def test_calculate_graduated_penalty_second_violation():
    """Second violation = base (cumulative: 0+5 = 5)."""
    from app.services.anti_cheat_service import calculate_graduated_penalty
    exam = {"penalty_per_violation": 5}
    result = calculate_graduated_penalty(2, exam)
    assert result["penalty"] == 5
    assert result["warning"] is False


def test_calculate_graduated_penalty_third_violation():
    """Third violation = cumulative 0+5+10 = 15."""
    from app.services.anti_cheat_service import calculate_graduated_penalty
    exam = {"penalty_per_violation": 5}
    result = calculate_graduated_penalty(3, exam)
    assert result["penalty"] == 15


def test_calculate_graduated_penalty_fourth_plus():
    """Fourth+ violation = cumulative 0+5+10+15+15+15..."""
    from app.services.anti_cheat_service import calculate_graduated_penalty
    exam = {"penalty_per_violation": 5}
    result = calculate_graduated_penalty(4, exam)
    assert result["penalty"] == 30  # 0+5+10+15
    result = calculate_graduated_penalty(5, exam)
    assert result["penalty"] == 45  # 0+5+10+15+15


def test_calculate_graduated_penalty_default_base():
    """Should use default 5 when penalty_per_violation not set."""
    from app.services.anti_cheat_service import calculate_graduated_penalty
    exam = {}
    result = calculate_graduated_penalty(2, exam)
    assert result["penalty"] == 5


def test_calculate_graduated_penalty_custom_base():
    """Should use custom penalty_per_violation."""
    from app.services.anti_cheat_service import calculate_graduated_penalty
    exam = {"penalty_per_violation": 10}
    result = calculate_graduated_penalty(2, exam)
    assert result["penalty"] == 10


# ─── Test: Answer Parsing ───

def test_parse_answer_simple_string():
    """MCQ answer as simple string."""
    from app.services.export_service import _parse_answer
    ans, text = _parse_answer("A", 0, "mcq", {})
    assert ans == "A"
    assert text == ""


def test_parse_answer_dict_with_answer():
    """MCQ answer as dict with answer field."""
    from app.services.export_service import _parse_answer
    ans, text = _parse_answer({"answer": "B", "pages": {}}, 0, "mcq", {})
    assert ans == "B"
    assert text == ""


def test_parse_answer_essay_with_text():
    """Essay answer with text boxes."""
    from app.services.export_service import _parse_answer
    ans, text = _parse_answer({
        "type": "essay_text",
        "pages": {"0": {"textBoxes": [{"text": "Hello world"}]}}
    }, 0, "essay_text", {})
    assert text == "Hello world"


def test_parse_answer_essay_multiple_texts():
    """Essay answer with multiple text boxes."""
    from app.services.export_service import _parse_answer
    ans, text = _parse_answer({
        "type": "essay_text",
        "pages": {"0": {"textBoxes": [
            {"text": "First"}, {"text": "Second"}
        ]}}
    }, 0, "essay_text", {})
    assert "First" in text
    assert "Second" in text


def test_parse_answer_none():
    """None answer should return empty."""
    from app.services.export_service import _parse_answer
    ans, text = _parse_answer(None, 0, "mcq", {})
    assert ans == ""
    assert text == ""


def test_get_answer_key():
    """Should return correct answer from exam dict."""
    from app.services.export_service import _get_answer_key
    exam = {"answer_key": {"0": "A", "1": "C"}}
    assert _get_answer_key(exam, 0) == "A"
    assert _get_answer_key(exam, 1) == "C"
    assert _get_answer_key(exam, 2) == ""


# ─── Test: MCQ Scoring Logic ───

def test_is_mcq_correct_exact_match():
    """Exact match should be correct."""
    from app.routes.teacher import _is_mcq_correct
    assert _is_mcq_correct("A", "A") is True
    assert _is_mcq_correct("B", "A") is False


def test_is_mcq_correct_bonus():
    """Bonus question: any non-empty answer is correct."""
    from app.routes.teacher import _is_mcq_correct
    assert _is_mcq_correct("A", "bonus") is True
    assert _is_mcq_correct("", "bonus") is False
    assert _is_mcq_correct(None, "bonus") is False


def test_is_mcq_correct_multiple():
    """Multiple correct answers."""
    from app.routes.teacher import _is_mcq_correct
    assert _is_mcq_correct("A", ["A", "B"]) is True
    assert _is_mcq_correct("B", ["A", "B"]) is True
    assert _is_mcq_correct("C", ["A", "B"]) is False


def test_is_mcq_correct_dict_answer():
    """Answer as dict (MCQ with canvas)."""
    from app.routes.teacher import _extract_mcq_answer, _is_mcq_correct
    ans = {"answer": "A", "pages": {}}
    extracted = _extract_mcq_answer(ans)
    assert extracted == "A"
    assert _is_mcq_correct(ans, "A") is True


# ─── Test: Export Service ───

def test_export_xlsx_creates_file():
    """XLSX export should produce a non-empty BytesIO."""
    from app.services.export_service import export_to_xlsx
    submissions = [
        {"student_name": "Siswa 1", "student_id": "id-1", "score": 80, "penalty": 0,
         "final_score": 80, "status": "graded", "submitted_at": "2026-06-01", "answers": {}},
    ]
    exam = {"total_questions": 0, "question_types": {}, "answer_key": {}}
    buf = export_to_xlsx(submissions, exam)
    assert buf.getvalue()[:2] == b'PK'  # XLSX is a ZIP file


def test_export_xlsx_with_answers():
    """XLSX should include MCQ answers."""
    from app.services.export_service import export_to_xlsx
    submissions = [
        {"student_name": "Siswa 1", "student_id": "id-1", "score": 80, "penalty": 0,
         "final_score": 80, "status": "graded", "submitted_at": "2026-06-01",
         "answers": {"0": "A", "1": "B"}},
    ]
    exam = {"total_questions": 2, "question_types": {"0": "mcq", "1": "mcq"}, "answer_key": {"0": "A", "1": "C"}}
    buf = export_to_xlsx(submissions, exam)
    assert buf.getvalue()[:2] == b'PK'


def test_export_pdf_creates_file():
    """PDF export should produce a non-empty BytesIO."""
    from app.services.export_service import export_to_pdf
    submissions = [
        {"student_name": "Siswa 1", "student_id": "id-1", "score": 80,
         "penalty": 0, "final_score": 80, "status": "graded", "answers": {}},
    ]
    exam = {"total_questions": 0, "question_types": {}, "answer_key": {},
            "title": "Test Exam"}
    buf = export_to_pdf(submissions, "Test Exam", exam)
    assert buf.getvalue()[:4] == b'%PDF'


def test_wrap_text():
    """Word wrap should split long text."""
    from app.services.export_service import _wrap_text
    text = "This is a long text that should be wrapped at 20 characters"
    lines = _wrap_text(text, 20)
    assert len(lines) > 1
    assert all(len(l) <= 20 for l in lines)


# ─── Test: Answer Sheet Generator ───

def test_generate_bubble_sheet():
    """Bubble sheet should produce a valid PDF."""
    from app.services.export_service import generate_bubble_sheet_pdf
    buf = generate_bubble_sheet_pdf("Test", 10, "Math")
    assert buf.getvalue()[:4] == b'%PDF'


def test_bubble_sheet_total_questions():
    """Bubble sheet with different question counts."""
    from app.services.export_service import generate_bubble_sheet_pdf
    for n in [5, 20, 50]:
        buf = generate_bubble_sheet_pdf("Test", n, "Math")
        assert buf.getvalue()[:4] == b'%PDF'


# ─── Test: OMR Service ───

def test_omr_zscore_single_filled():
    """z-score should detect 1 filled bubble among unfilled (default threshold)."""
    from app.services.omr_service import _zscore_bubble_detection
    # Unfilled = very bright; Filled = very dark — extreme contrast
    stats = [
        {"mean_dark": 1, "median_dark": 1, "fill_ratio": 0.005, "dark_pixel_ratio": 0.002},
        {"mean_dark": 1, "median_dark": 1, "fill_ratio": 0.005, "dark_pixel_ratio": 0.002},
        {"mean_dark": 1, "median_dark": 1, "fill_ratio": 0.005, "dark_pixel_ratio": 0.002},
        {"mean_dark": 1, "median_dark": 1, "fill_ratio": 0.005, "dark_pixel_ratio": 0.002},
        {"mean_dark": 250, "median_dark": 248, "fill_ratio": 0.98, "dark_pixel_ratio": 0.99},
    ]
    result = _zscore_bubble_detection(stats, z_threshold=1.2)
    assert len(result) >= 1
    assert result[0][0] == 4  # index 4 is the filled bubble


def test_omr_zscore_all_empty():
    """z-score should return empty when all bubbles are empty."""
    from app.services.omr_service import _zscore_bubble_detection
    stats = [
        {"mean_dark": 8, "median_dark": 7, "fill_ratio": 0.03, "dark_pixel_ratio": 0.02},
        {"mean_dark": 10, "median_dark": 8, "fill_ratio": 0.05, "dark_pixel_ratio": 0.03},
        {"mean_dark": 9, "median_dark": 7, "fill_ratio": 0.04, "dark_pixel_ratio": 0.02},
    ]
    result = _zscore_bubble_detection(stats, z_threshold=2.0)
    assert len(result) == 0


def test_omr_zscore_all_filled():
    """z-score should return empty (no outlier) when all bubbles equally filled."""
    from app.services.omr_service import _zscore_bubble_detection
    stats = [
        {"mean_dark": 140, "median_dark": 130, "fill_ratio": 0.80, "dark_pixel_ratio": 0.85},
        {"mean_dark": 145, "median_dark": 135, "fill_ratio": 0.82, "dark_pixel_ratio": 0.87},
        {"mean_dark": 150, "median_dark": 140, "fill_ratio": 0.85, "dark_pixel_ratio": 0.90},
    ]
    result = _zscore_bubble_detection(stats, z_threshold=2.0)
    assert len(result) == 0


def test_omr_zscore_few_options():
    """z-score with only 2 options (satu/tidak)."""
    from app.services.omr_service import _zscore_bubble_detection
    stats = [
        {"mean_dark": 10, "median_dark": 8, "fill_ratio": 0.04, "dark_pixel_ratio": 0.03},
        {"mean_dark": 160, "median_dark": 150, "fill_ratio": 0.88, "dark_pixel_ratio": 0.92},
    ]
    result = _zscore_bubble_detection(stats, z_threshold=1.2)
    assert len(result) >= 1


def test_omr_bubble_stats_basic():
    """_bubble_stats should return valid metrics for a small ROI."""
    import numpy as np
    from app.services.omr_service import _bubble_stats
    roi = np.zeros((10, 10, 3), dtype=np.uint8)  # black square
    stats = _bubble_stats(roi)
    assert "mean_dark" in stats
    assert "median_dark" in stats
    assert "fill_ratio" in stats
    assert stats["mean_dark"] > 200  # near-black has high darkness


def test_omr_bubble_stats_white():
    """White bubble should have low darkness."""
    import numpy as np
    from app.services.omr_service import _bubble_stats
    roi = np.full((10, 10, 3), 255, dtype=np.uint8)  # white square
    stats = _bubble_stats(roi)
    assert stats["mean_dark"] < 50


def test_omr_bubble_stats_partial():
    """Partially filled bubble (half black, half white)."""
    import numpy as np
    from app.services.omr_service import _bubble_stats
    roi = np.zeros((10, 10, 3), dtype=np.uint8)
    roi[5:, :] = 255  # bottom half white
    stats = _bubble_stats(roi)
    assert 50 < stats["mean_dark"] < 200


def test_omr_process_scan_none_image():
    """process_scan should return error for invalid bytes."""
    from app.services.omr_service import process_scan
    result = process_scan(b"not an image")
    assert "error" in result


# ─── Test: Grading Service ───

def test_grade_essay_no_api_key():
    """grade_essay should return fallback score when no API key."""
    from app.services.grading_service import grade_essay
    result = grade_essay("some answer", teacher_id="test")
    assert result is not None


def test_extract_mcq_answer_string():
    """MCQ answer as plain string."""
    from app.routes.teacher import _extract_mcq_answer
    assert _extract_mcq_answer("A") == "A"
    assert _extract_mcq_answer("") == ""


def test_extract_mcq_answer_dict():
    """MCQ answer as dict."""
    from app.routes.teacher import _extract_mcq_answer
    assert _extract_mcq_answer({"answer": "B"}) == "B"
    assert _extract_mcq_answer({}) == ""


def test_is_mcq_correct_bonus():
    """Bonus key should match any non-empty answer."""
    from app.routes.teacher import _is_mcq_correct
    assert _is_mcq_correct("A", "bonus") is True
    assert _is_mcq_correct({"answer": "B"}, "bonus") is True


def test_is_mcq_correct_list_key():
    """Answer key as list (multiple correct)."""
    from app.routes.teacher import _is_mcq_correct
    assert _is_mcq_correct("A", ["A", "B"]) is True
    assert _is_mcq_correct("C", ["A", "B"]) is False
