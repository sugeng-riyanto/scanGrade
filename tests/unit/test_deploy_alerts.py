"""The reading somebody would have made by hand, made on a timer.

`/super-admin/deploy-status` answers "is the runner the checkout's own?" — but only
when somebody opens it. That is the same failure one level up: the runner sat 45
commits behind for a week on a box that served students perfectly, and the only
trace was a page nobody had a reason to open.

`app/services/deploy_alert_service.py` mails that reading instead. These checks are
about the four properties that make such a channel worth having rather than worth
filtering:

1. **It is right about what is stale.** The policy is a pure function of the same
   report the page renders, so the email and the card cannot tell two stories; the
   threshold is "more than a few commits", and the count is part of the identity of
   the problem.
2. **It never fires on a reading it could not make** — a laptop, an unreadable
   runner. An alert nobody can act on is how a channel gets muted.
3. **It never sends twice for the same state, and never goes quiet on a growing
   one.** A week apart at most while it lasts.
4. **It cannot be silenced by a missing record, a killed worker, or three gunicorn
   workers ticking at once.** The tick is claimed with `O_EXCL`, an abandoned claim
   is stolen after a quarter of an hour.

The page half is checked here too: where the alerts go, whether the test button can
work, and that every outcome the button can answer with has a sentence in both
languages.
"""
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.conftest import build_app  # noqa: E402
from app.services import deploy_alert_service as alerts  # noqa: E402
from app.services import deploy_status_service as status  # noqa: E402
from tests.unit import test_deploy_status as base  # noqa: E402

TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "deploy_status.html"
ROUTES = ROOT / "app" / "routes" / "super_admin.py"
CONFIG = ROOT / "app" / "config.py"

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_caches():
    """The recipient cache and the loop's stop flag are process-wide."""
    alerts._page_recipients_cache.update({"at": 0.0, "value": None})
    yield
    alerts._page_recipients_cache.update({"at": 0.0, "value": None})
    alerts.stop_deploy_alert_scheduler()


# ── building the report the policy reads ─────────────────────────────────────

def report_of(runner: dict | None = None, checkout: dict | None = None,
              *, verdict: str | None = "fresh", **extra) -> dict:
    """A report shaped like `deploy_status_service.report()` returns one.

    The shape is not invented: `TestWhatCountsAsStale` asserts this dict's keys are
    the ones a real report carries, so a rename in the service fails here rather
    than quietly turning the alert off.
    """
    runner_state = {
        "path": "/usr/local/bin/scangrade-deploy", "exists": True, "size": 4096,
        "installed_at": "2026-09-01T00:00:00+00:00", "sha256": "0123456789ab",
        "kind": "launcher", "reason_key": None, "detail": None,
        "origin_key": "matches", "origin_commit": "a" * 40, "origin_short": "aaaaaaa",
        "origin_date": "2026-09-01T00:00:00+00:00", "origin_subject": "the launcher",
        "origin_behind": 0, "origin_stale_files": 0, "gate0": status.GATE0_PASSES,
        "matches_this_commit": True, "rendered_repo": "/opt/scangrade",
    }
    runner_state.update(runner or {})
    checkout_state = {
        "path": "/opt/scangrade", "available": True, "reason_key": None, "detail": None,
        "git": "/usr/bin/git", "branch": "main", "head": "b" * 12,
        "head_subject": "a commit", "head_date": "2026-09-20T00:00:00+00:00",
        "origin": "c" * 12, "behind": 0, "ahead": 0, "dirty": None,
        "origin_updated_at": "2026-09-20T00:00:00+00:00", "origin_age_seconds": 60,
        "detached": False,
    }
    checkout_state.update(checkout or {})
    report = {
        "measured_at": NOW.isoformat(), "repo": "/opt/scangrade",
        "runner": runner_state, "snapshot_runner": dict(runner_state),
        "checkout": checkout_state, "paused": False,
        "quarantine": {"held": False, "refused_at": None, "gate": None},
        "unarmed": {"path": "/var/lib/scangrade-deploy/unarmed", "present": False,
                    "key": "none", "at": None, "age_seconds": None, "detail": None,
                    "reason": None},
        "preflight": {"path": "/var/lib/scangrade-deploy/refused-before-merge",
                      "present": False, "key": "none", "gate": None, "gate_key": None,
                      "at": None, "age_seconds": None, "exit_code": None,
                      "commit": None, "short": None, "detail": None,
                      "reason": None},
        "verdict": {"level": "fresh", "key": verdict, "detail": None, "behind": None,
                    "runner_behind": None, "runner_from": None},
        # The paths the cards print, so a rendered fixture shows a file rather than
        # a blank where an operator expects to be told which file to read.
        "quarantine_file": "/var/lib/scangrade-deploy/quarantined",
        "unarmed_file": "/var/lib/scangrade-deploy/unarmed",
        "preflight_file": "/var/lib/scangrade-deploy/refused-before-merge",
    }
    report.update(extra)
    return report


