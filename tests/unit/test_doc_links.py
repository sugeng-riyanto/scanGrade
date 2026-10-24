"""The documentation check: a page may not point at a file that is not there.

`deploy/doc_links.py` is the check the pre-commit hook runs, and this file is the
check on the check. What it has to answer, in the order a reader would ask it:

* **what counts as a reference.** Three shapes: a markdown link, which is always an
  assertion; an inline code span that *is* a path; and — in a docstring, a template
  comment or a deploy script's own message — a bare path, because that copy writes
  one without backticks. The exclusions are the interesting half, because this
  repository writes data files, artifacts and branch templates in backticks —
  `docs/ljk/LJK-uas.pdf`, `deploy/x.intent`, `feature/description` — and a rule that
  failed on those would be deleted within a day, which is the failure this codebase
  keeps naming.
* **the copy nothing executes.** A docstring, a template comment and a `FAILED:`
  line are read for the same reason a page is: `app/services/exam_report.py` cited
  `docs/features/ANALYSIS.md` for the item-analysis thresholds and every check
  stayed green. What keeps that from being a false-positive machine is the other
  half of the same list: `tests/` is not read, a line that opens with a command is
  a snippet, and a line that says "for example" is naming a shape.
* **what is exempt, and why it is not hiding anything.** Scratch, planning pages,
  and the words that say a file is gone. That last one has a measured lesson in it:
  read against the *line*, it exempted 470 of `AGENTS.md`'s references, because this
  repository's bullets are paragraphs and "removed" four hundred characters away is
  not a sentence about the path. It reads a window now, and the window is tested
  from both sides.
* **the exception list is held in both directions.** An entry whose path resolves is
  a refusal, and so is one whose document stopped naming it — an exemption nobody
  needs is how the next real reference is missed.
* **the heading a link names.** `[x](b.md#the-cases)` is two claims and only the
  page was ever checked, so a renamed section left a link that opens the page at
  the top. The fragment is resolved against the target's headings by GitHub's
  slugger, against a same-page `#fragment` too, and both limits are tested: a
  non-page target and a page that is itself missing.
* **the exit codes**, as the contract the gate reads: 0 pass, 1 the release is
  wrong, 2 the box could not measure.
* **the gate runs it**, sliced from the block that does rather than grepped from the
  file: the gate's own failure text once told readers to run a tool that did not
  exist, so a mention is not a call site.
"""
from __future__ import annotations

import shutil

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.git_env import GIT_REPO_ENV, git

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
GATE = DEPLOY / "theme_gate.sh"
EXCEPTIONS = DEPLOY / "doc_links_gone.txt"

sys.path.insert(0, str(DEPLOY))
import doc_links as dl  # noqa: E402


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """A scratch checkout git knows about — the tool reads `git ls-files`.

    The four directories a reference can start with are seeded, because a scratch
    tree tracks only what a test writes and a reference to `deploy/x.py` is
    invisible without a tracked `deploy/` — that is the rule, not a fixture detail.
    """
    git("init", "-q", cwd=str(tmp_path))
    seeded = {"app/__init__.py": "", "deploy/keep.txt": "", "tests/keep.txt": ""}
    for rel, text in {**seeded, **files}.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git("add", "-A", cwd=str(tmp_path))
    return tmp_path


def _scan(repo: Path, exceptions: dict | None = None) -> dl.Report:
    return dl.scan(repo, exceptions=exceptions or {})


def _missing(repo: Path, exceptions: dict | None = None) -> list[str]:
    return [ref.path for ref in _scan(repo, exceptions).missing]


def _run(repo: Path, *extra: str) -> int:
    """The tool as the gate calls it, against a scratch tree."""
    return dl.main(["--repo", str(repo), "--exceptions",
                    str(repo / "no-such-exceptions.txt"), *extra])


