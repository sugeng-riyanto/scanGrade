"""Activating a subscription must leave exactly one invoice behind.

Phase 0 found the receipt never written: `_activate_subscription` ends with
`return code`, and the `_generate_invoice(...)` call — plus the "subscription
activated" log line — sit *after* it, so neither has ever run. Production holds
three invoices, all of them demo fixtures with a made-up code; no real activation
has a bill. A school that paid cannot show its finance office what it paid for.

The fix is an ordering one, and the guards here are about the side effect, not the
return value: the invoice is written, it carries the fields a receipt needs, and
nothing rewrites the demo rows already on the box.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


class _Table:
    def __init__(self, fake, name):
        self.fake, self.name = fake, name
        self._filter = {}
        self._op = None
        self._single = False

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filter[col] = val
        return self

    def limit(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def single(self):
        self._single = True
        return self

    def insert(self, row):
        self._op = ("insert", row)
        return self

    def update(self, patch):
        self._op = ("update", patch)
        return self

    def execute(self):
        if self._op:
            kind, payload = self._op
            self.fake.ops.append((self.name, kind, payload))
            return SimpleNamespace(data=[payload], count=1)
        rows = [r for r in self.fake.tables.get(self.name, [])
                if all(r.get(k) == v for k, v in self._filter.items())]
        data = (rows[0] if rows else {}) if self._single else rows
        return SimpleNamespace(data=data, count=len(rows))


class _Fake:
    def __init__(self, tables=None):
        self.tables = tables or {}
        self.ops = []

    def table(self, name):
        return _Table(self, name)

    def inserts(self, name):
        return [p for (t, k, p) in self.ops if t == name and k == "insert"]

    def updates(self, name):
        return [p for (t, k, p) in self.ops if t == name and k == "update"]


def _activate(monkeypatch, fake, plan_id=5):
    from app.services import midtrans_service as mid

    monkeypatch.setattr(mid, "_load_plan",
                        lambda pid: {"id": pid, "name": "1 Tahun", "duration_days": 365})
    monkeypatch.setattr(mid, "generate_activation_code", lambda: "SG-TEST-0001")
    monkeypatch.setattr(mid, "invalidate_school_active", lambda *a, **k: None)
    monkeypatch.setattr(mid, "current_app", SimpleNamespace(
        extensions={"supabase_auth": None},
        logger=SimpleNamespace(info=lambda *a, **k: None,
                               warning=lambda *a, **k: None,
                               error=lambda *a, **k: None)))
    return mid._activate_subscription("S1", plan_id, "ORD-1", fake)


def _tables():
    return {
        "payment_transactions": [{"order_id": "ORD-1", "gross_amount": 399000,
                                  "payment_type": "bank_transfer", "status": "success",
                                  "activation_code": "SG-TEST-0001"}],
        "subscription_plans": [{"id": 5, "name": "1 Tahun", "duration_days": 365}],
        "profiles": [],  # no admin row -> the email path is skipped
        "invoices": [],
    }


class TestTheActivationWritesItsReceipt:
    def test_exactly_one_invoice_is_written(self, monkeypatch):
        fake = _Fake(_tables())
        _activate(monkeypatch, fake)
        invoices = fake.inserts("invoices")
        assert len(invoices) == 1, (
            "the activation wrote no invoice — `_generate_invoice` is still "
            "stranded behind `return code`")

    def test_the_invoice_carries_what_a_receipt_needs(self, monkeypatch):
        fake = _Fake(_tables())
        _activate(monkeypatch, fake)
        inv = fake.inserts("invoices")[0]
        for field in ("invoice_number", "school_id", "order_id", "amount",
                      "status", "period_start", "paid_at"):
            assert inv.get(field) not in (None, ""), f"invoice has no {field}"
        assert inv["school_id"] == "S1"
        assert inv["plan_id"] == 5
        assert inv["amount"] == 399000, (
            "the amount came from the transaction and is what the school paid")
        assert inv["status"] == "paid"

    def test_the_subscription_is_still_activated(self, monkeypatch):
        """Fixing the ordering must not cost the activation itself."""
        fake = _Fake(_tables())
        code = _activate(monkeypatch, fake)
        assert code == "SG-TEST-0001"
        subs = fake.inserts("school_subscriptions")
        assert len(subs) == 1 and subs[0]["status"] == "active"


class TestTheDemoRowsAreNotTouched:
    def test_no_invoice_is_updated_or_deleted(self, monkeypatch):
        fake = _Fake(_tables())
        _activate(monkeypatch, fake)
        assert fake.updates("invoices") == [], (
            "the activation rewrote existing invoices; the three demo rows on "
            "the box must be left exactly as they are")

    def test_the_write_path_does_not_route_through_a_hardcoded_duration(self):
        """The plan's own duration is what the period end is built from."""
        from app.services import midtrans_service as mid
        import inspect
        body = inspect.getsource(mid._activate_subscription)
        assert "duration_days" in body, (
            "the period must come from the plan the school bought")