def copy_that_predates_gate_0(*, origin_behind: int = 45) -> dict:
    return report_of({"kind": "copy", "gate0": status.GATE0_CANNOT,
                      "matches_this_commit": False,
                      "origin_behind": origin_behind, "origin_short": "1234567",
                      "origin_subject": "45 commits ago"})


# ── 1. what counts as stale ──────────────────────────────────────────────────

class TestWhatCountsAsStale:
    def test_the_policy_reads_fields_a_real_report_actually_has(self, tmp_path):
        """The guard against a silent rename: a missing key makes the alert never
        fire, and nothing else in the suite would notice."""
        real = status.report(repo="/nonexistent", runner="/nonexistent",
                             snapshot_runner="/nonexistent",
                             pause_file="/nonexistent",
                             quarantine_file="/nonexistent",
                             unarmed_file="/nonexistent",
                             preflight_file="/nonexistent")
        assert set(real) >= {"runner", "checkout", "verdict", "quarantine", "repo"}
        assert set(real["runner"]) >= set(report_of()["runner"])
        assert set(real["checkout"]) >= set(report_of()["checkout"])
        # The refusal card is read attribute by attribute (`pf.key`, `pf.exit_code`,
        # ...), so the fixture has to carry the reader's shape *exactly*: a key the
        # reader grows and this fixture forgets raises `UndefinedError` inside the
        # template, which is a 500 on the one page an operator reads to find out why
        # nothing is deploying.
        assert set(report_of()["preflight"]) == set(real["preflight"])
        # And the reading the policy keys on is really in there.
        assert "gate0" in real["runner"] and "origin_behind" in real["runner"]

    @pytest.mark.parametrize("kind", ["unknown", "absent", None, "other"])
    def test_a_reading_it_could_not_make_is_silence_not_an_incident(self, kind):
        """`unknown` covers an absent launcher and a checkout that is not a git tree
        — states the *page* reports and a human judges, on a laptop as much as on a
        box. Mailing those would make the channel noise."""
        assert alerts.staleness(report_of({"kind": kind, "gate0": None,
                                           "origin_behind": 900})) is None

    def test_no_report_at_all_is_silence(self):
        assert alerts.staleness(None) is None
        assert alerts.staleness({}) is None

    def test_a_copy_that_predates_gate_0_is_the_loudest_alert(self):
        """The state production was actually in: nothing can refuse that copy, so
        releases ship with the gates of the day it was installed."""
        found = alerts.staleness(copy_that_predates_gate_0())
        assert found["kind"] == alerts.KIND_COPY_PREDATES_GATE
        assert found["commits"] == 45
        assert found["from_short"] == "1234567"
        assert found["detail"]["origin_subject"] == "45 commits ago"

    def test_a_drifted_copy_is_reported_as_a_drifted_copy(self):
        """Not as "behind": Gate 0 refuses it, so *nothing* deploys, which is a
        different sentence and a different remedy."""
        found = alerts.staleness(report_of({"kind": "copy",
                                            "gate0": status.GATE0_REFUSES,
                                            "origin_behind": 2}))
        assert found["kind"] == alerts.KIND_COPY_DRIFTED

    def test_a_copy_that_cannot_refuse_outranks_a_small_distance(self):
        """A copy with no Gate 0 in it is broken at *any* distance, so the loud
        reading wins over the quiet arithmetic."""
        found = alerts.staleness(copy_that_predates_gate_0(origin_behind=0))
        assert found["kind"] == alerts.KIND_COPY_PREDATES_GATE

    def test_a_launcher_more_than_a_few_commits_behind_alerts(self):
        found = alerts.staleness(report_of({"origin_behind": 6}))
        assert found["kind"] == alerts.KIND_RUNNER_BEHIND
        assert found["commits"] == 6

    def test_a_launcher_exactly_at_the_line_stays_quiet(self):
        """"More than a few": a healthy box is a commit or two behind for a moment
        after every push, and the line has to be above that, not on it."""
        assert alerts.staleness(report_of({"origin_behind": 5})) is None
        assert alerts.staleness(report_of({"origin_behind": 5}),
                                min_commits=5) is None
        assert alerts.staleness(report_of({"origin_behind": 5}), min_commits=4)

    def test_a_fresh_launcher_is_silence(self):
        assert alerts.staleness(report_of()) is None

    def test_a_launcher_render_that_is_not_this_commit_alerts_without_a_count(self):
        """`launcher_stale` means deploy/entrypoint.sh changed after the install —
        not current, not broken, and worth saying even when git cannot count it."""
        found = alerts.staleness(report_of({"origin_behind": None},
                                           verdict="launcher_stale"))
        assert found["kind"] == alerts.KIND_RUNNER_BEHIND
        assert found["commits"] is None

    def test_the_checkout_stopping_is_a_different_alert(self):
        """A checkout that is behind `origin/main` means the *pipeline* stopped —
        a failed fetch, a quarantine, the pause file — so the mail names that,
        and carries what the page knows about the hold."""
        found = alerts.staleness(report_of(
            checkout={"behind": 30, "origin": "d" * 12, "branch": "main"},
            verdict="behind",
            quarantine={"held": True, "refused_at": "2026-09-19T00:00:00+00:00",
                        "gate": "perf gate"}))
        assert found["kind"] == alerts.KIND_CHECKOUT_BEHIND
        assert found["commits"] == 30
        assert found["anchor"] == "d" * 12, "the ref the count is measured against"
        assert found["detail"]["held"] is True
        assert found["detail"]["held_gate"] == "perf gate"

    def test_a_checkout_inside_the_threshold_is_silence(self):
        assert alerts.staleness(report_of(checkout={"behind": 3})) is None

    def test_the_runner_is_judged_before_the_checkout(self):
        """A runner that cannot check a release matters more than a checkout that
        has stopped moving, and one mail should carry the louder fact."""
        found = alerts.staleness(report_of({"origin_behind": 40},
                                           checkout={"behind": 90}))
        assert found["kind"] == alerts.KIND_RUNNER_BEHIND