class TestWhatCountsAsAReference:

    def test_a_link_one_directory_up_is_a_finding(self, tmp_path):
        """The commonest link in this repository's docs: `../deploy/…`."""
        repo = _repo(tmp_path, {
            "docs/a.md": "See [the gate](../deploy/nope.py).\n",
            "deploy/theme_gate.sh": "#!/bin/sh\n",
        })
        assert _missing(repo) == ["../deploy/nope.py"]

    def test_a_link_one_directory_up_that_resolves_is_fine(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "See [the gate](../deploy/theme_gate.sh).\n",
            "deploy/theme_gate.sh": "#!/bin/sh\n",
        })
        report = _scan(repo)
        assert report.missing == []
        assert report.checked == 1

    def test_a_link_next_to_the_page_resolves(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "[b](b.md)\n",
            "docs/b.md": "# b\n",
        })
        assert _scan(repo).missing == []

    def test_an_anchor_is_not_a_path_but_it_is_still_a_claim(self, tmp_path):
        """`#the-section` names no file — it names a heading, and that is checked
        where the heading is (see `TestTheAnchorALinkNames`)."""
        repo = _repo(tmp_path, {
            "docs/a.md": "## The section\n\nJump to [the section](#the-section).\n",
        })
        report = _scan(repo)
        assert report.missing == []
        assert [ref.path for ref in report.refs] == ["#the-section"]

    def test_a_remote_link_is_not_a_path(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "[x](https://example.com/app/main.py) and [y](mailto:a@b)\n",
        })
        assert _scan(repo).refs == []

    def test_a_fenced_block_is_code_not_a_claim(self, tmp_path):
        """The fence carries a real reference shape, not just prose: a block that
        showed `` `deploy/nope.sh` `` in a shell snippet is a snippet."""
        repo = _repo(tmp_path, {
            "docs/a.md": ("```text\n"
                          "cp deploy/nope.sh /opt/scangrade/\n"
                          "`deploy/nope.sh` and [it](deploy/also-missing.md)\n"
                          "```\n"),
        })
        assert _scan(repo).refs == []

    def test_an_inline_path_with_a_known_extension_is_checked(self, tmp_path):
        repo = _repo(tmp_path, {"docs/a.md": "Run `deploy/nope.sh` to fix it.\n"})
        assert _missing(repo) == ["deploy/nope.sh"]

    def test_a_data_file_is_not_a_claim(self, tmp_path):
        """`.pdf` is not a file this repository ships; naming one is not a promise."""
        repo = _repo(tmp_path, {"docs/a.md": "The scan is `docs/ljk/LJK-uas-100.pdf`.\n"})
        assert _scan(repo).refs == []

    def test_a_runtime_artifact_is_not_a_claim(self, tmp_path):
        repo = _repo(tmp_path, {"docs/a.md": "`deploy/perf_rebaseline.intent`\n"})
        assert _scan(repo).refs == []

    def test_a_module_name_that_resolves_is_checked(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "The reader is `app/utils/cache`.\n",
            "app/utils/cache.py": "#\n",
        })
        report = _scan(repo)
        assert report.missing == []
        assert report.checked == 1

    def test_a_module_name_that_resolves_to_nothing_is_not_a_claim(self, tmp_path):
        """The stated limit: a misspelled module name is not caught.

        Reading every extension-less span as a path is the false-positive machine
        that gets a guard deleted — `feature/description` is a git branch template
        in this repository's own CONTRIBUTING page.
        """
        repo = _repo(tmp_path, {
            "docs/a.md": "See `app/utils/nope` and `feature/description`.\n",
            "app/utils/cache.py": "#\n",
        })
        report = _scan(repo)
        assert report.missing == []
        assert report.refs == []

    def test_a_pytest_node_id_is_read_as_its_file(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "`tests/unit/test_gone.py::TestA::test_b`\n",
            "tests/unit/test_real.py": "",
        })
        assert _missing(repo) == ["tests/unit/test_gone.py"]

    def test_a_line_number_is_not_part_of_the_path(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "`app/main.py:437` says so.\n",
            "app/main.py": "",
        })
        assert _scan(repo).missing == []

    def test_a_symbol_suffix_is_read_as_its_file(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "`app/utils/auth._resolved_school` and `deploy/tool.references()`\n",
            "app/utils/auth.py": "",
            "deploy/tool.py": "",
        })
        report = _scan(repo)
        assert report.missing == []
        assert report.checked == 2

    def test_an_abbreviated_path_is_not_a_claim(self, tmp_path):
        repo = _repo(tmp_path, {"docs/a.md": "`supabase/migrations/062_…sql`\n"})
        assert _scan(repo).refs == []

    @pytest.mark.parametrize("raw", [
        "/opt/scangrade/deploy/x.py",
        "~/deploy/x.py",
        "C:\\opencode\\x.py",
        "deploy/*.py",
    ])
    def test_a_path_outside_this_checkout_is_not_checked(self, tmp_path, raw):
        repo = _repo(tmp_path, {"docs/a.md": f"`{raw}`\n", "deploy/tool.py": ""})
        assert _scan(repo).refs == []

    def test_a_percent_encoded_name_is_decoded(self, tmp_path):
        """`docs/foo%20bar.md` is a link to a file with a space in its name."""
        repo = _repo(tmp_path, {
            "docs/a.md": "See [it](foo%20bar.md).\n",
            "docs/foo bar.md": "# here\n",
        })
        assert _scan(repo).missing == []

    def test_an_untracked_top_level_directory_is_not_a_prefix(self, tmp_path):
        """`node_modules/` exists on a box and in no clone — the verdict must not
        depend on who asks."""
        repo = _repo(tmp_path, {"docs/a.md": "`node_modules/x/index.js`\n"})
        (repo / "node_modules" / "x").mkdir(parents=True)
        (repo / "node_modules" / "x" / "index.js").write_text("", encoding="utf-8")
        assert _scan(repo).refs == []


