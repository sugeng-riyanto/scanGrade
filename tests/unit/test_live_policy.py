"""Offline safety tests for Exam Mode (no external SDK, DB or LiveKit required)."""
from app.services import live_policy


def test_live_default_off():
    assert not live_policy.feature_enabled({})
    assert not live_policy.feature_enabled({"SCANGRADE_LIVE_ENABLED": True})
    assert not live_policy.feature_enabled({"SCANGRADE_LIVE_EXAM_ENABLED": True})


def test_live_needs_both_switches():
    assert live_policy.feature_enabled({
        "SCANGRADE_LIVE_ENABLED": True, "SCANGRADE_LIVE_EXAM_ENABLED": True,
    })


def test_live_exam_policy_locked():
    policy = live_policy.EXAM_POLICY
    assert policy["mode"] == "exam"
    assert policy["camera"] == "required"
    assert policy["microphone"] == "ask"
    assert not policy["chat"] and not policy["recording"]
    assert policy["raise_hand"] and policy["layout"] == "grid"
    assert not policy["screen_share"]


def test_student_grants_camera_only_without_approval():
    assert live_policy.allowed_sources("student") == ["camera"]
    assert live_policy.allowed_sources("student", True) == ["camera", "microphone"]
    assert live_policy.allowed_sources("teacher") == ["camera", "microphone"]


def test_identity_is_opaque_stable_and_scoped():
    config = {"LIVEKIT_API_SECRET": "test-secret"}
    a = live_policy.participant_identity(config, "session-a", "user-a")
    assert a == live_policy.participant_identity(config, "session-a", "user-a")
    assert a != live_policy.participant_identity(config, "session-b", "user-a")
    assert a != live_policy.participant_identity(config, "session-a", "user-b")
    assert "user-a" not in a
