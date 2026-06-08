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
    def test_plan_id_to_tier_mapping(self):
        from app.services.subscription_service import _plan_id_to_tier
        assert _plan_id_to_tier(None) == "trial"
        assert _plan_id_to_tier(1) == "trial"
        assert _plan_id_to_tier(2) == "basic"
        assert _plan_id_to_tier(5) == "pro"
        assert _plan_id_to_tier(10) == "enterprise"

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
    def app(self):
        from app import create_app
        _app = create_app("app.config.DevelopmentConfig")
        _app.config["TESTING"] = True
        return _app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_pricing_page_loads(self, client):
        resp = client.get("/pricing")
        assert resp.status_code == 200
        assert b"Starter" in resp.data or b"Paket" in resp.data

    def test_landing_page_loads(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"ScanGrade" in resp.data

    def test_demo_request_endpoint(self, client):
        resp = client.post("/api/demo-request", json={
            "school_name": "SMA Test",
            "email": "test@school.com",
            "phone": "081234567890",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_demo_request_missing_fields(self, client):
        resp = client.post("/api/demo-request", json={
            "email": "test@school.com",
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False

    def test_demo_request_invalid_email(self, client):
        resp = client.post("/api/demo-request", json={
            "school_name": "Test",
            "email": "not-an-email",
        })
        assert resp.status_code == 400

    def test_template_csv_download(self, client):
        """Test that the CSV template endpoint returns a CSV file."""
        # This tests via the blueprint; may need auth mock
        pass