class TestTheAnchorALinkNames:
    """`[x](b.md#the-cases)` is two claims, and the second one used to go unchecked.

    The page resolves, so the link looks right, while the heading was renamed and
    the reader lands at the top of a page that no longer says what the link
    promised. The fragment is resolved against the target page's headings, by the
    slugger GitHub uses — the same one a browser scrolls to.
    """

    def test_a_link_to_a_renamed_heading_is_a_finding(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "See [the cases](z.md#the-cases).\n",
            # `z.md` sorts after `a.md` on purpose: the index is built from the
            # whole tree before any page is judged, so a forward link is checked
            # by the same rule as a backward one.
            "docs/z.md": "# Report\n\n## Cases\n",
        })
        report = _scan(repo)
        missing = [ref for ref in report.missing if ref.kind == "link"]
        assert [ref.path for ref in missing] == ["z.md"]
        assert "no heading `#the-cases`" in missing[0].why
        assert "z.md" in missing[0].why

    def test_a_link_to_a_heading_that_is_there_resolves(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "See [the cases](b.md#the-cases).\n",
            "docs/b.md": "# Report\n\n## The cases\n",
        })
        report = _scan(repo)
        assert report.missing == []
        assert report.checked == 1

    def test_the_slug_drops_punctuation_and_leaves_its_spaces(self, tmp_path):
        """The shape this repository's own tables of contents rely on:
        `## Peran, Hak Akses & Sesi` links as `#peran-hak-akses--sesi`."""
        repo = _repo(tmp_path, {
            "docs/a.md": ("[right](b.md#peran-hak-akses--sesi) "
                          "[wrong](b.md#peran-hak-akses-sesi)\n"),
            "docs/b.md": "## Peran, Hak Akses & Sesi\n",
        })
        # Both are in one tree so the difference is the slug and not the fixture:
        # the comma and the ampersand leave their two spaces behind as `--`, and a
        # slug that replaced them with a single hyphen names nothing.
        missing = _scan(repo).missing
        assert [ref.raw for ref in missing] == ["b.md#peran-hak-akses-sesi"]

    def test_a_repeated_heading_is_suffixed_the_way_github_numbers_it(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "[one](b.md#notes) [two](b.md#notes-1) [three](b.md#notes-2)\n",
            "docs/b.md": "## Notes\n\n## Notes\n",
        })
        missing = [ref.raw for ref in _scan(repo).missing]
        assert missing == ["b.md#notes-2"]

    def test_markup_in_a_heading_is_not_part_of_its_anchor(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "[t](b.md#appjs-and-bold)\n",
            "docs/b.md": "## `app.js` and **bold**\n",
        })
        assert _scan(repo).missing == []

    def test_a_same_page_anchor_is_checked(self, tmp_path):
        """The shape that was invisible until now: `normalise` reads it as no path."""
        repo = _repo(tmp_path, {
            "docs/a.md": "Jump to [the cases](#the-cases).\n\n## The case list\n",
        })
        missing = _scan(repo).missing
        assert [ref.raw for ref in missing] == ["#the-cases"]
        assert "this page" in missing[0].why

    def test_a_heading_inside_a_fence_is_not_an_anchor(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "[t](b.md#not-a-heading)\n",
            "docs/b.md": "# b\n\n```sh\n# not a heading\n```\n",
        })
        assert [ref.raw for ref in _scan(repo).missing] == ["b.md#not-a-heading"]

    def test_an_explicit_anchor_the_page_writes_counts(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "[t](b.md#old-name)\n",
            "docs/b.md": '## A new name\n\n<a id="old-name"></a>\n',
        })
        assert _scan(repo).missing == []

    def test_a_line_anchor_is_not_a_heading(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "[t](app/main.py#L42) [u](b.md#L7)\n",
            "app/main.py": "",
            "docs/b.md": "# b\n",
        })
        assert _scan(repo).missing == []

    def test_a_regex_that_looks_like_a_link_is_not_one(self, tmp_path):
        """A `.py` file is read for its copy, so its code arrives too: the `](` in
        a pattern like `.table(\\s*[\"']([\\w-]+)[\"']` is a regular expression,
        not a link to `[\\w-]+`, and reading it as one refused a release over a
        pattern."""
        repo = _repo(tmp_path, {
            "docs/a.md": "",
            "app/scan.py": 'PATTERN = r"\\.table\\(\\s*[\"\']([\\w-]+)[\"\']"\n',
        })
        assert _scan(repo).refs == []

    def test_a_fragment_on_a_document_this_tool_does_not_parse_is_not_checked(
            self, tmp_path):
        """Stated limit: `x.html#section` is an address inside a document that has
        no heading index here — the path is still checked, the fragment is not."""
        repo = _repo(tmp_path, {
            "docs/a.md": "[t](../app/templates/x.html#anything)\n",
            "app/templates/x.html": "<p>b</p>\n",
        })
        assert _scan(repo).missing == []

    def test_a_missing_page_is_one_refusal_and_not_two(self, tmp_path):
        repo = _repo(tmp_path, {"docs/a.md": "[t](gone.md#the-cases)\n"})
        missing = _scan(repo).missing
        assert len(missing) == 1
        assert missing[0].why == "", "the page is the first thing that has to exist"

    def test_a_percent_encoded_fragment_is_decoded(self, tmp_path):
        """`%2D` is a hyphen: the fragment is compared to the slug, not to the
        bytes a browser would send."""
        repo = _repo(tmp_path, {
            "docs/a.md": "[t](b.md#re%2Dmeasured-on-25-sep)\n",
            "docs/b.md": "## Re-measured on 25 Sep\n",
        })
        assert _scan(repo).missing == []

    def test_the_refusal_says_which_heading_it_could_not_find(self, tmp_path, capsys):
        repo = _repo(tmp_path, {
            "docs/a.md": "## The case list\n\nSee [the cases](#the-cases).\n",
        })
        assert _run(repo) == 1
        err = capsys.readouterr().err
        assert "no heading `#the-cases`" in err
        assert "[the cases](#the-cases)" in err, (
            "the sentence is still printed under the reference")