# ── 2. when the same problem is worth repeating ──────────────────────────────

class TestTheKeyAndTheReminder:
    def test_no_record_means_send(self):
        assert alerts.should_send({"kind": "x", "commits": 1}, None, now=NOW)

    def test_a_new_state_is_sent(self):
        descriptor = {"kind": alerts.KIND_RUNNER_BEHIND, "commits": 6,
                      "from_short": "abc1234"}
        last = {"key": alerts.alert_key({**descriptor, "commits": 5}),
                "sent_at": NOW.isoformat()}
        assert alerts.should_send(descriptor, last, now=NOW)

    def test_the_same_state_is_not_repeated_inside_the_week(self):
        descriptor = {"kind": alerts.KIND_RUNNER_BEHIND, "commits": 6,
                      "from_short": "abc1234"}
        last = {"key": alerts.alert_key(descriptor), "sent_at": NOW.isoformat()}
        assert not alerts.should_send(descriptor, last,
                                      now=NOW + timedelta(days=6, hours=23))

    def test_a_week_later_it_reminds_once(self):
        descriptor = {"kind": alerts.KIND_RUNNER_BEHIND, "commits": 6,
                      "from_short": "abc1234"}
        last = {"key": alerts.alert_key(descriptor), "sent_at": NOW.isoformat()}
        assert alerts.should_send(descriptor, last, now=NOW + timedelta(days=7))

    def test_a_worse_count_is_a_new_event(self):
        """5 commits behind and 40 behind are the same kind of problem and not the
        same situation: the second one must reach somebody even an hour later."""
        one = {"kind": alerts.KIND_RUNNER_BEHIND, "commits": 6, "from_short": "abc"}
        worse = {**one, "commits": 40}
        assert alerts.alert_key(one) != alerts.alert_key(worse)
        assert alerts.should_send(worse, {"key": alerts.alert_key(one),
                                          "sent_at": NOW.isoformat()}, now=NOW)

    def test_a_runner_fixed_and_drifting_again_is_a_new_event(self):
        one = {"kind": alerts.KIND_RUNNER_BEHIND, "commits": 6, "from_short": "abc"}
        again = {**one, "from_short": "def"}
        assert alerts.alert_key(one) != alerts.alert_key(again)

    def test_a_record_it_cannot_read_must_not_silence_the_alert(self):
        """The wrong answer here is "never again": a corrupt record is a reason to
        send one extra mail, not to stop reporting an unarmed box."""
        descriptor = {"kind": alerts.KIND_RUNNER_BEHIND, "commits": 6}
        last = {"key": alerts.alert_key(descriptor), "sent_at": "not a date"}
        assert alerts.should_send(descriptor, last, now=NOW)


# ── 3. who gets it ───────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        return _FakeResponse(self._rows)


class _FakeAuth:
    def __init__(self, users):
        self.users = users

    def list_users(self):
        return self.users


class _FakeClient:
    """Just enough Supabase for the two shapes the service reads."""

    def __init__(self, *, settings=None, profiles=None, users=None):
        self._settings = settings or []
        self._profiles = profiles or []
        self.auth = SimpleNamespace(admin=_FakeAuth(users or []))

    def table(self, name):
        return _FakeTable(self._settings if name == "system_settings" else self._profiles)


