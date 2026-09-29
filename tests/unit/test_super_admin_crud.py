"""What the super-admin pages do when a write is refused, and who the door lets in.

Three unrelated defects, each measured on the running box before this file existed:

1. **``/super-admin/plans`` showed the operator a Postgres payload.** Reported twice
   as "Tidak bisa CRUD". ``subscription_plans`` is named by
   ``school_subscriptions.plan_id`` and ``payment_transactions.plan_id`` (migration
   012, both ``INTEGER REFERENCES`` with no ``ON DELETE``), so ``delete`` raised and
   the page printed ``Gagal: {'code': '23503', 'details': 'Key (id)=(7) is still
   referenced...'}``. Plan 7 was held by two *pending* payment transactions, and
   nothing on the page said so, or offered the way out that exists — deactivating the
   plan, which is what "stop offering this" actually means.

2. **``/super-admin/midtrans`` saved and never said so.** ``midtrans_settings.html``
   — like ``feature_flags.html`` — carries no ``get_flashed_messages`` loop, so the
   success message the route flashes is never rendered; and because Flask only *pops*
   a flash when a page renders one, the message then appeared on whatever page the
   operator opened next. Observed live: saving Midtrans settings, and being told
   "Pengaturan Midtrans berhasil disimpan" on **Email Settings**.

3. **The super-admin door was hand-rolled and behaved unlike every other door.**
   ``_sa_required`` redirected a signed-in non-super-admin to ``/auth/login`` — a
   login page they are already past, and a dead end — while ``role_required`` (which
   every other role's door uses) sends them to their own home and answers an API
   caller with JSON. Measured against the live box: five other roles, five
   ``302 -> /auth/login``, where every other role's pages answered ``->dashboard``.

What is asserted here, and why each assertion is there:

* the refusal **names the counts** rather than a Postgres code, because a number is
  what makes the next decision possible, and it **names the way out**;
* a confirmed delete **detaches before it deletes**, and the plan row of a refused
  delete is still there;
* a count that could not be *taken* is not treated as a count of zero — the
  distinction ``delete_class`` already makes, because "we could not tell" and
  "nobody holds it" are different answers;
* every super-admin page that flashes can **render** a flash, checked across the
  whole blueprint so the next page that adds a save and forgets the block fails here
  instead of in front of an operator;
* the door is the shared one: each role lands on its own dashboard, an API caller
  gets JSON, and the legacy ``admin`` name is still admitted.
"""
from __future__ import annotations

import contextlib
import re
from pathlib import Path

import pytest

from tests.conftest import app_instance

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
SUPER = ROOT / "app" / "routes" / "super_admin.py"
PLANS_TEMPLATE = TEMPLATES / "super_admin" / "subscription_plans.html"
FLASH_CALL = "get_flashed_messages"

from app.services import subscription_plans as sp  # noqa: E402


# ── a supabase stand-in whose tables are named, not guessed at ───────────────

class _FkError(Exception):
    """The driver's foreign-key refusal, in the two shapes it arrives in.

    supabase-py raises an error carrying ``code``/``details``/``message``, and its
    string form is the payload dict — which is what the page printed, truncated at 60
    characters, so the *table* it named never survived to the operator.
    """

    def __init__(self, table: str = "invoices", code: str = "23503"):
        self.code = code
        self.details = f'Key (id)=(7) is still referenced from table "{table}".'
        self.message = ('update or delete on table "subscription_plans" violates '
                        f'foreign key constraint "{table}_plan_id_fkey" on table '
                        f'"{table}"')
        super().__init__({"code": code, "details": self.details,
                          "message": self.message})


