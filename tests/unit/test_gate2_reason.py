"""Why Gate 2 refused — a variable, or a file this account cannot open.

The construct probe (`RUN_STEP="construct"`, exit 9) is the one gate whose refusal
is almost always about the *environment* rather than about the code, and it was the
one refusal whose reason reached no card. Two records described it and neither named
it: `last-stop` carries the step and the code, and the quarantine record carries the
traceback's tail — which names the variables when they are missing, and names nothing
at all when the file holding them could not be opened.

That last case is the one worth building for, and it is the state this box was in: a
`.env` is `600 root:root` (the mode `docs/SECRET_ROTATION.md` prescribes), the probe
runs as the service account with no `EnvironmentFile=`, so nothing is loaded and the
refusal reads `Missing required env vars: SUPABASE_URL, SUPABASE_SERVICE_KEY` — while
those variables are sitting in that very file. An operator follows the record into
the file and finds them there.

So the reason is assembled from two facts and neither is a second copy of the other:

* the names are the **box's own words**, parsed out of the gate's recorded lines
  rather than re-derived (a second implementation of `Config.validate()` would
  disagree with it the first time either changed);
* whether the file can be opened is **measured**, not inferred — and it is measurable
  here because the page runs as the same account the probe runs as. The loader's own
  verdict on the same file (`app.config.DOTENV_ERROR`) is read beside it.

What these tests hold, in both directions: the page names the variable when the
record names one, names the *file* instead when the file is the cause, and says
nothing at all when the last refusal was not Gate 2 — a reason block on an unrelated
step would be a card claiming a cause it never measured.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import deploy_status_service as status

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "deploy_status.html"

SHA = "a" * 40
WHEN = "2026-10-14T09:00:00+00:00"
GATE_REASON = "app did not construct (exit 9)"

#: The two lines the probe actually prints for a missing variable, cut the way the
#: runner cuts them (`grep -vE '^[[:space:]]*$' | tail -n 6`).
MISSING_LINES = [
    "    cfg.validate()",
    "  File \"/opt/scangrade/app/config.py\", line 234, in validate",
    "RuntimeError: Missing required env vars: SUPABASE_URL, SUPABASE_SERVICE_KEY",
]
PLACEHOLDER_LINES = [
    "  File \"/opt/scangrade/app/__init__.py\", line 93, in create_app",
    "RuntimeError: FLASK_SECRET_KEY is unset or still the repository's placeholder; "
    "refusing to sign sessions with a publicly-known value",
]
CODE_FAULT_LINES = [
    "  File \"/opt/scangrade/app/routes/x.py\", line 3, in <module>",
    "ImportError: cannot import name 'y' from 'app.utils'",
]


def record(tmp_path: Path, *, sha: str = SHA, reason: str = GATE_REASON,
           lines: list[str] | None = None) -> Path:
    """A quarantine record exactly as the runner writes one: sha · when · why · tail."""
    path = tmp_path / "quarantined"
    body = "\n".join([sha, WHEN, reason] + list(lines or []))
    path.write_text(body + "\n", encoding="utf-8")
    return path


def step_record(tmp_path: Path, *, step: str = "construct", code: int = 9) -> Path:
    path = tmp_path / "last-stop"
    path.write_text(f"{step}\n{WHEN}\n{code}\n{SHA}\n", encoding="utf-8")
    return path


def gate2_report(tmp_path: Path, *, env_file: str | None = None,
                 lines: list[str] | None = None, step: str = "construct",
                 code: int = 9, reason: str = GATE_REASON) -> dict:
    """A box whose last run stopped in the construct gate."""
    return status.report(
        repo=str(tmp_path), runner="/nonexistent", snapshot_runner="/nonexistent",
        pause_file=str(tmp_path / "no-pause"),
        quarantine_file=str(record(tmp_path, reason=reason, lines=lines)),
        last_stop_file=str(step_record(tmp_path, step=step, code=code)),
        request_dir=str(tmp_path / "requests"),
        env_file=env_file if env_file is not None else str(tmp_path / "no.env"),
    )


def render_status(app, report) -> str:
    from flask import g, render_template
    with app.test_request_context("/super-admin/deploy-status"):
        g.user_id = "a-super-admin"
        g.user_role = "super_admin"
        g.user_name = "Tester"
        g.user_email = "t@t"
        g.tz_offset = 7
        return render_template("super_admin/deploy_status.html", status=report,
                               alerts={}, testalert=None, released=None)


# ── the reading ──────────────────────────────────────────────────────────────

class TestTheReasonTheConstructGateRefused:
    def test_the_variables_are_the_records_own_words(self, tmp_path):
        state = gate2_report(tmp_path, lines=MISSING_LINES)["construct"]
        assert state["relevant"] is True
        assert state["because"] == "last_stop", (
            "the step record is what says this run stopped in Gate 2")
        assert state["reason_key"] == status.CONSTRUCT_MISSING_VARS
        assert state["variables"] == ["SUPABASE_URL", "SUPABASE_SERVICE_KEY"], (
            "the names come from the traceback the box wrote, not from a re-derivation")
        assert state["variables_source"] == "record"

    def test_the_names_are_read_in_the_gates_own_order(self, tmp_path):
        state = gate2_report(
            tmp_path,
            lines=["RuntimeError: Missing required env vars: SECRET_KEY, SUPABASE_URL"],
        )["construct"]
        assert state["variables"] == ["SECRET_KEY", "SUPABASE_URL"]

    def test_a_punctuated_message_still_names_its_variables(self):
        """The record is a file, and a file can be edited or written differently.

        A full stop after the list — or a name followed by one — must not hide the
        variable, because the page would then send the reader after nothing.
        """
        assert status._named_variables([
            "RuntimeError: Missing required env vars: SUPABASE_URL, SUPABASE_SERVICE_KEY.",
        ]) == ["SUPABASE_URL", "SUPABASE_SERVICE_KEY"]

    def test_a_word_where_a_name_goes_is_not_a_name(self):
        """The sentence is prose with a list in it, so it can carry prose after it.

        Rendering `and a key nobody named` where a variable goes would be a worse
        answer than naming only what is a name.
        """
        assert status._named_variables([
            "RuntimeError: Missing required env vars: SUPABASE_URL, and the rest",
        ]) == ["SUPABASE_URL"]

    def test_an_unreadable_environment_file_is_named_as_the_cause(self, tmp_path):
        """The state this box was in: the variables are in the file, unread by it."""
        env = tmp_path / ".env"
        env.write_text("SUPABASE_URL=https://example.supabase.co\n", encoding="utf-8")
        real_open = Path.open

        def deny(self, *args, **kwargs):
            if str(self) == str(env):
                raise PermissionError(13, "Permission denied")
            return real_open(self, *args, **kwargs)

        original = Path.open
        Path.open = deny
        try:
            state = gate2_report(tmp_path, env_file=str(env),
                                 lines=MISSING_LINES)["construct"]
        finally:
            Path.open = original

        assert state["reason_key"] == status.CONSTRUCT_ENV_UNREADABLE, (
            "the file is the cause: with nothing loaded, every required variable "
            "reads as missing, and that is the sentence an operator must not get")
        assert state["env_file"]["exists"] is True, (
            "an unreadable file is not a missing one, and the two have different fixes")
        assert state["env_file"]["readable"] is False
        assert "PermissionError" in state["env_file"]["error"], (
            "the page names the error, because that is what the mode did")
        assert state["variables"] == ["SUPABASE_URL", "SUPABASE_SERVICE_KEY"], (
            "what the probe said is still shown — it is the symptom the cause explains")

    def test_a_readable_file_makes_the_missing_variables_genuinely_missing(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("FLASK_ENV=production\n", encoding="utf-8")
        state = gate2_report(tmp_path, env_file=str(env), lines=MISSING_LINES)["construct"]
        assert state["reason_key"] == status.CONSTRUCT_MISSING_VARS
        assert state["env_file"]["readable"] is True

    def test_no_environment_file_is_not_reported_as_unreadable(self, tmp_path):
        """A box whose variables come from systemd is normal, not broken."""
        state = gate2_report(tmp_path, lines=MISSING_LINES)["construct"]
        assert state["env_file"]["exists"] is False
        assert state["env_file"]["readable"] is None, (
            "\"not there\" and \"cannot be read\" are different answers")
        assert state["reason_key"] == status.CONSTRUCT_MISSING_VARS

    def test_the_placeholder_secret_is_named(self, tmp_path):
        state = gate2_report(tmp_path, lines=PLACEHOLDER_LINES)["construct"]
        assert state["reason_key"] == status.CONSTRUCT_PLACEHOLDER_SECRET
        assert state["variables"] == ["FLASK_SECRET_KEY"]

    def test_a_fault_that_is_not_the_environment_keeps_the_gates_own_lines(self, tmp_path):
        state = gate2_report(tmp_path, lines=CODE_FAULT_LINES)["construct"]
        assert state["reason_key"] == status.CONSTRUCT_OTHER, (
            "nothing in the record is about a variable or a file, and the page must "
            "not invent one")
        assert state["variables"] == []
        assert any("ImportError" in line for line in state["lines"]), (
            "the gate's own words travel, so the reader has the fault itself")

    def test_a_held_commit_refused_there_is_enough_without_a_step_record(self, tmp_path):
        report = status.report(
            repo=str(tmp_path), runner="/nonexistent", snapshot_runner="/nonexistent",
            pause_file=str(tmp_path / "no-pause"),
            quarantine_file=str(record(tmp_path, lines=MISSING_LINES)),
            last_stop_file=str(tmp_path / "no-last-stop"),
            request_dir=str(tmp_path / "requests"),
            env_file=str(tmp_path / "no.env"))
        state = report["construct"]
        assert state["relevant"] is True
        assert state["because"] == "quarantine", (
            "the two records describe one refusal, and either can be the survivor")
        assert state["variables"] == ["SUPABASE_URL", "SUPABASE_SERVICE_KEY"]

    def test_a_fault_in_the_code_is_never_blamed_on_the_file(self, tmp_path):
        """An unreadable `.env` is common on a correctly-configured box.

        The units there supply the variables (`EnvironmentFile=`), so the file being
        unreadable says nothing about a refusal that never mentioned a variable — and
        a card that blamed it would send an operator to change a file mode that is
        exactly right.
        """
        env = tmp_path / ".env"
        env.write_text("X=1\n", encoding="utf-8")
        real_open = Path.open

        def deny(self, *args, **kwargs):
            if str(self) == str(env):
                raise PermissionError(13, "Permission denied")
            return real_open(self, *args, **kwargs)

        original = Path.open
        Path.open = deny
        try:
            state = gate2_report(tmp_path, env_file=str(env),
                                 lines=CODE_FAULT_LINES)["construct"]
        finally:
            Path.open = original

        assert state["env_file"]["readable"] is False, "the reading is still shown"
        assert state["reason_key"] == status.CONSTRUCT_OTHER, (
            "but it is not the reason: nothing in the record named a variable")

    def test_the_apps_own_required_list_is_never_the_missing_list(self, tmp_path):
        """The page's process is not the probe's, and the two can disagree."""
        state = gate2_report(tmp_path, lines=CODE_FAULT_LINES)["construct"]
        assert state["required"] == ["SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SECRET_KEY"]
        assert state["variables"] == [], (
            "a list the probe never produced must not be presented as what it said")

    def test_another_step_is_not_gate_2(self, tmp_path):
        """Neither record describes a construct refusal, so there is no reason here."""
        state = gate2_report(tmp_path, step="fetch", code=5, lines=MISSING_LINES,
                             reason="served commit (the app named another commit)")[
                                 "construct"]
        assert state["relevant"] is False
        assert state["reason_key"] == status.CONSTRUCT_NONE
        assert state["variables"] == []

    def test_the_reader_never_raises_on_a_record_that_is_not_text(self, tmp_path):
        """The page that answers \"why\" must not be the page that 500s."""
        report = status.report(
            repo=str(tmp_path), runner="/nonexistent", snapshot_runner="/nonexistent",
            pause_file=str(tmp_path / "no-pause"),
            quarantine_file=str(record(tmp_path)),
            last_stop_file=str(step_record(tmp_path)),
            request_dir=str(tmp_path / "requests"),
            env_file=str(tmp_path / "no.env"))
        report["quarantine"]["reasons"] = [None, 5, "", "   "]
        state = status.construct_state(report["last_stop"], report["quarantine"],
                                       env_path="", dotenv_error="", required=())
        assert state["relevant"] is True
        assert state["lines"] == []
        assert state["reason_key"] == status.CONSTRUCT_OTHER

    def test_every_reason_key_is_in_the_vocabulary(self, tmp_path):
        seen = {
            gate2_report(tmp_path, lines=MISSING_LINES)["construct"]["reason_key"],
            gate2_report(tmp_path, lines=PLACEHOLDER_LINES)["construct"]["reason_key"],
            gate2_report(tmp_path, lines=CODE_FAULT_LINES)["construct"]["reason_key"],
        }
        assert seen <= status.CONSTRUCT_REASONS
        assert status.CONSTRUCT_NONE in status.CONSTRUCT_REASONS


