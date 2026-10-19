"""Mutation check for the CI workflow — a job that runs nothing still reports green.

The workflow's whole value is that a push executes the suite, and there are three
ways it can stop doing that while every page still answers: the job never starts
(a trigger removed, a path filter added, YAML that does not parse), the job starts
and runs something else (a file list instead of the directory), or the job runs and
is allowed to pass anyway (`continue-on-error`, a swallowed exit code). So the
mutations here break one at a time and ask `tests/unit/test_ci_workflows.py` to go
red — plus the four conditions the suite needs, because a job that skips the
browser gates, the jsdom suites or the merge-runbook history is the same green tick
over tests nobody ran.

A survivor is a hole in the guard, and the baseline is checked first: a harness
whose guards already fail would call every mutation "caught".
"""
from __future__ import annotations

from tests.mutation.harness import main

GUARDS = ["tests/unit/test_ci_workflows.py"]

WORKFLOW = ".github/workflows/unit-tests.yml"

MUTATIONS = [

    ('the suite stops running on pull requests',
     WORKFLOW,
     'on:\n  push:\n  pull_request:\n  workflow_dispatch:\n',
     'on:\n  push:\n  workflow_dispatch:\n'),

    ('a path filter is added, so most commits are never tested',
     WORKFLOW,
     'on:\n  push:\n  pull_request:\n',
     'on:\n  push:\n    paths:\n      - "docs/**"\n  pull_request:\n'),

    ('the suite is named file-by-file instead of as a directory',
     WORKFLOW,
     'run: python -m pytest tests/unit -q -rs --junitxml=unit-suite.xml',
     'run: python -m pytest tests/unit/test_supabase_region.py -q --junitxml=unit-suite.xml'),

    ('the suite is allowed to fail',
     WORKFLOW,
     '        run: python -m pytest tests/unit -q -rs --junitxml=unit-suite.xml',
     '        run: python -m pytest tests/unit -q -rs --junitxml=unit-suite.xml\n'
     '        continue-on-error: true'),

    ('a shallow clone leaves the merge-runbook suite unable to read its refs',
     WORKFLOW,
     '          fetch-depth: 0',
     '          fetch-depth: 1'),

    ('the browser gates are left to skip by name',
     WORKFLOW,
     '        env:\n          SG_CHROME: ${{ steps.browser.outputs.path }}\n',
     ''),

    ('the job builds on an interpreter the project refuses',
     WORKFLOW,
     "          python-version: '3.12'",
     "          python-version: '3.11'"),

    ('the job pins a node the jsdom suites do not support',
     WORKFLOW,
     "          node-version: '24'",
     "          node-version: '20'"),

    ('a committed .env is allowed to change which tests run',
     WORKFLOW,
     '      - name: this checkout has no .env, and that is the tree being measured\n'
     '        run: |\n'
     '          if [ -e .env ]; then\n'
     '            echo "::error::a .env is present in the checkout: the suite behaves differently with one, and a committed .env is a leaked key"\n'
     '            exit 1\n'
     '          fi\n'
     '          echo "no .env here: the suite runs against the committed tree"\n',
     ''),

    ('the pre-commit hook never opens a workflow edit',
     'deploy/git-hooks/pre-commit',
     '|^\\.github/.*\\.ya?ml$',
     ''),

    ('the deploy gate stops running this guard',
     'deploy/theme_gate.sh',
     # Name *and* trailing space: the anchor used to include the closing quote, which
     # stopped matching the moment another suite was appended to the same list — the
     # one shape a mutation harness must not have, since a "not injected" line reads
     # like a clean score to anyone skimming the run.
     'tests/unit/test_ci_workflows.py ',
     ''),

    ('the workflow stops parsing, so the job never starts at all',
     WORKFLOW,
     'permissions:\n  contents: read',
     'permissions:\n\tcontents: read'),
]

if __name__ == "__main__":
    raise SystemExit(main("mutate_ci_workflows", GUARDS, MUTATIONS))
