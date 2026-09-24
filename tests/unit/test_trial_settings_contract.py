"""One setting, one row, and three flows that have to agree about it.

Measured against the running code before this test existed
----------------------------------------------------------
``/super-admin/trial-settings`` stored a number that three flows needed:

* approving a school registration (the door nearly every school arrives through);
* a cash activation by the super admin;
* the trial sentence on a school's own subscription page.

Two of them read the table. The approval flow wrote ``timedelta(days=14)`` and
``"trial_days": 14`` as literals, so the page that exists to set the number had no
effect on a single trial it claims to configure. The page could not admit this
either: it rendered the built-in default exactly like a saved value, so *never
opened this page* and *set it to 14* were the same picture — and there was no way
to stop configuring it at all.

Reading the same row from two places reaches a second defect, which this file also
holds down: ``trial_settings`` carries no uniqueness, and a reader used
``.limit(1)`` with **no ordering**, so a second row means the settings page edits
one value while a grant applies another. The page would be honest and the system
still wrong — the failure mode that looks like nothing is broken.

What is asserted:

* the approval flow stores what the setting says **and** expires the trial by that
  same number, from one read — a row that says 30 days and ends in 14 is worse
  than either mistake alone;
* the cash-activation path and the school's own subscription page read the same
  value, so the number an admin is promised is the number granted;
* every read takes the *newest* row, so a second row cannot split the page from
  the grants;
* the guard really refuses a school admin, a teacher and a student — with the
  guard's own code executed, not merely grepped for;
* ``parse_days`` refuses what is not a number of days, and names the bounds, while
  a *missing* field is the default rather than an error;
* the page shows the effective value, says whether it is an override or the
  built-in default, offers the delete only when there is something to delete, and
  reads its own change history back out of the audit log.

The views are driven for real with ``g`` populated, against a fake that scopes
rows and honours ordering the way PostgREST does.
"""
import re
from pathlib import Path

import pytest
from flask import g, get_flashed_messages

from tests.conftest import app_instance
from app.routes import admin as admin_mod
from app.routes import admin_sekolah as admin_sekolah_mod
from app.routes import super_admin as sa_mod
from app.services import trial_settings as ts

ROOT = Path(__file__).resolve().parents[2]
APPROVAL_SRC = ROOT / "app" / "routes" / "admin.py"
SUPER_SRC = ROOT / "app" / "routes" / "super_admin.py"
SUBSCRIPTION_SRC = ROOT / "app" / "routes" / "admin_sekolah.py"
TEMPLATE = (ROOT / "app" / "templates" / "super_admin"
            / "trial_settings.html")
MIGRATION = ROOT / "supabase" / "migrations" / "034_trial_settings_singleton.sql"

SUPER_ID = "11111111-0000-0000-0000-00000000000a"
ADMIN_ID = "22222222-0000-0000-0000-00000000000a"
REQUEST_ID = "99999999-0000-0000-0000-00000000000a"
NEW_SCHOOL = "aaaaaaaa-0000-0000-0000-00000000000a"


# ── a fake that orders and scopes the way PostgREST does ────────────────────