class TestWhatIsDeliberatelyExempt:

    def test_scratch_is_not_a_claim_about_this_tree(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "` .freebuff/mutate_x.py`\n".replace(" ", ""),
            ".freebuff/mutate_y.py": "",
        })
        report = _scan(repo)
        assert report.missing == []
        assert report.exempt == 1

    def test_a_plan_names_files_it_intends_to_write(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/planning/plan.md": "We will write `docs/features/never.md`.\n",
        })
        report = _scan(repo)
        assert report.missing == []
        assert report.exempt == 1

    def test_a_heading_that_drops_a_table_exempts_it(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": ("### dropped — branch-only\n\n"
                          "| file | why |\n|---|---|\n| `app/gone.py` | superseded |\n\n"
                          "## kept\n\n`app/also_gone.py`\n"),
            "app/real.py": "",
        })
        missing = _missing(repo)
        # the table row is exempt by its heading; the same kind of path under a
        # heading that says nothing is still a finding
        assert missing == ["app/also_gone.py"]

    def test_a_sentence_saying_it_is_gone_exempts_it(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "`tests/unit/test_old.py` was deleted with the feature.\n",
        })
        assert _missing(repo) == []

    def test_the_same_word_far_away_does_not_exempt(self, tmp_path):
        """Measured on `AGENTS.md`: read per line, 470 real references went exempt,
        because a bullet there is a paragraph and "removed" four hundred characters
        later is not a sentence about this path."""
        filler = "kata " * 60
        repo = _repo(tmp_path, {
            "docs/a.md": f"`app/gone.py` {filler} removed later in the paragraph.\n",
        })
        assert _missing(repo) == ["app/gone.py"]

    def test_an_exempt_reference_is_counted_not_silent(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "`app/gone.py` was deleted.\n`app/real.py`\n",
            "app/real.py": "",
        })
        report = _scan(repo)
        assert report.exempt == 1
        assert report.checked == 1


