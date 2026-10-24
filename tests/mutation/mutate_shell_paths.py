"""Mutation check for `tests/unit/test_shell_path_portability.py` — does the guard bite?

Nineteen entries, each one a way the rule could stop reading what it exists to read:
the executable of a sequence, the words that stand before one, every command in a
command string, the payload after `-c`, the same two positions written by keyword
(`args=`, `executable=`), the two path shapes (Unix and drive), the `which` half, a
method imported by name, the tree walk itself, the literal-prefix cut, and the gate's
own `TESTS=` line — and for the shebang half: a script's interpreter line, the `env`
form, the POSIX `/bin/sh` allowance, the CRLF strip, the walk over tracked files, and
`git ls-files` itself.

The last of those is deliberate: the guard's wiring is half of it, and a suite the hook
does not run is a suite that only CI runs — so removing the name from the gate has to
turn the suite red, or the "wired" claim is a comment.
"""
from __future__ import annotations

import sys

from tests.mutation.harness import main

GUARDS = ["tests/unit/test_shell_path_portability.py"]

MUTATIONS = [

    ('the executable of a sequence is never read',
     'tests/unit/test_shell_path_portability.py',
     '            out.append(element)\n            break',
     '            break'),

    ('the words before an executable stop being skipped',
     'tests/unit/test_shell_path_portability.py',
     '    return token in WRAPPERS or bool(ASSIGNMENT.match(token)) or token.startswith("-")',
     '    return bool(ASSIGNMENT.match(token)) or token.startswith("-")'),

    ('only the first command of a string is read',
     'tests/unit/test_shell_path_portability.py',
     '    for fragment in SEPARATORS.split(text):',
     '    for fragment in SEPARATORS.split(text)[:1]:'),

    ('the payload after a shell flag is not read',
     'tests/unit/test_shell_path_portability.py',
     '        if index and isinstance(element, ast.Constant) and element.value in SHELL_FLAGS:',
     '        if False and element.value in SHELL_FLAGS:'),

    ('an absolute Unix path stops being a defect',
     'tests/unit/test_shell_path_portability.py',
     '    if token.startswith("/"):',
     '    if token.startswith("") and False:'),

    ('a drive path stops being a defect',
     'tests/unit/test_shell_path_portability.py',
     '    if DRIVE.match(token):',
     '    if False and DRIVE.match(token):'),

    ('the lookup helper stops being recognised',
     'tests/unit/test_shell_path_portability.py',
     '    if (module, method) == WHICH:\n        return "which"',
     '    if False:\n        return "which"'),

    ('a method imported by name stops being recognised',
     'tests/unit/test_shell_path_portability.py',
     '        elif isinstance(func, ast.Name) and func.id in bare:',
     '        elif False and func.id in bare:'),

    ('the tree walk stops reading files',
     'tests/unit/test_shell_path_portability.py',
     '        out.extend(_findings_in(path, path.read_text(encoding="utf-8", errors="replace")))',
     '        out.extend([])'),

    ('a runtime tail is joined as if it were literal',
     'tests/unit/test_shell_path_portability.py',
     '        if piece is None:\n            break',
     '        if piece is None:\n            out.append("")\n            continue'),

    ('the sequence handed over as `args=` is not read',
     'tests/unit/test_shell_path_portability.py',
     '        for keyword in node.keywords:\n            if keyword.arg in ("args", "command", "cmd"):',
     '        for keyword in node.keywords:\n            if False and keyword.arg in ("args", "command", "cmd"):'),

    ('the binary chosen by `executable=` is not read',
     'tests/unit/test_shell_path_portability.py',
     '        if keyword.arg == "executable":',
     '        if False and keyword.arg == "executable":'),

    ('a script shebang stops being judged',
     'tests/unit/test_shell_path_portability.py',
     '        if line and shebang_defect(line):',
     '        if False and shebang_defect(line):'),

    ('the `env` form stops being portable',
     'tests/unit/test_shell_path_portability.py',
     '    if body.startswith("/usr/bin/env"):',
     '    if False and body.startswith("/usr/bin/env"):'),

    ('the POSIX shebang stops being allowed',
     'tests/unit/test_shell_path_portability.py',
     '    if body == "/bin/sh" or body.startswith("/bin/sh "):',
     '    if False:'),

    ('a CRLF shebang is compared without its carriage return stripped',
     'tests/unit/test_shell_path_portability.py',
     '    first = first.rstrip("\\r")',
     '    first = first'),

    ('the walk stops opening the tracked files',
     'tests/unit/test_shell_path_portability.py',
     '        path = root / name\n        if not path.is_file():',
     '        path = root / name\n        if True:'),

    ('the tracked-file list comes back empty',
     'tests/unit/test_shell_path_portability.py',
     '    return [name for name in done.stdout.split("\\0") if name]',
     '    return []'),

    ('the tools stop being read at all',
     'tests/unit/test_shell_path_portability.py',
     'SCAN_ROOTS = ("tests", "deploy")',
     'SCAN_ROOTS = ("tests",)'),

    # The anchor is the name and the space before it, not the closing quote: the
    # guard is no longer last in `TESTS=`, and an anchor that assumes a position
    # stops matching the moment a suite is appended after it.
    ('the gate stops running the guard',
     'deploy/theme_gate.sh',
     ' tests/unit/test_shell_path_portability.py ',
     ' '),
]

if __name__ == "__main__":
    raise SystemExit(main("mutate_shell_paths", GUARDS, MUTATIONS))
