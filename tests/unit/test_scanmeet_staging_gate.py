"""Isolated staging gate tests; no network and no real credentials."""
from pathlib import Path
from deploy import scanmeet_staging_gate as gate


def valid():
    return {
        "SCANMEET_STAGING": "true",
        "SCANMEET_STAGING_URL": "https://staging.school.edu",
        "SCANGRADE_LIVE_ENABLED": "true",
        "SCANGRADE_LIVE_EXAM_ENABLED": "true",
        "LIVEKIT_URL": "wss://school.livekit.cloud",
        "LIVEKIT_API_KEY": "synthetic-fixture-key",
        "LIVEKIT_API_SECRET": "synthetic-fixture-secret",
        "SUPABASE_URL": "https://independent-staging.supabase.co",
        "SUPABASE_SERVICE_KEY": "synthetic-stage-service-key",
        "SUPABASE_ANON_KEY": "synthetic-stage-anon-key",
        "FLASK_SECRET_KEY": "synthetic-stage-session-key",
    }


def test_isolated_private_stage_is_accepted():
    assert gate.validate(valid(), "prod-ref") == []


def test_real_production_project_is_blocked():
    env = valid()
    env["SUPABASE_URL"] = "https://prod-ref.supabase.co"
    assert any("isolated" in issue for issue in gate.validate(env, "prod-ref"))


def test_flags_off_cannot_pretend_to_be_a_pilot():
    env = valid()
    env["SCANGRADE_LIVE_EXAM_ENABLED"] = "false"
    assert any("both Live" in issue for issue in gate.validate(env, "prod-ref"))


def test_production_domain_refused():
    env = valid()
    env["SCANMEET_STAGING_URL"] = "https://scan-grade.app"
    assert any("separate HTTPS" in s for s in gate.validate(env, "prod-ref"))


def test_placeholder_supabase_key_refused():
    env = valid()
    env["SUPABASE_SERVICE_KEY"] = "replace-me"
    assert any("placeholder" in s for s in gate.validate(env, "prod-ref"))


def test_check_file_refuses_in_repo_secret(tmp_path):
    secret = tmp_path / "staging.env"
    secret.write_text("example=not-a-secret")
    secret.chmod(0o600)
    assert any("outside" in s for s in gate.validate_file(secret, tmp_path))


def test_check_file_refuses_world_readable_secret(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    secret = tmp_path / "outside.env"
    secret.write_text("example=not-a-secret")
    secret.chmod(0o644)
    assert any("0600" in s for s in gate.validate_file(secret, checkout))


def test_check_file_accepts_external_private_secret(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    secret = tmp_path / "outside.env"
    secret.write_text("example=not-a-secret")
    secret.chmod(0o600)
    assert gate.validate_file(secret, checkout) == []


def test_check_file_refuses_symlink(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    private = tmp_path / "outside.env"
    private.write_text("example")
    private.chmod(0o600)
    symlink = tmp_path / "linked.env"
    symlink.symlink_to(private)
    assert any("symlink" in s for s in gate.validate_file(symlink, checkout))
