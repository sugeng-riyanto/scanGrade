"""The main suite must execute browser guards, with separate pytest commands.

The three tests at the bottom run the workflow's *own* shell — lifted out of the
YAML — rather than a restatement of it, because a restatement is a second copy of
the step and passes after the step has been edited. That needs a real bash, so it
is located the way the rest of this suite locates one (`shutil.which`) and these
tests skip *by name* where there is none. CI is Linux, where bash is always
present, so the skip cannot hide a release: it only stops a developer box without
a shell from reporting red about something it cannot measure.

One assertion had to stop being a string equality. `command -v` answers in the
shell's own spelling of a path, and Git for Windows spells `%TEMP%` as `/tmp/...`
where Python spells it `C:\\...` — so comparing the exported value to
`str(binary)` was a test about the platform, not about the step, and it was the
reason these tests could not run on Windows at all. The exported value is now
compared by *identity*, through the same shell (`test -ef`), which is the claim
that was always meant: the step exported the browser it found. On Linux the two
spellings are already identical, so nothing about what is demanded there changed.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.unit.test_browser_gate import _shell_blocks

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/landing-quality.yml'

BASH = shutil.which('bash')

needs_bash = pytest.mark.skipif(
    BASH is None, reason='needs a bash to run the workflow step it is testing')


def browser_step():
    return next(block for block in _shell_blocks(WORKFLOW.read_text())
                if 'SG_CHROME=' in block)


def names_the_same_file(exported: str, expected: Path) -> bool:
    """Does the path the shell printed name the file Python made?

    Asked of the shell, because the two spellings are its dialect: it resolves
    `/tmp/...` and `C:\\...` to one file, where comparing the strings would fail
    on a step that behaved exactly as intended. A missing file answers False, so
    this cannot pass by both sides being unreadable.
    """
    probe = subprocess.run([BASH, '-c', 'test "$1" -ef "$2"', 'bash', exported,
                            str(expected)], capture_output=True, text=True)
    return probe.returncode == 0


def test_full_suite_commands_are_separate_shell_lines():
    text = WORKFLOW.read_text()
    body = text.split('- name: Full test suite\n', 1)[1].split('- name:', 1)[0]
    assert body.lstrip().startswith('run: |\n')
    commands = [line.strip() for line in body.splitlines() if 'python -m pytest' in line]
    assert len(commands) == 2
    assert commands[0].endswith('--ignore=tests/test_rls_security.py')
    assert commands[1].endswith("-k 'not TestSupabaseRLSPolicies'")


@needs_bash
def test_main_suite_exports_the_browser_it_found(tmp_path):
    binary = tmp_path / 'google-chrome'
    binary.write_text('#!/bin/sh\nexit 0\n')
    binary.chmod(0o755)
    export = tmp_path / 'github-env'
    result = subprocess.run([BASH, '-c', browser_step()],
                            env={**os.environ, 'PATH': str(tmp_path), 'GITHUB_ENV': str(export)},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    name, _, exported = export.read_text(encoding='utf-8').strip().partition('=')
    assert name == 'SG_CHROME', f'the step exported {name!r} rather than SG_CHROME'
    assert Path(exported).name == binary.name, (
        f'the step exported {exported!r}, which is not the first candidate it should '
        f'have found ({binary.name})')
    assert names_the_same_file(exported, binary), (
        f'the step exported {exported!r}, which does not name the browser it found '
        f'({binary})')


@needs_bash
def test_missing_browser_fails_instead_of_skipping(tmp_path):
    export = tmp_path / 'github-env'
    result = subprocess.run([BASH, '-c', browser_step()],
                            env={**os.environ, 'PATH': str(tmp_path), 'GITHUB_ENV': str(export)},
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert '::error::no Chrome/Chromium' in result.stdout
    assert not export.exists()


@needs_bash
def test_claims_wiring_uses_a_disposable_offline_roster(tmp_path):
    script = next(block for block in _shell_blocks(WORKFLOW.read_text())
                  if 'python deploy/claims_gate.py' in block)
    # A wiring test must never become a load test by dropping this flag.
    assert 'python deploy/claims_gate.py --roster "$roster" --check' in script
    result = subprocess.run([BASH, '-e', '-c', script], cwd=ROOT,
                            env={**os.environ, 'TMPDIR': str(tmp_path)},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'CHECK OK' in result.stdout
    assert list(tmp_path.iterdir()) == [], 'the synthetic roster must be cleaned up'