# ── the page ─────────────────────────────────────────────────────────────────

def block_only(app, tmp_path: Path, **kw) -> str:
    """The lines the reason block adds, read by rendering the page with and without it.

    The page quotes the gate's traceback in the quarantine card already, so "is the
    variable on the page" is not a question about *this* card — the difference between
    a construct refusal and any other step is, and that is what a reader sees.
    """
    with_block = render_status(app, gate2_report(tmp_path, **kw))
    without = render_status(app, gate2_report(tmp_path, step="fetch", code=5,
                                              lines=kw.get("lines"),
                                              reason="served commit (x)"))
    known = set(without.splitlines())
    return "\n".join(line for line in with_block.splitlines() if line not in known)


class TestThePageNamesIt:
    def test_the_last_stop_card_names_the_variable(self, app, tmp_path):
        added = block_only(app, tmp_path, lines=MISSING_LINES)
        assert "Kenapa Gate 2 menolak" in added and "Why Gate 2 refused" in added, (
            "both halves of the pair are rendered for the toggle to choose between")
        assert "Disebut pemeriksaan" in added and "Named by the probe" in added, (
            "the names are labelled as the probe's own, not as the app's rule")
        assert "SUPABASE_SERVICE_KEY" in added, (
            "the card that carries the step and the code has to carry the variable "
            "the refusal was about")

    def test_the_page_names_the_file_when_the_file_is_the_cause(self, app, tmp_path):
        env = tmp_path / ".env"
        env.write_text("SUPABASE_URL=https://example.supabase.co\n", encoding="utf-8")
        real_open = Path.open

        def deny(self, *args, **kwargs):
            if str(self) == str(env):
                raise PermissionError(13, "Permission denied")
            return real_open(self, *args, **kwargs)

        original = Path.open
        Path.open = deny
        try:
            state = gate2_report(tmp_path, env_file=str(env), lines=MISSING_LINES)
            added = block_only(app, tmp_path, env_file=str(env), lines=MISSING_LINES)
        finally:
            Path.open = original

        assert state["construct"]["reason_key"] == status.CONSTRUCT_ENV_UNREADABLE
        assert str(env) in added, "the page names the file it measured"
        assert "TIDAK terbaca oleh akun ini" in added and \
            "NOT readable by this account" in added, (
                "the reading is stated, not implied: 'missing variables' is the "
                "symptom and 'this account cannot open the file' is the cause")
        # Both halves on purpose: a remedy rendered only in the language half the
        # reader is not looking at is a remedy they do not have.
        assert "chmod 640 dengan grup akun itu" in added, "the Indonesian half"
        assert "chmod 640 with the group of that account" in added, "the English half"
        assert added.count("EnvironmentFile=") >= 2, (
            "the alternative remedy — the unit's EnvironmentFile= — is in both halves")
        assert "SUPABASE_SERVICE_KEY" in added

    def test_the_apps_own_required_list_is_not_shown_without_a_refusal(self, app, tmp_path):
        """`required` beside a refusal that named nothing is what the block must not do."""
        added = block_only(app, tmp_path, lines=CODE_FAULT_LINES)
        assert "SUPABASE_SERVICE_KEY" not in added
        assert "Wajib ada, menurut aplikasinya" not in added
        assert "What the app requires" not in added, (
            "a list the probe never produced, presented under a refusal that named "
            "no variable, is how a reader chases a variable that was never the cause")

    def test_the_card_says_which_record_the_reason_came_from(self, app, tmp_path):
        """The step on screen is not always the gate that refused.

        While a commit is held, a later run can stop somewhere else entirely and
        overwrite the last-stop record; the reason then comes off the held commit's
        own record, and a reader who assumed the step above was the refusing gate
        would go looking for the wrong run in the journal.
        """
        added = block_only(app, tmp_path, step="fetch", code=5, lines=MISSING_LINES)
        assert "Sebabnya dibaca dari catatan commit yang ditahan" in added, "Indonesian"
        assert "read from the held commit record" in added, "English"
        assert "SUPABASE_SERVICE_KEY" in added, "and the names come off that record"

    def test_a_step_that_is_not_gate_2_gets_no_reason_block(self, app, tmp_path):
        text = render_status(
            app, gate2_report(tmp_path, step="fetch", code=5, lines=MISSING_LINES,
                              reason="served commit (the app named another commit)"))
        assert "Why Gate 2 refused" not in text, (
            "a reason block on an unrelated step is a card claiming a cause it never "
            "measured")
        assert "Named by the probe" not in text
        assert "NOT readable by this account" not in text, (
            "the reading is measured on every load, but a step that did not refuse "
            "for that reason must not carry it")

    def test_a_healthy_box_shows_no_reason_at_all(self, app, tmp_path):
        report = status.report(
            repo=str(tmp_path), runner="/nonexistent", snapshot_runner="/nonexistent",
            pause_file=str(tmp_path / "no-pause"),
            quarantine_file=str(tmp_path / "no-quarantine"),
            last_stop_file=str(tmp_path / "no-last-stop"),
            request_dir=str(tmp_path / "requests"),
            env_file=str(tmp_path / "no.env"))
        text = render_status(app, report)
        assert "Why Gate 2 refused" not in text
        assert report["construct"]["reason_key"] == status.CONSTRUCT_NONE

    def test_the_env_file_row_is_not_rendered_as_a_path_with_no_reading(self, app, tmp_path):
        """`unknown` and `no file here` must not look the same on the card."""
        text = render_status(app, gate2_report(tmp_path, lines=MISSING_LINES))
        assert "not there — its variables come from systemd" in text or \
            "tidak ada di sini" in text


