"""The one way in that does not need a console.

A stuck box is one whose runner refuses *before* it fetches — measured on this box:
19 commits behind with `M app/routes/admin_sekolah.py`, three ssh keys and every
stored password refused, and the provider's console behind a captcha. Nothing a
push, a request or a page can do reaches it, because the thing that would apply
them is the thing that is refusing.

So the box installs its own way in, out of the repository it already trusts: the
public half is committed under `deploy/authorized-keys/`, the private half stays on
the operator's machine, and the runner appends what it finds there to root's
`authorized_keys`.

Every property below is a way the feature could look installed and open nothing:

* the append happens at all, into the file sshd actually reads, with the
  permissions sshd insists on (a file left world-readable is a key that exists and
  does not work);
* a second tick changes nothing, because a runner that appends every two minutes
  grows that file forever and turns a one-line inspection into a scroll;
* other keys survive, because the file is appended to and never rewritten;
* a key already present under a different comment is the same key;
* a `.pub` line carrying `authorized_keys` options is refused rather than written,
  because a file sshd parses is not a place for this script to author options
  into — it is a way to turn a copied file into a command;
* a missing directory of keys is a no-op, which is what lets this land on a box
  whose release predates it;
* no key material is printed, so a file that should never have been called `.pub`
  is not echoed into the journal by the install that refuses it.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SH = ROOT / "deploy" / "scangrade-deploy.sh"
KEYS_DIR = ROOT / "deploy" / "authorized-keys"

BASH = shutil.which("bash")

#: sshd reads modes, and a Windows filesystem has none to read. The two assertions
#: that need them are the only ones that care.
POSIX = os.name == "posix"

BLOCK_START = "# deploy-key-logic:start"
BLOCK_END = "# deploy-key-logic:end"

pytestmark = pytest.mark.skipif(BASH is None, reason="needs a bash to run the block")


def _block() -> str:
    """The block, lifted out of the runner between its own delimiters."""
    script = DEPLOY_SH.read_text(encoding="utf-8")
    assert BLOCK_START in script and BLOCK_END in script, (
        "deploy/scangrade-deploy.sh no longer carries the deploy-key block; the "
        "only way into a box whose runner refuses before it fetches is gone")
    return script.split(BLOCK_START, 1)[1].split(BLOCK_END, 1)[0]


def _repo_key() -> str:
    """The public key this repository commits, as one line."""
    files = sorted(KEYS_DIR.glob("*.pub"))
    assert files, (
        f"{KEYS_DIR} carries no .pub file: the runner would install nothing and "
        "every stuck box would be back to needing a console session")
    lines = [ln.strip() for ln in files[0].read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    assert len(lines) == 1, f"{files[0].name} is not one key on one line"
    return lines[0]


def _material(line: str) -> str:
    """A key's identity: its type and its blob, without the comment after them."""
    fields = line.split()
    assert len(fields) >= 2, f"not a key line: {line!r}"
    return f"{fields[0]} {fields[1]}"


def _harness(repo: Path, authorized: Path) -> str:
    """The block with the two things it reads: the checkout, and where to write.

    The path is the block's one knob, and it is exercised here exactly as the
    runner exercises it — through the environment, not through an argument, since
    `test_deploy_script_takes_no_arguments` keeps this script unsteerable.
    """
    return (
        "set -uo pipefail\n"
        f'REPO="{Path(repo).as_posix()}"\n'
        f'SCANGRADE_AUTHORIZED_KEYS="{Path(authorized).as_posix()}"\n'
        'log() { echo "LOG $*"; }\n'
        f"{_block()}\n"
        "echo REACHED_END\n"
    )


def _install(tmp_path, *, pub_lines=None, authorized_lines=None, with_dir=True):
    """Run the block over a throwaway checkout and a throwaway authorized_keys."""
    repo = tmp_path / "repo"
    keys = repo / "deploy" / "authorized-keys"
    if with_dir:
        keys.mkdir(parents=True)
        lines = [_repo_key()] if pub_lines is None else list(pub_lines)
        (keys / "deploy.pub").write_text("\n".join(lines) + "\n", encoding="utf-8")
    authorized = tmp_path / "root-ssh" / "authorized_keys"
    if authorized_lines is not None:
        authorized.parent.mkdir(parents=True, exist_ok=True)
        authorized.write_text("\n".join(authorized_lines) + "\n", encoding="utf-8")
    done = subprocess.run([BASH, "-c", _harness(repo, authorized)],
                          capture_output=True, text=True, check=False)
    return done, authorized


# ── what the repository commits ──────────────────────────────────────────────

def test_the_repository_commits_a_plain_public_key():
    key = _repo_key()
    assert key.startswith("ssh-ed25519 "), (
        "the committed key is not an ssh-ed25519 public key — this file is what "
        "the runner appends to root's authorized_keys")
    assert len(key.split()) == 3, (
        "a public key line is exactly type, blob and comment: anything else here "
        "travels into authorized_keys verbatim")