class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, store, table):
        self.store = store
        self.table = table
        self.filters = []
        self.negated = []
        self.at_least = []
        self.op = "select"
        self.payload = None
        self._limit = None
        self._single = False
        #: Accumulated like the client's own `order()`, which *appends*: the
        #: second call is a tie-break, not a replacement. Keeping only the last
        #: one would drop the `nullslast` spec and let every ordering assertion
        #: pass against a sort by id.
        self._orders = []
        self.want_count = False

    def select(self, *_a, count=None, **_k):
        # PostgREST answers an exact count in the Content-Range header, so it
        # counts every matching row — before `limit` is applied.
        self.want_count = bool(count)
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def neq(self, col, val):
        self.negated.append((col, val))
        return self

    def gte(self, col, val):
        # Its own list, read as a *comparison*: the reset deletes with
        # `.gte("id", 0)`, and treating that as an equality would filter every
        # row away and make the test agree with a no-op.
        self.at_least.append((col, val))
        return self

    def order(self, col, desc=False, **_k):
        self._orders.append((col, desc))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def single(self):
        self._single = True
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def _rows(self, live=False, limited=True):
        rows = [r for r in self.store.rows_for(self.table)
                if all(str(r.get(c)) == str(v) for c, v in self.filters)
                and all(str(r.get(c)) != str(v) for c, v in self.negated)
                and all(_at_least(r.get(c), v) for c, v in self.at_least)]
        # PostgREST applies the order specs left to right; sorting stably in
        # reverse makes the *first* spec the primary key, like it does there.
        for col, desc in reversed(self._orders):
            rows = self._sorted(rows, col, desc)
        if limited and self._limit is not None:
            rows = rows[:self._limit]
        return rows if live else [dict(r) for r in rows]

    @staticmethod
    def _sorted(rows, col, desc):
        """One order spec, including the option list the client packs into the
        column name (`updated_at.desc.nullslast`) — parsed the same way PostgREST
        parses it, so the fake cannot pretend to sort by a column with a dot in
        its name and leave every ordering assertion green."""
        parts = col.split(".")
        name, opts = parts[0], parts[1:]
        descending = "desc" in opts or desc
        nulls_last = "nullslast" in opts
        present = [r for r in rows if r.get(name) not in (None, "")]
        missing = [r for r in rows if r.get(name) in (None, "")]
        present.sort(key=lambda r: r.get(name), reverse=bool(descending))
        return present + missing if nulls_last else missing + present

    def execute(self):
        if self.op == "insert":
            row = dict(self.payload)
            row.setdefault("id", self.store.next_id(self.table))
            self.store.rows_for(self.table).append(row)
            self.store.inserts.append((self.table, dict(self.payload)))
            return _Resp([row])
        if self.op == "update":
            rows = self._rows(live=True)
            for row in rows:
                row.update(self.payload)
            self.store.updates.append((self.table, dict(self.payload)))
            return _Resp([dict(r) for r in rows])
        if self.op == "delete":
            rows = self._rows(live=True)
            self.store.rows_for(self.table)[:] = [
                r for r in self.store.rows_for(self.table) if r not in rows]
            self.store.deletes.append((self.table, list(self.filters)))
            return _Resp([dict(r) for r in rows])
        # The exact count PostgREST puts in Content-Range counts every matching
        # row — `limit` narrows the page, never the count.
        total = len(self._rows(limited=False))
        rows = self._rows()
        if self._single:
            if not rows:
                raise RuntimeError("no rows in single() request")
            return _Resp(rows[0])
        return _Resp(rows, count=total if self.want_count else None)


class _BlindSupabase:
    """A connection that answers nothing, the way a dropped one does."""

    def table(self, _name):
        raise RuntimeError("Server disconnected")


def _at_least(value, floor):
    try:
        return float(value) >= float(floor)
    except (TypeError, ValueError):
        return False


class FakeSupabase:
    """A registration request waiting for approval, and a settings table."""

    def __init__(self, trial_rows=None):
        self.data = {
            "trial_settings": list(trial_rows) if trial_rows is not None else [],
            "school_registration_requests": [
                {"id": REQUEST_ID, "status": "pending", "school_name": "SMP Baru",
                 "npsn": "12345678", "profile_id": ADMIN_ID,
                 "requester_email": "a@b.c", "requester_phone": "0800"},
            ],
            "schools": [],
            "school_subscriptions": [],
            "profiles": [{"id": ADMIN_ID, "role": "admin_sekolah",
                          "school_id": None, "status": "pending",
                          "full_name": "Admin Baru"}],
        }
        self.inserts = []
        self.updates = []
        self.deletes = []
        self._seq = 1000

    def next_id(self, table):
        self._seq += 1
        return self._seq

    def table(self, name):
        return _Query(self, name)

    def rows_for(self, name):
        return self.data.setdefault(name, [])

    def writes_to(self, table):
        return [p for t, p in self.inserts if t == table]


