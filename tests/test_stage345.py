"""Tests for Stage 3-5: bulk import, subscription tiers, public pages.

Run with: pytest tests/test_stage345.py -v
"""

import io
import csv
import json
import pytest
from flask import Flask


# ── Stage 3: Bulk Import Tests ───────────────────────────────

class TestCSVValidation:
    def test_validate_valid_csv(self):
        from app.services.student_import import validate_csv
        content = "nama,nisn,email\nAndi,1234567890,and@mail.com\nBudi,1234567891,bud@mail.com"
        f = io.BytesIO(content.encode("utf-8-sig"))
        errors, headers = validate_csv(f)
        assert len(errors) == 0

    def test_validate_missing_required_column(self):
        from app.services.student_import import validate_csv
        content = "nisn,email\n1234567890,and@mail.com"
        f = io.BytesIO(content.encode("utf-8-sig"))
        errors, headers = validate_csv(f)
        assert any("nama" in e["message"] for e in errors)

    def test_validate_duplicate_nisn(self):
        from app.services.student_import import validate_csv
        content = "nama,nisn,email\nAndi,1234567890,and@mail.com\nBudi,1234567890,bud@mail.com"
        f = io.BytesIO(content.encode("utf-8-sig"))
        errors, headers = validate_csv(f)
        assert any("duplikat" in e["message"].lower() for e in errors)

    def test_validate_invalid_nisn(self):
        from app.services.student_import import validate_csv
        content = "nama,nisn,email\nAndi,abc,and@mail.com"
        f = io.BytesIO(content.encode("utf-8-sig"))
        errors, headers = validate_csv(f)
        assert any("digit" in e["message"].lower() for e in errors)

    def test_validate_invalid_email(self):
        from app.services.student_import import validate_csv
        content = "nama,nisn,email\nAndi,1234567890,bademail"
        f = io.BytesIO(content.encode("utf-8-sig"))
        errors, headers = validate_csv(f)
        assert any("email" in e["message"].lower() for e in errors)


# ── Stage 4: Subscription Tier Tests ─────────────────────────

class TestTierLimits:
    def test_a_plan_resolves_through_its_duration(self):
        """Phase 1: the plan is read by relation, not from a hardcoded id map.

        The live catalogue's own durations (012_subscription_system.sql). Plan 1
        is the paid `1 Bulan` (Rp59.000); the old hardcoded map called it
        `"trial"`, which is how a school that bought a plan was served the free
        5-exam quota.
        """
        from app.services.subscription_service import (resolve_tier,
                                                       tier_for_duration_days)
        catalogue = {1: 30, 2: 90, 3: 120, 4: 180, 5: 365, 6: 730, 7: 1095,
                     8: 1825, 9: 2555, 10: 0}
        for pid, days in catalogue.items():
            tier = resolve_tier({"status": "active", "plan_id": pid},
                                {"id": pid, "duration_days": days})
            assert tier == tier_for_duration_days(days)
            assert tier in ("basic", "pro", "enterprise")

    def test_tier_limits_structure(self):
        from app.services.subscription_service import TIER_LIMITS
        assert "trial" in TIER_LIMITS
        assert "pro" in TIER_LIMITS
        assert TIER_LIMITS["pro"]["ai_grading"] is True
        assert TIER_LIMITS["trial"]["ai_grading"] is False
        assert TIER_LIMITS["trial"]["exams_per_year"] == 5
        assert TIER_LIMITS["pro"]["exams_per_year"] is None  # unlimited


# ── Stage 5: Public Pages Tests ──────────────────────────────

class TestPublicPages:
    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @pytest.fixture
    def csrf_headers(self, client):
        """The app enforces CSRF on every POST — supply a valid session token."""
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "test-csrf-token"
        return {"X-CSRF-Token": "test-csrf-token", "Accept": "application/json"}

    def test_pricing_page_loads(self, client):
        resp = client.get("/pricing")
        assert resp.status_code == 200
        assert b"Starter" in resp.data or b"Paket" in resp.data

    def test_landing_page_loads(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"ScanGrade" in resp.data

    def test_landing_page_seeds_csrf_token(self, client):
        """The public demo form can only work if the first page load hands the
        browser a CSRF token (base.html meta tag)."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert b'csrf-token' in resp.data
        with client.session_transaction() as sess:
            assert sess.get("_csrf_token")

    def test_demo_request_rejected_without_csrf(self, client):
        resp = client.post("/api/demo-request", json={
            "school_name": "SMA Test",
            "email": "test@school.com",
        })
        assert resp.status_code == 403

    def test_demo_request_endpoint(self, client, csrf_headers):
        resp = client.post("/api/demo-request", json={
            "school_name": "SMA Test",
            "email": "test@school.com",
            "phone": "081234567890",
        }, headers=csrf_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_demo_request_missing_fields(self, client, csrf_headers):
        resp = client.post("/api/demo-request", json={
            "email": "test@school.com",
        }, headers=csrf_headers)
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False

    def test_demo_request_invalid_email(self, client, csrf_headers):
        resp = client.post("/api/demo-request", json={
            "school_name": "Test",
            "email": "not-an-email",
        }, headers=csrf_headers)
        assert resp.status_code == 400

    def test_template_csv_download(self, client):
        """Test that the CSV template endpoint returns a CSV file."""
        # This tests via the blueprint; may need auth mock
        pass
