"""Sekolah demo tidak boleh berubah menjadi baca-saja dengan sendirinya.

Dilaporkan: CRUD di halaman-halaman admin sekolah "belum berjalan". Diukur pada
kotak yang berjalan, sebabnya bukan satu tombol melainkan **langganan**: seluruh
fitur tulis admin sekolah dijaga `subscription_write_required`, dan `manage.py`
menyemai sekolah demo dengan **trial 14 hari** — yang hanya ditulis bila sekolah
itu belum punya baris langganan sama sekali. Jadi empat belas hari setelah
penyemaian, setiap tulis di sekolah demo itu ditolak dengan pesan langganan,
sementara halamannya tetap terbuka (GET selalu diizinkan). Yang terlihat dari luar
adalah tombol yang tidak melakukan apa-apa.

Terukur pada kotak ini: `99887733` (SMK Teknologi ScanGrade, salah satu dari tiga
sekolah yang kartunya dipajang di `/demo`) hanya punya satu baris
`trial_expired` sejak 2026-06-21, sehingga `admin_smk@scan-grade.app` tidak bisa
membuat, mengubah, atau menghapus apa pun. Dua sekolah demo yang lain selamat
kebetulan: keduanya punya baris `active` yang dibuat belakangan.

Yang diuji di sini bukan angkanya, melainkan sifat yang membuatnya tidak bisa
terjadi lagi: penyemaian demo harus **menjamin** langganan yang aktif — termasuk
memperbaiki baris yang sudah kedaluwarsa — dan harus idempoten, sebab perintah
yang sama dijalankan berkali-kali adalah cara memperbaiki kotak yang sudah telanjur
rusak.
"""
from __future__ import annotations

import functools
import pathlib
import re
import sys
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

MANAGE = ROOT / "manage.py"
DEMO_TEMPLATE = (ROOT / "app" / "templates" / "demo.html").read_text(encoding="utf-8")

#: The three schools whose accounts `/demo` hands out. The repair is only a repair
#: if it covers the schools the page advertises — a demo account that cannot write
#: is a demo of an application that cannot write.
DEMO_NPSNS = {"99887711", "99887722", "99887733"}