# ── driving the real views ──────────────────────────────────────────────────

FLASHES: list = []


def peel(view):
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


def run(view, monkeypatch, fake, *, method="GET", data=None, role="super_admin",
        path="/super-admin/trial-settings", kwargs=None, peel_all=True,
        module=None):
    mod = module or sa_mod
    monkeypatch.setattr(mod, "get_supabase", lambda: fake)
    if hasattr(mod, "log_activity"):
        monkeypatch.setattr(mod, "log_activity", lambda *a, **k: None)
    app = app_instance()
    FLASHES.clear()
    with app.test_request_context(path, method=method, data=data or {}):
        g.user_id = SUPER_ID
        g.user_role = role
        g.user_school_id = None
        g.user_email = "sa@scan-grade.app"
        g.user_name = "Super Admin"
        g.tz_offset = 7
        fn = peel(view) if peel_all else view
        result = fn(**(kwargs or {}))
        FLASHES.extend(get_flashed_messages(with_categories=True))
        return result


def flashes():
    return list(FLASHES)


def body_of(result):
    resp = result[0] if isinstance(result, tuple) else result
    if isinstance(resp, str):
        return resp
    return resp.get_data(as_text=True)


def status_of(result):
    if isinstance(result, tuple):
        return result[1]
    return getattr(result, "status_code", 200)


# ── the setting reaches the door schools actually arrive through ────────────

class TestTheApprovalFlowGrantsWhatTheSettingSays:
    def _approve(self, monkeypatch, trial_rows):
        fake = FakeSupabase(trial_rows)
        monkeypatch.setattr(admin_mod, "notify_approval", lambda **k: None)
        run(admin_mod.approve_request, monkeypatch, fake, method="POST",
            path=f"/admin/registration-requests/{REQUEST_ID}/approve",
            module=admin_mod, kwargs={"request_id": REQUEST_ID},
            data={"duration": "1_month"})
        return fake.writes_to("school_subscriptions")

    def test_a_stored_override_is_what_is_granted(self, monkeypatch):
        written = self._approve(monkeypatch, [{"id": 7, "trial_days": 30}])
        assert written and written[0]["trial_days"] == 30, (
            "the approval flow granted a length the setting page never chose. "
            "It used to be a literal 14, which is how the page had no effect.")

    def test_the_expiry_is_computed_from_that_same_number(self, monkeypatch):
        """One read, two uses. A row claiming 30 days and ending in 14 is worse
        than either mistake on its own, because the page looks right."""
        written = self._approve(monkeypatch, [{"id": 7, "trial_days": 30}])
        row = written[0]
        from datetime import datetime
        start = datetime.fromisoformat(row["trial_start"])
        end = datetime.fromisoformat(row["trial_end"])
        assert (end - start).days == row["trial_days"] == 30

    def test_nothing_stored_grants_the_built_in_default(self, monkeypatch):
        written = self._approve(monkeypatch, [])
        assert written[0]["trial_days"] == ts.DEFAULT_TRIAL_DAYS

    def test_a_zero_setting_grants_no_trial(self, monkeypatch):
        """0 is a policy ("no free trial"), not a missing value: reading it as
        unset would silently hand out the 14 days the operator refused."""
        written = self._approve(monkeypatch, [{"id": 7, "trial_days": 0}])
        assert written[0]["trial_days"] == 0

    def test_no_literal_fourteen_survives_in_the_flow(self):
        source = APPROVAL_SRC.read_text(encoding="utf-8")
        body = source[source.index("def approve_request("):]
        body = body[:body.index("\n@admin_bp.route")]
        assert "days=14" not in body and '"trial_days": 14' not in body, (
            "a hard-coded trial length is back in the approval flow")