class TestWhoGetsIt:
    def test_the_setting_wins(self):
        who = alerts.recipients(
            settings={alerts.RECIPIENTS_KEY: "head@school.id, ops@school.id"},
            supabase=_FakeClient(profiles=[{"id": "p1"}],
                                 users=[SimpleNamespace(id="p1", email="ignored@x")]))
        assert who["emails"] == ["head@school.id", "ops@school.id"]
        assert who["source"] == alerts.SOURCE_SETTING

    def test_the_setting_is_split_generously_because_a_human_wrote_it(self):
        assert alerts.split_recipients("a@x\n b@x;\nc@x , a@x") == \
            ["a@x", "b@x", "c@x"]

    def test_something_that_is_not_an_address_is_dropped_rather_than_bounced(self):
        assert alerts.split_recipients("the head teacher, ok@x, ,<b@x>") == \
            ["ok@x", "b@x"]

    def test_an_empty_setting_falls_through_to_the_super_admins(self):
        who = alerts.recipients(
            settings={alerts.RECIPIENTS_KEY: "   "},
            supabase=_FakeClient(
                profiles=[{"id": "p1"}, {"id": "p2"}],
                users=[SimpleNamespace(id="p1", email="one@x"),
                       SimpleNamespace(id="p2", email="two@x"),
                       SimpleNamespace(id="p3", email="not-an-admin@x")]))
        assert who["emails"] == ["one@x", "two@x"]
        assert who["source"] == alerts.SOURCE_SUPER_ADMINS

    def test_a_database_that_cannot_be_reached_still_finds_a_way_to_a_human(self, monkeypatch):
        """A box unhealthy enough to need this alert may not answer a query, so the
        fallback is the account that would send the mail — flagged as the fallback
        it is, on the page."""
        monkeypatch.setattr(alerts, "_smtp_account", lambda: "ops@school.id")

        class _Broken:
            auth = SimpleNamespace(admin=SimpleNamespace())

            def table(self, _name):
                raise RuntimeError("no connection")

        who = alerts.recipients(supabase=_Broken())
        assert who["emails"] == ["ops@school.id"]
        assert who["source"] == alerts.SOURCE_SMTP

    def test_with_nowhere_to_go_it_says_so_rather_than_looking_armed(self, monkeypatch):
        monkeypatch.setattr(alerts, "_smtp_account", lambda: None)
        who = alerts.recipients(supabase=_FakeClient())
        assert who["emails"] == []
        assert who["source"] == alerts.SOURCE_NONE
        assert who["detail"], "a silent channel has to say why it is silent"

    def test_the_smtp_account_is_read_from_the_key_the_app_actually_uses(
            self, app, monkeypatch):
        monkeypatch.setitem(app.config, "SMTP_EMAIL", "ops@school.id")
        with app.app_context():
            assert alerts._smtp_account() == "ops@school.id"

    def test_every_source_the_service_can_name_is_one_of_four(self):
        assert alerts.RECIPIENT_SOURCES == {"setting", "super_admins", "smtp", "none"}


# ── 4. the tick ──────────────────────────────────────────────────────────────

def check(tmp_path, report, *, sends, now=NOW, **kwargs):
    """`check()` with a recording sender, so a test can count what left the box."""
    sent = []

    def sender(address, subject, html):
        sends.append((address, subject, html))
        return True

    return alerts.check(report=report, now=now, send=sender,
                        state=tmp_path / "deploy_alerts.json", **kwargs), sent


