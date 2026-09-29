"""Every file under app/static is a page on the site, so every file there has to
be meant to be one.

nginx serves `app/static/` wholesale and Flask mounts the same directory at
`/static/`, so a file dropped in it is not inert: it is reachable by anyone who
guesses the name, on the school's site as much as on a developer's machine, with
no template, no route and no reader. Two failures live in that gap:

* **a file git does not track.** The site everyone else uses is built from the
  repository, so the file is on one machine and nowhere else — behaviour that
  cannot be reproduced, reviewed or rolled back.
* **a file no static URL reaches.** Committing it is not the same as serving it.
  Three files were in that state when this guard was written: a scratch copy of
  the loader.io verification page sitting beside the route that already answers
  its URL, and two dead copies of exam-page scripts that the exam page had long
  since inlined. No template, script or config named either script, and nothing
  in the suite could notice — the page answered 200 either way.

What counts as *reached* is deliberately narrow, and the narrowness is the whole
discrimination. A path is reached when the app turns it into a static URL —
`/static/<path>`, `asset_v('<path>')`, `url_for('static', filename='<path>')` — or
when a file beside it under `app/static` names it (a stylesheet's own fonts, the
manifest's icon), or when the app builds URLs inside a directory the file is in
(the payment page assembles the Midtrans build path from a variable, so only
`vendor/midtrans/` is literal). A bare mention of the *name* is not a reference,
and that exclusion has to stay: `app/routes/public.py` contains
`/loaderio-<hash>.html` as a **route**, which is exactly the kind of mention that
let its redundant static copy go unnoticed.

Honest limits, both deliberate. A file can be reached and still be scratch — this
asks whether the app asks for it, not whether asking for it is a good idea. And a
build with no git skips the readings that need git rather than failing them: a
checker that cannot see must never be the reason a release is refused.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app" / "static"
GATE = ROOT / "deploy" / "theme_gate.sh"

#: Directories under app/static that are written at runtime and gitignored on
#: purpose, so no commit can carry them and a release must not be judged on them.
RUNTIME_DIRS = ("uploads",)

#: Suffixes that can hold a static URL. Nothing here is read for its prose.
SOURCE_SUFFIXES = {".py", ".html", ".css", ".js", ".json", ".svg"}

#: Files outside app/ that name static assets.
EXTRA_SOURCES = ("tailwind.config.js", "package.json")


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=False)


def _prefix(static: Path, root: Path) -> str:
    return static.relative_to(root).as_posix()


def _listed(static: Path, root: Path, *flags: str) -> set:
    """Paths git reports under ``static``, relative to it.

    `git ls-files` answers from the index, so a file staged but not committed
    counts as tracked — which is right, because it is in the release being
    prepared and the deploy will carry it.
    """
    prefix = _prefix(static, root)
    out = _git("ls-files", *flags, "--", prefix, cwd=root)
    found = set()
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.startswith(prefix + "/"):
            found.add(line[len(prefix) + 1:])
    return found


def tracked(static: Path, root: Path) -> set:
    return _listed(static, root)


def untracked(static: Path, root: Path) -> set:
    """Files git neither tracks nor ignores — dropped into the served tree.

    `--exclude-standard` is what keeps `uploads/` out of this answer, so the
    finding is "a file nobody committed" and never "a file the app wrote".
    """
    return _listed(static, root, "--others", "--exclude-standard")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def url_sources(root: Path) -> dict:
    """Every file a static URL could be written in, keyed by repo-relative path.

    The whole of `app/`, not just the templates: `base.html` writes
    `asset_v('css/theme.css')`, a page writes `/static/js/...` in a `<script>`,
    and `app/static/manifest.json` is what names the icon.
    """
    out = {}
    for path in sorted((root / "app").rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        if any(rel.startswith("app/static/" + d + "/") for d in RUNTIME_DIRS):
            continue
        out[rel] = _read(path)
    for extra in EXTRA_SOURCES:
        path = root / extra
        if path.is_file():
            out[extra] = _read(path)
    return out


def _url_forms(rel: str) -> tuple:
    """The ways this app turns a static path into a URL. Nothing else counts."""
    return (
        "/static/" + rel,
        "asset_v('" + rel + "')",
        'asset_v("' + rel + '")',
        "filename='" + rel + "'",
        'filename="' + rel + '"',
    )


def _dirs(rel: str) -> tuple:
    """Directory prefixes of ``rel`` the app could be building URLs under.

    Two segments at least. `vendor/` alone is a substring of every
    `/static/vendor/...` URL in the tree, so one segment would let a scratch file
    sit beside the vendored libraries and call itself reached.
    """
    parts = rel.split("/")[:-1]
    return tuple("/".join(parts[:i]) + "/" for i in range(2, len(parts) + 1))


def reached(rel: str, texts: dict, prefix: str, own: str = "") -> str:
    """The source that reaches ``rel``, or "" when nothing does.

    ``own`` is the file's own repo-relative path: a file naming its own path is
    not evidence that anything asks for it.
    """
    for source, text in texts.items():
        if source == own:
            continue
        if any(form in text for form in _url_forms(rel)):
            return source
    dirs = _dirs(rel)
    for source, text in texts.items():
        if source == own:
            continue
        if any(d in text for d in dirs):
            return source
    name = rel.rsplit("/", 1)[-1]
    for source, text in texts.items():
        if source == own or not source.startswith(prefix + "/"):
            continue
        if name in text:
            return source
    return ""


def unreached(static: Path, root: Path) -> dict:
    """rel path -> why nothing reaches it, for every tracked file under static."""
    prefix = _prefix(static, root)
    texts = url_sources(root)
    out = {}
    for rel in sorted(tracked(static, root)):
        if rel.split("/", 1)[0] in RUNTIME_DIRS:
            continue
        if not reached(rel, texts, prefix, prefix + "/" + rel):
            out[rel] = (
                "nothing turns this into a static URL: no `/static/" + rel
                + "`, no `asset_v('" + rel + "')`, no `url_for('static', "
                "filename=...)`, no directory it is built under, and no file "
                "beside it that names it. `/static/` serves every file below it "
                "to anyone, so an asset has to be asked for by a page — and "
                "scratch has to be deleted."
            )
    return out


# ── what counts as reached, read from the rule rather than from the tree ─────

class TestWhatCountsAsReached:
    def test_a_static_url_reaches(self):
        texts = {"app/templates/page.html":
                 '<script src="/static/js/thing.js"></script>'}
        assert reached("js/thing.js", texts, "app/static",
                       "app/static/js/thing.js") == "app/templates/page.html"

    @pytest.mark.parametrize("text", [
        "{{ asset_v('css/theme.css') }}",
        '{{ asset_v("css/theme.css") }}',
        "url_for('static', filename='css/theme.css')",
        'url_for("static", filename="css/theme.css")',
    ])
    def test_a_url_the_app_builds_reaches(self, text):
        texts = {"app/templates/base.html": text}
        assert reached("css/theme.css", texts, "app/static",
                       "app/static/css/theme.css") == "app/templates/base.html"

    def test_a_route_that_merely_echoes_the_name_does_not_reach(self):
        """The defect this guard was written for.

        `app/routes/public.py` answers `/loaderio-<hash>.html` with the token as a
        string. Its presence in that file made the redundant *static* copy of the
        same page look referenced, and it sat in the served tree beside a route
        that already answered the URL.
        """
        texts = {"app/routes/public.py":
                 '@public_bp.route("/loaderio-abc123.html")\n'
                 "def loaderio_verify():\n"
                 '    return "loaderio-abc123", 200\n'}
        assert reached("loaderio-abc123.html", texts, "app/static",
                       "app/static/loaderio-abc123.html") == ""

    def test_prose_naming_a_file_does_not_reach_it(self):
        """A note about a script is not a page asking for it."""
        texts = {"app/services/notes.py":
                 "# the old anti-cheat.js was inlined into the exam page in 2025\n"}
        assert reached("js/anti-cheat.js", texts, "app/static",
                       "app/static/js/anti-cheat.js") == ""

    def test_a_file_beside_it_reaches_it(self):
        """A stylesheet's fonts are named by the stylesheet, not by a page."""
        texts = {"app/static/vendor/inter/inter.css":
                 "@font-face { src: url('Inter-400.ttf') format('truetype'); }"}
        assert reached("vendor/inter/Inter-400.ttf", texts, "app/static",
                       "app/static/vendor/inter/Inter-400.ttf") == \
            "app/static/vendor/inter/inter.css"

    def test_a_template_naming_a_basename_reaches_nothing(self):
        """The peer rule is for files that ship *beside* the asset, so a page
        saying `snap.js` cannot make every snap.js in the tree reached."""
        texts = {"app/templates/admin_sekolah/payment.html": "// load snap.js\n"}
        rel = "vendor/midtrans/production/veritrans.co.id/snap.js"
        assert reached(rel, texts, "app/static", "app/static/" + rel) == ""

    def test_the_directory_rule_is_deliberately_broad(self):
        """A stated cost rather than an accident.

        `payment.html` assembles the Midtrans build name from a variable, so the
        directory is the most precise thing that can be written down — and every
        file under it is therefore reached, including one that is scratch.
        """
        texts = {"app/templates/admin_sekolah/payment.html":
                 "filename='vendor/midtrans/' ~ build ~ '/veritrans.co.id/snap.js'"}
        rel = "vendor/midtrans/whatever/scratch.html"
        assert reached(rel, texts, "app/static", "app/static/" + rel) == \
            "app/templates/admin_sekolah/payment.html"

    def test_the_directory_the_app_builds_under_reaches_its_files(self):
        """`payment.html` assembles the build name, so only `vendor/midtrans/` is
        literal — and the file still has a URL that reaches it."""
        texts = {"app/templates/admin_sekolah/payment.html":
                 "{% set snap_local = url_for('static', filename='vendor/midtrans/'"
                 " ~ ('production' if is_production else 'sandbox')"
                 " ~ '/veritrans.co.id/snap.js') %}"}
        rel = "vendor/midtrans/production/veritrans.co.id/snap.js"
        assert reached(rel, texts, "app/static", "app/static/" + rel) == \
            "app/templates/admin_sekolah/payment.html"

    def test_one_directory_segment_is_not_a_reach(self):
        """`vendor/` is a substring of every `/static/vendor/...` URL in base.html
        and of any prose that says "see vendor/", so a single segment would reach
        a scratch file sitting beside the vendored libraries."""
        texts = {"app/templates/base.html":
                 '<script src="/static/vendor/alpine.min.js"></script>\n'
                 "# the vendored libraries live in vendor/\n"}
        assert reached("vendor/scratch.html", texts, "app/static",
                       "app/static/vendor/scratch.html") == ""

    def test_a_file_cannot_be_its_own_evidence(self):
        texts = {"app/static/js/thing.js": "fetch('/static/js/thing.js');\n"}
        assert reached("js/thing.js", texts, "app/static",
                       "app/static/js/thing.js") == ""


