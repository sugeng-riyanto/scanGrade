"""Sign-off output remains complete, ordered and fail-closed."""
from deploy.scanmeet_media_gate import (
    Row, ROW_ORDER, EXIT_OK, EXIT_FINDING, EXIT_CANNOT_MEASURE,
    signoff_markdown,
)


def render(code, rows):
    return signoff_markdown(base_url="https://staging.example.edu/?token=secret",
                            exam_id="exam-123", code=code, rows=rows)


def test_all_pass_signoff():
    rows = [Row(name, "pass", "verified") for name in ROW_ORDER]
    sheet = render(EXIT_OK, rows)
    assert "**PASS**" in sheet
    assert "All 12 measurements passed" in sheet
    assert "secret" not in sheet
    assert sheet.count("| PASS |") == 12
    assert sheet.index("`sdk-loaded`") < sheet.index("`room-ended`")


def test_skipped_is_not_signed_off():
    rows = [Row(name, "pass", "verified") for name in ROW_ORDER[:-1]]
    rows.append(Row("room-ended", "skipped", "missing outsider | account"))
    sheet = render(EXIT_CANNOT_MEASURE, rows)
    assert "**INCOMPLETE**" in sheet
    assert "| SKIPPED | missing outsider \\| account |" in sheet
    assert "NOT APPROVED" in sheet


def test_failure_preserves_evidence():
    rows = [Row(name, "pass", "ok") for name in ROW_ORDER]
    rows[4] = Row("pupil-isolation", "fail", "unauthorized inbound RTP")
    sheet = render(EXIT_FINDING, rows)
    assert "**FAIL**" in sheet
    assert "unauthorized inbound RTP" in sheet
    assert "NOT APPROVED" in sheet


def test_missing_measurements_never_turn_into_pass():
    sheet = render(EXIT_CANNOT_MEASURE, [])
    assert sheet.count("| SKIPPED |") == 12
    assert "NOT APPROVED" in sheet
