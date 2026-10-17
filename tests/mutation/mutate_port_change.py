"""Mutation check for `deploy/port_change.py` — the refusals a port depends on.

Sixteen entries, each breaking one clause the tool exists for: the base comparison,
the backup, the exit code a finding reaches, the discovery of a harness, and every
reading of a number a ported sentence carries. Each is demanded to turn the guards
red, because a refusal that still writes is the one defect that looks exactly like a
working tool.

Two of these were survivors on the first run with the guards in the right place, and
both are kept in the list because of what they cost to find: the follower guard's
illustrative number happened to equal the fixture's own count (so reading a sentence
about shapes as a claim still held), and one mutation had become a no-op once a count
must *follow* its subject (the nearer end is then always the answer). The first is
repaired in the guard, the second is retired for one that bites.
"""
from __future__ import annotations

import sys

from tests.mutation.harness import main

GUARDS = ["tests/unit/test_port_change.py"]

MUTATIONS = [

    ('the base comparison folds CRLF',
     'deploy/port_change.py',
     '            if current != base_bytes:',
     '            if current.replace(b"\\r\\n", b"\\n") != base_bytes.replace(b"\\r\\n", b"\\n"):'),
    ('a drifted target is written anyway',
     'deploy/port_change.py',
     '                    first_difference(current, base_bytes))\n                continue',
     '                    first_difference(current, base_bytes))\n                pass'),
    ('the backup copy is empty',
     'deploy/port_change.py',
     '        destination.write_bytes(before)',
     '        destination.write_bytes(b"")'),
    ('a red suite is reported as a pass',
     'deploy/port_change.py',
     '    elif code == 1:\n        state, summary = "fail", last_line(text)',
     '    elif code == 1:\n        state, summary = "pass", last_line(text)'),
    ('a suite that could not run is reported as a pass',
     'deploy/port_change.py',
     '        state, summary = "unmeasured", f"pytest exited {code}: {last_line(text)}"',
     '        state, summary = "pass", f"pytest exited {code}: {last_line(text)}"'),
    ('a survivor is reported as caught',
     'deploy/port_change.py',
     '    elif code == 1:\n        state = "survivor"',
     '    elif code == 1:\n        state = "caught"'),
    ('a refusal does not reach the exit code',
     'deploy/port_change.py',
     '    if any(target.state in ("refused", "failed") for target in targets):\n        return UNMEASURED',
     '    if any(target.state in ("refused", "failed") for target in targets):\n        return PASS'),
    ('restore overwrites a file changed after the port',
     'deploy/port_change.py',
     '        if current != entry.get("after_sha256") and not args.force:',
     '        if False and not args.force:'),
    ('a missing base is inferred',
     'deploy/port_change.py',
     '        if missing:\n            raise Usage("porting needs " + ", ".join(missing) +',
     '        if False:\n            raise Usage("porting needs " + ", ".join(missing) +'),
    ('an already-identical target is rewritten',
     'deploy/port_change.py',
     '        if current == new:',
     '        if False:'),
    ('a claim that does not hold is written anyway',
     'deploy/port_change.py',
     '                    if target.claims["state"] == "fail":',
     '                    if False:'),
    ('the claims are read from the whole file, not the added lines',
     'deploy/port_change.py',
     '            claims = claims_in(added_lines(current, new))',
     '            claims = claims_in(new.decode("utf-8", "replace").splitlines())'),
    ('a claim that could not be measured is written anyway',
     'deploy/port_change.py',
     '                    if target.claims["state"] == "unmeasured":',
     '                    if False:'),
    ('a tool is read as the subject of a test count',
     'deploy/port_change.py',
     '    r"(?<![\\w./-])((?:tests/[\\w./-]+|test_[\\w]+)\\.py)")',
     '    r"(?<![\\w./-])((?:tests|deploy)/[\\w./-]+\\.py|test_[\\w]+\\.py)")'),
    ('a count is read against a path that follows it',
     'deploy/port_change.py',
     '            subject = _nearest(tests, at, follows=True)',
     '            subject = _nearest(tests, at)'),
    ('a count is bound to the first path on the line rather than the nearest',
     'deploy/port_change.py',
     '    best = min(subjects, key=distance)',
     '    best = subjects[0]'),
]

if __name__ == "__main__":
    raise SystemExit(main("mutate_port_change", GUARDS, MUTATIONS))