class _Res:
    def __init__(self, data, count=0):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, fake, table):
        self.fake = fake
        self.table = table
        self._eq = []
        self._count = None
        self._op = None

    def select(self, *cols, **kw):
        self._count = kw.get("count")
        return self

    def eq(self, col, val):
        self._eq.append((col, val))
        return self

    def in_(self, col, vals):
        self._eq.append((col, list(vals)))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def update(self, patch):
        self.fake.writes.append(("update", self.table, dict(patch)))
        self._op = "update"
        return self

    def delete(self):
        self.fake.writes.append(("delete", self.table, None))
        self._op = "delete"
        return self

    def execute(self):
        if self._op and self.fake.fail_writes.get(self.table) == self._op:
            raise _FkError(self.fake.fail_writes_table or "invoices")
        if self.table in self.fake.fail:
            raise RuntimeError("connection reset while counting")
        rows = list(self.fake.tables.get(self.table, []))
        for col, val in self._eq:
            if isinstance(val, list):
                rows = [r for r in rows if r.get(col) in val]
            else:
                rows = [r for r in rows if r.get(col) == val]
        if self._count == "exact":
            return _Res(rows, count=len(rows))
        return _Res(rows)


class _Fake:
    def __init__(self, tables=None, fail=None, fail_writes=None, fail_writes_table=None):
        self.tables = tables or {}
        self.fail = set(fail or ())
        #: ``{table: "delete"|"update"}`` — a write the database refuses, so the
        #: sentence built for that refusal can be read through the route itself.
        self.fail_writes = dict(fail_writes or {})
        self.fail_writes_table = fail_writes_table
        self.writes: list[tuple] = []
        self.asked: list[str] = []

    def table(self, name):
        self.asked.append(name)
        return _Query(self, name)


def _flashed():
    """The flashes written during the request just made, in order.

    ``flash()`` queues into the session and the *next* rendered page pops them;
    reading them inside the same request context is therefore the only way to see
    what the operator would have been told — which is exactly the point, since a
    page that never renders them is defect number 2.
    """
    from flask import get_flashed_messages
    return [msg for _category, msg in get_flashed_messages(with_categories=True)]


@contextlib.contextmanager
def _post(path, data=None):
    with app_instance().test_request_context(path, method="POST", data=data or {}):
        yield


def _body(name):
    """The route body, with the role door peeled.

    Signing in would drag the token, the cookie and the idle clock into a question
    about what a *refused delete* says — the same trade
    ``test_crud_button_wiring.py`` makes for the API routes.
    """
    from app.routes import super_admin as mod
    return getattr(mod, name).__wrapped__