# ── the other two readers agree ─────────────────────────────────────────────

class TestTheOtherReadersAgreeWithIt:
    def test_the_cash_activation_reads_it(self, monkeypatch):
        source = SUPER_SRC.read_text(encoding="utf-8")
        body = source[source.index("def activate_cash("):]
        assert "trial_cfg.get_trial_days(" in body, (
            "the cash activation decides the trial length on its own again")
        assert "trial_cfg.days_until(" in body, (
            "the expiry is computed from a different number than the stored one")

    def test_the_school_s_subscription_page_reads_it(self, monkeypatch):
        fake = FakeSupabase([{"id": 7, "trial_days": 45}])
        # The route imports these inside the view, so the patch has to land on the
        # service module they are imported *from*, not on the route.
        from app.services import midtrans_service as midtrans
        monkeypatch.setattr(midtrans, "get_school_subscription", lambda _s: None)
        monkeypatch.setattr(midtrans, "get_pricing_config",
                            lambda: {"model": "flat", "tiers": []})
        monkeypatch.setattr(midtrans, "get_student_count_for_school",
                            lambda _s: 0)
        monkeypatch.setattr(midtrans, "get_payment_fee_config",
                            lambda: {"fee_flat": 0, "fee_percent": 0, "fee_note": ""})
        captured = {}
        monkeypatch.setattr(admin_sekolah_mod, "render_template",
                            lambda _t, **ctx: captured.update(ctx) or "rendered")
        monkeypatch.setattr(admin_sekolah_mod, "get_supabase", lambda: fake)
        app = app_instance()
        with app.test_request_context("/admin-sekolah/subscription"):
            g.user_id = ADMIN_ID
            g.user_role = "admin_sekolah"
            g.user_school_id = NEW_SCHOOL
            g.user_email = "admin@scan-grade.app"
            g.user_name = "Admin"
            g.tz_offset = 7
            peel(admin_sekolah_mod.subscription)()
        assert captured.get("trial_days") == 45, (
            "the page a school admin reads promised a different trial than the "
            "one the setting would grant")


# ── one row, and the newest one, so page and grants cannot disagree ─────────

class TestEveryReaderTakesTheSameRow:
    def test_a_second_row_cannot_split_the_page_from_the_grant(self):
        """The mechanism the whole module exists to make impossible."""
        fake = FakeSupabase([{"id": 3, "trial_days": 30},
                             {"id": 9, "trial_days": 60}])
        assert ts.get_trial_days(fake) == 60, (
            "an unordered `.limit(1)` returns whichever row Postgres feels like")

    def test_the_page_describes_the_row_a_grant_would_use(self):
        fake = FakeSupabase([{"id": 3, "trial_days": 30},
                             {"id": 9, "trial_days": 60}])
        assert ts.describe(fake)["days"] == ts.get_trial_days(fake)

    def test_the_setting_that_was_made_last_wins_not_the_newest_row(self):
        """The live table, reproduced. On production `trial_settings` held fifteen
        rows: fourteen June seeds (`14`) and a `30` an operator set on 22
        September, which carried the *lowest* ids. Ordering by id — the obvious
        choice — kept a seed and granted 14 days while the page showed 30."""
        live = FakeSupabase([
            {"id": 1, "trial_days": 30, "updated_at": "2026-09-22T23:30:54+00:00"},
            {"id": 2, "trial_days": 30, "updated_at": "2026-09-22T23:31:21+00:00"},
            {"id": 3, "trial_days": 14, "updated_at": "2026-06-06T16:35:06+00:00"},
            {"id": 16, "trial_days": 14, "updated_at": "2026-06-16T00:25:49+00:00"},
        ])
        assert ts.get_trial_days(live) == 30, (
            "the row a human wrote last is not the row a grant reads")

    def test_a_row_without_a_timestamp_is_not_the_last_change(self):
        """Postgres sorts NULLs *first* under DESC, so an unqualified order would
        put a row with no timestamp ahead of every real one."""
        fake = FakeSupabase([
            {"id": 9, "trial_days": 99, "updated_at": None},
            {"id": 3, "trial_days": 30, "updated_at": "2026-09-22T23:30:54+00:00"},
        ])
        assert ts.get_trial_days(fake) == 30

    def test_the_table_says_how_many_other_rows_it_holds(self):
        """Reported rather than repaired: the setting can be right on this page and
        still be one of fifteen rows, and that is worth an operator knowing."""
        assert ts.describe(FakeSupabase([{"id": 1, "trial_days": 14},
                                         {"id": 2, "trial_days": 14},
                                         {"id": 3, "trial_days": 30}]))["duplicates"] == 2
        assert ts.describe(FakeSupabase([{"id": 1, "trial_days": 14}]))["duplicates"] == 0
        assert ts.describe(FakeSupabase([]))["duplicates"] == 0

    def test_the_duplicate_count_reports_nothing_when_it_cannot_be_read(self):
        """A count that cannot be taken is not a count of zero — but it is also not
        an error the settings page should die on."""
        assert ts.duplicate_rows(_BlindSupabase()) == 0

    def test_a_row_outside_the_bounds_is_read_as_unset(self):
        """A hand-edited row is not a policy anybody meant to set, and refusing
        it loudly would take the registration flow down with it."""
        assert ts.get_trial_days(FakeSupabase([{"id": 1, "trial_days": -5}])) == 14
        assert ts.get_trial_days(FakeSupabase([{"id": 1, "trial_days": 99999}])) == 14


