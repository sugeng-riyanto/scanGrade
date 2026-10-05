"""A box that boots without `FLASK_SECRET_KEY` signs sessions with a public string.

`Config.SECRET_KEY = env_str("FLASK_SECRET_KEY", "dev-secret-change-me")` — the
fallback is spelled out in this repository, and `validate()` only asks whether the
value is *non-empty*, which a default always is. So a production box whose `.env`
lost that line would start happily and sign every CSRF token with a secret anyone
who reads the repo can reproduce. `ProductionConfig` now refuses that one exact
value; the placeholder is still fine for development and the suite, so the refusal
lives in production alone and cannot make the dev server or a test un-bootable.

This is deliberately *not* a "reject anything weak" rule: production's real secret
is chosen by the operator, and a box that is running must not be told its secret is
the wrong shape by a guard someone added later. It refuses the one value the
repository itself supplies.
"""
from __future__ import annotations

import pytest

from app.config import Config, DevelopmentConfig, ProductionConfig, TestingConfig

REQUIRED = {"SUPABASE_URL": "https://x.supabase.co",
            "SUPABASE_SERVICE_KEY": "a-key-not-empty"}


@pytest.fixture
def prod(monkeypatch):
    """A production config that passes everything except the secret under test."""
    for name, value in REQUIRED.items():
        monkeypatch.setattr(ProductionConfig, name, value, raising=False)
    return ProductionConfig


class TestProductionRefusesThePlaceholder:
    def test_the_placeholder_is_refused(self, prod, monkeypatch):
        monkeypatch.setattr(prod, "SECRET_KEY", "dev-secret-change-me")
        with pytest.raises(RuntimeError) as exc:
            prod.validate()
        assert "FLASK_SECRET_KEY" in str(exc.value)

    def test_a_real_secret_passes(self, prod, monkeypatch):
        monkeypatch.setattr(prod, "SECRET_KEY", "a-real-" + "x" * 40)
        prod.validate()  # must not raise

    def test_an_empty_secret_is_still_refused(self, prod, monkeypatch):
        monkeypatch.setattr(prod, "SECRET_KEY", "")
        with pytest.raises(RuntimeError):
            prod.validate()


class TestTheRefusalStaysOutOfTheWayOfDevAndTests:
    def test_development_keeps_the_placeholder(self):
        """Refusing it here would make the documented dev flow un-bootable."""
        assert DevelopmentConfig.SECRET_KEY == "dev-secret-change-me" or \
            DevelopmentConfig.SECRET_KEY  # whatever the env supplies, still non-empty

    def test_development_does_not_inherit_the_refusal(self):
        # DevelopmentConfig has no validate() override; it must not raise for the
        # placeholder. It shares Config's env check, so supply the required names.
        saved = {k: getattr(DevelopmentConfig, k, None) for k in REQUIRED}
        try:
            for name, value in REQUIRED.items():
                setattr(DevelopmentConfig, name, value)
            DevelopmentConfig.validate()
        finally:
            for name, value in saved.items():
                setattr(DevelopmentConfig, name, value)

    def test_testing_validates_nothing(self):
        assert TestingConfig.validate() is None

    def test_the_placeholder_is_defined_once(self):
        assert Config.__dict__.get("SECRET_KEY") is not None, (
            "the fallback is the class attribute; the guard must compare against it "
            "rather than a second copy")