class TestTheCopyNothingExecutes:
    """A path is also written where nothing renders it, and it rots the same way.

    `app/services/exam_report.py` cited `docs/features/ANALYSIS.md` for the
    item-analysis thresholds — a page that was never written, in a docstring no
    page shows, with every other check green. A deploy script's `FAILED:` line is
    the worst version of it: the reader who follows that path is already having a
    bad day. So the same reading runs over docstrings, template comments and the
    deploy scripts' own messages — and these are the rules that keep it from
    failing on prose that is not a claim.
    """

    def _tree(self, tmp_path: Path, **files: str) -> Path:
        return _repo(tmp_path, {"docs/a.md": "", **files})

    def test_a_docstring_that_names_a_dropped_tool_is_a_finding(self, tmp_path):
        repo = self._tree(tmp_path, **{
            "app/service.py": 'def run():\n    """Delegates to `deploy/gone.sh`."""\n',
        })
        assert _missing(repo) == ["deploy/gone.sh"]

    def test_a_comment_that_names_a_dropped_tool_is_a_finding(self, tmp_path):
        repo = self._tree(tmp_path, **{
            "app/service.py": "# the gate is `deploy/gone.sh`\n",
        })
        assert _missing(repo) == ["deploy/gone.sh"]

    def test_a_bare_path_in_a_docstring_is_a_claim(self, tmp_path):
        """No backticks: docstrings and messages write paths the way prose does."""
        repo = self._tree(tmp_path, **{
            "app/service.py": '"""See deploy/gone.sh for the refusals."""\n',
        })
        assert _missing(repo) == ["deploy/gone.sh"]

    def test_a_bare_path_on_a_page_is_still_prose(self, tmp_path):
        """The asymmetry, held on purpose: a page marks its paths, so a bare one
        there is a sentence that happens to look like a path."""
        repo = _repo(tmp_path, {"docs/a.md": "See deploy/gone.sh for the refusals.\n"})
        assert _scan(repo).refs == []

    def test_a_bare_filename_is_not_read(self, tmp_path):
        """The stated limit: `config.py` in a sentence means *this* file, and a
        rule that read every filename would refuse its own documentation."""
        repo = self._tree(tmp_path, **{
            "app/service.py": '"""The loader config.py reads it."""\n',
            "app/config.py": "",
        })
        assert _scan(repo).refs == []

    def test_a_template_comment_is_read(self, tmp_path):
        repo = self._tree(tmp_path, **{
            "app/templates/x.html": "{# deploy/gone.sh renders this #}\n"
                                    "<p>hello</p>\n",
        })
        assert _missing(repo) == ["deploy/gone.sh"]

    def test_an_html_comment_is_read(self, tmp_path):
        repo = self._tree(tmp_path, **{
            "app/templates/x.html": "<!-- see deploy/gone.sh -->\n<p>b</p>\n",
        })
        assert _missing(repo) == ["deploy/gone.sh"]

    def test_a_template_s_addresses_are_not_claims(self, tmp_path):
        """`/static/js/x.js` is what the browser fetches, not what a clone has."""
        repo = self._tree(tmp_path, **{
            "app/templates/x.html": '<script src="/static/js/gone.js"></script>\n',
        })
        assert _scan(repo).refs == []

    def test_a_deploy_script_s_failure_text_is_a_claim(self, tmp_path):
        """The reader of this line is mid-incident; the path has to be there."""
        repo = self._tree(tmp_path, **{
            "deploy/gate.sh": ('#!/bin/sh\n'
                               'if [ $? -ne 0 ]; then\n'
                               '  echo "FAILED - run deploy/gone.sh --check" >&2\n'
                               'fi\n'),
        })
        assert _missing(repo) == ["deploy/gone.sh"]

    def test_a_script_that_runs_a_missing_script_is_a_finding(self, tmp_path):
        """`bash deploy/x.sh` is a use of the file, and shell is not a snippet:
        a script is the command list, so *every* line of it is a claim."""
        repo = self._tree(tmp_path, **{
            "deploy/gate.sh": "#!/bin/sh\nbash deploy/gone.sh --check\n",
        })
        assert _missing(repo) == ["deploy/gone.sh"]

    def test_a_script_that_runs_a_script_that_is_there_is_fine(self, tmp_path):
        repo = self._tree(tmp_path, **{
            "deploy/gate.sh": "#!/bin/sh\nbash deploy/theme_gate.sh\n",
            "deploy/theme_gate.sh": "#!/bin/sh\n",
        })
        report = _scan(repo)
        assert report.missing == []
        assert report.checked == 1

    def test_a_line_that_opens_with_a_command_is_a_snippet(self, tmp_path):
        """A usage block in a docstring is showing a command, exactly as a fenced
        block on a page is: the migration is the one *you* will write."""
        repo = self._tree(tmp_path, **{
            "supabase/migrations/keep.sql": "",
            "deploy/tool.py": ('"""\nUsage\n-----\n'
                               '    python deploy/gone.py supabase/migrations/025_x.sql\n'
                               '"""\n'),
        })
        report = _scan(repo)
        assert report.missing == []
        # Both are read and both are exempt, rather than invisible: a line that
        # nobody counted is how an exemption becomes a hole.
        assert report.exempt == 2

    def test_the_same_path_in_prose_is_a_finding(self, tmp_path):
        """The control for the rule above: it is the command that exempts."""
        repo = self._tree(tmp_path, **{
            "deploy/tool.py": '"""This one calls deploy/gone.py for real."""\n',
        })
        assert _missing(repo) == ["deploy/gone.py"]

    def test_the_copy_may_say_it_is_an_example(self, tmp_path):
        repo = self._tree(tmp_path, **{
            "app/service.py": "# For example `tests/unit/a.py` (17), `b.py` (15).\n",
        })
        report = _scan(repo)
        assert report.missing == []
        assert report.exempt == 1

    def test_an_example_word_far_away_does_not_exempt(self, tmp_path):
        filler = "kata " * 40
        repo = self._tree(tmp_path, **{
            "app/service.py": f"# `app/gone.py` {filler} for example later.\n",
        })
        assert _missing(repo) == ["app/gone.py"]

    def test_a_wrapped_message_reads_one_line_up_for_its_sentence(self, tmp_path):
        """A message in a docstring wraps where a page's line does not: the
        `e.g.` that says this name is a shape is on the line above."""
        repo = self._tree(tmp_path, **{
            "deploy/tool.py": ('        parser.error("--adopt needs the migration file it records, e.g. "\n'
                               '                     "supabase/migrations/062_x.sql --adopt")\n'),
        })
        assert _scan(repo).refs == []

    def test_a_test_fixture_is_not_a_claim(self, tmp_path):
        """A test builds fixture trees; a docstring naming a path in one of them
        is not a claim about this checkout."""
        repo = self._tree(tmp_path, **{
            "tests/unit/test_x.py": '"""Writes deploy/gone.sh into a scratch tree."""\n',
        })
        assert _scan(repo).refs == []

    def test_the_summary_names_the_copy_it_read(self, tmp_path, capsys):
        """A run that read the pages and none of the files would otherwise print
        the same first number and look complete."""
        repo = self._tree(tmp_path, **{
            "app/service.py": "# nothing to see\n",
            "deploy/gate.sh": "#!/bin/sh\n",
            "app/templates/x.html": "<p>b</p>\n",
        })
        assert _run(repo) == 0
        out = capsys.readouterr().out
        assert "4 file(s) of comment copy" in out
        assert "1 document(s)" in out

    def test_an_exception_can_record_a_comment_source(self, tmp_path):
        repo = self._tree(tmp_path, **{
            "app/service.py": "# `app/cr.py` read as `pp/cr.py`\n",
        })
        entries = {("app/service.py", "app/cr.py"): "illustrative, not a file"}
        report = _scan(repo, entries)
        assert report.missing == [] and report.stale == []
        assert report.exempt == 1

    def test_an_exception_for_a_comment_source_goes_stale_the_same_way(
            self, tmp_path):
        repo = self._tree(tmp_path, **{"app/service.py": "# nothing\n"})
        report = _scan(repo, {("app/service.py", "app/cr.py"): "illustrative"})
        assert report.stale and "no longer named" in report.stale[0][2]


class TestTheExceptionList:

    def _entry(self, doc: str, path: str, why: str = "because") -> dict:
        return {(doc, path): why}

    def test_a_recorded_exception_passes(self, tmp_path):
        repo = _repo(tmp_path, {"docs/a.md": "`app/gone.py`\n"})
        report = _scan(repo, self._entry("docs/a.md", "app/gone.py"))
        assert report.missing == []
        assert report.exempt == 1
        assert report.stale == []

    def test_an_exception_whose_file_is_now_tracked_is_stale(self, tmp_path):
        repo = _repo(tmp_path, {
            "docs/a.md": "`app/real.py`\n",
            "app/real.py": "",
        })
        report = _scan(repo, self._entry("docs/a.md", "app/real.py"))
        assert report.stale, "an exemption nobody needs is a hole"
        assert "tracked again" in report.stale[0][2]

    def test_an_exception_the_document_stopped_naming_is_stale(self, tmp_path):
        repo = _repo(tmp_path, {"docs/a.md": "nothing here\n"})
        report = _scan(repo, self._entry("docs/a.md", "app/gone.py"))
        assert report.stale
        assert "no longer named" in report.stale[0][2]

    def test_a_malformed_record_names_its_line(self, tmp_path):
        listing = tmp_path / "list.txt"
        listing.write_text("docs/a.md\tapp/gone.py\n", encoding="utf-8")
        with pytest.raises(ValueError) as excinfo:
            dl.load_exceptions(listing)
        assert "list.txt:1" in str(excinfo.value)

    def test_a_malformed_record_is_a_finding_not_a_measurement_failure(
            self, tmp_path, capsys):
        repo = _repo(tmp_path, {"docs/a.md": "`app/gone.py`\n"})
        listing = repo / "list.txt"
        listing.write_text("docs/a.md\tapp/gone.py\n", encoding="utf-8")
        assert dl.main(["--repo", str(repo), "--exceptions", str(listing)]) == 1

    def test_a_missing_list_is_empty(self, tmp_path):
        repo = _repo(tmp_path, {"docs/a.md": "`app/gone.py`\n"})
        assert _run(repo) == 1