class TestTheTick:
    def test_a_stale_reading_sends_once_and_records_what_it_said(self, tmp_path):
        sends = []
        outcome, _ = check(tmp_path, report_of({"origin_behind": 9}), sends=sends,
                           recipients_info={"emails": ["ops@x"], "source": "setting"})
        assert outcome["outcome"] == alerts.OUTCOME_SENT
        assert [s[0] for s in sends] == ["ops@x"]
        record = alerts.read_state(tmp_path / "deploy_alerts.json")
        assert record["commits"] == 9
        assert record["to"] == ["ops@x"]
        assert record["sent_at"].startswith("2026-09-21")

    def test_the_next_tick_does_not_send_the_same_alert_again(self, tmp_path):
        sends = []
        recipients = {"emails": ["ops@x"], "source": "setting"}
        check(tmp_path, report_of({"origin_behind": 9}), sends=sends,
              recipients_info=recipients)
        outcome, _ = check(tmp_path, report_of({"origin_behind": 9}), sends=sends,
                           recipients_info=recipients,
                           now=NOW + timedelta(hours=6))
        assert outcome["outcome"] == alerts.OUTCOME_ALREADY_SENT
        assert len(sends) == 1

    def test_nothing_stale_means_nothing_is_sent(self, tmp_path):
        sends = []
        outcome, _ = check(tmp_path, report_of(), sends=sends,
                           recipients_info={"emails": ["ops@x"], "source": "setting"})
        assert outcome["outcome"] == alerts.OUTCOME_NOT_STALE
        assert sends == []
        assert not (tmp_path / "deploy_alerts.json").exists()

    def test_a_channel_with_nowhere_to_go_is_its_own_outcome(self, tmp_path):
        sends = []
        outcome, _ = check(tmp_path, report_of({"origin_behind": 9}), sends=sends,
                           recipients_info={"emails": [], "source": "none",
                                            "detail": "nothing configured"})
        assert outcome["outcome"] == alerts.OUTCOME_NO_RECIPIENTS
        assert sends == []

    def test_a_send_that_failed_is_not_recorded_so_the_next_tick_tries_again(self, tmp_path):
        """Recording a failure as sent would turn one SMTP wobble into silence for
        a week, which is the opposite of what this exists for."""
        outcome = alerts.check(report=report_of({"origin_behind": 9}), now=NOW,
                               send=lambda *_a: False,
                               state=tmp_path / "deploy_alerts.json",
                               recipients_info={"emails": ["ops@x"], "source": "setting"})
        assert outcome["outcome"] == alerts.OUTCOME_SEND_FAILED
        assert not (tmp_path / "deploy_alerts.json").exists()

    def test_one_worker_sends_and_the_others_stand_down(self, tmp_path):
        """Three gunicorn workers tick together; three identical emails is how a
        channel teaches people to filter it."""
        (tmp_path / "deploy_alerts.lock").write_text(NOW.isoformat(), encoding="utf-8")
        sends = []
        outcome, _ = check(tmp_path, report_of({"origin_behind": 9}), sends=sends,
                           recipients_info={"emails": ["ops@x"], "source": "setting"})
        assert outcome["outcome"] == alerts.OUTCOME_UNKNOWN
        assert sends == []

    def test_an_abandoned_claim_is_stolen_rather_than_blocking_forever(self, tmp_path):
        """A worker killed mid-send leaves the file behind; a claim that outlives
        its holder would silence every later alert."""
        stale = NOW - timedelta(seconds=alerts.STALE_CLAIM_SECONDS + 60)
        (tmp_path / "deploy_alerts.lock").write_text(stale.isoformat(), encoding="utf-8")
        sends = []
        outcome, _ = check(tmp_path, report_of({"origin_behind": 9}), sends=sends,
                           recipients_info={"emails": ["ops@x"], "source": "setting"})
        assert outcome["outcome"] == alerts.OUTCOME_SENT
        assert not (tmp_path / "deploy_alerts.lock").exists(), (
            "the claim has to be released, not left for the next tick to steal")

    def test_the_claim_is_released_even_when_sending_raises(self, tmp_path):
        def boom(*_a):
            raise RuntimeError("smtp exploded")

        with pytest.raises(RuntimeError):
            alerts.check(report=report_of({"origin_behind": 9}), now=NOW, send=boom,
                         state=tmp_path / "deploy_alerts.json",
                         recipients_info={"emails": ["ops@x"], "source": "setting"})
        assert not (tmp_path / "deploy_alerts.lock").exists()

    def test_a_box_with_no_writable_state_directory_says_so(self, tmp_path, monkeypatch):
        def boom(_app=None):
            raise OSError("read-only file system")

        monkeypatch.setattr(alerts, "state_dir", boom)
        outcome = alerts.check(report=report_of({"origin_behind": 9}), now=NOW,
                               send=lambda *_a: True)
        assert outcome["outcome"] == alerts.OUTCOME_CANNOT_READ
        assert "read-only" in outcome["detail"]


# ── where the record lives ───────────────────────────────────────────────────

