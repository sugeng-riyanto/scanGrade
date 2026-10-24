"""Sign-off output must not echo URL credentials or query parameters."""
from deploy.scanmeet_media_gate import Row, ROW_ORDER, EXIT_CANNOT_MEASURE, signoff_sheet


def test_signoff_url_is_sanitized():
    rows = [Row(name, 'skipped', 'not measured') for name in ROW_ORDER]
    sheet = signoff_sheet('https://pilot:private@staging.example.edu/live?token=hidden#fragment', 'exam-1', rows, EXIT_CANNOT_MEASURE)
    assert 'staging.example.edu' in sheet
    for sensitive in ('pilot:', 'private@', 'token=', 'hidden', '#fragment'):
        assert sensitive not in sheet
