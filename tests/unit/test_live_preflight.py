"""Offline preflight sanity checks, with no LiveKit or database dependency."""
import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[2] / "deploy" / "livekit_preflight.py"
SPEC = importlib.util.spec_from_file_location("livekit_preflight", PATH)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def test_disabled_template_needs_no_credentials():
    assert preflight.audit({}) == []


def test_non_pilot_always_refuses_active_flags():
    assert preflight.audit({"SCANGRADE_LIVE_ENABLED": "true"})
    assert preflight.audit({"SCANGRADE_LIVE_EXAM_ENABLED": "true"})


def test_pilot_needs_both_switches_and_keys():
    issues = preflight.audit({}, pilot=True)
    assert any("both Live" in s for s in issues)
    assert any("LIVEKIT_API_SECRET" in s for s in issues)


def test_pilot_accepts_wss_without_leaking_secret():
    value = {
        "SCANGRADE_LIVE_ENABLED": "true",
        "SCANGRADE_LIVE_EXAM_ENABLED": "true",
        "LIVEKIT_URL": "wss://org.livekit.cloud",
        "LIVEKIT_API_KEY": "test-pilot-key",
        "LIVEKIT_API_SECRET": "__PRIVATE_SECRET_1234__",
    }
    assert preflight.audit(value, pilot=True) == []
    assert "__PRIVATE_SECRET" not in repr(preflight.audit(value, pilot=True))


def test_insecure_endpoint_refused():
    value = {
        "SCANGRADE_LIVE_ENABLED": "true", "SCANGRADE_LIVE_EXAM_ENABLED": "true",
        "LIVEKIT_URL": "ws://127.0.0.1:7880", "LIVEKIT_API_KEY": "test-pilot-key",
        "LIVEKIT_API_SECRET": "good-secret-here",
    }
    assert any("secure wss" in s for s in preflight.audit(value, pilot=True))


def test_simple_dotenv_parser_does_not_execute_shell():
    parsed = preflight.parse_env("# note\nexport SCANGRADE_LIVE_ENABLED='false'\nLIVEKIT_URL=wss://test.invalid\n")
    assert parsed["SCANGRADE_LIVE_ENABLED"] == "false"
    assert parsed["LIVEKIT_URL"] == "wss://test.invalid"