class TestWhereTheRecordLives:
    """The choice of directory is load-bearing, not administrative.

    The record is what stops the same stale runner being mailed every tick, so a
    box that cannot write one mails forever. Production cannot use the checkout:
    the deploy pulls into it **as root**, so the service user cannot write a byte
    inside it, which is why the installer's directory is preferred and why the
    app's own instance folder is only the last resort.
    """

    def test_an_explicit_directory_is_used_alone(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCANGRADE_ALERT_STATE_DIR", str(tmp_path / "asked"))
        assert alerts.state_dir() == tmp_path / "asked"
        assert (tmp_path / "asked").is_dir(), "the directory it was told to use"

    def test_an_explicit_directory_that_cannot_be_written_is_an_error(self, tmp_path,
                                                                     monkeypatch):
        """Silently falling through would ignore what an operator asked for, and
        put the alert's state somewhere nobody looks."""
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("SCANGRADE_ALERT_STATE_DIR", str(blocker))
        with pytest.raises(OSError) as exc:
            alerts.state_dir()
        assert "SCANGRADE_ALERT_STATE_DIR" in str(exc.value)

    def test_the_installers_directory_wins_over_the_instance_folder(self, tmp_path,
                                                                   monkeypatch):
        made = tmp_path / "alerts"
        made.mkdir()
        monkeypatch.setattr(alerts, "DEPLOY_STATE_DIR", str(made))
        assert alerts.state_dir() == made

    def test_a_directory_that_is_not_there_is_skipped_rather_than_created(
            self, tmp_path, monkeypatch, app):
        """The app must not create anything under /var/lib/scangrade-deploy: its
        parent is root's 0750, and being unable to write there is what keeps a
        refusal record safe from the process it constrains."""
        missing = tmp_path / "nobody-made-this" / "alerts"
        monkeypatch.setattr(alerts, "DEPLOY_STATE_DIR", str(missing))
        chosen = alerts.state_dir(app)
        assert not missing.exists(), "the app created root's directory"
        assert chosen == Path(app.instance_path)

    def test_nothing_writable_is_an_error_the_page_can_report(self, tmp_path,
                                                              monkeypatch):
        """Reported, not silent: `check()` and `summary()` turn this into a
        visible outcome instead of a channel that looks armed."""
        monkeypatch.setattr(alerts, "DEPLOY_STATE_DIR", str(tmp_path / "absent"))
        monkeypatch.setattr(alerts.os, "access", lambda *_a, **_k: False)
        with pytest.raises(OSError) as exc:
            alerts.state_dir()
        assert "no writable directory" in str(exc.value)
        assert "SCANGRADE_ALERT_STATE_DIR" in str(exc.value), (
            "the error has to name the remedy")

    def test_the_summary_reports_it_rather_than_claiming_a_record(self, monkeypatch):
        def boom(_app=None):
            raise OSError("no writable directory for the alert record")

        monkeypatch.setattr(alerts, "state_dir", boom)
        summary = alerts.summary(recipients_info={"emails": ["ops@x"],
                                                 "source": "setting"})
        assert summary["state_dir"] is None
        assert "no writable directory" in summary["state_error"]

# ── the mail ─────────────────────────────────────────────────────────────────

class TestTheMail:
    def test_every_alert_kind_has_a_subject_and_an_action(self):
        for kind in sorted(alerts.ALERT_KINDS):
            descriptor = {"kind": kind, "commits": 7, "from_short": "abc1234",
                          "detail": {"paused": False}}
            message = alerts.render_email(descriptor, {"repo": "/opt/scangrade"},
                                          app_url="https://scangrade.web.id")
            assert message["subject"].startswith("[ScanGrade] "), kind
            assert "https://scangrade.web.id/super-admin/deploy-status" in message["html"]
            # The action is the point of the mail: a reader who does nothing else
            # should know what to do.
            assert len(message["html"].split("</table>")[-1]) > 40, kind

    def test_the_subject_names_the_count_so_the_inbox_can_be_read_from_the_list(self):
        message = alerts.render_email(
            {"kind": alerts.KIND_RUNNER_BEHIND, "commits": 12, "detail": {},
             "from_short": "abc1234"}, {"repo": "/opt/scangrade"})
        assert "12" in message["subject"]

    def test_the_checkout_alert_says_deploys_are_not_running(self):
        message = alerts.render_email(
            {"kind": alerts.KIND_CHECKOUT_BEHIND, "commits": 31,
             "detail": {"paused": True, "held": True, "held_since": "2026-09-19",
                        "held_gate": "theme gate"}, "from_short": None},
            {"repo": "/opt/scangrade"})
        assert "origin/main" in message["subject"]
        assert "31" in message["html"]

    def test_a_test_message_says_nothing_is_wrong(self):
        """A test mail indistinguishable from an incident is a test mail somebody
        acts on."""
        message = alerts.render_email({"kind": alerts.KIND_RUNNER_BEHIND,
                                       "commits": None, "detail": {}},
                                      {"repo": "/opt/scangrade"}, test=True)
        assert "test" in message["subject"].lower()
        assert "Nothing is wrong" in message["html"]
        assert "no alert has been raised" in message["html"]

    def test_the_reader_is_pointed_at_the_page_that_has_the_rest(self):
        message = alerts.render_email(
            {"kind": alerts.KIND_COPY_PREDATES_GATE, "commits": 45, "detail": {},
             "from_short": "abc1234"}, {"repo": "/opt/scangrade"},
            app_url="https://scangrade.web.id/")
        assert "https://scangrade.web.id/super-admin/deploy-status" in message["html"]


class TestTheTestButton:
    def test_it_sends_and_can_say_it_did(self, tmp_path):
        sends = []
        outcome = alerts.send_test(send=lambda a, s, h: sends.append((a, s, h)) or True,
                                   recipients_info={"emails": ["ops@x"],
                                                    "source": "setting"})
        assert outcome["outcome"] == alerts.OUTCOME_SENT
        assert sends[0][0] == "ops@x"

    def test_it_records_nothing_so_it_cannot_silence_a_real_alert(self, tmp_path):
        """A test that wrote the last-alert record would mute the very state it was
        sent to prove reachable."""
        alerts.send_test(send=lambda *_a: True,
                         recipients_info={"emails": ["ops@x"], "source": "setting"})
        assert not (tmp_path / "deploy_alerts.json").exists()

    def test_with_no_destination_it_says_there_was_nothing_to_send_to(self):
        sends = []
        outcome = alerts.send_test(send=lambda a, s, h: sends.append(a) or True,
                                   recipients_info={"emails": [], "source": "none"})
        assert outcome["outcome"] == alerts.OUTCOME_NO_RECIPIENTS
        assert sends == []

    def test_a_failed_test_send_is_reported_as_failed(self):
        outcome = alerts.send_test(send=lambda *_a: False,
                                   recipients_info={"emails": ["ops@x"],
                                                    "source": "setting"})
        assert outcome["outcome"] == alerts.OUTCOME_SEND_FAILED


# ── the page ─────────────────────────────────────────────────────────────────

def template_alert_kinds() -> set:
    return set(re.findall(r"A\.last\.kind == '([a-z_]+)'",
                          TEMPLATE.read_text(encoding="utf-8")))


def template_test_outcomes() -> set:
    return set(re.findall(r"testalert == '([a-z_]+)'",
                          TEMPLATE.read_text(encoding="utf-8")))


def template_recipient_sources() -> set:
    return set(re.findall(r"A\.source == '([a-z_]+)'",
                          TEMPLATE.read_text(encoding="utf-8")))


class TestThePage:
    def _render(self, app, report, alerts_summary, **kwargs):
        return base.render_status(app, report, alerts=alerts_summary, **kwargs)

    def test_every_alert_kind_has_a_sentence_in_both_languages(self):
        assert template_alert_kinds() == alerts.ALERT_KINDS, (
            "the card must be able to name every reason an alert was sent")

    def test_every_test_outcome_has_a_sentence_in_both_languages(self):
        assert template_test_outcomes() == alerts.TEST_ALERT_OUTCOMES

    def test_every_recipient_source_the_card_names_is_one_of_the_four(self):
        assert template_recipient_sources() <= alerts.RECIPIENT_SOURCES

    def test_the_card_names_the_addresses_the_threshold_and_the_cadence(self, app, tmp_path):
        summary = alerts.summary(state=tmp_path / "deploy_alerts.json",
                                 recipients_info={"emails": ["head@school.id"],
                                                  "source": "setting"},
                                 interval=6 * 3600)
        html = self._render(app, report_of(), summary)
        assert "head@school.id" in html
        assert "Runner Staleness Alerts" in html
        assert "from system_settings.deploy_alert_recipients" in html
        assert "one email per new state" in html
        # The threshold is the reading, so the number has to be on the page.
        assert str(summary["min_commits"]) in html

    def test_a_test_that_was_sent_is_confirmed_on_the_page(self, app, tmp_path):
        summary = alerts.summary(state=tmp_path / "deploy_alerts.json",
                                 recipients_info={"emails": ["ops@x"],
                                                  "source": "setting"})
        html = self._render(app, report_of(), summary, testalert="sent")
        assert "The test email was sent" in html

    def test_a_channel_with_nowhere_to_go_offers_no_test_button(self, app, tmp_path):
        summary = alerts.summary(state=tmp_path / "deploy_alerts.json",
                                 recipients_info={"emails": [], "source": "none",
                                                  "detail": "nothing configured"})
        html = self._render(app, report_of(), summary)
        assert "deploy-status/test-alert" not in html
        assert "Alerts cannot be sent yet" in html
        assert "nothing configured" in html, (
            "a silent channel has to show the reason it is silent")

    def test_the_last_alert_is_on_the_page_with_its_date(self, app, tmp_path):
        path = tmp_path / "deploy_alerts.json"
        alerts.write_state(path, {"key": "k", "kind": alerts.KIND_CHECKOUT_BEHIND,
                                  "commits": 31, "sent_at": "2026-09-20T03:00:00+00:00",
                                  "to": ["ops@x"], "subject": "s"})
        summary = alerts.summary(state=path,
                                 recipients_info={"emails": ["ops@x"],
                                                  "source": "setting"})
        html = self._render(app, report_of(), summary)
        assert "2026-09-20T03:00:00+00:00" in html
        assert "the checkout is behind origin/main" in html
        assert "31" in html

    def test_the_route_passes_the_summary_to_the_page(self):
        """A card whose data nobody passes is a card that renders the cold state on
        every box — including the boxes the alert exists for."""
        route = ROUTES.read_text(encoding="utf-8").split("def deploy_status(", 1)[1]
        block = route.split("\n@", 1)[0]
        assert "alerts = deploy_alert_summary()" in block
        assert "alerts=alerts" in block
        assert "testalert=request.args.get(\"testalert\")" in block, (
            "the outcome of a test send has to reach the card that answers for it")

    def test_the_test_button_is_a_guarded_post_form(self):
        source = ROUTES.read_text(encoding="utf-8")
        assert re.search(
            r'@super_bp\.route\("/deploy-status/test-alert", methods=\["POST"\]\)\s*\n'
            r"@_sa_required", source), "the test send is not a guarded POST"
        assert re.search(
            r'<form method="POST" action="/super-admin/deploy-status/test-alert"',
            TEMPLATE.read_text(encoding="utf-8")), (
            "base.html injects CSRF into POST forms; a fetch would go without it")

    def test_the_summary_is_cached_so_a_render_cannot_list_every_user(self, monkeypatch):
        """Resolving the fallback walks the whole auth user list, and this page's
        job is to answer immediately."""
        calls = []

        def counted(*_a, **_k):
            calls.append(1)
            return {"emails": ["ops@x"], "source": "setting", "detail": None}

        monkeypatch.setattr(alerts, "recipients", counted)
        first = alerts.summary(recipients_info=None, state=Path("alerts.json"))
        second = alerts.summary(recipients_info=None, state=Path("alerts.json"))
        assert first["to"] == second["to"] == ["ops@x"]
        assert len(calls) == 1, "the page paid for the resolution twice"
        alerts.summary(recipients_info=None, state=Path("alerts.json"), fresh=True)
        assert len(calls) == 2, "the button's own card must be able to ask fresh"

    def test_a_record_that_cannot_be_read_is_reported_on_the_card(self, app, tmp_path,
                                                                 monkeypatch):
        def boom(_app=None):
            raise PermissionError("[Errno 13] Permission denied")

        monkeypatch.setattr(alerts, "state_dir", boom)
        summary = alerts.summary(recipients_info={"emails": ["ops@x"],
                                                  "source": "setting"})
        assert summary["state_error"] and "Errno 13" in summary["state_error"]
        html = self._render(app, report_of(), summary)
        assert "The record cannot be written" in html


# ── the loop ─────────────────────────────────────────────────────────────────

class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class TestTheScheduler:
    def test_the_app_starts_the_loop(self):
        source = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
        assert "start_deploy_alert_scheduler(" in source, (
            "a service nobody starts is a service that never alerts")
        assert source.index("START_BACKGROUND_SCHEDULERS") < \
            source.index("start_deploy_alert_scheduler("), (
                "the loop must sit behind the same switch as the other two — a "
                "deploy probe constructs the app and must not start threads")

    def test_the_interval_and_the_threshold_are_configurable_without_a_release(self):
        """The alert exists for the case where the release machinery is not working,
        so arming or quietening it must not need a release. Asserted as assignments,
        not as mentions: a name that only survives in the comment beside its own
        deletion would satisfy a substring test."""
        text = CONFIG.read_text(encoding="utf-8")
        assert 'DEPLOY_ALERT_MIN_COMMITS = env_int("DEPLOY_ALERT_MIN_COMMITS"' in text
        assert 'DEPLOY_ALERT_INTERVAL_SECONDS = env_int(' in text
        assert 'DEPLOY_ALERT_STATE_DIR = env_str("SCANGRADE_ALERT_STATE_DIR"' in text
        # And the loop reads them rather than its own defaults.
        started = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
        assert 'app.config.get("DEPLOY_ALERT_INTERVAL_SECONDS")' in started

    def test_the_default_threshold_is_a_few_commits_not_a_few_dozen(self):
        assert 1 < alerts.DEFAULT_MIN_COMMITS <= 10

    def test_the_first_pass_waits_for_the_interval(self, monkeypatch):
        """An alert about a runner that was already stale belongs on the page, not
        in an inbox the instant the service restarts (which happens on every
        deploy)."""
        waited = {}

        class _Event:
            def wait(self, timeout):
                waited["timeout"] = timeout
                return True

            def clear(self):
                pass

            def set(self):
                pass

        monkeypatch.setattr(alerts, "_stop", _Event())
        alerts._loop(SimpleNamespace(app_context=lambda: _Ctx()), 1234)
        assert waited["timeout"] == 1234