# ── what git says about a checkout ───────────────────────────────────────────

def git_answers(root: Path = ROOT) -> bool:
    """Whether git can be asked at all.

    A build that cannot run git must not fail this check — and must not pass it
    either, because an unanswerable question is not a clean tree. So the readings
    below are skipped, and a skip says which question went unasked.
    """
    if shutil.which("git") is None:
        return False
    out = _git("rev-parse", "--is-inside-work-tree", cwd=root)
    return out.returncode == 0 and out.stdout.strip() == "true"


needs_git = pytest.mark.skipif(not git_answers(), reason="git cannot be asked here")


def _commit(repo: Path, message: str = "init") -> None:
    _git("-c", "user.email=t@example.com", "-c", "user.name=t",
         "commit", "-q", "-m", message, cwd=repo)


def _checkout(tmp_path: Path) -> Path:
    """A checkout whose one script *is* asked for, so it starts clean."""
    repo = tmp_path / "repo"
    (repo / "app" / "static" / "js").mkdir(parents=True)
    (repo / "app" / "templates").mkdir(parents=True)
    (repo / "app" / "static" / "js" / "thing.js").write_text(
        "// a script\n", encoding="utf-8")
    (repo / "app" / "templates" / "page.html").write_text(
        '<script src="/static/js/thing.js"></script>\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, text=True)
    _git("add", "-A", cwd=repo)
    _commit(repo)
    return repo


@needs_git
class TestWhatGitSays:
    def test_a_checkout_where_everything_is_asked_for_is_clean(self, tmp_path):
        repo = _checkout(tmp_path)
        static = repo / "app" / "static"
        # Asserted first: an empty reading passes every check below, so a fixture
        # that is not being read would look exactly like a clean tree.
        assert tracked(static, repo) == {"js/thing.js"}
        assert unreached(static, repo) == {}
        assert untracked(static, repo) == set()

    def test_git_that_cannot_answer_says_so(self, tmp_path):
        """The one hazard of the no-git path: `ls-files` on a directory that is
        not a checkout answers nothing, and nothing reads as a clean tree."""
        assert git_answers(tmp_path) is False
        assert tracked(tmp_path / "app" / "static", tmp_path) == set()

    def test_an_untracked_file_is_reported(self, tmp_path):
        """The file nobody committed: on this machine's site, not the school's."""
        repo = _checkout(tmp_path)
        (repo / "app" / "static" / "js" / "scratch.js").write_text(
            "<script src='/static/js/scratch.js'></script>\n", encoding="utf-8")
        assert untracked(repo / "app" / "static", repo) == {"js/scratch.js"}

    def test_an_untracked_file_is_reported_even_when_a_page_asks_for_it(self, tmp_path):
        """Being served is not the same as being in the release."""
        repo = _checkout(tmp_path)
        (repo / "app" / "static" / "js" / "scratch.js").write_text(
            "// left behind\n", encoding="utf-8")
        page = repo / "app" / "templates" / "page.html"
        page.write_text(page.read_text(encoding="utf-8")
                        + '<script src="/static/js/scratch.js"></script>\n',
                        encoding="utf-8")
        assert untracked(repo / "app" / "static", repo) == {"js/scratch.js"}
        assert unreached(repo / "app" / "static", repo) == {}


    def test_a_file_the_app_wrote_is_not_a_scratch_file(self, tmp_path):
        """`uploads/` is gitignored on purpose; the app writes it at runtime, so a
        scan that reported it would be reporting the app working."""
        repo = _checkout(tmp_path)
        (repo / ".gitignore").write_text("app/static/uploads/\n", encoding="utf-8")
        logos = repo / "app" / "static" / "uploads" / "logos"
        logos.mkdir(parents=True)
        (logos / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        static = repo / "app" / "static"
        assert untracked(static, repo) == set()
        assert unreached(static, repo) == {}

    def test_the_route_mention_and_the_scratch_page_together_are_reported(self, tmp_path):
        """The real shape, end to end in a scratch tree: a served route and the
        redundant static copy of the same page."""
        repo = _checkout(tmp_path)
        (repo / "app" / "routes").mkdir(parents=True)
        (repo / "app" / "routes" / "public.py").write_text(
            '@public_bp.route("/loaderio-abc123.html")\n'
            "def loaderio_verify():\n"
            '    return "loaderio-abc123", 200\n', encoding="utf-8")
        (repo / "app" / "static" / "loaderio-abc123.html").write_text(
            "loaderio-abc123\n", encoding="utf-8")
        _git("add", "-A", cwd=repo)
        _commit(repo, "the scratch page")
        assert list(unreached(repo / "app" / "static", repo)) == \
            ["loaderio-abc123.html"]

    def test_a_staged_file_counts_as_tracked(self, tmp_path):
        """It is in the release being prepared, even before the commit."""
        repo = _checkout(tmp_path)
        (repo / "app" / "static" / "js" / "new.js").write_text(
            "// new\n", encoding="utf-8")
        _git("add", "app/static/js/new.js", cwd=repo)
        assert "js/new.js" in tracked(repo / "app" / "static", repo)
        assert untracked(repo / "app" / "static", repo) == set()


# ── the readings are wide enough to be worth anything ────────────────────────

class TestTheReadingIsWideEnough:
    def test_the_sources_include_every_place_a_static_url_can_be_written(self):
        texts = url_sources(ROOT)
        for rel in ("app/templates/base.html", "app/static/manifest.json",
                    "app/static/css/theme.css", "tailwind.config.js"):
            assert rel in texts, (
                rel + " is not read as a source, so a URL written there would be "
                "invisible — and the assets it names would read as scratch")

    def test_losing_a_source_would_be_reported_not_forgiven(self):
        """If the scan stopped reading templates, the guard must not go quiet: the
        assets they name have to come back as unreached."""
        texts = url_sources(ROOT)
        texts.pop("app/templates/base.html")
        assert reached("vendor/alpine.min.js", texts, "app/static",
                       "app/static/vendor/alpine.min.js") == "", (
            "base.html was dropped and the vendored library still read as "
            "reached, so something else is answering for it and the guard would "
            "survive losing the file that names the whole tree")

    def test_the_runtime_directory_is_not_scanned(self):
        texts = url_sources(ROOT)
        assert not [rel for rel in texts if "/uploads/" in rel], (
            "a file the app writes at runtime is being read as a source; it is "
            "not in the release and it is not an asset's reference")


# ── this checkout ────────────────────────────────────────────────────────────

@needs_git
class TestThisCheckout:
    def test_this_checkout_has_files_to_judge(self):
        """A reading is only worth something if it read something: an empty list
        means git did not answer, not that nothing is wrong."""
        assert tracked(STATIC, ROOT), (
            "git listed no files under app/static, so both checks below would pass "
            "vacuously — this is the only thing standing between a broken reading "
            "and a green gate")

    def test_nothing_under_static_is_untracked(self):
        loose = sorted(untracked(STATIC, ROOT))
        assert not loose, (
            "these files are served by /static/ but are in no commit, so they "
            "exist on one machine and nowhere else — commit them or delete "
            "them:\n  " + "\n  ".join(loose))

    def test_every_committed_file_under_static_is_asked_for(self):
        bad = unreached(STATIC, ROOT)
        assert not bad, (
            "these files are committed, served to anyone who asks, and named by "
            "nothing the app ships:\n  "
            + "\n  ".join(
                rel + "\n      " + why.replace("\n", " ")
                for rel, why in bad.items()))


class TestTheGateRunsIt:
    """A guard the deploy does not run cannot stop a release shipping one."""

    def test_the_theme_gate_lists_this_file(self):
        src = GATE.read_text(encoding="utf-8")
        tests = [ln for ln in src.splitlines() if ln.startswith("TESTS=")]
        assert tests, "the gate no longer lists the checks it runs"
        assert "tests/unit/test_static_tree.py" in tests[0], (
            "the static-tree guard is not in the gate's list, so a release that "
            "adds a scratch file under app/static passes the pre-commit hook and "
            "the auto-deploy with nothing looking")

    def test_the_gate_says_what_it_checked(self):
        src = GATE.read_text(encoding="utf-8")
        assert "every file under /static/ is one the app asks for" in src, (
            "the gate's OK line does not mention the served tree, so its "
            "verdict understates what it read")
