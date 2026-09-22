"""ProxyFix wiring tests.

Behind nginx every request arrives from the proxy (127.0.0.1), so without
ProxyFix the client IP is identical for all users. That silently breaks per-IP
rate limiting (everyone shares one bucket), audit logging, and the anti-cheat
device-mismatch check. It must only be enabled when the deployment declares a
trusted proxy, otherwise a directly-reachable instance could let a client spoof
its own IP via a hand-crafted X-Forwarded-For header.
"""
from unittest import mock

from flask import request

import app as app_pkg
from tests.conftest import build_app
from app.config import Config, ProductionConfig, TestingConfig


class _ProxyTestingConfig(TestingConfig):
    """Testing config that declares one trusted proxy hop (like nginx)."""

    TRUSTED_PROXY_HOPS = 1


def _client_for(config):
    app = build_app(config)

    @app.route("/_whoami")
    def _whoami():  # pragma: no cover - trivial echo endpoint
        return {"ip": request.remote_addr, "scheme": request.scheme}

    return app.test_client()


def _client_behind_proxy():
    """Build an app whose config declares one trusted hop."""
    with mock.patch.object(app_pkg, "get_config", return_value=_ProxyTestingConfig):
        return _client_for(None)


def test_proxy_fix_not_applied_by_default():
    """No trusted proxy declared => X-Forwarded-For must be ignored."""
    client = _client_for(TestingConfig)
    r = client.get("/_whoami", headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.get_json()["ip"] == "127.0.0.1"


def test_proxy_fix_applied_when_configured():
    """One trusted hop => the real client address is restored."""
    client = _client_behind_proxy()
    r = client.get("/_whoami", headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.get_json()["ip"] == "203.0.113.9"


def test_proxy_fix_trusts_only_the_last_hop():
    """nginx appends the client to X-Forwarded-For; a client-supplied prefix
    must not be able to impersonate another address."""
    client = _client_behind_proxy()
    r = client.get("/_whoami", headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.9"})
    assert r.get_json()["ip"] == "203.0.113.9"


def test_proxy_fix_restores_https_scheme():
    """Secure-cookie and redirect logic depends on the forwarded scheme."""
    client = _client_behind_proxy()
    r = client.get(
        "/_whoami",
        headers={"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"},
    )
    assert r.get_json()["scheme"] == "https"


def test_production_declares_one_trusted_hop():
    """Production is fronted by nginx, so it must trust exactly one hop."""
    assert ProductionConfig.TRUSTED_PROXY_HOPS == 1


def test_base_config_does_not_trust_proxies():
    assert Config.TRUSTED_PROXY_HOPS == 0
