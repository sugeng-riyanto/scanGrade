from datetime import datetime, timezone

import pytest

from app.services.exam_management import order_exams, parse_points


def test_date_order_uses_school_midnight_and_start_times():
    now = datetime(2026, 11, 25, 17, 30, tzinfo=timezone.utc)
    starts = {
        "unscheduled": None,
        "oldest": "2026-10-26T10:00:00+00:00",
        "later": "2026-11-30T10:00:00+00:00",
        "tonight": "2026-11-26T13:00:00+00:00",
        "previous": "2026-11-25T16:59:00+00:00",
        "next": "2026-11-27T03:00:00+00:00",
        "today": "2026-11-25T17:00:00+00:00",
    }
    exams = [{"id": label, "start_at": start} for label, start in starts.items()]
    assert [(e["id"], e["date_group"]) for e in order_exams(exams, 7, now)] == [
        ("today", "today"), ("tonight", "today"), ("next", "upcoming"),
        ("later", "upcoming"), ("previous", "previous"), ("oldest", "previous"),
        ("unscheduled", "unscheduled"),
    ]


@pytest.mark.parametrize("value,expected", [("80", 80), ("82.5", 82.5), ("100", 100)])
def test_valid_point_scale(value, expected):
    assert parse_points(value) == expected


@pytest.mark.parametrize("value", ["", "0", "-1", "10001", "0.01", "Infinity", "NaN"])
def test_invalid_point_scale(value):
    with pytest.raises(ValueError):
        parse_points(value)
