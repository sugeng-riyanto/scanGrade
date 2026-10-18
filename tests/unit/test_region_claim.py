"""Where the database is — one value, two pages that publish it.

The UU PDP disclosure (`/privacy` §8) tells a reader where their child's data is
stored, and the capacity page prints the same fact beside the figures a school
checks against the evidence files. Those were two copies of one sentence, and
that is how they drifted: the disclosure named Singapore — a Cloudflare edge —
while `DIRECT_URL`'s pooler said `ap-south-1`. Both pages answered 200 for weeks.

So the value lives in one place, `app.config.database_region()`, derived from the
pooler host the app actually connects through; both pages render it and neither
spells it. `deploy/supabase_region.py` refuses a release that puts a region back
into either page (the gate's half); this suite is the app's half — that the
derivation answers, that the pages follow it when it moves, and that the value
they publish is the region the deploy gate's record names.

The last test is the one that would have caught the original defect: with the
pooler in `eu-central-1`, both pages say Frankfurt — and neither still says the
region the other had yesterday.
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config as app_config  # noqa: E402
from app.services import capacity_service as capacity  # noqa: E402

PRIVACY = ROOT / "app" / "templates" / "compliance" / "privacy.html"
CAPACITY_PAGE = ROOT / "app" / "templates" / "public" / "capacity.html"
CAPACITY_SERVICE = ROOT / "app" / "services" / "capacity_service.py"
GUARD = ROOT / "deploy" / "supabase_region.py"

#: A region code, as a page would have had to spell it. Deliberately not tied to
#: the app's own region: the rule is "no code here at all", not "not this one".
REGION_SHAPED = re.compile(r"\bap-[a-z]+-\d\b")

#: The pooler a Supabase project hands out (`db.<ref>.supabase.co` → this host).
POOLER = ("postgresql://postgres.{ref}:pw@aws-1-{region}"
          ".pooler.supabase.com:5432/postgres")
REF_SHAPED = "abcdefghijklmnopqrst"


@pytest.fixture()
def pooler(monkeypatch):
    """Point the app's configuration at a pooler in a chosen region."""

    def point(region: str, ref: str = REF_SHAPED) -> None:
        monkeypatch.setenv("DIRECT_URL", POOLER.format(ref=ref, region=region))

    yield point
    monkeypatch.delenv("DIRECT_URL", raising=False)


@pytest.fixture()
def disclosure(app, monkeypatch):
    """`/privacy`, with the DPO lookup stubbed out — the route already tolerates
    a failed one, but a test must not wait on a socket to prove what §8 says."""
    from app.routes import public as public_routes

    monkeypatch.setattr(public_routes, "get_supabase", lambda: None)

    def read() -> str:
        response = app.test_client().get("/privacy")
        assert response.status_code == 200, response.status_code
        return response.get_data(as_text=True)

    return read


class TestTheValueHasOneDerivation:

    def test_the_pooler_host_is_what_the_app_connects_through(self, pooler):
        pooler("eu-central-1")
        claim = app_config.database_region()
        assert claim["code"] == "eu-central-1"
        assert claim["label"] == "Frankfurt, Germany (eu-central-1)"
        assert claim["short"] == "eu-central-1 (Frankfurt)"

    def test_with_no_pooler_to_read_the_measured_region_is_the_answer(
            self, monkeypatch):
        """A workstation has no `.env`; the page still has to say somewhere."""
        monkeypatch.delenv("DIRECT_URL", raising=False)
        assert app_config.database_region()["code"] == \
            app_config.SUPABASE_REGION_DEFAULT

    def test_a_connection_string_without_a_pooler_is_not_a_region(
            self, monkeypatch):
        """"No host to read" must not turn into "region 5432"."""
        monkeypatch.setenv(
            "DIRECT_URL",
            f"postgresql://postgres.{REF_SHAPED}:pw@db.{REF_SHAPED}.supabase.co:5432/postgres")
        assert app_config.database_region()["code"] == \
            app_config.SUPABASE_REGION_DEFAULT

    def test_an_unknown_region_is_published_as_its_code(self, pooler):
        """Better a bare code than a city that belongs to another region."""
        pooler("xx-north-9")
        claim = app_config.database_region()
        assert claim["label"] == "xx-north-9"
        assert claim["short"] == "xx-north-9"
        assert claim["city"] == ""

    def test_every_known_place_has_a_city_and_a_country(self):
        for code, places in app_config.REGION_PLACES.items():
            assert len(places) == 2 and all(places), code

    def test_every_known_code_is_region_shaped(self):
        """`database_region()` fills the label from this map; a key that is not a
        region code would publish a place under a code `.env` can never carry."""
        for code in app_config.REGION_PLACES:
            assert re.fullmatch(r"[a-z]{2}-[a-z]+-\d", code), code


