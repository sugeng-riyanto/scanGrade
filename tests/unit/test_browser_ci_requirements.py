"""The main suite must execute browser guards, with separate pytest commands."""
import os
from pathlib import Path
import subprocess

from tests.unit.test_browser_gate import _shell_blocks

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/landing-quality.yml'


def browser_step():
    return next(block for block in _shell_blocks(WORKFLOW.read_text())
                if 'SG_CHROME=' in block)


def test_full_suite_commands_are_separate_shell_lines():
    text = WORKFLOW.read_text()
    body = text.split('- name: Full test suite\n', 1)[1].split('- name:', 1)[0]
    assert body.lstrip().startswith('run: |\n')
    commands = [line.strip() for line in body.splitlines() if 'python -m pytest' in line]
    assert len(commands) == 2
    assert commands[0].endswith('--ignore=tests/test_rls_security.py')
    assert commands[1].endswith("-k 'not TestSupabaseRLSPolicies'")


def test_main_suite_exports_the_browser_it_found(tmp_path):
    binary = tmp_path / 'google-chrome'
    binary.write_text('#!/bin/sh\nexit 0\n')
    binary.chmod(0o755)
    export = tmp_path / 'github-env'
    result = subprocess.run(['/bin/bash', '-c', browser_step()],
                            env={**os.environ, 'PATH': str(tmp_path), 'GITHUB_ENV': str(export)},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert export.read_text().strip() == f'SG_CHROME={binary}'


def test_missing_browser_fails_instead_of_skipping(tmp_path):
    export = tmp_path / 'github-env'
    result = subprocess.run(['/bin/bash', '-c', browser_step()],
                            env={**os.environ, 'PATH': str(tmp_path), 'GITHUB_ENV': str(export)},
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert '::error::no Chrome/Chromium' in result.stdout
    assert not export.exists()


def test_claims_wiring_uses_a_disposable_offline_roster(tmp_path):
    script = next(block for block in _shell_blocks(WORKFLOW.read_text())
                  if 'python deploy/claims_gate.py' in block)
    # A wiring test must never become a load test by dropping this flag.
    assert 'python deploy/claims_gate.py --roster "$roster" --check' in script
    result = subprocess.run(['/bin/bash', '-e', '-c', script], cwd=ROOT,
                            env={**os.environ, 'TMPDIR': str(tmp_path)},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'CHECK OK' in result.stdout
    assert list(tmp_path.iterdir()) == [], 'the synthetic roster must be cleaned up'
