"""A dirty checkout is two different problems wearing one word.

The deploy refuses a dirty checkout, and the page says so — "the checkout has
uncommitted changes". That sentence covers two states whose right answers are
opposite:

* **a hand edit the release can replace** — somebody changed a tracked file, the
  box's version is worth keeping, and the runner sets it aside as a patch before
  merging;
* **a blob no checkout can reproduce** — the commit stores bytes the path's own
  `.gitattributes` filter would never produce (this project shipped 108 carriage
  returns inside `app/routes/admin_sekolah.py`, a file `.gitattributes` promises is
  `eol=lf`). `git checkout`, `git restore` and `git stash` all write *through* the
  filter, so the path reads as modified no matter how many times it is restored and
  `git merge --ff-only` refuses for that reason, forever. The heal for it is not a
  restore at all: HEAD's own bytes are written in verbatim, and the filter is
  overridden for that one path.

An operator staring at "the checkout has uncommitted changes" cannot tell these
apart without reading bytes, and the remedies are not the same. So the page
measures it, read-only, and says which one it is holding.

The measurement is done by the *page*, not by the runner, and that is deliberate:
the box that needs it most is the one running a runner old enough to refuse a dirty
checkout outright, which will never write this classification down. `git status`,
`git check-attr` and `git cat-file` are all it needs, and all three are reads.

What these tests hold:

* **the two states are told apart on a real repository**, and the unreproducible
  one is *proved* to be unreproducible — `git checkout HEAD -- <path>` is run and
  the path is asserted to still read as modified;
* **"a hand edit" is not guessed from a name or an extension** — it is the answer
  only when the path's own attributes could not have produced the blob;
* **a path HEAD has never seen is a hand edit**, because there is no blob to blame;
* **both at once is its own answer**, not a rounding to one of them;
* and the page has a sentence for each, in both languages.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services import deploy_status_service as status  # noqa: E402
from tests.unit.git_env import git_env  # noqa: E402

TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "deploy_status.html"
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(GIT is None, reason="needs git to build a checkout")


def _git(repo: Path, *args: str, data: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.com",
         "-c", "user.name=t", *args],
        input=data, capture_output=True, check=False, env=git_env())


def _repo(tmp_path: Path) -> Path:
    """A checkout whose attributes normalise Python to LF — this project's own rule."""
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / ".gitattributes").write_text("*.py text eol=lf\n", encoding="utf-8")
    (repo / "app" / "clean.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "app" / "added.py").write_text("new = True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, check=False,
                   env=git_env())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _unreproducible_path(repo: Path, rel: str = "app/cr.py") -> None:
    """Commit a blob that bypasses the filters, then leave the filtered file in place.

    This is the shape the box was in: the commit holds bytes the attribute would
    never produce, so the worktree git checks out of it can never compare equal.
    """
    blob = b"x = 1\r\n\r\ny = 2\r\n"
    written = _git(repo, "hash-object", "-w", "--no-filters", "--stdin", data=blob)
    sha = written.stdout.decode().strip()
    assert written.returncode == 0 and sha, written.stderr
    _git(repo, "update-index", "--add", "--cacheinfo", f"100644,{sha},{rel}")
    _git(repo, "commit", "-q", "-m", "a blob the filters cannot reproduce")
    # The file a checkout leaves behind: normalised, and therefore different from
    # the blob it came from.
    (repo / rel).write_text("x = 1\n\ny = 2\n", encoding="utf-8")


def _kinds(repo: Path) -> dict:
    return status.dirty_kinds_state(repo, now=_now())


def _now():
    return status._dt.datetime(2026, 9, 30, 12, 0, tzinfo=status._dt.timezone.utc)


def _paths(repo: Path) -> list[str]:
    rc, out = status._git_out(GIT, repo, "status", "--porcelain")
    assert rc == 0, out
    return [status._porcelain_path(ln) for ln in out.splitlines() if ln.strip()]


# ── 1. a clean checkout ──────────────────────────────────────────────────────

class TestACleanCheckout:
    def test_a_clean_tree_is_none(self, tmp_path):
        state = _kinds(_repo(tmp_path))
        assert state["key"] == status.DIRTY_NONE
        assert state["total"] == 0 and state["paths"] == []

    def test_it_does_not_measure_a_path_that_is_not_dirty(self, tmp_path):
        repo = _repo(tmp_path)
        state = _kinds(repo)
        assert state["paths"] == [], "a clean path must not appear in the reading"


# ── 2. the two kinds ─────────────────────────────────────────────────────────

