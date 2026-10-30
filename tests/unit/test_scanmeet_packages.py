from datetime import datetime, timedelta, timezone

import pytest

from app.services import scanmeet_packages as packages

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("code,minutes,participants,sessions,days,price", [
    ("trial", 30, 5, 3, 7, 0), ("kelas", 60, 10, 5, 30, 149000),
    ("sekolah", 120, 35, 3, 30, 399000),
])
def test_activation_uses_catalog_limits(code, minutes, participants, sessions, days, price):
    row = packages.activation(None, code, "activate", code != "trial", now=NOW)
    assert row["session_limit_minutes"] == minutes
    assert row["participant_limit"] == participants
    assert row["session_allowance"] == sessions
    assert packages.parse_time(row["valid_until"]) == NOW + timedelta(days=days)
    assert packages.plan(code)["price"] == price


def test_trial_history_survives_archive_and_prevents_repeated_claim():
    with pytest.raises(ValueError, match="satu kali"):
        packages.activation({"trial_started_at": NOW.isoformat(), "archived_at": NOW.isoformat()},
                            "trial", "activate", False, now=NOW)


@pytest.mark.parametrize("code", ["kelas", "sekolah"])
def test_paid_package_requires_verified_payment(code):
    with pytest.raises(ValueError, match="pembayaran"):
        packages.activation(None, code, "activate", False, now=NOW)


def test_extension_adds_days_and_allowance_without_erasing_usage():
    old = {"plan_code": "kelas", "valid_until": (NOW + timedelta(days=5)).isoformat(),
           "session_allowance": 5, "sessions_used": 2}
    row = packages.activation(old, "kelas", "extend", True, now=NOW)
    assert row["session_allowance"] == 10 and row["sessions_used"] == 2
    assert packages.parse_time(row["valid_until"]) == NOW + timedelta(days=35)


def test_expired_package_renews_without_carrying_expired_slots():
    old = {"plan_code": "kelas", "valid_until": (NOW - timedelta(days=1)).isoformat(),
           "session_allowance": 5, "sessions_used": 2}
    row = packages.activation(old, "kelas", "extend", True, now=NOW)
    assert row["session_allowance"] == 5 and row["sessions_used"] == 0


def test_extension_cannot_silently_change_package():
    with pytest.raises(ValueError, match="paket yang sama"):
        packages.activation({"plan_code": "kelas"}, "sekolah", "extend", True, now=NOW)


@pytest.mark.parametrize("expiry,expected", [
    (None, True), (NOW.isoformat(), False), ((NOW - timedelta(seconds=1)).isoformat(), False),
    ((NOW + timedelta(seconds=1)).isoformat(), True), ("invalid", False),
    ("2026-10-30T12:00:01", False),
])
def test_session_deadline_is_checked_on_server(expiry, expected):
    assert packages.session_open({"status": "active", "expires_at": expiry}, now=NOW) is expected


def test_expired_and_archived_licenses_never_look_active():
    row = {"enabled": True, "status": "paid", "valid_until": NOW.isoformat()}
    assert packages.describe(row, now=NOW)["effective_status"] == "expired"
    row["archived_at"] = NOW.isoformat()
    assert packages.describe(row, now=NOW)["effective_status"] == "archived"