@functools.lru_cache(maxsize=1)
def _manage():
    """`manage.py` as a module, with its seed functions reachable.

    Cached: importing it builds the whole Flask app, and an uncached import per
    test spent a minute booting it fourteen times.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("sg_manage", MANAGE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["sg_manage"] = module
    spec.loader.exec_module(module)
    return module


class _Subscriptions:
    """A `school_subscriptions` stand-in that records what it is asked to do.

    It records the *chain* as well as the writes, because one of the rules here is
    about the question rather than the answer: which row counts as "the newest one"
    is decided by the `order` the helper asks the database for. A fake that returns
    the rows it was handed cannot notice an unordered read, and the first version of
    this file could not either.
    """

    def __init__(self, existing):
        self.existing = existing
        self.inserted: list[dict] = []
        self.updates: list[dict] = []
        self.calls: list[tuple] = []
        self._mode = None

    def select(self, *a, **k):
        self.calls.append(("select", a, k))
        return self

    def eq(self, *a, **k):
        self.calls.append(("eq", a, k))
        return self

    def order(self, *a, **k):
        self.calls.append(("order", a, k))
        return self

    def limit(self, *a, **k):
        self.calls.append(("limit", a, k))
        return self

    def insert(self, row):
        self.inserted.append(row)
        self._mode = "insert"
        return self

    def update(self, patch):
        self.updates.append(patch)
        self._mode = "update"
        return self

    def execute(self):
        if self._mode in ("insert", "update"):
            self._mode = None
            return SimpleNamespace(data=self.existing, count=0)
        return SimpleNamespace(data=self.existing, count=len(self.existing))


def _fake(existing):
    table = _Subscriptions(existing)
    return SimpleNamespace(table=lambda name: table), table


def _cli_choices(source: str) -> set[str]:
    """The `command` argument's allowed values, read out of the argparse setup.

    Read from the *choices list* rather than from the file: `demo-subscription` also
    appears in the module's usage docstring, so a grep for the name passes after the
    choice has been deleted — which is exactly what the first version of this test
    did, and what its first reader did too (it split the source on a delimiter that
    does not occur, so "the list" silently became "the rest of the file", where the
    dispatch branch still contains the name).
    """
    m = re.search(r'parser\.add_argument\("command",\s*choices=\[(.*?)\]\)', source, re.S)
    assert m, "the CLI's `command` argument no longer lists its choices"
    return set(re.findall(r'"([\w-]+)"', m.group(1)))


class TestTheSeedCannotLeaveADemoReadOnly:
    def test_an_expired_trial_is_repaired_rather_than_left_alone(self):
        """The measured defect, in one assertion.

        `if not existing.data:` is the whole bug: a demo school that has *any*
        subscription row is skipped, so a trial that ran out three months ago stays
        the newest row and every write in that school is refused for ever.
        """
        manage = _manage()
        supabase, table = _fake([{"id": 7, "status": "trial_expired"}])
        outcome = manage._ensure_demo_subscription(supabase, "school-demo")
        assert outcome == "repaired", (
            "the seed left an expired trial in place; every write in that demo "
            "school is then refused by `subscription_write_required`")
        assert table.inserted, "nothing was written to repair it"

    def test_it_asks_the_database_for_the_newest_row(self):
        """Which row is "the newest one" is the query's answer, not the caller's.

        `get_school_subscription` — the gate that decides whether a school may
        write — reads the newest row. If the repair read an unordered one it could
        judge an active school broken (writing a second row every time it runs) or
        judge a lapsed one fine. The two have to be asking the same question.
        """
        manage = _manage()
        supabase, table = _fake([{"id": 7, "status": "trial_expired"}])
        manage._ensure_demo_subscription(supabase, "school-demo")
        orders = [c for c in table.calls if c[0] == "order"]
        assert orders, (
            "the read is unordered, so which row counts as newest is left to the "
            "database — and the gate orders by created_at")
        args, kwargs = orders[0][1], orders[0][2]
        assert args[0] == "created_at" and kwargs.get("desc") is True, (
            f"the read orders by {args}/{kwargs} instead of newest-first")

    def test_the_row_it_writes_never_expires(self):
        """`active` with no end date is the shape that survives.

        `get_school_subscription` only re-checks an end date when one is set, so a
        demo school with `status='active'` and `subscription_end=None` stays writable
        without anyone rolling it forward — and it is already the shape the two
        working demo schools carry on this box.
        """
        manage = _manage()
        supabase, table = _fake([{"id": 7, "status": "trial_expired"}])
        manage._ensure_demo_subscription(supabase, "school-demo")
        row = table.inserted[0]
        assert row["status"] == "active", row
        assert row["subscription_end"] is None, (
            "an end date is what the trial rotted on; the demo must not carry one")
        assert row["school_id"] == "school-demo"

    def test_an_active_school_is_left_alone(self):
        """Idempotent: running the repair twice must not pile up rows."""
        manage = _manage()
        supabase, table = _fake([{"id": 10, "status": "active"}])
        assert manage._ensure_demo_subscription(supabase, "school-demo") == "kept"
        assert not table.inserted and not table.updates

    @pytest.mark.parametrize("status", ["trial", "trial_expired", "expired", "cancelled",
                                        "suspended", None])
    def test_every_status_that_is_not_active_is_repaired(self, status):
        """`trial` counts as broken here on purpose.

        A trial is a date waiting to pass: it is exactly how the demo schools ended
        up read-only. `is_school_active` still accepts a *live* trial for real
        schools; a demo fixture must not be one, or the demo has a fuse.
        """
        manage = _manage()
        supabase, table = _fake([{"id": 1, "status": status}])
        assert manage._ensure_demo_subscription(supabase, "s") == "repaired"
        assert table.inserted[0]["status"] == "active"

    def test_a_school_with_no_subscription_at_all_gets_one(self):
        manage = _manage()
        supabase, table = _fake([])
        assert manage._ensure_demo_subscription(supabase, "s") == "repaired"
        assert table.inserted[0]["status"] == "active"

    def test_the_seed_path_uses_it(self):
        source = MANAGE.read_text(encoding="utf-8")
        block = source.split("def _seed_school_relations", 1)[1].split("\n\n\n", 1)[0]
        assert "_ensure_demo_subscription" in block, (
            "`manage.py seed` still writes the 14-day trial itself, so a re-seed "
            "reintroduces the fuse")
        assert '"status": "trial"' not in block, (
            "the seeding still writes a trial subscription for a demo school")


class TestAnOperatorCanRepairABoxAlreadyBroken:
    def test_there_is_a_command_for_it(self):
        """Re-seeding a whole school is the wrong tool for a lapsed subscription:
        it recreates users and exams. The repair is its own command."""
        source = MANAGE.read_text(encoding="utf-8")
        assert "demo-subscription" in _cli_choices(source), (
            f"`manage.py` offers no way to repair a lapsed demo subscription: "
            f"{sorted(_cli_choices(source))}")
        assert 'args.command == "demo-subscription"' in source, (
            "the command is offered and never dispatched")
        assert "def cmd_demo_subscription" in source

    def test_the_command_covers_every_school_the_demo_page_advertises(self):
        manage = _manage()
        assert {s["npsn"] for s in manage.DEMO_SCHOOLS} == DEMO_NPSNS, (
            "the demo page and the seed disagree about which schools are on demo")
        for npsn in DEMO_NPSNS:
            assert npsn in DEMO_TEMPLATE, (
                f"/demo hands out no account for {npsn}")

    def test_the_command_reports_what_it_did_for_each_school(self, capsys, monkeypatch):
        """A repair that prints nothing cannot be told from one that did nothing."""
        manage = _manage()

        class _SchoolQuery:
            def select(self, *_a, **_k):
                return self

            def eq(self, *_a, **_k):
                return self

            def limit(self, *_a, **_k):
                return self

            def execute(self):
                return SimpleNamespace(
                    data=[{"id": "sid-1", "name": "SMP N 1 ScanGrade",
                           "npsn": "99887711"}], count=1)

        class _Supabase:
            def table(self, name):
                if name == "school_subscriptions":
                    return _Subscriptions([{"id": 1, "status": "trial_expired"}])
                return _SchoolQuery()

        monkeypatch.setattr(manage, "get_supabase", lambda: _Supabase())
        monkeypatch.setattr(manage, "app", SimpleNamespace(app_context=lambda: _Ctx()))
        code = manage.cmd_demo_subscription(SimpleNamespace())
        out = capsys.readouterr().out
        assert code == 0
        assert "repaired" in out.lower(), out
        assert "99887711" in out or "SMP" in out, out


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class TestTheBoxesThatAreNotDemoSchools:
    def test_the_repair_is_scoped_to_the_demo_schools(self):
        """Reactivating a paying tenant is a billing decision, not a fixture fix.

        The box carries other schools whose trial has lapsed (`20623248`,
        `34567890`, and two rows with placeholder NPSNs). They are not ours to
        switch on, and the command must not touch them.
        """
        source = MANAGE.read_text(encoding="utf-8")
        # Bounded at the next definition: the first version of this test read to the
        # end of the file, where `DEMO_SCHOOLS` appears in other functions, so it
        # passed even when the command had been pointed at every school on the box.
        block = source.split("def cmd_demo_subscription", 1)[1].split("\ndef ", 1)[0]
        assert "DEMO_SCHOOLS" in block, (
            "the command iterates something other than the demo schools")
        # Positive, not negative: the first version of this line looked for the
        # absence of a global read and passed on a mutation that had one, because
        # the read was formatted across two lines. What must be *there* is the
        # lookup scoped by the demo configuration's own NPSN.
        assert '.eq("npsn", school["npsn"])' in block, (
            "the command looks schools up by something other than the demo list, "
            "which would flip tenants that are not on demo")