class TestExitCodes:

    def test_a_clean_tree_is_zero(self, tmp_path, capsys):
        repo = _repo(tmp_path, {
            "docs/a.md": "`app/real.py` and [b](b.md)\n",
            "app/real.py": "",
            "docs/b.md": "",
        })
        assert _run(repo) == 0
        assert "0 unresolved" in capsys.readouterr().out

    def test_a_missing_reference_is_one_and_names_the_line(self, tmp_path, capsys):
        repo = _repo(tmp_path, {"docs/a.md": "intro\n\n`app/gone.py`\n"})
        assert _run(repo) == 1
        captured = capsys.readouterr()
        assert "docs/a.md:3" in captured.err
        assert "app/gone.py" in captured.err

    def test_a_stale_exception_is_one(self, tmp_path, capsys):
        repo = _repo(tmp_path, {
            "docs/a.md": "`app/real.py`\n",
            "app/real.py": "",
        })
        listing = repo / "list.txt"
        listing.write_text("docs/a.md\tapp/real.py\tkept for the test\n", encoding="utf-8")
        assert dl.main(["--repo", str(repo), "--exceptions", str(listing)]) == 1

    def test_no_documents_is_two(self, tmp_path):
        repo = _repo(tmp_path, {"app/main.py": ""})
        assert _run(repo) == 2

    def test_a_tree_without_git_is_two(self, tmp_path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "a.md").write_text("`app/gone.py`\n", encoding="utf-8")
        assert _run(tmp_path) == 2

    def test_show_names_each_verdict(self, tmp_path, capsys):
        repo = _repo(tmp_path, {
            "docs/a.md": "`app/gone.py`\n`app/real.py`\n",
            "app/real.py": "",
        })
        assert _run(repo, "--show", "docs/a.md") == 1
        out = capsys.readouterr().out
        assert "MISSING" in out and "ok" in out


class TestWhatTheRefusalPrints:
    """A refusal names the line **and shows it**.

    The number alone is a lookup: the reader opens a 1.2 MB file, finds line 38, and
    scans a 3 KB bullet for the reference. The window is the fix's first step — the
    sentence around the reference, cut at word boundaries so the words that survive
    are words the page wrote, and bounded so a refusal per offender is readable.
    """

    def _window(self, line: str) -> str:
        start = line.index("`")
        end = line.index("`", start + 1) + 1
        return dl.context_for(line, start, end)

    def test_a_short_line_is_shown_whole(self):
        line = "See `app/gone.py` for the loader."
        assert self._window(line) == line

    #: Several word lengths on purpose, and that is measured rather than tidy: one
    #: length is not a guard here. A cut at `CONTEXT` characters lands inside a word
    #: or exactly on its end depending on how long the word is, so a line of 5-letter
    #: words is cut mid-word while a line of 7-letter ones is not — and the first
    #: version of this test used one length, passed, and let a mutation that cuts
    #: wherever it likes survive. These lengths do not divide `CONTEXT` the same way.
    @pytest.mark.parametrize("length", [5, 6, 8, 9, 11, 13])
    def test_a_long_line_is_cut_at_word_boundaries_on_both_sides(self, length):
        word = "w" * length
        line = (word + " " + (word + " ") * 40 + "`app/gone.py` "
                + (word + " ") * 40 + word + " end")
        window = self._window(line)
        assert window.startswith("...") and window.endswith("...")
        assert "`app/gone.py`" in window
        # Whole words only. Checked against the line's **words** and not with `in
        # line`: a half word is a substring of the word it was cut out of, so the
        # weaker check passes for `wwww` and the guard would survive the mutation it
        # exists for.
        words = set(line.split())
        for token in window.replace("...", " ").split():
            assert token in words, f"{token!r} is a fragment of a word the line wrote"
        assert len(window) < len(line)

    def test_a_long_bullet_is_not_printed_in_full(self):
        line = ("Halo " * 300) + "`app/gone.py` " + ("selesai " * 300)
        window = self._window(line)
        assert len(window) < 3 * dl.CONTEXT + 40, (
            "the window must be bounded — AGENTS.md's bullets are single 3 KB lines")
        assert "app/gone.py" in window

    def test_the_window_puts_the_reference_between_its_own_words(self):
        line = "the loader `app/gone.py` lives beside the check"
        window = self._window(line)
        assert window == "the loader `app/gone.py` lives beside the check"

    def test_whitespace_becomes_one_line(self):
        line = "|\t`app/gone.py`\t|   the\tloader   |"
        window = self._window(line)
        assert "\t" not in window and "  " not in window
        assert window == "| `app/gone.py` | the loader |"

    def test_this_repo_s_punctuation_stays_legible(self):
        """ASCII on purpose, but an em dash escaped as `\u2014` is a snippet nobody
        reads: the handful this repository writes fold to their ASCII twins."""
        assert dl.printable("a \u2014 b \u2026 c \u2192 d") == "a - b ... c -> d"
        assert dl.printable("a \u2014 b").isascii()
        assert dl.printable("supabase/migrations/062_\u2026.sql").isascii()

    def test_only_a_refusal_carries_a_window(self, tmp_path):
        """The window is the printed half, so it is built where it is printed.

        `scan` reads every reference in the tree (1435 of them here) on every
        commit; a window for each is work nobody sees, and the module's measured
        runtime is a claim about the cheaper version.
        """
        # `missing`, not `gone`: a neighbouring reference whose own name carries an
        # exemption word exempts the whole line, which is the trap this guard's
        # own docs describe and a fixture is not immune to.
        repo = _repo(tmp_path, {
            "docs/a.md": "`app/real.py` plus `app/missing.py`\n",
            "app/real.py": "",
        })
        report = _scan(repo)
        by_state = {ref.state: ref for ref in report.refs}
        assert by_state["resolved"].context == ""
        assert by_state["missing"].context != ""

    def test_the_refusal_prints_the_line_under_the_reference(self, tmp_path, capsys):
        repo = _repo(tmp_path, {"docs/a.md": "See `app/gone.py` for the loader.\n"})
        assert _run(repo) == 1
        err = capsys.readouterr().err
        assert "docs/a.md:1" in err and "app/gone.py" in err
        assert "for the loader" in err, (
            "the fix is one edit away only if the sentence is on screen")

    def test_a_long_bullet_reaches_the_console_as_a_window(self, tmp_path, capsys):
        line = ("padding " * 200) + "`app/gone.py` " + ("trailing " * 200)
        repo = _repo(tmp_path, {"docs/a.md": line + "\n"})
        assert _run(repo) == 1
        err = capsys.readouterr().err
        assert "..." in err
        longest = max(len(row) for row in err.splitlines())
        assert longest < 400, f"a refusal printed {longest} characters on one line"