# ── and the rule itself is written once ──────────────────────────────────────

class TestTheRequiredListIsTheRule:
    @pytest.mark.parametrize("blank", ["SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SECRET_KEY"])
    def test_validate_raises_from_the_same_list_the_page_names(self, blank):
        """A second copy of the rule is how a page names a variable that is not it.

        Asked of every variable in the list, one at a time: a refusal that reads its
        own copy of the rule agrees about whichever names that copy happens to hold,
        and disagrees about the one it left out — which is exactly the variable an
        operator would then go looking for in the wrong place.
        """
        from app import config as app_config

        real = {"SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_SERVICE_KEY": "a-service-key",
                "SECRET_KEY": "a-real-secret"}
        cls = type("Incomplete", (app_config.ProductionConfig,),
                   {**real, blank: ""})

        assert cls.missing_required() == [blank]
        with pytest.raises(RuntimeError) as refusal:
            cls.validate()
        assert str(refusal.value) == f"Missing required env vars: {blank}"
        assert tuple(app_config.ProductionConfig.REQUIRED_ENV) == (
            "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SECRET_KEY"), (
            "the list the page names is the one the app refuses on")

    def test_the_env_file_the_reader_names_is_the_one_the_loader_reads(self):
        from app import config as app_config
        from dotenv import find_dotenv
        assert app_config.env_file_path() == (find_dotenv(usecwd=False) or ""), (
            "the reader must not name a file the loader is not reading")


# ── the template is guarded for its own sake ─────────────────────────────────

def test_the_reason_block_stays_out_of_the_step_record():
    """The runner is not changed by this: the page reads what it already wrote."""
    template = TEMPLATE.read_text(encoding="utf-8")
    assert "status.construct" in template
    assert "status.construct.relevant" not in template, (
        "a dot-path in a Jinja condition is not what the neighbouring cards do; the "
        "reading is bound to a name first so the block reads as one thing")


def test_the_audit_trail_of_the_reader_is_a_measurement_and_not_a_guess():
    """`os.access` answers about the *real* uid and lies under a different one."""
    source = (ROOT / "app" / "services" / "deploy_status_service.py").read_text(
        encoding="utf-8")
    body = source.split("def env_file_state(", 1)[1].split("\ndef ", 1)[0]
    assert "os.access" not in body, (
        "the readability has to be the syscall the probe's own boot makes, not a "
        "permission query that can disagree with it")
    assert ".open(" in body
