"""The committed stylesheet has to be the one the templates produce.

`app/static/css/tailwind.css` is committed, not built on the box, so a template
that gains a utility and a `npm run css:build` nobody ran ship a class that does
not exist: no error, no missing file, and an element that quietly keeps its
ancestor's spacing, size or colour. `deploy/css_freshness.py` is the check, and
this is the guard on the check.

Four things are worth pinning, and the last two are the ones that decide whether
the check can be trusted in production at all:

* **the comparison is about content, not about the machine.** Git stores LF and
  checks out CRLF on Windows; a byte comparison without normalising would fail on
  one developer's laptop and pass on the deploy, which is the wrong way round.
* **the command it checks against is the project's own.** It reads `css:build`
  out of package.json instead of hardcoding `--minify`, because a check that runs
  a *different* command from the build it is checking is measuring the wrong
  thing and would go on passing after someone changed the build.
* **a stale stylesheet is exit 1 and an unanswerable question is exit 2.** A box
  with no node_modules must not be able to reject a release it cannot judge —
  that is the difference between a gate and an outage. The deploy already treats
  the gate's exit 2 that way, and that reading is asserted here too.
* **a plain check never writes the stylesheet.** Rebuilding is `--rebuild`, a
  decision somebody makes after reading why it is stale, not something a release
  does to itself.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
GATE = DEPLOY / "theme_gate.sh"
DEPLOY_SCRIPT = DEPLOY / "scangrade-deploy.sh"
HOOK = DEPLOY / "git-hooks" / "pre-commit"
TOOL = DEPLOY / "css_freshness.py"

sys.path.insert(0, str(DEPLOY))
import css_freshness as css  # noqa: E402

FRESH = b".a{color:red}.b{color:blue}"
STALE = b".a{color:red}.b{color:blue}.c{color:green}"


def _build_returning(payload):
    """A stand-in for the real build: writes `payload` where the CLI would."""

    def build(out_path: Path):
        if isinstance(payload, Exception):
            raise payload
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(payload)

    return build


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A miniature checkout: one stylesheet, one template directory."""
    css_file = tmp_path / "tailwind.css"
    css_file.write_bytes(FRESH)
    templates = tmp_path / "templates"
    templates.mkdir()
    monkeypatch.setattr(css, "CSS_PATH", css_file)
    monkeypatch.setattr(css, "TEMPLATES", templates)
    monkeypatch.setattr(css, "build", _build_returning(FRESH))
    return css_file, templates


# ── the comparison ───────────────────────────────────────────────────────────

class TestTheComparison:
    def test_identical_stylesheets_are_in_sync(self):
        assert css.compare(FRESH, FRESH) == []

    def test_line_endings_do_not_decide_it(self):
        """The deploy is Linux and the laptop is Windows; both must agree."""
        crlf = b".a{color:red}\r\n.b{color:blue}\r\n"
        lf = b".a{color:red}\n.b{color:blue}\n"
        assert css.compare(crlf, lf) == []
        assert css.compare(lf, crlf) == []

    def test_the_toolchain_banner_is_not_staleness(self):
        """Measured against production, not imagined.

        The deploy box has tailwindcss 3.4.19 and the laptop 3.4.17, both matching
        their lockfiles. They produce the same classes and the same rules, and the
        only difference between the two stylesheets is the version in Tailwind's
        banner — so a raw byte comparison would have called production's perfectly
        good stylesheet stale and refused every release from then on.
        """
        older = (b"/*! tailwindcss v3.4.17 | MIT License | https://tailwindcss.com */\n"
                 b".a{color:red}.b{color:blue}")
        newer = (b"/*! tailwindcss v3.4.19 | MIT License | https://tailwindcss.com */\n"
                 b".a{color:red}.b{color:blue}")
        assert css.compare(older, newer) == []

    def test_the_banner_never_becomes_a_utility(self):
        """`.4.17` read out of `v3.4.17` was a real false positive here."""
        banner = b"/*! tailwindcss v3.4.17 | MIT License */\n.a{color:red}"
        assert css.utilities(css.comparable(banner)) == {"a"}
        reasons = " ".join(css.compare(banner, banner + b".b{color:blue}"))
        assert "4.17" not in reasons, (
            "the version banner is not a class name, and reporting one would send "
            "the reader looking for a utility no template can contain:" + reasons
        )

    def test_a_changed_rule_is_still_stale(self):
        """Ignoring comments must not blunt the check to class names only."""
        assert css.compare(b".a{color:red}", b".a{color:blue}")
        assert css.compare(b".a{color:red}.b{c:d}", b".a{color:red}")

    def test_a_missing_utility_names_the_template_that_uses_it(self, checkout):
        """The message is the fix, so it has to say which page asked for it."""
        _css_file, templates = checkout
        (templates / "teacher").mkdir()
        (templates / "teacher" / "exam_form.html").write_text(
            '<div class="lg:block rotate-45"></div>', encoding="utf-8"
        )
        reasons = css.compare(FRESH, STALE + b".lg\\:block{display:block}")
        assert reasons, "a stylesheet missing a utility the templates use is stale"
        joined = " ".join(reasons)
        assert "lg:block" in joined
        assert "teacher/exam_form.html" in joined, (
            "the diagnosis has to name the template, or the reader is left "
            "searching 115 of them:\n" + joined
        )
        # The escape is Tailwind's (`lg\:block` in CSS, `lg:block` in a template)
        # and the search has to survive it.
        assert "lg\\:block" not in joined

    def test_an_extra_utility_is_reported_too(self):
        reasons = css.compare(STALE, FRESH)
        assert any("no template asks for them" in r for r in reasons), reasons

    def test_the_same_names_with_other_bytes_says_so(self):
        """A config change is not a missing class, and saying so saves the hunt."""
        same_names_other_bytes = b".a{color:orange}.b{color:blue}"
        reasons = css.compare(FRESH, same_names_other_bytes)
        assert reasons, "different rules are stale however they differ"
        joined = " ".join(reasons)
        assert "every utility name matches" in joined
        assert "tailwind.config.js" in joined