# ── the numbers a stranger may post ────────────────────────────────────────

class TestWhatTheFieldAccepts:
    @pytest.mark.parametrize("raw,expected", [("30", 30), (30, 30), (" 30 ", 30),
                                              ("0", 0), ("365", 365)])
    def test_a_plain_number_of_days_is_accepted(self, raw, expected):
        assert ts.parse_days(raw) == expected

    def test_a_missing_field_is_the_default_not_an_error(self):
        assert ts.parse_days(None) == ts.DEFAULT_TRIAL_DAYS
        assert ts.parse_days("") == ts.DEFAULT_TRIAL_DAYS

    @pytest.mark.parametrize("raw", ["abc", "-1", "366", "14.5", "1e3", True])
    def test_anything_else_is_refused(self, raw):
        with pytest.raises(ValueError):
            ts.parse_days(raw)


# ── read / update / delete, behind the role guard ──────────────────────────

class TestThePageIsARealSettingsPage:
    def test_it_prints_the_effective_value_and_its_source(self, monkeypatch):
        result = run(sa_mod.trial_settings, monkeypatch,
                     FakeSupabase([{"id": 7, "trial_days": 30,
                                    "updated_by": SUPER_ID,
                                    "updated_at": "2026-09-01T10:00:00+00:00"}]))
        html = body_of(result)
        assert ">30<" in html or "30<" in html, "the page does not print the value"
        assert 'isOverride: true' in html, (
            "the page cannot tell an override from the built-in default, which is "
            "what made a reset indistinguishable from a save")
        assert "2026-09-01T10:00:00" in html, "no record of when it was set"

    def test_it_says_when_nothing_is_stored(self, monkeypatch):
        html = body_of(run(sa_mod.trial_settings, monkeypatch, FakeSupabase([])))
        assert 'isOverride: false' in html
        assert str(ts.DEFAULT_TRIAL_DAYS) in html, (
            "the reader cannot see what applies instead of the missing override")

    def test_a_save_creates_the_row_and_records_who_did_it(self, monkeypatch):
        fake = FakeSupabase([])
        run(sa_mod.trial_settings, monkeypatch, fake, method="POST",
            data={"trial_days": "21"})
        rows = fake.data["trial_settings"]
        assert rows and rows[0]["trial_days"] == 21
        assert rows[0]["updated_by"] == SUPER_ID
        assert any("21" in m for _c, m in flashes())

    def test_a_save_updates_the_existing_row_rather_than_adding_one(
            self, monkeypatch):
        fake = FakeSupabase([{"id": 7, "trial_days": 30}])
        run(sa_mod.trial_settings, monkeypatch, fake, method="POST",
            data={"trial_days": "21"})
        assert len(fake.data["trial_settings"]) == 1, (
            "saving added a second row, which is what makes two readers disagree")
        assert fake.data["trial_settings"][0]["trial_days"] == 21

    def test_a_bad_value_is_refused_with_the_bounds_named(self, monkeypatch):
        fake = FakeSupabase([{"id": 7, "trial_days": 30}])
        run(sa_mod.trial_settings, monkeypatch, fake, method="POST",
            data={"trial_days": "abc"})
        assert fake.data["trial_settings"][0]["trial_days"] == 30, (
            "a refused value still reached the table")
        messages = " ".join(m for _c, m in flashes())
        assert str(ts.MIN_TRIAL_DAYS) in messages and str(ts.MAX_TRIAL_DAYS) in messages

    def test_the_delete_removes_every_row_and_falls_back(self, monkeypatch):
        # Two rows on purpose: deleting only the newest would look like a
        # complete reset of a one-row table (§"the table still looks
        # configured") while leaving the very rows a second reader could pick up.
        fake = FakeSupabase([{"id": 3, "trial_days": 30},
                             {"id": 7, "trial_days": 60}])
        run(sa_mod.trial_settings, monkeypatch, fake, method="POST",
            data={"action": "reset"})
        assert fake.data["trial_settings"] == [], (
            "a reset left rows behind, so the reader still ignores them and the "
            "table still looks configured")
        assert ts.get_trial_days(fake) == ts.DEFAULT_TRIAL_DAYS

    def test_the_page_offers_the_delete_only_when_there_is_one(self):
        """Scanned over the reset card's own region, not the page: `x-show` appears
        elsewhere on this page, so a whole-file search finds a guard that belongs
        to a different element and passes while this one is unguarded."""
        source = TEMPLATE.read_text(encoding="utf-8")
        start = source.index("{# \u2500\u2500 delete")
        region = source[start:source.index('name="action" value="reset"')]
        assert 'x-show="isOverride"' in region, (
            "the delete is offered with nothing to delete")

    def test_the_page_says_when_the_table_holds_extra_rows(self, monkeypatch):
        """The state that produced the live mismatch: the number in force is right
        and the table is still carrying fourteen seeds. The page has to say so,
        because from every other screen that state looks like nothing at all."""
        fake = FakeSupabase([{"id": 1, "trial_days": 30,
                              "updated_at": "2026-09-22T23:30:54+00:00"},
                             {"id": 2, "trial_days": 14,
                              "updated_at": "2026-06-06T16:35:06+00:00"},
                             {"id": 3, "trial_days": 14,
                              "updated_at": "2026-06-07T03:20:40+00:00"}])
        html = body_of(run(sa_mod.trial_settings, monkeypatch, fake))
        assert "034_trial_settings_singleton.sql" in html, (
            "three rows and no word about it: the operator cannot tell this table "
            "apart from a healthy one")
        assert re.search(r"</span>\s*3\s*</p>", html), (
            "the warning is there but the number of rows is not printed")
        assert ts.get_trial_days(fake) == 30, "the recency rule is not in force"

    def test_a_single_row_page_carries_no_warning(self, monkeypatch):
        html = body_of(run(sa_mod.trial_settings, monkeypatch,
                           FakeSupabase([{"id": 1, "trial_days": 30}])))
        assert "034_trial_settings_singleton.sql" not in html

    def test_the_history_comes_from_the_audit_log(self, monkeypatch):
        monkeypatch.setattr(sa_mod, "fetch_audit_logs",
                            lambda **k: [{"action": "update", "created_at":
                                          "2026-09-02T08:00:00+00:00",
                                          "user_id": SUPER_ID,
                                          "new_data": {"trial_days": 21},
                                          "profiles": {"full_name": "Sari"}}])
        html = body_of(run(sa_mod.trial_settings, monkeypatch, FakeSupabase([])))
        assert "Sari" in html and "21" in html, (
            "the page cannot say who changed the setting, which is the question "
            "a settings page exists to answer after the fact")

    def test_an_unreadable_log_does_not_break_the_page(self, monkeypatch):
        def boom(**_k):
            raise RuntimeError("Server disconnected")
        monkeypatch.setattr(sa_mod, "fetch_audit_logs", boom)
        assert status_of(run(sa_mod.trial_settings, monkeypatch,
                             FakeSupabase([]))) == 200


