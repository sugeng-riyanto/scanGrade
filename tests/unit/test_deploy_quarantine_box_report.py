"""Deploy refusal snapshots must stay useful after a rollback or quarantine lift."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from app.services import deploy_status_service as status

REPO = Path(__file__).resolve().parents[2]
SHA = "a" * 40
NOW = dt.datetime(2026, 10, 24, 1, 0, tzinfo=dt.timezone.utc)

SAMPLE = "\n".join((
    SHA,
    "2026-10-24T00:00:00+00:00",
    "theme gate",
    "css freshness: stale",
    "=== box_report snapshot ===",
    "=== ScanGrade box report ===",
    "worker health: cannot measure",
    "checkout HEAD: behind",
    "=== end box_report snapshot ===",
    "",
))


def test_quarantine_report_is_kept_separate_from_gate_reasons(tmp_path):
    path = tmp_path / "quarantined"
    path.write_text(SAMPLE, encoding="utf-8")
    report = status.quarantine_state(path, tmp_path, now=NOW)
    assert report["held"] is True
    assert report["reasons"] == ["css freshness: stale"]
    assert report["reasons_total"] == 1
    assert "worker health: cannot measure" in report["box_report"]
    assert "\n" in report["box_report"]
    assert "=== end box_report snapshot ===" not in report["box_report"]


def test_historical_refusal_preserves_same_snapshot():
    record = status._refusal_record(SAMPLE, now=NOW)
    assert record["held"] is True
    assert record["reasons"] == ["css freshness: stale"]
    assert "checkout HEAD: behind" in record["box_report"]


def test_legacy_quarantine_without_snapshot_is_unchanged():
    old = "\n".join((SHA, "2026-10-24T00:00:00+00:00", "theme gate", "failed test"))
    record = status._refusal_record(old, now=NOW)
    assert record["reasons"] == ["failed test"]
    assert record["box_report"] is None


def test_truncated_or_hostile_snapshot_is_bounded():
    lines = [SHA, "2026-10-24T00:00:00+00:00", "theme gate",
             "=== box_report snapshot ==="]
    lines.extend("line-%03d " % i + "x" * 1000 for i in range(250))
    record = status._refusal_record("\n".join(lines), now=NOW)
    snapshot = record["box_report"].splitlines()
    assert len(snapshot) == 70
    assert all(len(line) <= status.QUARANTINE_DETAIL_WIDTH for line in snapshot)


def test_runner_and_status_page_wire_the_snapshot():
    deploy = (REPO / "deploy" / "scangrade-deploy.sh").read_text(encoding="utf-8")
    template = (REPO / "app" / "templates" / "super_admin" / "deploy_status.html").read_text(encoding="utf-8")
    assert "=== box_report snapshot ===" in deploy
    assert "=== end box_report snapshot ===" in deploy
    assert "deploy/box_report.py" in deploy
    assert "q.box_report" in template
    assert "r.box_report" in template