# ── the exit codes ───────────────────────────────────────────────────────────

class TestExitCodes:
    def test_in_sync_is_zero(self, checkout):
        code, report, lines = css.run()
        assert code == css.EXIT_FRESH
        assert report["verdict"] == "fresh"
        assert "in sync" in lines[0]

    def test_stale_is_one_and_names_the_fix(self, checkout):
        css.build = _build_returning(STALE)
        code, report, lines = css.run()
        assert code == css.EXIT_STALE
        assert report["verdict"] == "stale"
        assert any("npm run css:build" in line for line in lines), lines

    def test_a_plain_check_never_writes_the_stylesheet(self, checkout):
        css_file, _templates = checkout
        before = (css_file.read_bytes(), css_file.stat().st_mtime_ns)
        css.build = _build_returning(STALE)
        css.run()                                    # stale, so a rebuild is tempting
        css.run()
        after = (css_file.read_bytes(), css_file.stat().st_mtime_ns)
        assert after == before, (
            "the check wrote the file it was checking — a gate that fixes what it "
            "finds cannot also be the evidence that a release was verified"
        )

    def test_a_banner_move_is_reported_and_is_not_a_failure(self, checkout):
        """A rebuild that changes no rule should not look like a stale file."""
        css_file, _templates = checkout
        css_file.write_bytes(b"/*! tailwindcss v3.4.17 */\n.a{color:red}")
        css.build = _build_returning(b"/*! tailwindcss v3.4.19 */\n.a{color:red}")
        code, report, lines = css.run()
        assert code == css.EXIT_FRESH
        assert report["verdict"] == "fresh"
        assert any("banner" in line for line in lines), lines

    def test_rebuild_writes_it(self, checkout):
        css_file, _templates = checkout
        css.build = _build_returning(STALE)
        code, report, lines = css.run(rebuild=True)
        assert code == css.EXIT_FRESH
        assert report["verdict"] == "rebuilt"
        assert css_file.read_bytes() == STALE
        assert "rebuild" in lines[0] or "rebuilt" in lines[0]

    def test_no_node_is_two_and_never_one(self, monkeypatch):
        monkeypatch.setattr(css, "find_node", _raise(css.CannotRun("no node")))
        code, report, lines = css.run()
        assert code == css.EXIT_CANNOT_RUN, (
            "a box with no node cannot judge a release; exit 1 here would let an "
            "unanswerable question take a working site down"
        )
        assert report["verdict"] == "cannot_measure"
        assert "no node" in lines[0]

    def test_no_node_modules_is_two(self, monkeypatch):
        monkeypatch.setattr(css, "find_node", lambda: "/usr/bin/node")
        monkeypatch.setattr(css, "find_cli", _raise(css.CannotRun("no tailwind CLI")))
        code, _report, _lines = css.run()
        assert code == css.EXIT_CANNOT_RUN

    def test_a_failed_build_is_two_not_one(self, checkout):
        css.build = _build_returning(css.CannotRun("the build exited 1: boom"))
        code, _report, lines = css.run()
        assert code == css.EXIT_CANNOT_RUN
        assert "boom" in lines[0]

    def test_a_build_that_writes_nothing_is_two(self, checkout, monkeypatch):
        """A report of success is not an artefact — the same silence this finds."""
        monkeypatch.setattr(css, "build", lambda out: None)
        code, _report, lines = css.run()
        assert code == css.EXIT_CANNOT_RUN
        assert "wrote no stylesheet" in lines[0]

    def test_a_missing_stylesheet_is_two(self, tmp_path, monkeypatch):
        monkeypatch.setattr(css, "CSS_PATH", tmp_path / "gone.css")
        code, _report, lines = css.run()
        assert code == css.EXIT_CANNOT_RUN
        assert "does not exist" in lines[0]

    def test_a_real_build_failure_is_caught(self, tmp_path, monkeypatch):
        """The subprocess path, not just the stand-in above."""
        bad_cli = tmp_path / "cli.js"
        bad_cli.write_text("process.exit(3);", encoding="utf-8")
        monkeypatch.setattr(css, "find_node", lambda: sys.executable and _node_or_skip())
        monkeypatch.setattr(css, "find_cli", lambda: bad_cli)
        if css.find_node() is None:
            pytest.skip("no node on this box")
        with pytest.raises(css.CannotRun) as caught:
            css.build(tmp_path / "out.css")
        assert "exited 3" in str(caught.value)