# ── the role guard, executed ───────────────────────────────────────────────

class TestOnlyTheSuperAdminReachesIt:
    def test_the_route_carries_the_guard(self):
        source = SUPER_SRC.read_text(encoding="utf-8")
        block = source[source.index('@super_bp.route("/trial-settings"'):
                       source.index("def trial_settings(")]
        assert "@_sa_required" in block, "the route lost its role guard"

    @pytest.mark.parametrize("role", ["admin_sekolah", "guru", "murid", "teacher",
                                      "student"])
    def test_another_role_is_sent_away_without_the_body_running(
            self, monkeypatch, role):
        """The guard's own code, run: a wrapper built now, with login_required
        stubbed out, so this measures the role check and nothing else."""
        monkeypatch.setattr(sa_mod, "login_required", lambda f: f)
        reached = []
        probe = sa_mod._sa_required(lambda *a, **k: reached.append(role) or "body")
        app = app_instance()
        with app.test_request_context("/super-admin/trial-settings"):
            g.user_role = role
            result = probe()
        assert not reached, f"{role} reached the super admin's settings page"
        assert result != "body"

    def test_the_super_admin_does_reach_it(self, monkeypatch):
        monkeypatch.setattr(sa_mod, "login_required", lambda f: f)
        probe = sa_mod._sa_required(lambda *a, **k: "body")
        app = app_instance()
        with app.test_request_context("/super-admin/trial-settings"):
            g.user_role = "super_admin"
            assert probe() == "body"