class TestBothPagesSayTheSameThing:

    def test_the_disclosure_renders_the_configured_label(self, pooler, disclosure):
        pooler("eu-central-1")
        page = disclosure()
        assert "Frankfurt, Germany (eu-central-1)" in page
        # ...and not the region it used to spell.
        assert app_config.SUPABASE_REGION_DEFAULT not in page

    def test_the_capacity_row_is_the_same_value(self, pooler):
        pooler("eu-central-1")
        assert capacity.machine()["database"] == "Supabase, eu-central-1 (Frankfurt)"

    def test_the_capacity_page_prints_that_row(self):
        """The value is only published if something renders it."""
        assert "cap.machine.database" in CAPACITY_PAGE.read_text(encoding="utf-8")

    def test_the_two_pages_move_together(self, pooler, disclosure):
        """The drift test: the claim follows the configuration, both times."""
        seen = []
        for region, label in (
                ("ap-south-1", "Mumbai, India (ap-south-1)"),
                ("eu-west-2", "London, United Kingdom (eu-west-2)"),
                ("ap-southeast-2", "Sydney, Australia (ap-southeast-2)")):
            pooler(region)
            page = disclosure()
            assert label in page, region
            assert capacity.machine()["database"] == \
                f"Supabase, {region} ({label.split(',')[0]})"
            for older in seen:
                assert older not in page, (region, older)
            seen.append(label)

    def test_neither_page_spells_a_region(self):
        """The invariant the gate enforces at release time, held here too."""
        for path in (PRIVACY, CAPACITY_PAGE):
            text = path.read_text(encoding="utf-8")
            found = REGION_SHAPED.findall(text)
            assert not found, (path.name, found)

    def test_the_disclosure_states_it_in_both_renderings(self):
        """The page renders the policy twice; a single claim is a half-updated page."""
        text = PRIVACY.read_text(encoding="utf-8")
        assert text.count("region_claim.label") == 2

    def test_the_service_carries_no_database_region_of_its_own(self):
        """`MACHINE`'s own `region` is the **box's** and stays a literal — it is not
        the claim. The database's row must be built, not typed."""
        source = CAPACITY_SERVICE.read_text(encoding="utf-8")
        assert "database_region(" in source
        for number, line in enumerate(source.splitlines(), 1):
            if '"database"' not in line:
                continue
            assert not REGION_SHAPED.search(line), (number, line)
            assert "database_region()" in line, (number, line)


class TestTheClaimIsTheRegionTheGateRecords:

    def test_the_source_and_the_deploy_records_name_one_region(self):
        """The app publishes `SUPABASE_REGION_DEFAULT`; the gate holds the record.

        Read rather than imported: this suite must not depend on `deploy/` being on
        `sys.path`, and the two constants are a fact about the tree.
        """
        text = GUARD.read_text(encoding="utf-8")
        found = re.search(r'^REGION = "([^"]+)"', text, re.MULTILINE)
        assert found, "deploy/supabase_region.py no longer records REGION"
        assert app_config.SUPABASE_REGION_DEFAULT == found.group(1)

    def test_the_record_region_has_a_place_the_label_can_name(self):
        text = GUARD.read_text(encoding="utf-8")
        recorded = re.search(r'^REGION = "([^"]+)"', text, re.MULTILINE).group(1)
        assert recorded in app_config.REGION_PLACES

    def test_the_gates_own_record_passes_on_this_tree(self):
        """The tool as the gate runs it, against the checkout, is clean."""
        spec = importlib.util.spec_from_file_location("supabase_region_guard", GUARD)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report = module.scan(ROOT)
        assert report.offenders() == [], report.offenders()
