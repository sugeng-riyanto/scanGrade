"""The payment journey, driven without a gateway account.

Phase 0 measured production and found the money path had never once succeeded:
24 `pending` transactions, 5 `cash`, **0 `success`**, and no activation code ever
redeemed. The code for every leg existed; nothing had ever run it end to end.

This file makes the leg runnable, and pins it:

* **simulation is impossible in production.** `ProductionConfig.PAYMENT_SIMULATION`
  is `False` as a class attribute, so no `.env` on a live box can turn a fake
  payment on. Read from the class, not a running app — that is the property.
* **the notification is idempotent.** Midtrans retries until acknowledged; a second
  settlement of the same order must not retire the subscription just made, write a
  second invoice or mint a second code.
* **the journeys work.** A signed VA callback through `/webhook/midtrans` and a
  redemption through `/api/activation/redeem` each end with an active, correctly
  tiered subscription and exactly one invoice — proving Phase 1 (tier) and Phase 2
  (invoice) together.
* **the failures are honest**: an expired notification, a code that does not exist,
  a code already used, a code for another school.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from app.config import Config, ProductionConfig, TestingConfig
from app.services import midtrans_service as mid


# ── a small in-memory Supabase ───────────────────────────────────────────────
# Supports the exact chain the payment paths use: select/eq/order/limit/single,
# insert and update. Rows are mutated in place so a later read sees an earlier
# write, which is what makes the end-to-end assertions mean anything.

class _Query:
    def __init__(self, store, name):
        self.store, self.name = store, name
        self._filters = []
        self._op = None
        self._single = False

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def single(self):
        self._single = True
        return self

    def insert(self, row):
        self._op = ("insert", dict(row))
        return self

    def update(self, patch):
        self._op = ("update", dict(patch))
        return self

    def _match(self):
        rows = self.store.rows(self.name)
        for col, val in self._filters:
            rows = [r for r in rows if r.get(col) == val]
        return rows

    def execute(self):
        if self._op:
            kind, payload = self._op
            if kind == "insert":
                payload.setdefault("id", f"{self.name}-{len(self.store.rows(self.name)) + 1}")
                self.store.rows(self.name).append(payload)
                self.store.ops.append((self.name, "insert", dict(payload)))
                return SimpleNamespace(data=[payload], count=1)
            matched = self._match()
            for row in matched:
                row.update(payload)
            self.store.ops.append((self.name, "update", dict(payload)))
            return SimpleNamespace(data=matched, count=len(matched))
        matched = self._match()
        data = (matched[0] if matched else {}) if self._single else matched
        return SimpleNamespace(data=data, count=len(matched))


class _Store:
    def __init__(self, tables):
        self.tables = {name: [dict(r) for r in rows] for name, rows in tables.items()}
        self.ops = []

    def rows(self, name):
        return self.tables.setdefault(name, [])

    def table(self, name):
        return _Query(self, name)

    def inserts(self, name):
        return [p for (t, kind, p) in self.ops if t == name and kind == "insert"]


def _tables(**over):
    base = {
        "payment_transactions": [{
            "id": "TX-1", "order_id": "ORD-1", "school_id": "S1", "plan_id": 5,
            "gross_amount": 399000, "status": "pending", "payment_type": "bank_transfer",
            "activation_code": None,
        }],
        "subscription_plans": [{"id": 5, "name": "1 Tahun", "duration_days": 365}],
        "school_subscriptions": [],
        "schools": [{"id": "S1", "name": "SMA Satu", "status": "pending", "npsn": "123"}],
        "profiles": [],
        "invoices": [],
    }
    base.update(over)
    return base


# ── 1. simulation can never be on in production ──────────────────────────────

class TestSimulationIsPinnedOffInProduction:
    def test_the_production_class_forces_it_off(self):
        """The attribute, not a runtime read: `.env` cannot re-enable it."""
        assert ProductionConfig.PAYMENT_SIMULATION is False

    def test_the_other_environments_may_simulate(self):
        assert TestingConfig.PAYMENT_SIMULATION is True
        assert isinstance(Config.PAYMENT_SIMULATION, bool)


# ── 2. the notification handler ──────────────────────────────────────────────

def _post_webhook(app, body, server_key="test-server-key"):
    """Sign the callback the way Midtrans does and hand it to the real route."""
    order_id = body["order_id"]
    status_code = str(body.get("status_code", "200"))
    gross = str(body.get("gross_amount", "399000.00"))
    payload = f"{order_id}{status_code}{gross}{server_key}"
    body = {**body, "signature_key": hashlib.sha512(payload.encode()).hexdigest()}
    app.config["MIDTRANS_SERVER_KEY"] = server_key
    app.config["PAYMENT_SIMULATION"] = True
    with app.test_client() as client:
        return client.post("/webhook/midtrans", json=body)


def _settlement(**over):
    body = {
        "order_id": "ORD-1", "transaction_status": "settlement",
        "fraud_status": "accept", "payment_type": "bank_transfer",
        "status_code": "200", "gross_amount": "399000.00",
        "va_numbers": [{"bank": "bca", "va_number": "111"}],
    }
    body.update(over)
    return body


class TestTheCallbackActivatesAndBills:
    def test_a_settlement_leaves_one_activated_invoice(self, app, monkeypatch):
        store = _Store(_tables())
        app.extensions["supabase"] = store

        resp = _post_webhook(app, _settlement())

        assert resp.status_code == 200 and resp.get_json()["ok"] is True
        tx = store.rows("payment_transactions")[0]
        assert tx["status"] == "success"
        subs = store.rows("school_subscriptions")
        assert len(subs) == 1, "the settlement did not create exactly one subscription"
        assert subs[0]["status"] == "active"
        assert subs[0]["tier"] == "pro", (
            "a 365-day plan must resolve to the paid 'pro' tier, not the free trial")
        invoices = store.inserts("invoices")
        assert len(invoices) == 1, "the activation left no invoice (Phase 2 regression)"
        assert invoices[0]["amount"] == 399000
        assert invoices[0]["school_id"] == "S1"

    def test_the_activation_email_is_attempted(self, app, monkeypatch):
        """The one mail a school forwards to its finance office is actually sent."""
        store = _Store(_tables(profiles=[{
            "id": "P1", "full_name": "Admin", "phone": "bendahara@sekolah.id",
            "school_id": "S1", "role": "admin_sekolah",
        }]))
        app.extensions["supabase"] = store
        sent = []

        from app.services import smtp_settings
        monkeypatch.setattr(smtp_settings, "send",
                            lambda to, subject, body, **k: (sent.append(to), (True, None))[1])
        monkeypatch.setattr(mid, "generate_activation_code", lambda: "SG-TEST-0001")

        _post_webhook(app, _settlement())

        assert sent == ["bendahara@sekolah.id"], "no activation email was attempted"

    def test_an_expired_notification_does_not_activate(self, app):
        store = _Store(_tables())
        app.extensions["supabase"] = store

        _post_webhook(app, _settlement(transaction_status="expire"))

        assert store.rows("payment_transactions")[0]["status"] == "expired"
        assert store.rows("school_subscriptions") == [], (
            "an expired payment activated a subscription")

    def test_a_duplicate_settlement_is_ignored(self, app):
        """Midtrans retries; a second success must not re-activate or re-bill."""
        store = _Store(_tables(payment_transactions=[{
            **{k: v for k, v in _tables()["payment_transactions"][0].items()},
            "status": "success",  # already settled by the first delivery
        }]))
        app.extensions["supabase"] = store

        resp = _post_webhook(app, _settlement())

        assert resp.status_code == 200
        assert store.rows("school_subscriptions") == [], (
            "a duplicate settlement retired/duplicated the live subscription")
        assert store.inserts("invoices") == [], (
            "a duplicate settlement wrote a second invoice for one payment")

    def test_a_bad_signature_is_refused(self, app):
        store = _Store(_tables())
        app.extensions["supabase"] = store
        app.config["MIDTRANS_SERVER_KEY"] = "test-server-key"

        body = _settlement()
        body["signature_key"] = "0" * 128
        with app.test_client() as client:
            resp = client.post("/webhook/midtrans", json=body)

        assert resp.status_code == 403
        assert store.rows("school_subscriptions") == []


# ── 3. redemption through the API ────────────────────────────────────────────

def _as_admin(app, monkeypatch, school_id="S1"):
    from app.utils import auth as authmod
    monkeypatch.setattr(authmod, "_session_for", lambda token: {
        "user_id": "U1", "email": "admin@x", "name": "Admin", "role": "admin_sekolah",
        "school_id": school_id, "status": "active",
    })
    app.config["WTF_CSRF_ENABLED"] = False


def _redeem(app, code, school_id="S1", monkeypatch=None):
    from app.utils import auth as authmod
    from app.routes import api as apimod
    client = app.test_client()
    client.set_cookie("access_token", "tok")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "csrf"
    return client.post("/api/activation/redeem", json={"code": code},
                       headers={"X-CSRF-Token": "csrf"})


def _redeem_fixture(school_id="S1", **over):
    tx = {"id": "TX-1", "order_id": "ORD-1", "school_id": "S1", "plan_id": 5,
          "gross_amount": 399000, "status": "success", "payment_type": "bank_transfer",
          "activation_code": "SG-AAAA-BBBB-CCCC"}
    tables = _tables(payment_transactions=[tx])
    tables.update(over)
    return tables


class TestRedemption:
    def test_a_code_redeems_into_a_tiered_subscription_and_invoice(self, app, monkeypatch):
        store = _Store(_redeem_fixture())
        app.extensions["supabase"] = store
        _as_admin(app, monkeypatch)
        monkeypatch.setattr("app.routes.api.get_supabase", lambda: store)

        resp = _redeem(app, "SG-AAAA-BBBB-CCCC", monkeypatch=monkeypatch)

        assert resp.status_code == 200 and resp.get_json()["success"] is True
        subs = store.rows("school_subscriptions")
        assert len(subs) == 1 and subs[0]["tier"] == "pro"
        assert len(store.inserts("invoices")) == 1

    def test_a_code_that_does_not_exist_is_a_404(self, app, monkeypatch):
        store = _Store(_tables())
        app.extensions["supabase"] = store
        _as_admin(app, monkeypatch)
        monkeypatch.setattr("app.routes.api.get_supabase", lambda: store)

        resp = _redeem(app, "SG-NOPE-NOPE-NOPE", monkeypatch=monkeypatch)

        assert resp.status_code == 404
        assert store.rows("school_subscriptions") == []

    def test_a_code_already_used_is_refused(self, app, monkeypatch):
        store = _Store(_redeem_fixture(school_subscriptions=[{
            "id": "SUB-1", "school_id": "S1",
            "activation_code": "SG-AAAA-BBBB-CCCC",
        }]))
        app.extensions["supabase"] = store
        _as_admin(app, monkeypatch)
        monkeypatch.setattr("app.routes.api.get_supabase", lambda: store)

        resp = _redeem(app, "SG-AAAA-BBBB-CCCC", monkeypatch=monkeypatch)

        assert resp.status_code == 400
        assert "sudah" in resp.get_json()["message"].lower()

    def test_a_code_for_another_school_is_refused(self, app, monkeypatch):
        store = _Store(_redeem_fixture())
        app.extensions["supabase"] = store
        _as_admin(app, monkeypatch, school_id="S2")
        monkeypatch.setattr("app.routes.api.get_supabase", lambda: store)

        resp = _redeem(app, "SG-AAAA-BBBB-CCCC", monkeypatch=monkeypatch)

        assert resp.status_code == 403
        assert store.rows("school_subscriptions") == []