class TestTheGateRefuses:
    """The block that runs the tool, executed — not read.

    A line of shell that maps exit 1 to exit 1 is the whole promise of "fails
    before the commit", so it is run the way the hook runs it. Lifted from the
    fenced block rather than re-typed: a copy would pass while the gate kept its
    own opinion.
    """

    def _block(self) -> str:
        text = GATE.read_text(encoding="utf-8")
        return text.split("# doclinks:start", 1)[1].split("# doclinks:end", 1)[0]

    def _tool(self) -> str:
        """The real tool, with its own docstring replaced.

        The block runs `$REPO/deploy/doc_links.py`, so the copy lands **inside**
        the tree it scans — and the real module docstring names this repository's
        pages and tests, which a three-file fixture does not have. The copy under
        test is the code; the docstring's own references are asserted against the
        real tree (`TestTheRepository`), where they are a claim worth making.
        """
        text = (DEPLOY / "doc_links.py").read_text(encoding="utf-8")
        start = text.index('"""')
        end = text.index('"""', start + 3) + 3
        return text[:start] + '"""The check, copied into one scratch tree."""' + text[end:]

    def _gate_tree(self, tmp_path: Path, files: dict[str, str]) -> Path:
        """A scratch tree that can run the block, including the tool it runs.

        `$REPO/deploy/doc_links.py` puts the copy **inside** the tree it scans,
        and this tool reads the copy of every file it is given — its own code
        names `tests/unit/git_env.py` and the exception list, which a three-file
        fixture has no reason to hold. They are stubbed rather than exempted: a
        fixture that records exceptions for the tool under test would be hiding
        the thing it is testing.
        """
        return _repo(tmp_path, {
            "deploy/doc_links.py": self._tool(),
            "deploy/doc_links_gone.txt": "",
            "tests/unit/git_env.py": "",
            **files,
        })

    def _run_block(self, repo: Path) -> subprocess.CompletedProcess:
        script = repo.parent / "block.sh"
        # Bytes, not `write_text`: on Windows that translates `\n` to `\r\n`, and
        # bash reads a CRLF script as `$'\r': command not found`.
        script.write_bytes(self._block().replace("\r\n", "\n").encode("utf-8"))
        env = dict(os.environ)
        env["PY"] = sys.executable
        env["REPO"] = str(repo)
        return subprocess.run([shutil.which("bash") or "bash", str(script)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=env)

    def test_a_broken_reference_stops_the_commit(self, tmp_path):
        repo = self._gate_tree(tmp_path, {"docs/a.md": "`app/gone.py`\n"})
        done = self._run_block(repo)
        assert done.returncode == 1, done.stderr
        assert "FAILED" in done.stderr
        assert "app/gone.py" in done.stderr

    def test_a_clean_tree_lets_it_through(self, tmp_path):
        repo = self._gate_tree(tmp_path, {
            "docs/a.md": "`app/real.py`\n",
            "app/real.py": "",
        })
        done = self._run_block(repo)
        assert done.returncode == 0, done.stderr
        assert "0 unresolved" in done.stdout

    def test_the_gate_relays_the_line_and_not_only_the_number(self, tmp_path):
        """The block indents the tool's output; the sentence has to survive that."""
        repo = self._gate_tree(tmp_path, {
            "docs/a.md": "See `app/gone.py` for the loader.\n",
        })
        done = self._run_block(repo)
        assert done.returncode == 1, done.stderr
        assert "See `app/gone.py` for the loader." in done.stderr, done.stderr


    def test_a_link_to_a_renamed_heading_stops_the_commit(self, tmp_path):
        """The rename this rule exists for: every path still resolves, and the
        reader still lands somewhere the link never promised."""
        repo = self._gate_tree(tmp_path, {
            "docs/a.md": "See [the cases](b.md#the-cases).\n",
            "docs/b.md": "## Case list\n",
        })
        done = self._run_block(repo)
        assert done.returncode == 1, done.stderr
        assert "docs/a.md:1" in done.stderr
        assert "no heading `#the-cases`" in done.stderr

    def test_a_gate_s_own_failure_text_stops_the_commit(self, tmp_path):
        """The version of this defect a reader meets at the worst moment: the
        message says to run a script that is not there."""
        repo = self._gate_tree(tmp_path, {
            "docs/a.md": "nothing here\n",
            "deploy/gate.sh": ('#!/bin/sh\n'
                               '  echo "FAILED - run deploy/gone.sh --check" >&2\n'),
        })
        done = self._run_block(repo)
        assert done.returncode == 1, done.stderr
        assert "deploy/gate.sh:2" in done.stderr
        assert "FAILED - run deploy/gone.sh --check" in done.stderr


class TestTheRepository:

    def test_every_tracked_document_is_scanned(self):
        report = dl.scan(dl.ROOT)
        tracked = [p for p in dl.tracked(dl.ROOT) if p.endswith(".md")]
        assert report.docs == sorted(tracked)
        assert len(report.docs) > 50

    def test_the_real_tree_passes(self):
        """The guard's own claim, measured on the tree it ships in."""
        report = dl.scan(dl.ROOT, exceptions=dl.load_exceptions(EXCEPTIONS))
        assert report.missing == []
        assert report.stale == []
        assert report.checked > 500

    def test_the_exception_list_stays_small_and_explained(self):
        entries = dl.load_exceptions(EXCEPTIONS)
        assert entries, "the list exists because three mentions are deliberate"
        assert len(entries) <= 8, "a list this long is a rule nobody is applying"
        for (doc, path), why in entries.items():
            assert (dl.ROOT / doc).exists(), doc
            assert len(why) > 20, f"{path}: the reason is a word, not a reason"
            assert not dl.resolve(dl.ROOT, doc, path), \
                f"{path}: the file exists — drop the exception"

    def test_this_tool_s_own_copy_is_a_shape_not_a_path(self):
        """The guard reads itself, so an example in here has to be a shape.

        `deploy/doc_links.py` is one of the files it scans once committed, and its
        docstring is where the rules are explained with examples. Written as
        `deploy/<gate>.sh` on purpose: a made-up path there would refuse every
        release with a sentence about the guard itself, and a shape cannot rot.
        """
        paths = dl.tracked(dl.ROOT) + ["deploy/doc_links.py", "deploy/doc_links_gone.txt"]
        report = dl.scan(dl.ROOT, docs=paths,
                         exceptions=dl.load_exceptions(EXCEPTIONS))
        own = [ref for ref in report.missing if ref.doc == "deploy/doc_links.py"]
        assert own == [], (
            "the tool's own copy names a path this repository does not track: "
            + ", ".join(ref.path for ref in own))

    def test_the_git_variables_match_the_suites(self):
        """`deploy/` may not import the suite, so the list is written twice."""
        assert dl.GIT_REPO_ENV == GIT_REPO_ENV

    def test_the_gate_arms_and_runs_it(self):
        text = GATE.read_text(encoding="utf-8")
        armament = next(line for line in text.splitlines()
                        if line.startswith("ARMAMENT="))
        assert "deploy/doc_links.py" in armament
        assert "deploy/doc_links_gone.txt" in armament
        block = text.split("# doclinks:start", 1)[1].split("# doclinks:end", 1)[0]
        assert '"$REPO/deploy/doc_links.py"' in block
        assert "DOCLINKS_CLAIM" in block
        assert "exit 1" in block
        assert "${DOCLINKS_CLAIM}" in text, "a run whose result nobody prints"

    def test_the_gate_names_files_that_exist(self):
        text = GATE.read_text(encoding="utf-8")
        armament = next(line for line in text.splitlines()
                        if line.startswith("ARMAMENT="))
        for name in armament.split("=", 1)[1].strip('"').split():
            if name.startswith("$"):        # `$TESTS` is expanded by the shell
                continue
            assert (ROOT / name).exists(), name
            assert (ROOT / name).stat().st_size > 0, name

    def test_a_non_ascii_reference_does_not_crash_the_report(self, tmp_path):
        """Measured: a U+2026 in an offender crashed `print` on a cp1252 console,
        and a guard that dies while reporting reports nothing."""
        assert dl._safe("supabase/migrations/062_…sql").isascii()

    def test_the_report_survives_a_console_that_cannot_encode_it(self, tmp_path):
        repo = _repo(tmp_path, {"docs/a.md": "`app/café.py`\n"})
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "cp1252"
        done = subprocess.run(
            [sys.executable, str(DEPLOY / "doc_links.py"), "--repo", str(repo),
             "--exceptions", str(repo / "none.txt")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env,
        )
        assert done.returncode == 1
        assert "Traceback" not in done.stderr
        assert "caf" in done.stderr