class TestAHandEdit:
    def test_a_changed_tracked_file_is_a_hand_edit(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "app" / "clean.py").write_text("x = 2\n", encoding="utf-8")
        state = _kinds(repo)
        assert state["key"] == status.DIRTY_HAND, state
        assert state["hand"] == 1 and state["blob"] == 0
        assert [p["path"] for p in state["paths"]] == ["app/clean.py"]
        assert state["paths"][0]["kind"] == status.DIRTY_HAND

    def test_a_restore_clears_it(self, tmp_path):
        """The property that makes it the other kind: a checkout can replace it."""
        repo = _repo(tmp_path)
        target = repo / "app" / "clean.py"
        target.write_text("x = 2\n", encoding="utf-8")
        assert _git(repo, "checkout", "HEAD", "--", "app/clean.py").returncode == 0
        assert status._git_out(GIT, repo, "status", "--porcelain")[1].strip() == ""


class TestAnUnreproducibleBlob:
    def test_it_is_classified_as_the_blob_not_the_box(self, tmp_path):
        repo = _repo(tmp_path)
        _unreproducible_path(repo)
        state = _kinds(repo)
        assert state["key"] == status.DIRTY_BLOB, state
        assert state["blob"] == 1 and state["hand"] == 0
        assert [p["path"] for p in state["paths"]] == ["app/cr.py"]

    def test_a_checkout_cannot_clear_it(self, tmp_path):
        """The whole point: the ordinary remedy does nothing here, and the page says
        which remedy applies."""
        repo = _repo(tmp_path)
        _unreproducible_path(repo)
        assert _kinds(repo)["key"] == status.DIRTY_BLOB
        _git(repo, "checkout", "HEAD", "--", "app/cr.py")
        assert status._git_out(GIT, repo, "status", "--porcelain")[1].strip() != "", (
            "the fixture is not reproducing the defect: a checkout cleared it")
        assert _kinds(repo)["key"] == status.DIRTY_BLOB, (
            "the reading changed after a restore it should not have survived")

    def test_a_path_head_has_never_seen_is_a_hand_edit(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "app" / "fresh.py").write_text("brand = new\n", encoding="utf-8")
        state = _kinds(repo)
        assert state["key"] == status.DIRTY_HAND
        assert state["paths"][0]["path"] == "app/fresh.py"

    def test_without_a_filter_a_carriage_return_is_nothing_odd(self, tmp_path):
        """`crlf` is what some attributes *ask* for; only a blob the path's own
        attribute would never produce is the defect."""
        repo = _repo(tmp_path)
        (repo / ".gitattributes").write_text("*.py text eol=lf\n*.md text eol=crlf\n",
                                             encoding="utf-8")
        (repo / "notes.md").write_text("hello\r\nworld\r\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "a crlf document, as its attribute asks")
        state = _kinds(repo)
        kinds = {p["path"]: p["kind"] for p in state["paths"]}
        assert kinds.get("notes.md") in (None, status.DIRTY_HAND), (
            "a file whose attribute asks for CRLF must not be called a defect")


class TestBothAtOnce:
    def test_a_mixture_is_its_own_answer(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "app" / "clean.py").write_text("x = 2\n", encoding="utf-8")
        _unreproducible_path(repo)
        state = _kinds(repo)
        assert state["key"] == status.DIRTY_MIXED, state
        assert state["hand"] == 1 and state["blob"] == 1 and state["total"] == 2

    def test_the_paths_carry_their_own_kind(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "app" / "clean.py").write_text("x = 2\n", encoding="utf-8")
        _unreproducible_path(repo)
        kinds = {p["path"]: p["kind"] for p in _kinds(repo)["paths"]}
        assert kinds == {"app/clean.py": status.DIRTY_HAND, "app/cr.py": status.DIRTY_BLOB}


# ── 3. the boundary ──────────────────────────────────────────────────────────

class TestTheBounds:
    def test_a_repo_that_is_not_a_checkout_is_unreadable_not_none(self, tmp_path):
        state = status.dirty_kinds_state(tmp_path / "nowhere", now=_now())
        assert state["key"] == status.DIRTY_UNREADABLE, (
            "`none` is a claim that the tree is clean, and a tree nobody could read "
            "is not that claim")
        assert state["total"] == 0

    def test_a_very_large_dirty_set_is_bounded(self, tmp_path):
        """The page is served from a 1-vCPU box; the reading is per path and capped."""
        repo = _repo(tmp_path)
        for i in range(status.DIRTY_PATHS_LIMIT + 5):
            (repo / "app" / f"many_{i}.py").write_text("v = 1\n", encoding="utf-8")
        state = _kinds(repo)
        assert len(state["paths"]) == status.DIRTY_PATHS_LIMIT
        assert state["truncated"] >= 5, (
            "a capped reading must say how many it did not measure, or a short list "
            "reads as a small change")

    def test_the_checkout_card_carries_it(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "app" / "clean.py").write_text("x = 2\n", encoding="utf-8")
        state = status.checkout_state(repo, now=_now())
        assert state["dirty"] == 1
        assert state["dirty_kinds"]["key"] == status.DIRTY_HAND, (
            "the card that says the checkout is dirty must also say what kind of "
            "dirty it is")


# ── 4. the page ──────────────────────────────────────────────────────────────

class TestThePage:
    def test_every_kind_has_a_sentence(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        for key in sorted(status.DIRTY_KIND_KEYS):
            assert f"dirty_kinds.key == '{key}'" in text, (
                f"the page has no sentence for the `{key}` dirty checkout, so an "
                f"operator would read a blank")

    def test_the_sentences_say_what_to_do(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        # The blob case is the one nobody can guess: it must name the remedy that
        # works (HEAD's own bytes, verbatim) rather than the one that never did.
        assert "byte blob" in text or "bytes verbatim" in text or "apa adanya" in text, (
            "the blob sentence must name the remedy a restore cannot perform")

    def test_the_card_is_bilingual(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        block = text.split("dirty_kinds.key == ", 1)[1]
        window = block[:2500]
        assert window.count("x-text=\"t('") >= 3, (
            "the dirty-checkout reading is copy on a bilingual page like every other")


# ── the other direction: one state's sentence under another state's key ──────
#
# "Every kind has a sentence" is satisfied by four copies of the *same* sentence, and by
# the blob's sentence pasted under `hand` — a branch chain of five is exactly the shape a
# copy/paste edit breaks. The reader of the card cannot tell the difference: each one is a
# fluent sentence about a dirty checkout, and only one of them is about the one in front
# of them. So the sentences are read back out of their own branch and required to be
# distinct, and the blob branch may not hold the hand branch's remedy (or the reverse),
# because a restore provably does not clear a blob — `git checkout HEAD -- <path>` is run
# in `test_a_checkout_cannot_clear_it` above and the path stays modified.

def _branch(text: str, key: str) -> str:
    """The window a key's sentence lives in: from its own test to the next one's."""
    start = text.index(f"dirty_kinds.key == '{key}'")
    rest = text[start + 1:]
    nxt = rest.find("dirty_kinds.key == ")
    return rest[:nxt] if nxt != -1 else rest.split("{% endif %}", 1)[0]


def _sentence(html: str) -> str:
    """The copy inside the branch's one `x-text` pair, both halves, as one string."""
    import re
    found = re.search(r"x-text=\"t\('(.*?)','(.*?)'\)\"", html, re.S)
    return f"{found.group(1)} | {found.group(2)}" if found else ""


class TestNoStateShowsAnotherStateSentence:
    def test_every_branch_carries_exactly_one_sentence(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        for key in sorted(status.DIRTY_KIND_KEYS):
            branch = _branch(text, key)
            count = branch.count('x-text="t(')
            assert count == 1, (
                f"the `{key}` branch carries {count} sentence(s); one state, one sentence")

    def test_no_two_states_share_a_sentence(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        sentences = {key: _sentence(_branch(text, key))
                     for key in sorted(status.DIRTY_KIND_KEYS)}
        assert all(sentences.values()), f"a branch has no readable sentence: {sentences}"
        assert len(set(sentences.values())) == len(sentences), (
            f"two dirty states are described by the same words, so the card tells an "
            f"operator nothing about which one they are holding: {sentences}")

    def test_the_blob_branch_does_not_offer_the_hand_remedy(self):
        """The one wrong remedy the page exists to stop: 'set it aside as a patch'.

        The blob branch may *mention* `git restore` — it is there to say the command
        cannot work — but it may not offer the hand edit's remedy, which is the sentence
        an operator would act on.
        """
        text = TEMPLATE.read_text(encoding="utf-8")
        blob, hand = _sentence(_branch(text, "blob")), _sentence(_branch(text, "hand"))
        assert "verbatim" in blob or "apa adanya" in blob, (
            "the blob branch must name the only heal that works")
        for phrase in ("set it aside as a patch", "menyingkirkannya sebagai patch",
                       "keep it, and carry on"):
            assert phrase not in blob, (
                f"the blob branch offers the hand edit's remedy ({phrase!r}) — a restore "
                "does not clear a blob, and this is the sentence an operator acts on")
        assert blob != hand