def _node_or_skip():
    for candidate in ("node", "nodejs"):
        import shutil

        found = shutil.which(candidate)
        if found:
            return found
    pytest.skip("no node on this box")


def _raise(exc):
    def raiser(*_args, **_kwargs):
        raise exc

    return raiser


# ── the command it checks against is the project's ───────────────────────────

class TestTheBuildCommandIsTheProjects:
    def test_it_is_the_package_script_with_only_the_output_replaced(self, tmp_path):
        out = tmp_path / "out.css"
        args = css.build_command(out)
        target = args.index("-o") if "-o" in args else args.index("--output")
        assert args[target + 1] == str(out), (
            "the build has to write somewhere other than the committed file"
        )
        assert str(css.CSS_PATH) not in args, (
            "the check must not rebuild over the stylesheet it is checking"
        )
        # Everything else is what `npm run css:build` says, including the minify
        # flag the project chose.
        assert "--minify" in args
        assert "-c" in args and "tailwind.config.js" in args

    def test_a_changed_script_is_followed(self, tmp_path, monkeypatch):
        """The one way to make this check and the build disagree is a copy."""
        pkg = tmp_path / "package.json"
        pkg.write_text(
            '{"scripts": {"css:build": "npx tailwindcss -c tailwind.config.js '
            '-o app/static/css/tailwind.css --minify --postcss"}}',
            encoding="utf-8",
        )
        monkeypatch.setattr(css, "PACKAGE_JSON", pkg)
        args = css.build_command(tmp_path / "out.css")
        assert "--postcss" in args, (
            "a flag added to css:build has to reach the check as well, or the "
            "gate is rebuilding a different stylesheet from the one that ships"
        )

    def test_a_script_with_no_output_flag_gets_one(self, tmp_path, monkeypatch):
        pkg = tmp_path / "package.json"
        pkg.write_text(
            '{"scripts": {"css:build": "npx tailwindcss -c tailwind.config.js"}}',
            encoding="utf-8",
        )
        monkeypatch.setattr(css, "PACKAGE_JSON", pkg)
        out = tmp_path / "out.css"
        args = css.build_command(out)
        assert args[-2:] == ["-o", str(out)]

    def test_no_script_is_two_not_a_guess(self, tmp_path, monkeypatch):
        pkg = tmp_path / "package.json"
        pkg.write_text('{"scripts": {}}', encoding="utf-8")
        monkeypatch.setattr(css, "PACKAGE_JSON", pkg)
        with pytest.raises(css.CannotRun):
            css.build_command(tmp_path / "out.css")

    def test_an_unreadable_package_json_is_two(self, tmp_path, monkeypatch):
        monkeypatch.setattr(css, "PACKAGE_JSON", tmp_path / "nope.json")
        code, _report, _lines = css.run()
        assert code == css.EXIT_CANNOT_RUN


# ── the gate runs it ─────────────────────────────────────────────────────────

