"""A box-local edit the release also writes is set aside, and the page says so.

A dirty checkout used to be a permanent stop: `scangrade-deploy.sh` refused on any
local change *before* it fetched, so a box holding one hand edit could never receive
the release that would have cleared it. The runner now sets the box's version of each
overlapping path aside — the diff for a path HEAD has, the whole file for one it does
not — restores the path, and merges.

`tests/unit/test_box_edits.py` holds what that heal does to a real tree. This is the
other half, and it is the half that made the deadlock invisible for hours: the state
directory is only useful if an operator can see inside it without a shell.

What these tests hold:

* **Three answers, and `unreadable` is never `none`.** A box that has never had a heal
  is not a box whose record could not be read, and the second is the one that starts
  reading as the first.
* **Nothing is presented as deleted.** The card names where the preserved bytes are —
  the patch file, the snapshot directory — and says that a path it left alone was left
  alone.
* **A heal is visible in the verdict**, which is what a stalled box's page is for: it
  explains a checkout that reads clean because the box's change is in the state
  directory now. A refusal still outranks it, because a refusal is a stop — and a
  checkout that is dirty *now* does too, because the heal is an event and the dirty
  tree is the fact the page just read.
* **The page has a sentence for the key, in both languages.**
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services import deploy_status_service as status  # noqa: E402

TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "deploy_status.html"
RUNNER = ROOT / "deploy" / "scangrade-deploy.sh"

SHA_A = "a" * 40
SHA_B = "b" * 40
WHEN = "2026-09-26T05:00:00+00:00"


def _now():
    import datetime as dt
    return dt.datetime(2026, 9, 26, 6, 0, tzinfo=dt.timezone.utc)


def record_text(sha: str = SHA_A, *, when: str = WHEN,
                set_aside: tuple[str, ...] = ("app/routes/admin_sekolah.py",),
                stale: tuple[str, ...] = (), kept: tuple[str, ...] = (),
                patch: str | None = "patch",
                files: str = "") -> str:
    """One heal record, in the positional format `box_edits_record` writes."""
    lines = [when, sha,
             f"patch {patch or ''}",
             f"files {files}"]
    lines += [f"set-aside {path}" for path in set_aside]
    lines += [f"stale {path}" for path in stale]
    lines += [f"kept {path}" for path in kept]
    return "\n".join(lines) + "\n"


def edits_dir(tmp_path: Path, entries: list[tuple[str, str]]) -> Path:
    """A set-aside directory as the runner names it: `<stamp>-<sha12>-<pid>.txt`."""
    directory = tmp_path / "set-aside"
    directory.mkdir(exist_ok=True)
    for name, text in entries:
        (directory / (name + ".txt")).write_text(text, encoding="utf-8")
    return directory


# ── the reader ───────────────────────────────────────────────────────────────

class TestTheReader:
    def test_an_absent_directory_is_none_rather_than_an_error(self, tmp_path):
        state = status.box_edits_state(tmp_path / "missing", tmp_path, now=_now())
        assert state["key"] == status.BOX_EDITS_NONE
        assert state["records"] == [] and state["total"] == 0
        assert state["present"] is False

    def test_an_empty_directory_is_none_too(self, tmp_path):
        (tmp_path / "set-aside").mkdir()
        state = status.box_edits_state(tmp_path / "set-aside", tmp_path, now=_now())
        assert state["key"] == status.BOX_EDITS_NONE

    def test_records_are_read_newest_first(self, tmp_path):
        directory = edits_dir(tmp_path, [
            ("20260926T050001Z-aaaaaaaaaaaa-1", record_text(SHA_A)),
            ("20260926T050003Z-cccccccccccc-3", record_text(SHA_B)),
            ("20260926T050002Z-bbbbbbbbbbbb-2", record_text(SHA_A)),
        ])
        state = status.box_edits_state(directory, tmp_path, now=_now())
        assert state["key"] == status.BOX_EDITS_PRESENT
        assert [r["name"][:16] for r in state["records"]] == [
            "20260926T050003Z", "20260926T050002Z", "20260926T050001Z"], (
            "the newest heal is the one an operator is asking about, and the name "
            "carries the time exactly so no record has to be stat'ed to find it")

    def test_only_the_newest_are_returned_but_all_found_are_counted(self, tmp_path):
        directory = edits_dir(tmp_path, [
            (f"20260926T05000{i}Z-{'%012x' % i}-{i}", record_text(SHA_A))
            for i in range(1, 7)
        ])
        state = status.box_edits_state(directory, tmp_path, now=_now(), limit=2)
        assert state["total"] == 6, "the count is what exists, not what is shown"
        assert len(state["records"]) == 2

    def test_the_newest_record_is_flattened_onto_the_state(self, tmp_path):
        patch = tmp_path / "heal.patch"
        patch.write_text("diff --git a/x b/x\n+the box's line\n", encoding="utf-8")
        directory = edits_dir(tmp_path, [
            ("20260926T050001Z-aaaaaaaaaaaa-1",
             record_text(SHA_A, set_aside=("app/routes/admin_sekolah.py",),
                         kept=("notes.txt",), patch=str(patch))),
        ])
        state = status.box_edits_state(directory, tmp_path, now=_now())
        assert state["short"] == "aaaaaaa"
        assert state["set_aside"] == ["app/routes/admin_sekolah.py"]
        assert state["set_aside_total"] == 1
        assert state["kept"] == ["notes.txt"]
        assert state["patch"] == str(patch)
        assert state["patch_bytes"] == patch.stat().st_size, (
            "the card must be able to say the preserved bytes are really there")
        assert state["age_seconds"] is not None

    def test_a_path_set_aside_for_being_stale_says_so(self, tmp_path):
        """Two reasons start the set-aside now, and they need different answers from
        an operator: one means the release wrote the file, the other means nobody had
        touched it for a day and the runner decided it was abandoned."""
        directory = edits_dir(tmp_path, [
            ("20260926T050001Z-aaaaaaaaaaaa-1",
             record_text(SHA_A, set_aside=("notes.txt",), stale=("notes.txt",),
                         kept=("README.md",))),
        ])
        state = status.box_edits_state(directory, tmp_path, now=_now())
        assert state["set_aside"] == ["notes.txt"], (
            "the preserved copy is not named at all, so the record does not say where "
            "the box's version went")
        assert state["stale"] == ["notes.txt"], (
            "the record does not say the edit was old rather than overlapping")
        assert state["stale_total"] == 1

    def test_a_path_set_aside_because_the_release_writes_it_is_not_called_stale(
            self, tmp_path):
        """A record written before the staleness rule existed — and every record for
        an overlap — has no `stale` line, and must not read as if it had one."""
        directory = edits_dir(tmp_path, [
            ("20260926T050001Z-aaaaaaaaaaaa-1", record_text(SHA_A)),
        ])
        state = status.box_edits_state(directory, tmp_path, now=_now())
        assert state["stale"] == [] and state["stale_total"] == 0

    def test_a_record_that_cannot_be_read_is_reported_and_not_dropped(self, tmp_path):
        directory = edits_dir(tmp_path, [
            ("20260926T050001Z-aaaaaaaaaaaa-1", record_text(SHA_A)),
        ])
        # A directory named like a record: it has the suffix the reader looks for
        # and cannot be read, which is the only way to reach the unreadable branch
        # without a permission bit Windows will not honour.
        (directory / "20260926T050002Z-bbbbbbbbbbbb-2.txt").mkdir()
        state = status.box_edits_state(directory, tmp_path, now=_now())
        assert state["total"] == 2, "a file this reader cannot read is still a file"
        unreadable = [r for r in state["records"] if r["unreadable"]]
        assert unreadable and unreadable[0]["detail"], (
            "a record that could not be read was dropped, so the directory reads as "
            "one heal smaller than it is")
        assert any(not r["unreadable"] for r in state["records"]), (
            "one unreadable record hid the readable ones")

    def test_only_the_runners_own_extension_is_read(self, tmp_path):
        directory = edits_dir(tmp_path, [
            ("20260926T050001Z-aaaaaaaaaaaa-1", record_text(SHA_A)),
        ])
        (directory / "notes.md").write_text("ignored\n", encoding="utf-8")
        (directory / "20260926T050002Z-bbbbbbbbbbbb-2.patch").write_text(
            record_text(SHA_B), encoding="utf-8")
        state = status.box_edits_state(directory, tmp_path, now=_now())
        assert state["total"] == 1, (
            "a stray file in the directory was counted as a heal record")

    def test_the_age_travels_with_each_record(self, tmp_path):
        directory = edits_dir(tmp_path, [
            ("20260926T050001Z-aaaaaaaaaaaa-1", record_text(SHA_A)),
        ])
        state = status.box_edits_state(directory, tmp_path, now=_now())
        assert state["records"][0]["age_seconds"] is not None

    def test_it_touches_nothing(self, tmp_path):
        """A page that moves or prunes the evidence is a page that can lose it."""
        directory = edits_dir(tmp_path, [
            ("20260926T050001Z-aaaaaaaaaaaa-1", record_text(SHA_A)),
        ])
        before = sorted(p.name for p in directory.iterdir())
        status.box_edits_state(directory, tmp_path, now=_now())
        assert sorted(p.name for p in directory.iterdir()) == before


# ── what a heal adds up to ───────────────────────────────────────────────────

class TestWhatAHealAddsUpTo:
    _RUNNER = {"kind": "launcher", "reason_key": None, "detail": None,
               "gate0": status.GATE0_PASSES, "matches_this_commit": True}

    def _checkout(self, **kw) -> dict:
        return {"available": True, "reason_key": None, "detail": None,
                "behind": 0, "dirty": 0, **kw}

    def _state(self, tmp_path, **kw):
        directory = edits_dir(tmp_path, [
            ("20260926T050001Z-aaaaaaaaaaaa-1",
             record_text(SHA_A, kept=("notes.txt",))),
        ])
        return status.box_edits_state(directory, tmp_path, now=_now(), **kw)

    def test_a_heal_is_named_rather_than_left_as_a_clean_box(self, tmp_path):
        state = self._state(tmp_path)
        verdict = status.verdict(self._RUNNER, self._checkout(), paused=False,
                                 box_edits=state)
        assert verdict["key"] == "box_edits"
        assert verdict["level"] == status.WARN
        assert verdict["detail"] == "aaaaaaa", (
            "the verdict names the commit the box's edit was moved aside for, "
            "which is the one thing a reader needs to find the record")

    def test_a_checkout_dirty_now_outranks_a_standing_heal(self, tmp_path):
        """A heal is an event; a dirty tree is a fact read at this moment.

        Measured on the live box, 2026-10-01: a heal record from the day before
        held the verdict at `box_edits` while the checkout was genuinely dirty, so
        the page never rendered the *kind* of dirty it was holding — the one card
        this whole reading exists for. The heal is still named on its own card; the
        verdict says what is true now.
        """
        state = self._state(tmp_path)
        verdict = status.verdict(self._RUNNER, self._checkout(dirty=2),
                                 paused=False, box_edits=state)
        assert verdict["key"] == "dirty", (
            "a checkout that is dirty now must be the verdict, not a heal from "
            "before it")
        assert verdict["level"] == status.WARN

    def test_a_refusal_still_outranks_a_heal(self, tmp_path):
        """A refusal is a stop; a heal is an intervention that let a release go."""
        state = self._state(tmp_path)
        verdict = status.verdict(
            self._RUNNER, self._checkout(), paused=False, box_edits=state,
            preflight={"present": True, "gate_key": "dirty_checkout",
                       "gate": "dirty_checkout"})
        assert verdict["key"] == "refused"

    def test_the_waiting_commit_count_is_still_reported(self, tmp_path):
        state = self._state(tmp_path)
        verdict = status.verdict(self._RUNNER, self._checkout(behind=3),
                                 paused=False, box_edits=state)
        assert verdict["behind"] == 3, (
            "a heal must not hide how far behind the box still is")

    def test_a_box_with_no_heal_reads_exactly_as_before(self, tmp_path):
        empty = status.box_edits_state(tmp_path / "missing", tmp_path, now=_now())
        assert status.verdict(self._RUNNER, self._checkout(), paused=False,
                              box_edits=empty)["key"] == "fresh"
        assert status.verdict(self._RUNNER, self._checkout(behind=1),
                              paused=False, box_edits=empty)["key"] == "behind"

    def test_an_unreadable_directory_is_not_a_heal(self, tmp_path, monkeypatch):
        """`unreadable` means the page cannot say; it does not mean a heal happened."""
        def boom(self):
            raise OSError("permission denied")

        with monkeypatch.context() as m:
            m.setattr(Path, "iterdir", boom)
            state = status.box_edits_state(tmp_path / "set-aside", tmp_path,
                                           now=_now())
        assert state["key"] == status.BOX_EDITS_UNREADABLE
        assert status.verdict(self._RUNNER, self._checkout(), paused=False,
                              box_edits=state)["key"] == "fresh"

    def test_the_report_carries_the_state_and_its_directory(self, tmp_path):
        report = status.report(repo=str(tmp_path), runner="/nonexistent",
                               snapshot_runner="/nonexistent",
                               pause_file=str(tmp_path / "no-pause"),
                               box_edits_dir=str(tmp_path / "set-aside"))
        assert report["box_edits"]["key"] == status.BOX_EDITS_NONE
        assert report["box_edits_dir"].endswith("set-aside")


# ── the page ─────────────────────────────────────────────────────────────────

def render_status(app, report) -> str:
    from flask import g, render_template
    with app.test_request_context("/super-admin/deploy-status"):
        g.user_id = "a-super-admin"
        g.user_role = "super_admin"
        g.user_name = "Tester"
        g.user_email = "t@t"
        g.tz_offset = 7
        return render_template("super_admin/deploy_status.html", status=report,
                               alerts={"interval_seconds": 21600}, testalert=None,
                               released=None)


class TestThePage:
    def report(self, tmp_path, directory):
        # Every path the page reads is pinned: `report()` falls back to the real
        # box's state files, and a page that renders another box's leftovers makes
        # this suite depend on the machine rather than on the fixture.
        return status.report(
            repo=str(tmp_path), runner="/nonexistent", snapshot_runner="/nonexistent",
            pause_file=str(tmp_path / "no-pause"),
            quarantine_file=str(tmp_path / "no-quarantine"),
            unarmed_file=str(tmp_path / "no-unarmed"),
            preflight_file=str(tmp_path / "no-preflight"),
            last_stop_file=str(tmp_path / "no-last-stop"),
            request_dir=str(tmp_path / "no-requests"),
            release_request=str(tmp_path / "no-release"),
            box_edits_dir=str(directory))

    def _record(self, tmp_path, *, kept=(), when=WHEN):
        patch = tmp_path / "heal.patch"
        patch.write_text("diff --git a/x b/x\n+the box's line\n", encoding="utf-8")
        return edits_dir(tmp_path, [
            ("20260926T050001Z-aaaaaaaaaaaa-1",
             record_text(SHA_A, set_aside=("app/routes/admin_sekolah.py",),
                         kept=kept, patch=str(patch), when=when)),
        ])

    def test_the_card_names_what_was_set_aside_and_where_it_went(self, app, tmp_path):
        directory = self._record(tmp_path)
        html = render_status(app, self.report(tmp_path, directory))
        assert "A Local Change Set Aside" in html
        assert "app/routes/admin_sekolah.py" in html
        assert "heal.patch" in html, (
            "the card must name where the preserved bytes are, or the only way to "
            "recover the box's change is a shell")
        assert "aaaaaaa" in html

    def test_the_card_says_a_path_it_did_not_write_was_left_alone(self, app, tmp_path):
        directory = self._record(tmp_path, kept=("notes.txt",))
        html = render_status(app, self.report(tmp_path, directory))
        assert "notes.txt" in html
        assert "Local changes this release did NOT write" in html

    def test_an_empty_directory_says_so_rather_than_showing_a_blank(self, app, tmp_path):
        html = render_status(app, self.report(tmp_path, tmp_path / "missing"))
        assert "No local change has had to be set aside" in html
        assert "Belum ada perubahan lokal yang perlu disisihkan" in html
        assert "A Local Change Set Aside" not in html

    def test_an_unreadable_directory_is_not_shown_as_an_empty_one(self, app, tmp_path,
                                                                 monkeypatch):
        """`unreadable` and `none` need different remedies and must not look alike."""
        def boom(self):
            raise OSError("permission denied")

        with monkeypatch.context() as m:
            m.setattr(Path, "iterdir", boom)
            state = status.box_edits_state(tmp_path / "set-aside", tmp_path,
                                           now=_now())
        assert state["key"] == status.BOX_EDITS_UNREADABLE
        monkeypatch.setattr(status, "box_edits_state", lambda *a, **k: state)
        html = render_status(app, self.report(tmp_path, tmp_path / "set-aside"))
        assert "cannot be read" in html or "tidak bisa membacanya" in html
        assert "No local change has had to be set aside" not in html

    def test_the_newest_heal_comes_before_the_older_one(self, app, tmp_path):
        directory = edits_dir(tmp_path, [
            ("20260926T050001Z-aaaaaaaaaaaa-1", record_text(SHA_A)),
            ("20260926T050002Z-bbbbbbbbbbbb-2", record_text(SHA_B)),
        ])
        html = render_status(app, self.report(tmp_path, directory))
        assert html.index("bbbbbbb") < html.index("aaaaaaa")

    def test_the_card_renders_both_languages(self, app, tmp_path):
        directory = self._record(tmp_path)
        html = render_status(app, self.report(tmp_path, directory))
        assert html.count("t('") >= 8

    def test_the_card_says_when_an_edit_was_set_aside_for_being_stale(self, app,
                                                                     tmp_path):
        """The rule acts on age, not on an overlap, so the card has to say so — a
        reader who is told only "set aside" would go looking for the release that
        wrote the file, and there is not one."""
        patch = tmp_path / "heal.patch"
        patch.write_text("diff --git a/x b/x\n+the box's line\n", encoding="utf-8")
        directory = edits_dir(tmp_path, [
            ("20260926T050001Z-aaaaaaaaaaaa-1",
             record_text(SHA_A, set_aside=("notes.txt",), stale=("notes.txt",),
                         patch=str(patch))),
        ])
        html = render_status(app, self.report(tmp_path, directory))
        assert "notes.txt" in html
        assert "had stood untouched for longer than the runner" in html, (
            "the card does not say the edit was stale, so a preserved copy reads as an "
            "overlap the release never had")
        assert "sudah lama tidak disentuh" in html, "the sentence is not bilingual"

    def test_the_card_names_the_set_aside_directory(self, app, tmp_path):
        directory = self._record(tmp_path)
        html = render_status(app, self.report(tmp_path, directory))
        assert str(directory) in html


# ── the pieces that must agree across the three layers ───────────────────────

def test_the_verdict_key_has_a_sentence_in_the_verdict_card():
    template = TEMPLATE.read_text(encoding="utf-8")
    assert re.search(r"v\.key == 'box_edits'", template), (
        "the verdict names a key the verdict card has no sentence for, so a healed "
        "box would render as whatever the fallback happens to be")
    assert "box_edits" in status.REASON_KEYS


def test_the_page_defaults_to_the_directory_the_runner_writes():
    """One path, two files — the same relation the preflight record is held to."""
    script = RUNNER.read_text(encoding="utf-8")
    assert 'BOX_EDITS_DIR="$STATE_DIR/set-aside"' in script, (
        "the runner writes its set-aside records somewhere else than the page reads")
    assert status.DEFAULT_BOX_EDITS_DIR == status.DEFAULT_STATE_DIR + "/set-aside"


def test_the_page_only_reads_and_never_offers_to_delete():
    """Keep-or-discard belongs to the operator, and the evidence must survive it."""
    template = TEMPLATE.read_text(encoding="utf-8")
    card = template.split("the box's own version of a file a release had to write", 1)[1]
    card = card.split("<!-- ── the step the last run stopped at", 1)[0]
    assert "<form" not in card, (
        "the card grew a write, so a mis-click can move a record the operator has "
        "not read yet")


if __name__ == "__main__":                             # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