def _route_fake(monkeypatch, fake):
    from app.routes import super_admin as mod
    monkeypatch.setattr(mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(mod, "log_activity", lambda *a, **k: None)
    return mod


# ── 1. the counts, and the sentence built out of them ────────────────────────

#: Every place the repository declares a foreign key to `subscription_plans`, read out
#: of the SQL: the defect this pins was a constant with one fewer entry than the
#: schema, so a plan held only by an invoice read as free and the delete raised.
_CREATES = re.compile(r"\bCREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+"
                      r"([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?)", re.I)
_PLAN_FK = re.compile(r"plan_id\s+\S+\s+REFERENCES\s+subscription_plans", re.I)


def _sql_files() -> list[Path]:
    directory = ROOT / "supabase"
    return (sorted(directory.glob("*.sql"))
            + sorted((directory / "migrations").glob("*.sql")))


def _referencing_tables() -> set[str]:
    """Every table the repository declares a plan foreign key on, by CREATE TABLE."""
    found: set[str] = set()
    for path in _sql_files():
        table = None
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            declared = _CREATES.search(line)
            if declared:
                table = declared.group(1).split(".")[-1]
            if table and _PLAN_FK.search(line):
                found.add(table)
    return found


def _declared_references() -> int:
    """How many plan foreign keys the SQL declares at all — attributed or not."""
    return sum(len(_PLAN_FK.findall(path.read_text(encoding="utf-8", errors="replace")))
               for path in _sql_files())


class TestTheHolderListCannotDrift:
    """`invoices` declares the same foreign key as the two tables the list already
    held, and it was not in the list — so a plan an invoice still names was treated as
    free: the route skipped the detach, the delete raised, and the operator got the
    payload back. The list is pinned to the schema now, not to a comment."""

    def test_the_scan_finds_the_references_the_repository_declares(self):
        declared = _referencing_tables()
        assert declared >= {"school_subscriptions", "payment_transactions",
                            "invoices"}, declared

    def test_every_declared_reference_is_attributed_to_a_table(self):
        """A reference written somewhere this scan does not read (an `ALTER TABLE`, a
        file in another directory) would otherwise be silently missing from the list
        this test exists to protect."""
        assert _declared_references() == sum(
            len(_PLAN_FK.findall(path.read_text(encoding="utf-8", errors="replace")))
            for path in _sql_files()), "the scan read a different number of references"
        assert _declared_references() >= 3, (
            "the scan found fewer references than the schema declares, so it is "
            "reading the wrong thing")

    def test_the_holder_list_covers_them_all(self):
        assert {table for table, _key in sp.PLAN_HOLDERS} == _referencing_tables(), (
            "a table declares a plan foreign key and the delete does not know about "
            "it, which is exactly how the delete raised on a plan nothing "
            "'held'")

    def test_every_kind_of_holder_has_a_label(self):
        """The sentence is built from these keys, so a holder without a label is a
        number with no noun."""
        assert {key for _table, key in sp.PLAN_HOLDERS} <= set(sp.HOLDER_LABELS), (
            sp.HOLDER_LABELS)


class TestWhatHoldsAPlan:
    def test_it_counts_every_table_that_names_a_plan(self):
        """Migration 012 declares two of the three references. A guard that watched
        only some of them refuses on a subscription and happily deletes a plan the
        ledger still names — and the FK then raises the payload this change removes."""
        fake = _Fake({"school_subscriptions": [{"id": 1, "plan_id": 7}],
                      "payment_transactions": [{"id": 20, "plan_id": 7},
                                               {"id": 21, "plan_id": 7}],
                      "invoices": [{"id": 30, "plan_id": 7}]})
        usage = sp.plan_usage(fake, 7)
        assert usage["subscription"] == 1, usage
        assert usage["payment"] == 2, usage
        assert usage["invoice"] == 1, usage
        assert usage["measured"] is True

    def test_a_plan_held_only_by_an_invoice_is_not_free(self):
        fake = _Fake({"school_subscriptions": [], "payment_transactions": [],
                      "invoices": [{"id": 30, "plan_id": 7}]})
        usage = sp.plan_usage(fake, 7)
        assert sp.used_total(usage) == 1, usage
        assert sp.usage_confirmation_needed(usage) is True, usage

    def test_the_sentence_names_an_invoice_holder(self):
        message = sp.usage_message({"subscription": 0, "payment": 0, "invoice": 1,
                                    "measured": True})
        assert "1" in message, message
        low = message.lower()
        assert "invoice" in low or "faktur" in low, message

    def test_a_read_that_failed_is_not_a_count_of_zero(self):
        fake = _Fake({"school_subscriptions": [{"id": 1, "plan_id": 7}],
                      "payment_transactions": []},
                     fail=["school_subscriptions"])
        assert sp.plan_usage(fake, 7)["measured"] is False

    def test_the_count_is_scoped_to_this_plan(self):
        fake = _Fake({"school_subscriptions": [{"id": 1, "plan_id": 7},
                                               {"id": 2, "plan_id": 8}],
                      "payment_transactions": []})
        assert sp.plan_usage(fake, 7)["subscription"] == 1

    @pytest.mark.parametrize("usage,needed", [
        ({"subscription": 0, "payment": 0, "measured": True}, False),
        ({"subscription": 1, "payment": 0, "measured": True}, True),
        ({"subscription": 0, "payment": 2, "measured": True}, True),
        ({"subscription": 0, "payment": 0, "measured": False}, True),
    ])
    def test_confirmation_is_needed_exactly_when_something_would_be_detached(
            self, usage, needed):
        assert sp.usage_confirmation_needed(usage) is needed

    def test_the_sentence_carries_the_numbers(self):
        message = sp.usage_message({"subscription": 1, "payment": 2,
                                    "measured": True})
        assert "1" in message and "2" in message, message
        low = message.lower()
        assert "langganan" in low or "subscription" in low
        assert "transaksi" in low or "payment" in low

    def test_the_sentence_never_quotes_a_postgres_code(self):
        """The defect in one assertion: `23503` is not something an operator can act
        on, and it was the entire message."""
        message = sp.usage_message({"subscription": 1, "payment": 2,
                                    "measured": True})
        assert "23503" not in message
        assert "'code'" not in message and "code" not in message.lower()

    def test_the_sentence_names_the_way_out(self):
        """Deactivating is what "stop offering this" means, and it is the one action
        that does not touch the ledger."""
        message = sp.usage_message({"subscription": 1, "payment": 2,
                                    "measured": True})
        assert "nonaktif" in message.lower() or "deactivat" in message.lower(), message

    def test_an_unmeasured_plan_gets_its_own_sentence(self):
        message = sp.usage_message({"subscription": 0, "payment": 0,
                                    "measured": False})
        assert "23503" not in message
        low = message.lower()
        assert "tidak bisa" in low or "could not" in low, message


class TestARefusalThatStillHappens:
    """The list can be right and the delete can still be refused — a reference this
    repository does not declare, a constraint added on the box. When that happens the
    operator gets one sentence naming the table, not the payload truncated at 60
    characters, which is what the reported failure actually was."""

    def test_a_foreign_key_refusal_names_the_table(self):
        message = sp.plan_failure_sentence(_FkError(table="invoices"))
        assert "invoices" in message, message
        assert "23503" not in message, message
        assert "'code'" not in message and "code" not in message.lower(), message

    def test_it_names_a_table_it_was_not_told_about(self):
        """The whole point: the table is read out of the refusal, so a reference the
        code does not know is still named instead of becoming a mystery."""
        message = sp.plan_failure_sentence(_FkError(table="some_future_table"))
        assert "some_future_table" in message, message

    def test_an_error_with_no_payload_still_reads_as_a_sentence(self):
        message = sp.plan_failure_sentence(RuntimeError("connection reset"))
        assert "23503" not in message
        assert "{'code'" not in message, message
        low = message.lower()
        assert "gagal" in low or "failed" in low or "coba" in low or "try" in low, message

    def test_a_payload_string_never_leaks_through_the_fallback(self):
        """A driver that raises with only the payload as its text must not turn into
        the message: that is the defect being removed, one table over."""
        exc = Exception(str(_FkError(table="invoices")))
        message = sp.plan_failure_sentence(exc)
        assert "{'code'" not in message, message
        assert "invoices" in message, message


# ── 2. the page shows what a held plan costs, before you click ───────────────

def _plans_page(usage_per_plan):
    """Render the plans page the way the route does — for a *signed-in* reader.

    `base.html` picks one of two layouts on `g.user_id`, and the anonymous layout
    renders none of the role pages' content: rendering into the wrong one yields a
    whole page with its body silently thrown away, which is how a template guard
    passes against a page that shows nothing at all.
    """
    from flask import g
    app = app_instance()
    plans = [{"id": pid, "name": f"Paket {pid}", "duration_label": "1 bulan",
              "duration_days": 30, "price": 50000, "is_active": True,
              "sort_order": pid}
             for pid in sorted(usage_per_plan)]
    with app.test_request_context("/super-admin/plans"):
        g.user_id = "sa-1"
        g.user_role = "super_admin"
        g.user_name = "Super Admin"
        g.user_email = "sa@example.test"
        g.tz_offset = 7
        g.show = {}
        return app.jinja_env.get_template("super_admin/subscription_plans.html").render(
            plans=plans, usage=usage_per_plan, plan_held=sp.used_total)


class TestThePageShowsTheCost:
    def test_a_held_plan_says_how_many_rows_hold_it(self):
        html = _plans_page({7: {"subscription": 1, "payment": 2, "measured": True},
                            8: {"subscription": 0, "payment": 0, "measured": True}})
        assert "data-plan-held" in html, (
            "the page does not mark a plan something still holds")
        assert re.search(r"data-plan-held=\"3\"", html), (
            "the mark does not total what holds the plan (1 + 2)")

    def test_the_card_counts_every_kind_of_holder(self):
        """The card is where the operator looks *before* pressing the trash, so a
        total summed by hand here is the same omission as a holder list one entry
        short — the third table invisible exactly where the decision is made."""
        html = _plans_page({7: {"subscription": 1, "payment": 2, "invoice": 4,
                                "measured": True}})
        assert re.search(r'data-plan-held="7"', html), (
            "the card's total omits a holder the refusal counts (1 + 2 + 4)")

    def test_a_free_plan_is_not_dressed_up_as_a_problem(self):
        html = _plans_page({8: {"subscription": 0, "payment": 0,
                                "measured": True}})
        assert "data-plan-held" not in html, (
            "a plan nothing references is marked as held")

    def test_the_page_offers_deactivating(self):
        html = _plans_page({7: {"subscription": 1, "payment": 2,
                                "measured": True}})
        assert 'value="deactivate"' in html, (
            "the page offers no way to stop offering a held plan")


# ── 3. the route: a refusal you can act on ──────────────────────────────────

class TestDeletingAPlan:
    def test_a_held_plan_is_refused_in_words_instead_of_a_postgres_payload(
            self, monkeypatch):
        fake = _Fake({"school_subscriptions": [{"id": 1, "plan_id": 7}],
                      "payment_transactions": [{"id": 20, "plan_id": 7},
                                               {"id": 21, "plan_id": 7}]})
        _route_fake(monkeypatch, fake)

        with _post("/super-admin/plans/7/delete"):
            _body("plan_delete")(7)
            flashed = _flashed()

        assert flashed, "the operator is told nothing at all"
        joined = " ".join(flashed)
        assert "23503" not in joined, f"the Postgres payload is still the message: {joined}"
        assert "1" in joined and "2" in joined, (
            f"the refusal does not say how many rows hold it: {joined}")

    def test_a_refused_delete_deletes_nothing(self, monkeypatch):
        fake = _Fake({"school_subscriptions": [{"id": 1, "plan_id": 7}],
                      "payment_transactions": []})
        _route_fake(monkeypatch, fake)

        with _post("/super-admin/plans/7/delete"):
            _body("plan_delete")(7)
            _flashed()

        kinds = [(kind, table) for kind, table, _ in fake.writes]
        assert ("delete", "subscription_plans") not in kinds, kinds

    def test_a_count_that_could_not_be_taken_refuses_rather_than_deletes(
            self, monkeypatch):
        """A read that failed is not a count of zero. The plan the ledger names must
        survive a dropped connection, which is what `delete_class` already decided."""
        fake = _Fake({"school_subscriptions": [{"id": 1, "plan_id": 7}],
                      "payment_transactions": []},
                     fail=["school_subscriptions"])
        _route_fake(monkeypatch, fake)

        with _post("/super-admin/plans/7/delete"):
            _body("plan_delete")(7)
            flashed = _flashed()

        assert flashed, "an unmeasurable delete said nothing"
        assert ("delete", "subscription_plans") not in [
            (kind, table) for kind, table, _ in fake.writes]

    def test_confirming_detaches_the_references_before_it_deletes(self, monkeypatch):
        """The FK has no ON DELETE, so the rows have to lose the pointer first. Order
        matters: a delete issued before the detach raises exactly the payload this
        change exists to remove."""
        fake = _Fake({"school_subscriptions": [{"id": 1, "plan_id": 7}],
                      "payment_transactions": [{"id": 20, "plan_id": 7}]})
        _route_fake(monkeypatch, fake)

        with _post("/super-admin/plans/7/delete", {"confirm": "1"}):
            _body("plan_delete")(7)
            flashed = _flashed()

        writes = fake.writes
        detaches = [(kind, table, payload) for kind, table, payload in writes
                    if kind == "update"]
        assert {t for _k, t, _p in detaches} == {table for table, _key in sp.PLAN_HOLDERS}, (
            detaches)
        assert all(p == {"plan_id": None} for _k, _t, p in detaches), detaches
        delete_at = [i for i, (k, t, _p) in enumerate(writes)
                     if (k, t) == ("delete", "subscription_plans")]
        assert delete_at, writes
        assert max(i for i, (k, _t, _p) in enumerate(writes) if k == "update") < delete_at[0], (
            "the delete was issued before the detach")
        assert flashed and "23503" not in " ".join(flashed)

    def test_an_unreferenced_plan_is_deleted_without_ceremony(self, monkeypatch):
        fake = _Fake({"school_subscriptions": [], "payment_transactions": []})
        _route_fake(monkeypatch, fake)

        with _post("/super-admin/plans/9/delete"):
            _body("plan_delete")(9)
            flashed = _flashed()

        kinds = [(kind, table) for kind, table, _ in fake.writes]
        assert ("delete", "subscription_plans") in kinds, kinds
        assert not [k for k in kinds if k[0] == "update"], (
            "there was nothing to detach, so nothing should have been rewritten")
        assert flashed, "a successful delete said nothing"

    def test_a_plan_held_only_by_an_invoice_is_not_deleted_as_unreferenced(
            self, monkeypatch):
        """The reported failure, reproduced: an invoice names the plan, the code does
        not ask about invoices, so nothing is detached, the delete is issued, and the
        database refuses it."""
        fake = _Fake({"school_subscriptions": [], "payment_transactions": [],
                      "invoices": [{"id": 30, "plan_id": 7}]})
        _route_fake(monkeypatch, fake)

        with _post("/super-admin/plans/7/delete"):
            _body("plan_delete")(7)
            flashed = _flashed()

        kinds = [(kind, table) for kind, table, _ in fake.writes]
        assert ("delete", "subscription_plans") not in kinds, kinds
        assert flashed, "the operator is told nothing at all"

    def test_confirming_detaches_the_invoice_pointer_too(self, monkeypatch):
        fake = _Fake({"school_subscriptions": [], "payment_transactions": [],
                      "invoices": [{"id": 30, "plan_id": 7}]})
        _route_fake(monkeypatch, fake)

        with _post("/super-admin/plans/7/delete", {"confirm": "1"}):
            _body("plan_delete")(7)
            _flashed()

        writes = fake.writes
        detached = [table for kind, table, payload in writes
                    if kind == "update" and payload == {"plan_id": None}]
        assert "invoices" in detached, writes
        delete_at = [i for i, (kind, table, _p) in enumerate(writes)
                     if (kind, table) == ("delete", "subscription_plans")]
        assert delete_at, writes
        assert max(i for i, (kind, _t, _p) in enumerate(writes)
                   if kind == "update") < delete_at[0], (
            "the delete was issued before the invoice pointer was detached")

    def test_a_refusal_the_list_cannot_see_names_the_table(self, monkeypatch):
        """A constraint the code does not know about (or one added on the box) still
        reaches the operator as a sentence naming where it is held."""
        fake = _Fake({"school_subscriptions": [], "payment_transactions": [],
                      "invoices": []},
                     fail_writes={"subscription_plans": "delete"},
                     fail_writes_table="some_future_table")
        _route_fake(monkeypatch, fake)

        with _post("/super-admin/plans/7/delete"):
            _body("plan_delete")(7)
            flashed = _flashed()

        joined = " ".join(flashed)
        assert "some_future_table" in joined, joined
        assert "23503" not in joined and "{'code'" not in joined, joined

    def test_deactivating_keeps_the_row_and_flips_the_flag(self, monkeypatch):
        fake = _Fake({"school_subscriptions": [{"id": 1, "plan_id": 7}],
                      "payment_transactions": [{"id": 20, "plan_id": 7}]})
        _route_fake(monkeypatch, fake)

        with _post("/super-admin/plans/7/delete", {"action": "deactivate"}):
            _body("plan_delete")(7)
            flashed = _flashed()

        kinds = [(kind, table) for kind, table, _ in fake.writes]
        assert ("update", "subscription_plans") in kinds, kinds
        assert ("delete", "subscription_plans") not in kinds, (
            "deactivating deleted the row")
        assert ("delete", "payment_transactions") not in kinds
        assert flashed, "deactivating said nothing"

    def test_the_list_route_measures_every_plan_in_two_round_trips(self, monkeypatch):
        """The page needs the counts for every card. One query per plan per table is
        14 round trips for a seven-plan catalogue, on a box where one is ~150 ms."""
        fake = _Fake({"subscription_plans": [
            {"id": 7, "name": "3 Tahun", "duration_label": "3 tahun",
             "duration_days": 1000, "price": 1, "is_active": True, "sort_order": 7}],
            "school_subscriptions": [],
            "payment_transactions": [{"id": 20, "plan_id": 7}]})
        mod = _route_fake(monkeypatch, fake)
        monkeypatch.setattr(mod, "render_template",
                            lambda tpl, **kw: (tpl, kw))

        with app_instance().test_request_context("/super-admin/plans"):
            out = mod.subscription_plans.__wrapped__()

        tpl, ctx = out
        assert tpl == "super_admin/subscription_plans.html"
        usage = ctx["usage"]
        assert usage[7]["payment"] == 1, usage
        assert ctx["plan_held"] is sp.used_total, (
            "the page is handed a different totaller than the one the refusal uses, "
            "so the count on the card can disagree with what the server enforces")
        holder_tables = {table for table, _key in sp.PLAN_HOLDERS}
        counted = [t for t in fake.asked if t in holder_tables]
        assert sorted(counted) == sorted(holder_tables), (
            f"the page did not ask each referencing table exactly once: {counted}")


# ── 4. a page that flashes must be able to show it ──────────────────────────

def _routes():
    """``[(full_pattern, source_of_the_route)]`` for the super-admin blueprint."""
    parts = re.split(r"\n(?=@super_bp\.route)", SUPER.read_text(encoding="utf-8"))
    out = []
    for part in parts:
        m = re.match(r'@super_bp\.route\("([^"]+)"', part)
        if m:
            out.append(("/super-admin" + m.group(1), part))
    return out


def _matches(pattern, path):
    rx = re.sub(r"<[^>]+>", r"[^/]+", pattern)
    return re.fullmatch(rx, path) is not None


def _templates_it_can_end_on(pattern, body, seen=None):
    """Every template a request to this route can be rendered with.

    Follows literal ``redirect("/super-admin/...")`` targets, because that is how
    these settings pages answer a save: the flash is written by the POST and popped
    by the GET it redirects to.
    """
    seen = seen if seen is not None else set()
    if pattern in seen:
        return set()
    seen.add(pattern)
    names = set(re.findall(r'render_template\(\s*"([^"]+)"', body))
    for target in re.findall(r'redirect\(\s*"([^"]+)"', body):
        for other, other_body in _routes():
            if _matches(other, target):
                names |= _templates_it_can_end_on(other, other_body, seen)
    return names


class TestASavedSettingCanBeSeen:
    def test_every_super_admin_page_that_flashes_can_render_it(self):
        """A flash nobody renders is not a message: Flask only pops it when a page
        asks, so it waits and then appears on an unrelated page later. Measured:
        the Midtrans save confirmation was rendered by Email Settings."""
        offenders = []
        for pattern, body in _routes():
            if "flash(" not in body:
                continue
            pages = _templates_it_can_end_on(pattern, body)
            if not pages:
                continue
            if not any(FLASH_CALL in (TEMPLATES / name).read_text(encoding="utf-8")
                       for name in pages):
                offenders.append(f"{pattern} -> {sorted(pages)}")
        assert not offenders, (
            "these routes flash and no page they can end on renders a flash, so the "
            "message is never shown and then leaks onto the next page opened:\n  "
            + "\n  ".join(offenders))

    def test_the_midtrans_page_renders_its_own_confirmation(self):
        html = (TEMPLATES / "super_admin" / "midtrans_settings.html").read_text(
            encoding="utf-8")
        assert FLASH_CALL in html, (
            "saving Midtrans settings flashes and the page never shows it")

    def test_the_feature_flag_page_renders_the_warning_it_flashes(self):
        html = (TEMPLATES / "super_admin" / "feature_flags.html").read_text(
            encoding="utf-8")
        assert FLASH_CALL in html, (
            "the missing-table warning is flashed and never shown")


# ── 5. the door is the shared one ───────────────────────────────────────────

@contextlib.contextmanager
def _sa_probe(monkeypatch, role):
    """A fresh app whose only role-guarded route is the super-admin door itself.

    A probe rather than a real page, for two reasons: whether the *page* works is a
    different question (and answered above), and every real super-admin route reads
    Supabase, so asking it who may enter would spend a minute per role waiting on a
    network the suite is meant to stay off. This is the same shape
    ``test_login_door.py`` uses for the door it owns.
    """
    from app.routes import super_admin as supermod
    from app.utils import auth as authmod
    from tests.conftest import build_app

    application = build_app("app.config.TestingConfig")
    application.config["RATELIMIT_ENABLED"] = False
    # The app validates CSRF for every non-GET request in a global hook that runs
    # *before* any decorator, so an unauthenticated POST would answer 403 from CSRF
    # and this test would be about the wrong guard entirely — which is how its first
    # version passed while measuring nothing. `LOAD_TEST` is the app's own bypass.
    monkeypatch.setenv("LOAD_TEST", "true")

    def _probe():
        return "admitted"

    application.add_url_rule("/_probe_super_admin", "_probe_super_admin",
                             supermod._sa_required(_probe))
    # A real API-shaped path on purpose: `/super-admin/api/...` is the case the door
    # got wrong, and a probe route named without an `api` segment would not exercise
    # the path rule at all.
    application.add_url_rule("/super-admin/api/_probe/suspend",
                             "_probe_super_api",
                             supermod._sa_required(_probe), methods=["POST"])

    monkeypatch.setattr(authmod, "_session_for", lambda token, _r=role: {
        "user_id": "u-1", "email": "u@x", "name": "U", "role": _r,
        "school_id": None, "class_id": None, "status": "active",
    })
    client = application.test_client()
    client.set_cookie("access_token", "tok")
    yield client


@pytest.mark.parametrize("role,home", [
    ("admin_sekolah", "/admin-sekolah/dashboard"),
    ("guru", "/teacher/dashboard"),
    ("murid", "/student/dashboard"),
    ("principal", "/principal/dashboard"),
    ("vice_principal", "/vice-principal/dashboard"),
])
def test_a_signed_in_non_super_admin_lands_on_its_own_home(monkeypatch, role, home):
    """Not the login page: they are already past it, and being sent there is a dead
    end. Every other role door in the app already answers this way."""
    with _sa_probe(monkeypatch, role) as client:
        resp = client.get("/_probe_super_admin")
    assert resp.status_code == 302, resp.status_code
    assert resp.headers["Location"] == home, (
        f"{role} was sent to {resp.headers['Location']} instead of {home}")


def test_the_legacy_admin_name_is_still_admitted(monkeypatch):
    """`ROLE_ALIASES` maps `admin` to `super_admin`, and every other admin route
    accepts it. The hand-rolled door compared the raw name and locked it out."""
    with _sa_probe(monkeypatch, "admin") as client:
        resp = client.get("/_probe_super_admin")
    assert resp.status_code == 200, (
        f"a legacy `admin` session is refused by the super-admin door: {resp.status_code}")


def test_the_super_admin_is_admitted(monkeypatch):
    with _sa_probe(monkeypatch, "super_admin") as client:
        resp = client.get("/_probe_super_admin")
    assert resp.status_code == 200
    assert resp.data == b"admitted"


def test_an_api_caller_gets_json_not_a_login_page(monkeypatch):
    """The page's own buttons do `.then(r => r.json())`. An HTML redirect makes that
    throw, and the button looks dead — the shape this repository has fixed twice."""
    with _sa_probe(monkeypatch, "guru") as client:
        resp = client.post("/super-admin/api/_probe/suspend",
                           headers={"Accept": "application/json"})
    assert resp.status_code == 403, resp.status_code
    assert resp.is_json and resp.get_json().get("error"), resp.data[:120]


def test_a_gui_caller_on_an_api_path_is_still_refused_but_not_redirected(monkeypatch):
    """The wider `_wants_json` is about the *path*, not only the Accept header, so
    that a browser `fetch` (which sends `Accept: */*`) is answered too."""
    with _sa_probe(monkeypatch, "guru") as client:
        resp = client.post("/super-admin/api/_probe/suspend")
    assert resp.status_code == 403, resp.status_code
    assert resp.is_json


def test_the_door_is_the_shared_one():
    """Source guard: the whole point of the change is that this decorator stops
    re-implementing the rule. A future edit that puts the hand-rolled redirect back
    still passes every behavioural test above on the paths they happen to cover."""
    source = SUPER.read_text(encoding="utf-8")
    body = source[source.index("def _sa_required"):]
    body = body[:body.index("\ndef ", 10)]
    assert 'redirect("/auth/login")' not in body, (
        "the super-admin door hard-codes the login page again instead of using "
        "the app's shared role door")
    assert "role_required" in body or "super_admin_required" in body, (
        "the door no longer delegates to the app's own role door")