class TestTheGateRunsIt:
    """Wired, invoked, and read the way the deploy reads it.

    The gate's own failure text names the tool, so a substring assertion here
    would be satisfied by that sentence alone — the invocation is asserted with
    its command substitution, as `test_i18n_coverage.py` had to do for the tool
    whose run had been deleted while its name stayed in the message.
    """

    def test_the_gate_invokes_it(self):
        src = GATE.read_text(encoding="utf-8")
        assert '"$REPO/deploy/css_freshness.py" 2>&1' in src, (
            "deploy/theme_gate.sh no longer runs the freshness check — a gate that "
            "is named in the gate's help text but never invoked is the failure "
            "this assertion exists to catch"
        )
        assert 'CSS_OUT=$("$PY"' in src

    def test_the_gate_runs_the_guard_file_too(self):
        """Otherwise this file can be dropped from the gate and nothing says so."""
        src = GATE.read_text(encoding="utf-8")
        tests = [ln for ln in src.splitlines() if ln.startswith("TESTS=")]
        assert tests, "the gate no longer lists the checks it runs"
        assert "tests/unit/test_css_freshness.py" in tests[0], (
            "the freshness check's own guard is not in the gate's list, so it can "
            "be deleted or ignored without the gate noticing"
        )

    def test_the_tool_it_names_exists(self):
        assert TOOL.is_file(), (
            "the gate names deploy/css_freshness.py; if that file is gone, the "
            "gate tells the reader to run something that does not exist"
        )

    def test_a_stale_stylesheet_fails_the_gate(self):
        src = GATE.read_text(encoding="utf-8")
        stale_branch = src.split('if [ "$CSS_RC" -eq 1 ]', 1)
        assert len(stale_branch) == 2, "the gate has no stale-stylesheet branch"
        assert "exit 1" in stale_branch[1][:900], (
            "a stale stylesheet has to fail the gate, not warn about it"
        )

    def test_an_unanswerable_check_does_not_fail_the_gate(self):
        src = GATE.read_text(encoding="utf-8")
        tail = src.split('echo "theme gate: the stylesheet freshness check COULD NOT RUN', 1)
        assert len(tail) == 2, "the gate has no 'could not measure' branch"
        # The first exit after that message is the branch's own — not an
        # unrelated `exit 2` elsewhere in the file.
        exits = [ln.strip() for ln in tail[1].splitlines() if ln.strip().startswith("exit ")]
        assert exits, "the 'could not measure' branch never exits at all"
        assert exits[0] == "exit 2", (
            "a box without node cannot judge a release and must say so with the "
            "code the deploy reads as 'not checked' rather than 'failed'; this "
            f"branch exits {exits[0]!r}"
        )

    def test_the_deploy_treats_the_gates_cannot_run_as_no_rollback(self):
        """The exit code only means something if the deploy reads it that way."""
        src = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        block = src.split("# ── Gate 3: is this release readable?", 1)[1]
        block = block.split("# ── Reload", 1)[0]
        # The body of the `-eq 2` branch is what has to not roll back, so it is
        # taken on its own: an assertion over the whole block passes even when a
        # reset is added to the branch that must not have one.
        after = block.split('elif [ "$THEME_RC" -eq 2 ]', 1)[1]
        cannot_body = after.split("else", 1)[0]
        stale_body = after.split("else", 1)[1]
        assert "reset --hard" not in cannot_body, (
            "a theme gate that could not run must not roll the release back — a "
            "checker that breaks would then be able to take the site down\n"
            + cannot_body
        )
        assert "reset --hard" in stale_body, (
            "the theme gate's real-failure branch still has to roll back"
        )

    def test_the_pre_commit_hook_runs_this_gate(self):
        src = HOOK.read_text(encoding="utf-8")
        assert "theme_gate.sh" in src
        assert "app/templates/" in src and "app/static/css/" in src

    def test_the_hook_also_fires_when_the_build_itself_changes(self):
        """A config or script change makes the stylesheet stale too.

        Neither of these touches a template or the stylesheet, so a hook that
        only watched those two would let the stalest possible commit through:
        the one that changed what the build produces without rebuilding.
        """
        src = HOOK.read_text(encoding="utf-8")
        trigger = [ln for ln in src.splitlines() if "grep -qE" in ln and "CHANGED" in ln]
        assert trigger, "the hook no longer filters the staged files"
        assert "tailwind" in trigger[0] and "package\\.json" in trigger[0], (
            "the pre-commit trigger does not fire on a change to the config or the "
            "build script:\n    " + trigger[0]
        )


# ── this checkout ────────────────────────────────────────────────────────────

class TestThisCheckout:
    def test_the_committed_stylesheet_is_the_one_the_templates_produce(self):
        """The end-to-end answer: the file in git matches the templates in git.

        It skips only when the question cannot be asked here — and the reason is
        printed, so a silent pass is not available.
        """
        code, report, lines = css.run()
        if code == css.EXIT_CANNOT_RUN:
            pytest.skip("css freshness cannot run here: " + lines[0])
        assert code == css.EXIT_FRESH, (
            "app/static/css/tailwind.css is stale — run `npm run css:build` and "
            "commit it:\n    " + "\n    ".join(lines)
        )
        assert report["committed_bytes"] == report["fresh_bytes"]

    def test_the_tool_is_ascii_where_it_prints(self):
        """Its output is relayed into the journal and onto non-UTF-8 consoles."""
        labels = (css.EXIT_FRESH, css.EXIT_STALE, css.EXIT_CANNOT_RUN)
        assert labels == (0, 1, 2)
        src = TOOL.read_text(encoding="utf-8")
        for line in src.splitlines():
            if line.strip().startswith(("print(", "raise CannotRun", "return EXIT")):
                assert line.isascii(), f"non-ASCII in relayed output: {line}"