def test_nothing_private_lives_beside_it():
    """A private half in this directory is a private half in a git history."""
    for path in sorted(KEYS_DIR.iterdir()):
        body = path.read_text(encoding="utf-8", errors="replace")
        assert "PRIVATE KEY" not in body, (
            f"{path.name} carries a private key: the half that opens the door has "
            "to stay on the operator's machine, never in a commit")
        if path.suffix != ".pub" and path.name != "README.md":
            pytest.fail(f"{path.name} is neither a .pub file nor the README — the "
                        "runner globs *.pub, so this file is installed by nothing "
                        "and reviewed by nobody")


# ── what the runner does with it ─────────────────────────────────────────────

def test_it_installs_the_key_where_sshd_reads_it(tmp_path):
    done, authorized = _install(tmp_path)

    assert done.returncode == 0, done
    assert "REACHED_END" in done.stdout, "the block did not reach its end"
    assert authorized.is_file(), (
        "no authorized_keys was written, so the key this repository commits opens "
        "nothing")
    assert [ln for ln in authorized.read_text(encoding="utf-8").splitlines() if ln.strip()] \
        == [_repo_key()]
    assert f"LOG deploy key installed in {Path(authorized).as_posix()}" in done.stdout, (
        "the install is silent: an operator reading the journal cannot tell that "
        "the way in was created")
    if POSIX:
        assert stat.S_IMODE(authorized.stat().st_mode) == 0o600, (
            "authorized_keys is not 0600 — sshd refuses a file anyone else can write")
        assert stat.S_IMODE(authorized.parent.stat().st_mode) == 0o700, (
            "~/.ssh is not 0700 — sshd refuses a directory others can write")


def test_a_second_tick_changes_nothing(tmp_path):
    done_first, authorized = _install(tmp_path)
    assert done_first.returncode == 0, done_first
    first = authorized.read_bytes()

    repo = tmp_path / "repo"
    second = subprocess.run(
        [BASH, "-c", _harness(repo, authorized)],
        capture_output=True, text=True, check=False)

    assert second.returncode == 0, second
    assert authorized.read_bytes() == first, (
        "a second run rewrote the file: an install every two minutes grows it "
        "forever and turns one inspection into a scroll")
    assert "LOG deploy key installed" not in second.stdout, (
        "the second run announced an install it did not make")


def test_other_keys_are_left_exactly_as_they_were(tmp_path):
    others = [
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA admin",
        "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA old",
    ]
    done, authorized = _install(tmp_path, authorized_lines=others)

    assert done.returncode == 0, done
    lines = authorized.read_text(encoding="utf-8").splitlines()
    assert lines[:len(others)] == others, (
        "the file was rewritten rather than appended to: a key somebody else put "
        "there is not this runner's to remove")
    assert _material(lines[-1]) == _material(_repo_key())


def test_the_same_key_under_another_comment_is_the_same_key(tmp_path):
    existing = _material(_repo_key()) + "  another-host"
    done, authorized = _install(tmp_path, authorized_lines=[existing])

    assert done.returncode == 0, done
    assert authorized.read_text(encoding="utf-8").splitlines() == [existing], (
        "the same key was appended again because its comment differed — the "
        "identity of a key is its type and its blob")


def test_a_line_that_is_not_a_plain_key_is_refused(tmp_path):
    hostile = 'command="/bin/false",no-pty ' + _repo_key()
    done, authorized = _install(tmp_path, pub_lines=[hostile])

    assert done.returncode == 0, done
    assert not authorized.exists() or not authorized.read_text(encoding="utf-8").strip(), (
        "a .pub line carrying authorized_keys options was installed: that is how a "
        "copied file becomes a command on every login")
    assert "not a plain public key" in done.stdout, (
        "the refusal is silent, so a typo in this directory looks like a key that "
        "does not work")


def test_it_never_prints_key_material(tmp_path):
    done, _authorized = _install(tmp_path)
    blob = _material(_repo_key()).split()[1]

    assert done.returncode == 0, done
    assert blob not in done.stdout, (
        "the install printed the key's blob — a file that should never have been "
        "named .pub would then be echoed into the journal")


def test_no_directory_of_keys_is_a_no_op(tmp_path):
    done, authorized = _install(tmp_path, with_dir=False)

    assert done.returncode == 0, done
    assert not authorized.exists() and not authorized.parent.exists(), (
        "a checkout with no deploy/authorized-keys created one and installed "
        "nothing: this block has to be a no-op on a box whose release predates it")


# ── the shape of the block and where it sits ─────────────────────────────────

def test_it_appends_and_never_rewrites(tmp_path):
    block = _block()
    assert '>> "$DEPLOY_KEYS_FILE"' in block, "the key is not appended"
    assert not re.search(r'(?<!>)>\s*"\$DEPLOY_KEYS_FILE"', block), (
        "the block truncates authorized_keys somewhere: everything in it before "
        "that write is gone")


def test_it_runs_after_the_root_check_and_before_the_pause_check():
    """Position is the feature. Before the pause check, because a box frozen for
    exam week is exactly a box nobody is watching; after the root check, because
    writing root's key is what it is for."""
    script = DEPLOY_SH.read_text(encoding="utf-8")
    root_check = script.index("must run as root")
    call = script.index("deploy_keys_install\n")
    pause = script.index('if [ -e "$PAUSE_FILE" ]')

    assert root_check < call, "the key install runs before the root check"
    assert call < pause, (
        "the key install sits after the pause check: a frozen box would never "
        "become reachable")