# ── the migration resolves the duplicates the same way the reader does ──────

class TestTheMigrationKeepsTheSameRow:
    """The two halves must agree, or applying the migration *changes* the setting:
    a reader that follows the last human write and a migration that keeps the
    newest id would keep a June seed and drop the operator's 30."""

    def _sql(self):
        """The migration with its comment lines removed.

        Commented out on purpose: a statement this file merely *mentions* — the
        index commented out to disable it, say — still matches a plain grep, so a
        text assertion over the raw file would report a constraint that no longer
        runs. This asserts what will execute.
        """
        return "\n".join(
            line for line in MIGRATION.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("--")
        )

    def test_the_survivor_is_chosen_by_when_it_was_written(self):
        sql = self._sql()
        block = sql[sql.index("DELETE FROM trial_settings"):sql.index(
            "CREATE UNIQUE INDEX")]
        assert "ORDER BY updated_at DESC NULLS LAST" in block, (
            "the migration picks the survivor by id, which is a different row "
            "from the one every reader takes")
        assert "id DESC" in block, "no tie-break, so equal timestamps are undefined"

    def test_it_keeps_exactly_one_row_and_forbids_a_second(self):
        sql = self._sql()
        assert "LIMIT 1" in sql
        assert "CREATE UNIQUE INDEX IF NOT EXISTS trial_settings_singleton" in sql

    def test_it_is_safe_to_apply_twice(self):
        sql = self._sql()
        assert "IF NOT EXISTS" in sql
