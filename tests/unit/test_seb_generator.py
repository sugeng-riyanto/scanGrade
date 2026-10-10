"""The ``.seb`` file is generated **from the exam's own settings** — guarded here.

What changed, and why it needed guards
--------------------------------------
The generator used to write one constant template plus the exam's address and two
password hashes. That is a *valid* config, and it silently ignored the teacher: a
paper whose exam form said "screenshots are allowed" was handed a file that refused
them, and nothing anywhere said so — the two switches were read by the exam page's
own JavaScript and by nothing else. The file now mirrors them, through
``seb_service.EXAM_OWNS``, and that mapping is the subject of this file.

Two properties are the contract, and neither is a style preference:

1. **One Config Key, for every platform.** The Config Key is the only key this app
   can compute *server-side*; the Browser Exam Key is a hash over the SEB binary's
   own signature, so it differs per platform and per build and can never be
   reproduced here. The failure mode of getting this wrong is not an error but a
   *silent* one: a config carrying a per-platform key (Apple's ``examKeySalt``, a
   Windows-only variant, anything keyed by build) would open on one device and refuse
   on the next, and the school would report "SEB does not work on the iPads".
2. **No fractional value, anywhere.** Windows and macOS serialise ``<real>``
   differently for the same number (SEB issue #1495), so one float in a config means
   two Config Keys from one file — the same cross-platform guarantee broken a second
   way. The generator's values are ``bool`` **by construction**, and the finished map
   is still handed to the Config Key module's own type check, because a construction
   argument is an argument (that check is asserted here to *run*, not merely to
   exist).

The mapping and the drift rule
------------------------------
A file that follows the exam is a file that can go stale: the row can be edited after
the file was issued, and the stored key then describes a config the row no longer
produces. Both surfaces that matter are asserted — the download refuses (unchanged
behaviour, now reachable by an ordinary edit), and the **panel says so**, because a
refusal nobody can see is the shape of failure this repository keeps finding.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import seb_config_key as ck
from app.services import seb_crypto, seb_service

ROOT = Path(__file__).resolve().parents[2]
PANEL = ROOT / "app" / "templates" / "teacher" / "seb_panel.html"
EXAM_FORM = ROOT / "app" / "templates" / "teacher" / "exam_form.html"
MIGRATIONS = ROOT / "supabase" / "migrations"

EXAM_ID = "ex-1"
SCHOOL = "sc-1"
TEACHER = "u-teacher"
URL = f"https://sekolah.test/teacher/exams/{EXAM_ID}/seb"


def _exam(**over) -> dict:
    """An exam row as the routes see it: switches on, and a problem-free default."""
    row = {"id": EXAM_ID, "school_id": SCHOOL, "teacher_id": TEACHER,
           "title": "Fisika", "require_seb": True,
           "block_right_click": True, "block_screenshot": True}
    row.update(over)
    return row


def _credential() -> dict:
    return {"exam_id": EXAM_ID,
            "quit_password_hash": seb_crypto.sha256_hex("quit-pass-1"),
            "admin_password_hash": seb_crypto.sha256_hex("admin-pass-1")}


def _settings(exam=None, **over) -> dict:
    row = _exam(**over) if exam is None else exam
    return seb_service.settings_for(
        row, start_url="https://sekolah.test/student/exams/ex-1",
        quit_hash=seb_crypto.sha256_hex("quit-pass-1"),
        admin_hash=seb_crypto.sha256_hex("admin-pass-1"))


# ── 1. the switches the teacher set are the switches the file carries ────────

def test_a_switch_the_teacher_turned_off_reaches_the_file():
    """Both directions, on the whole group the ``block_screenshot`` switch owns.

    One direction is not enough: a generator that always wrote the blocked value
    would pass "the switch is honoured" if only the on-state were asserted, and the
    blocked value is what the file carried *before* this change.
    """
    allowed = _settings(block_right_click=False, block_screenshot=False)
    for key in seb_service.EXAM_OWNS:
        assert allowed[key] is True, f"{key} stayed blocked for a switch the teacher turned off"

    blocked = _settings(block_right_click=True, block_screenshot=True)
    for key in seb_service.EXAM_OWNS:
        assert blocked[key] is False, f"{key} was allowed while the teacher blocked it"


def test_the_exam_column_and_the_seb_key_are_inverted_exactly_once():
    """The direction is written down because the *names* lie in both directions.

    `block_screenshot = true` means "refuse screenshots", and SEB spells that
    `enablePrintScreen = false` — one negation. `allowScreenSharing` is an
    allow-worded key, so a reader skimming the table would invert it twice and ship
    a file that permits screen sharing on a paper whose switch forbids it.
    """
    assert _settings(block_screenshot=True)["enablePrintScreen"] is False
    assert _settings(block_screenshot=False)["enablePrintScreen"] is True
    assert _settings(block_right_click=True)["enableRightMouse"] is False
    assert _settings(block_right_click=False)["enableRightMouse"] is True
    assert _settings(block_screenshot=True)["allowScreenSharing"] is False
    assert _settings(block_screenshot=False)["allowScreenSharing"] is True


def test_a_row_that_does_not_carry_the_switch_leaves_the_capability_blocked():
    """A column left out of a select arrives *absent*, never as an error.

    PostgREST reads a missing column as missing, so the two ways a row can be
    short — an older row, a select that forgot the column — must fail **closed**.
    The other direction would mean a typo in a select silently loosened every file
    the school downloads, with no error anywhere.
    """
    absent = _settings(exam={})
    for key in seb_service.EXAM_OWNS:
        assert absent[key] is False, f"{key} was allowed by a row that never said so"
    # `None` is the install test, and `{}` is a row this code could not read: both
    # mean "no switch said otherwise", and they must not disagree.
    assert absent == _settings(exam=None)


def test_the_switch_survives_the_plist_writer_and_the_container():
    """End to end through the artefact a pupil opens, not through the map.

    A boolean is the one type a plist writer is most likely to lose (``<true/>``
    read back as the string ``"true"`` would change the Config Key the client
    computes), so the value is checked after the double-gzip round trip.
    """
    settings = _settings(block_screenshot=False, block_right_click=True)
    from_file = seb_service.decode_seb(seb_service.seb_file_bytes(settings))
    for key in seb_service.EXAM_OWNS:
        assert from_file[key] is settings[key], key
        assert type(from_file[key]) is bool, key
    assert ck.config_key(from_file) == ck.config_key(settings)


def test_every_switch_in_the_table_is_a_switch_the_teacher_can_actually_set():
    """A column no form posts is a mapping that can never do anything.

    Three ways to be wrong, all of them silent: a column the migrations never
    created, a column the exam form has no control for, and — the one this
    repository has been bitten by — a column the route's own select leaves out,
    which arrives absent and therefore reads as "blocked" forever.
    """
    from app.routes.seb import EXAM_COLUMNS

    migrations = "\n".join(path.read_text(encoding="utf-8")
                           for path in sorted(MIGRATIONS.glob("*.sql")))
    form = EXAM_FORM.read_text(encoding="utf-8")
    for column in set(seb_service.EXAM_OWNS.values()):
        assert re.search(rf"ADD COLUMN IF NOT EXISTS {column}\b", migrations, re.I), (
            f"{column} is mapped but no migration creates it")
        assert f'name="{column}"' in form, f"{column} has no control on the exam form"
        assert column in EXAM_COLUMNS, (
            f"{column} is missing from the SEB select — the row would arrive without "
            "it and the file would block a capability the teacher allowed")


# ── 2. one Config Key, whatever platform opens it ───────────────────────────

def test_the_generator_is_given_no_platform_and_writes_no_platform_key():
    """The guarantee, asserted on the shape of the function rather than on a doc.

    A parameter is how a per-platform key would arrive — the honest-looking version
    of this defect is a `platform=` argument threaded to one extra key — and a
    Browser Exam Key (`examKeySalt` on Apple clients, the BEK hash itself) is what
    it would write. Both are refused here, so adding either has to delete a test.
    """
    params = list(inspect.signature(seb_service.settings_for).parameters)
    assert params == ["exam", "start_url", "quit_hash", "admin_hash"], (
        "a platform (or anything else) joined the generator's inputs: "
        f"{params} — one Config Key for every platform is the whole point")
    settings = _settings()
    for key in ("examKeySalt", "browserExamKey", "browserExamKeyHash",
                "configKey", "sendBrowserExamKeyHash"):
        assert key not in settings, f"{key} is a per-platform value in a cross-platform config"
    # The Config Key header must still be *requested* — that is the one key we do
    # use, and it is the same value for every platform.
    assert settings["sendBrowserExamKey"] is True


def test_one_exam_has_one_key_whatever_order_the_row_is_read_in():
    """Same switches, same key — the property a second platform depends on.

    Supabase hands a row back as a JSON object and nothing guarantees key order, so
    a generator that hashed in row order would produce a different key depending on
    how the row was serialised. The order-independence is the Config Key module's
    job and is asserted there; what is asserted *here* is that the exam's switches
    are the only thing this generator reads, so two reads of one paper cannot differ.
    """
    row = _exam(block_screenshot=False)
    forward = _settings(exam=row)
    reversed_row = dict(reversed(list(row.items())))
    assert _settings(exam=reversed_row) == forward
    assert ck.config_key(_settings(exam=reversed_row)) == ck.config_key(forward)


def test_changing_a_switch_changes_the_key():
    """The other half of the same rule, and the reason a stale file must be refused.

    If the key did not move with the switches, the door would admit a client whose
    file no longer describes the paper it is opening — which is the one thing the
    Config Key exists to prevent.
    """
    before = ck.config_key(_settings(block_screenshot=True))
    after = ck.config_key(_settings(block_screenshot=False))
    assert before != after


# ── 3. no fractional value, anywhere ────────────────────────────────────────

def _walk(node, path=""):
    """Every leaf of the generated config, with its path — for the type assertions."""
    if isinstance(node, dict):
        for key, item in node.items():
            yield from _walk(item, f"{path}.{key}" if path else str(key))
    elif isinstance(node, (list, tuple)):
        for index, item in enumerate(node):
            yield from _walk(item, f"{path}[{index}]")
    else:
        yield node, path


@pytest.mark.parametrize("junk", [1.0, 0.5, "false", "", 0, 1, None, [], True, False])
def test_a_value_that_came_from_a_row_cannot_make_the_config_fractional(junk):
    """Feeding a float through *every* mapped column, because the row is the input.

    The exam form posts strings, a hand-edited row can hold anything, and a switch
    read as ``1.0`` must become a boolean — not travel into a config where it would
    be serialised as ``<real>`` and split the key in two. Every value the mapping
    produces is asserted to be a ``bool``, which is the shape that makes a float
    impossible rather than merely unlikely.
    """
    for column in set(seb_service.EXAM_OWNS.values()):
        settings = _settings(**{column: junk})
        for key in seb_service.EXAM_OWNS:
            assert settings[key] is True or settings[key] is False, (
                f"{column}={junk!r} produced {key}={settings[key]!r}, which is not a bool")


def test_the_whole_generated_config_carries_no_float_and_an_int_only_where_it_means_pixels():
    """The invariant, over both switch positions and the install test.

    A float is refused by the Config Key module during hashing, so a float that
    reached this map would surface as a paper that cannot be opened — with the
    exception raised somewhere inside a dict, which is not a diagnosis. The only
    number the config carries is ``taskBarHeight``, in pixels, and it is an ``int``
    (``bool`` excluded: ``True`` is an ``int`` in Python and a check that forgot
    that would accept a boolean as a pixel count).
    """
    for exam in (_exam(), _exam(block_screenshot=False), _exam(block_right_click=False), {}):
        settings = _settings(exam=exam)
        for value, path in _walk(settings):
            assert not isinstance(value, float), f"{path} is a float"
            if isinstance(value, int) and not isinstance(value, bool):
                assert path == "taskBarHeight", f"{path} is a number nobody meant to write"
        assert isinstance(settings["taskBarHeight"], int)
        assert not isinstance(settings["taskBarHeight"], bool)


def test_the_generator_runs_the_type_check_rather_than_trusting_its_own_construction(monkeypatch):
    """The refusal is *asserted to fire*, not asserted to exist.

    A construction argument is still an argument: the day someone adds a setting
    from a numeric column, this is the check that catches it at generation time and
    names the setting, instead of at hash time inside a dict.
    """
    monkeypatch.setattr(seb_service, "_template",
                        lambda *a, **k: {"startURL": "https://x/", "taskBarHeight": 40.5})
    with pytest.raises(ck.ConfigKeyError) as excinfo:
        _settings()
    assert "taskBarHeight" in str(excinfo.value), (
        "the refusal does not name the setting that caused it")


# ── 4. a row edited after the file was issued ───────────────────────────────

def _issued(app) -> tuple[dict, dict, str]:
    """One exam, its credential, and the key its **current** switches generate."""
    exam = _exam()
    credential = _credential()
    with app.test_request_context(URL):
        key = seb_service.current_key(exam, credential)
    exam["seb_config_key"] = key
    return exam, credential, key


def _is_current(app, exam, credential) -> bool:
    """`file_is_current` as its callers reach it — inside the request it serves.

    The `startURL` half of the hash comes from `request.url_root`, so this is not a
    test convenience: the drift check is a *door-time* check by construction, and
    the exam's address is the address this server is answering on right now.
    """
    with app.test_request_context(URL):
        return seb_service.file_is_current(exam, credential)


def test_an_untouched_exam_is_current(app):
    exam, credential, key = _issued(app)
    assert key and len(key) == 64
    assert _is_current(app, exam, credential) is True


def test_a_switch_flipped_after_the_file_was_issued_is_reported(app):
    """The edit this feature makes possible, and the answer both surfaces read."""
    exam, credential, _ = _issued(app)
    edited = dict(exam, block_screenshot=False)
    assert _is_current(app, edited, credential) is False
    # ...and the key the file would be built from really is a different one.
    with app.test_request_context(URL):
        assert seb_service.current_key(edited, credential) != exam["seb_config_key"]


def test_a_missing_credential_or_key_is_never_current(app):
    """Fail closed: the two ways to have nothing to compare are not "fine"."""
    exam, credential, _ = _issued(app)
    assert _is_current(app, exam, None) is False
    assert _is_current(app, {"id": EXAM_ID}, credential) is False
    assert _is_current(app, exam, {}) is False
    # A row with no id has no `startURL` to hash, and inventing one here would
    # report drift for every exam — the opposite mistake, and just as useless.
    with app.test_request_context(URL):
        assert seb_service.current_key({"seb_config_key": "a" * 64}, credential) is None


# ── 5. the panel says it, because a refusal nobody can see is invisible ─────

class _Query:
    """The two response shapes postgrest really gives: a list, or one row.

    `maybe_single()` is not decoration — `row_or_none` reads `.data` and a single
    row arrives *as* that row, while `credentials_for` reads a list. A fake that
    answered one shape for both would make the route test pass on a row the route
    could never really have received.
    """

    def __init__(self, rows):
        self._rows = rows
        self._single = False

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        self._single = True
        return self

    def execute(self):
        if self._single:
            return SimpleNamespace(data=self._rows[0] if self._rows else None)
        return SimpleNamespace(data=list(self._rows))


class _Db:
    """Only the two tables this route reads; everything else is substituted."""

    def __init__(self, exam, credential):
        self._tables = {"exams": [exam] if exam else [],
                        "exam_seb_credential": [credential] if credential else []}

    def table(self, name):
        return _Query(self._tables.get(name, []))


@pytest.fixture()
def _panel_data(monkeypatch):
    """Two narrow substitutions: the panel's device history and its access log.

    Both are database reads whose *contents* are asserted in their own suites; what
    matters here is that the page renders, so the data sources are stubbed and the
    decision itself is exercised for real.
    """
    from app.routes import seb as sebmod
    from app.services import invigilation

    monkeypatch.setattr(sebmod, "_mobile_risk", lambda *a, **k: {"known": 0, "android": 0})
    monkeypatch.setattr(sebmod.seb_access, "log_for_exam", lambda *a, **k: [])
    monkeypatch.setattr(invigilation, "invigilated_exam_ids", lambda *a, **k: set())


def _panel(app, exam, credential, *, user=TEACHER, role="guru", school=SCHOOL) -> str:
    """Render the real panel through its own route, with the real decision."""
    from flask import g
    from app.routes import seb as sebmod

    app.extensions["supabase"] = _Db(exam, credential)
    with app.test_request_context(URL):
        g.user_id, g.user_role = user, role
        g.user_name, g.user_email = "Bu Rina", "rina@sekolah.test"
        g.user_school_id, g.user_class_id, g.user_status = school, None, "active"
        g.tz_offset, g.show = 7, {}
        # `render_template` returns the page as a string; Flask turns it into a
        # response. Asserting on the string is the same bytes a browser gets.
        return sebmod.panel.__wrapped__(EXAM_ID)


def test_the_owner_can_open_the_panel_before_seb_is_switched_on(app, _panel_data):
    """The page that switches the feature on has to open while it is off.

    The enable button, the confirmation checkbox and the Android warning all live
    here, so a panel that only opened *after* SEB was required made the whole
    feature unreachable: the only way in was a URL that answered 403, and the button
    that would have fixed it was on the page that refused. Asserted as a render, so
    the fix cannot be a route that returns 200 with the button missing.
    """
    page = _panel(app, _exam(require_seb=False, seb_config_key=None), None)
    assert "Wajibkan Safe Exam Browser" in page, "the enable button did not render"
    assert "Require Safe Exam Browser" in page
    # No access role has been decided, and no password card exists: there is nothing
    # to reveal while the door is open, and "Your role: School staff" would be a
    # sentence about the reading teacher that is not true.
    assert "Your role:" not in page
    assert "Kunci keluar" not in page


def test_nobody_but_the_owner_reaches_the_panel_before_seb_is_on(app, _panel_data):
    """The narrow half of the same fix: the exception is ownership, nothing else."""
    from werkzeug.exceptions import Forbidden

    exam = _exam(require_seb=False, seb_config_key=None)
    with pytest.raises(Forbidden):
        _panel(app, exam, None, user="u-colleague")


def test_a_disabled_paper_hides_the_credential_it_left_behind(app, _panel_data):
    """SEB switched off after a sitting: the row survives, the page must not show it.

    `disable` deliberately keeps the credential so switching back on is one click.
    What must not survive the switch-off is the *display* of passwords that open a
    door nothing is checking any more.
    """
    exam, credential, _ = _issued(app)
    page = _panel(app, dict(exam, require_seb=False), credential)
    assert "Kunci keluar" not in page
    assert "issue a new file" not in page
    assert "Wajibkan Safe Exam Browser" in page


def test_the_panel_warns_when_the_exam_changed_after_the_file_was_issued(app, _panel_data):
    """Rendered, in both languages, and only when it is true.

    The download refuses in this state, so the teacher meets the failure on the
    screen that can fix it — and the sentence has to carry the fact a school acts
    on: a file already handed out keeps working, so the answer is "re-issue and
    redistribute", not "every pupil is locked out".
    """
    exam, credential, _ = _issued(app)
    fresh = _panel(app, exam, credential)
    # The *exact* sentence, both halves. A phrase matched case-insensitively once
    # passed against the notice's absence in the wrong case, which is how a negative
    # assertion becomes decoration — it has to name the copy that would appear.
    for sentence in ("The exam settings changed after this .seb file was made.",
                     "Pengaturan ujian berubah setelah berkas .seb ini dibuat."):
        assert sentence not in fresh, (
            "the panel warns about a change that has not happened")
    assert "Kunci keluar" in fresh, "the panel did not render its own body"

    stale = _panel(app, dict(exam, block_screenshot=False), credential)
    assert "The exam settings changed after this .seb file was made." in stale
    assert "Pengaturan ujian berubah setelah berkas .seb ini dibuat." in stale
    assert "Files already shared keep working" in stale
    assert "Berkas lama yang sudah beredar masih bisa dibuka" in stale


def test_the_exam_form_renders_the_link_to_the_panel_beside_the_switches(app):
    """Rendered, because reading the file passed with the link switched off.

    The first version of this test asserted the `url_for` was *in the template*, and
    the mutation harness showed it green with the whole link wrapped in `{% if
    False %}`: an anchor inside a dead branch is still a string in a file. So the
    anti-cheat card is rendered as the template ships it, and the link has to come
    out of the render — above the `block_screenshot` control, since a link that
    drifts away from the switches it explains is a link nobody finds.

    What this does not prove: that the route renders this markup for a real request.
    That is the route's own terrain (its guards, its scope reads) and it is covered
    where the builder is; here the subject is the card and the branch inside it.
    """
    from flask import render_template_string

    text = EXAM_FORM.read_text(encoding="utf-8")
    start = text.index('<div class="card p-5 space-y-4" x-data="antiCheatSettings({')
    block = text[start:text.index("<!-- Question settings.", start)]
    with app.test_request_context(URL):
        card = render_template_string(block, exam=_exam())
        without = render_template_string(block, exam=None)

    assert "Anti-Cheat" in card, "the card did not render — the slice or the branch is wrong"
    assert f"/teacher/exams/{EXAM_ID}/seb" in card, "the panel is not reachable from the form"
    assert card.index(f"/teacher/exams/{EXAM_ID}/seb") < card.index('name="block_screenshot"'), (
        "the link sits below the switches it explains")
    # A paper that has not been saved has no panel to link to, and a link to an
    # exam-less panel is a BuildError waiting for a teacher to click it.
    assert "/seb" not in without, "an unsaved paper links a panel it cannot have"


def test_the_panel_notice_is_switched_by_the_route_and_not_by_the_template():
    """The wiring, asserted where it could vanish silently.

    The notice is rendered only when the route computes `file_is_current`; a panel
    that stopped asking would render a page with no warning and no error, which is
    the dead end this notice exists to remove. The answer is asked of the *service*
    rather than recomputed in the route, so the download and the page cannot
    disagree about what "current" means.
    """
    from app.routes import seb as sebmod

    source = Path(sebmod.__file__).read_text(encoding="utf-8")
    assert "file_is_current=live and seb_service.file_is_current(exam, credential)" in source
    page = PANEL.read_text(encoding="utf-8")
    assert "{% if has_credential and not file_is_current %}" in page
