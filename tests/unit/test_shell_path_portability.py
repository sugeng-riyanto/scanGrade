"""A test that reaches the shell through `/bin/bash` runs on the box and not here.

The suite runs in two places, and they are not the same operating system: this
checkout on Windows (Git Bash), and the Linux box the deploy runs on. A shell is
found the same way on both — `BASH = shutil.which("bash")` with a skip when it is
`None`, which is what every suite that runs a script already does — and that is the
one convention that keeps a deploy script's own tests runnable in both. A copied
snippet that says `/bin/bash` passes on the box and dies here, or worse, is skipped
here and never runs at all: the failure is invisible in exactly the place it happens.

So the rule is mechanical, and it is about *paths*, not about shells: code may run
whatever it likes by **name** (`shutil.which`, `sys.executable`, `"git"`, `"node"`),
and may not name an executable by a path that exists on one platform only.

It is checked in two places, because a checkout names a binary in two places:

1. **The Python the suite and its tools run** — `tests/` and `deploy/`, read with
   `ast` at the positions an executable is run from. Both trees are read from a
   checkout on either machine; `app/` is not, because the app runs on the box, where
   an absolute path is a fact rather than a portability bug.
2. **Every tracked script's `#!` line** — a script states where its interpreter is
   before it runs a single command, and `#!/bin/bash` is the same defect one line
   earlier in the read. The repository is asked (`git ls-files`), not the disk, so a
   scratch script here is not a claim about what a clone has.

What is refused
---------------
Every call that reaches a process, at the positions an executable is run from:

* **the executable of a sequence** — `subprocess.run(["/bin/bash", script])`,
  including a path built out of literals (`["/bin/" + "bash", script]`);
* **the command of a command string** — `os.system("/bin/sh -c …")`,
  `subprocess.run("/bin/bash " + script, shell=True)`, and every command after a
  separator inside one (`"echo hi; /usr/bin/env bash x"`);
* **the payload after a shell flag** — `["bash", "-c", "/usr/bin/env python3 x"]`,
  because that string is a command like any other;
* **the keyword spellings of the same two positions** — `run(args=["/bin/bash", x])`
  and `run(["x.sh"], executable="/bin/bash")`, which are the same call with eight
  more characters, and the second is the one shape where argv names the script
  instead of the binary;
* **a path handed to `shutil.which`** — `which("/bin/bash")` defeats the function it
  calls: the argument of `which` is a *name* to look up;
* a **Windows drive path** in any of those positions (`C:\\Program Files\\…`), for
  the same reason the other way round. An environment variable is the convention
  there (`SG_CHROME`), not a hard-coded path.

And every tracked script's shebang, unless it is `#!/usr/bin/env <name>` — which
looks the interpreter up on `PATH` — or `/bin/sh`, the one absolute path POSIX
requires to be there. `#! /bin/bash` (with a space) and `#!/usr/bin/env` with no
interpreter after it are refused too.

What is not refused, and why
---------------------------
* **A `#!` line inside a fixture.** The deploy's own scripts are rendered into a
  fixture with `write_text("#!/usr/bin/env bash\\n…")`, and writing a file is not a
  process call: for the Python half the guard reads what is *executed*, and never
  the content of a file. A shebang inside a payload is the shell's own comment, and
  its first token starts with `#`, so nothing reads it as a path either. (The
  shebang half reads real tracked scripts only — a fixture written into `tmp_path`
  is not one.)
* **A bare name** — `["bash", …]`, `["git", …]`, `[sys.executable, …]`. A name is
  resolved by the platform; only a path claims to know where a binary lives.
* **An argument that is a path** — `[BASH, script, "/tmp/out.json"]`. The rule reads
  the executable position and the payload after `-c`, not every string in the call.
* **Anything outside a process call** — a docstring, a comment, or an assertion
  about a rendered shebang says `/bin/bash` without running one, and a guard that
  reads prose refuses its own explanation.

Limits, stated rather than implied
----------------------------------
* Python is read under `tests/` and `deploy/` only. Nothing else is parsed: a shell
  script's *body* may call whatever it likes, because a shell script is the box's
  and the `#!` line is the only part a second machine reads.
* Only **literal** strings are judged. A command assembled at runtime (`cmd =
  "/bin/" + tool; subprocess.run(cmd)`) and a call reached through a hand-rolled
  alias (`spawn = subprocess.run`) are not read — the guard refuses the literal, and
  the review that reads the diff is what catches arithmetic.
* The call set is `subprocess.run/call/check_call/check_output/Popen/getoutput/
  getstatusoutput`, `os.system/popen` and `asyncio.create_subprocess_exec/shell`, by
  module alias so `import subprocess as sp` is read too. `os.spawn*`, a `-c` payload
  built by a helper, and a path produced by `pathlib` are outside it.

It is named in `deploy/theme_gate.sh`'s own `TESTS=`, so the pre-commit hook and the
auto-deploy run it — and the gate refuses a release that *deletes* it rather than
warning about one, which is the difference between a guard and a habit.
"""
from __future__ import annotations

import ast
import pathlib
import re
from typing import NamedTuple

import pytest

from tests.unit.git_env import git

ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
GATE = ROOT / "deploy" / "theme_gate.sh"

#: What the *process-call* rule reads: the suite itself and the tools the suite
#: runs. Both are read from a checkout on either machine, which is the whole claim.
#: `app/` is deliberately not here: it runs on the box, where an absolute path is a
#: fact rather than a portability bug — `deploy_status_service.py`'s
#: `/usr/local/bin/scangrade-deploy` is the launcher the box installs.
SCAN_ROOTS = ("tests", "deploy")

# By module, because a method name alone is not enough to know what is being run.
MODULE_CALLS = {
    "subprocess": frozenset({"run", "call", "check_call", "check_output", "Popen",
                             "getoutput", "getstatusoutput"}),
    "os": frozenset({"system", "popen"}),
    "asyncio": frozenset({"create_subprocess_exec", "create_subprocess_shell"}),
}
WHICH = ("shutil", "which")

# One command per fragment: `a && b`, `a; b`, `a | b`, and one per line.
SEPARATORS = re.compile(r"&&|\|\||;|\n|\|")
# Words that stand *before* the executable: skipping them lands on the real one, so
# `exec /bin/bash x` and `env FOO=1 /bin/bash x` are refused like the bare path is.
WRAPPERS = frozenset({"sudo", "doas", "exec", "command", "time", "nohup", "env",
                      "builtin", "eval", "xargs", "then", "do", "else"})
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
# The flags whose next argument is a command, in the shells and in cmd.exe.
SHELL_FLAGS = ("-c", "-lc", "-ic", "-cl", "/c")

# Floors, not counts: a guard that stopped recognising process calls would otherwise
# report "no findings" over a tree it never read. Measured on this checkout — 440
# files, 303 calls, 239 executables read as a literal over `tests/` and `deploy/` —
# the floors sit below.
MIN_FILES, MIN_CALLS, MIN_EXECUTABLES = 380, 240, 180

#: The shebang half. A script finds its interpreter the same way the Python half
#: finds a binary: by **name**, through `env`. `/bin/sh` is the one absolute path
#: that is portable, because POSIX requires it to be there.
def shebang_defect(line: str) -> str | None:
    """Why this `#!` line is a portability defect, or `None` when it is portable."""
    body = line[2:].strip()
    if body == "/bin/sh" or body.startswith("/bin/sh "):
        return None
    if body.startswith("/usr/bin/env"):
        tokens = [token for token in body[len("/usr/bin/env"):].split()
                  if not token.startswith("-")]
        if tokens:
            return None
    head = body.split()[0]
    return (f"it names its interpreter by an absolute path (`{head}`), which exists "
            f"on one platform only — write `#!/usr/bin/env "
            f"{head.rsplit('/', 1)[-1] or '<name>'}` instead: `env` looks the "
            "interpreter up on `PATH` on both machines, and `#!/bin/sh` is the one "
            "path POSIX guarantees")


SHEBANG_MINIMUM = 40


class Finding(NamedTuple):
    path: str
    line: int
    call: str
    token: str
    why: str


# ── reading a string out of the source ───────────────────────────────────────

def _parts(node: ast.AST) -> list[str | None]:
    """A string expression as ordered pieces, `None` where the value is unknown.

    `f"/usr/bin/{tool}"` is two pieces, and keeping the unknown one as a break is
    what stops `"/bin/" + x + "bash"` from being read as the path `/bin/bash`: the
    guard refuses what the source *says*, not what it might evaluate to.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [value.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
                else None for value in node.values]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _parts(node.left) + _parts(node.right)
    return [None]


def _literal(node: ast.AST) -> str | None:
    """The whole value, or `None` when any piece of it is unknown."""
    pieces = _parts(node)
    return None if any(piece is None for piece in pieces) else "".join(
        piece or "" for piece in pieces)


def _prefix(node: ast.AST) -> str:
    """The literal pieces before the first unknown one.

    A command string whose tail is runtime data can still be judged by its head:
    `"/bin/bash " + script` names the executable, and that is the whole claim.
    """
    out = []
    for piece in _parts(node):
        if piece is None:
            break
        out.append(piece)
    return "".join(out)


def _stands_before_an_executable(token: str) -> bool:
    """A word that comes *before* the executable rather than being one.

    `sudo`, `env`, `time`, a `VAR=value` assignment, and a `-flag`: all of them sit
    in front of the binary, so reading the first token without skipping them would
    guess the wrong word — and a guard that guesses is one that cries wolf.
    """
    return token in WRAPPERS or bool(ASSIGNMENT.match(token)) or token.startswith("-")


def command_words(text: str) -> list[str]:
    """The executable of every command in a shell string, in order.

    Fragments are split on the separators a shell honours, and the executable of
    each is the first token that is not a word standing before it. An argument is
    never read: `echo /bin/bash` runs `echo`.

    The limit this has, and it is stated rather than hidden: an option's *value* is
    not a wrapper word either, so `sudo -u root /bin/bash x` stops at `root` and the
    path behind it is not read. The plain shapes — `sudo /bin/bash`, `env FOO=1
    /bin/sh -c …` — are read, and those are the ones a copied snippet takes.
    """
    words = []
    for fragment in SEPARATORS.split(text):
        for token in fragment.strip().lstrip("{(").split():
            if _stands_before_an_executable(token):
                continue
            words.append(token)
            break
    return words


# ── the rules ────────────────────────────────────────────────────────────────

def _path_defect(token: str) -> str | None:
    """Why this token is a portability defect, or `None` when it is a bare name.

    Both platforms are refused for one reason: a path claims to know where a binary
    lives, and each of these claims is true on one operating system.
    """
    if token.startswith("/"):
        return ("it names an executable by an absolute Unix path, which exists on "
                "the box and not on the Windows checkout the suite also runs on")
    if DRIVE.match(token):
        return ("it names an executable by a Windows drive path, which exists on "
                "this checkout and not on the box")
    return None


def _aliases(tree: ast.AST) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """`import subprocess as sp` and `from os import system`, as names to match.

    A call is recognised through the alias because the alternative — matching the
    method name alone — reads an unrelated `run(...)` as a process call.
    """
    modules: dict[str, str] = {}
    bare: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in MODULE_CALLS or alias.name == WHICH[0]:
                    modules[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module in MODULE_CALLS:
                for alias in node.names:
                    bare[alias.asname or alias.name] = (node.module, alias.name)
            elif node.module == WHICH[0]:
                for alias in node.names:
                    if alias.name == WHICH[1]:
                        bare[alias.asname or alias.name] = WHICH
    return modules, bare


def _kind(module: str, method: str) -> str | None:
    if (module, method) == WHICH:
        return "which"
    return "process" if method in MODULE_CALLS.get(module, ()) else None


def _calls(tree: ast.AST):
    """Every measuring call in a module, as `(node, kind, module, method)`."""
    modules, bare = _aliases(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        module = method = None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            module, method = modules.get(func.value.id), func.attr
        elif isinstance(func, ast.Name) and func.id in bare:
            module, method = bare[func.id]
        kind = _kind(module, method) if module else None
        if kind:
            yield node, kind, module, method


def _executables_and_payloads(node: ast.Call) -> list[ast.AST]:
    """The string expressions a process is actually started from.

    Not every argument: `[BASH, script, "/tmp/out.json"]` has one path in it, and it
    is an argument. What is read is the executable position and the payload after a
    `-c`-style flag.

    In a sequence the executable is the first string element that is not a wrapper
    or a flag — `["sudo", "/bin/bash", script]` is the same defect as `["/bin/bash",
    script]` — and the reading stops there, because everything after it is an
    argument. A head that is a name (`[BASH, …]`, `[sys.executable, …]`) is not a
    literal, so nothing is claimed about it.
    """
    argv = node.args[0] if node.args else None
    if argv is None:
        # `subprocess.run(args=[…])` is the same call written by keyword, and a
        # guard that reads only the positional form lets the next snippet through
        # by adding eight characters.
        for keyword in node.keywords:
            if keyword.arg in ("args", "command", "cmd"):
                argv = keyword.value
                break
    out: list[ast.AST] = []
    if argv is not None and not isinstance(argv, (ast.List, ast.Tuple)):
        out.append(argv)
    if isinstance(argv, (ast.List, ast.Tuple)):
        for element in argv.elts:
            head = _literal(element)
            if head is None:
                if out:
                    break
                continue
            if _stands_before_an_executable(head.strip().split()[0] if head.strip() else ""):
                continue
            out.append(element)
            break
        elements = argv.elts
        for index, element in enumerate(elements):
            if index and isinstance(element, ast.Constant) and element.value in SHELL_FLAGS:
                if index + 1 < len(elements):
                    out.append(elements[index + 1])
    for keyword in node.keywords:
        # `run(["script"], executable="/bin/bash")` runs *that* binary; the argv
        # head is the script's name, so the path is the only place it appears.
        if keyword.arg == "executable":
            out.append(keyword.value)
    return out


def _findings_in(path: pathlib.Path, source: str) -> list[Finding]:
    """Every finding in one module's source, in source order and without repeats."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found: list[Finding] = []
    for node, kind, module, method in _calls(tree):
        call = f"{module}.{method}"
        if kind == "which":
            if not node.args:
                continue
            name = _literal(node.args[0])
            if name is None:
                name = _prefix(node.args[0])
            defect = _path_defect(name) if name else None
            if defect:
                found.append(Finding(_shown(path), node.lineno, call, name, defect))
            continue
        for expression in _executables_and_payloads(node):
            text = _literal(expression)
            if text is None:
                # A head whose tail is runtime data names one command at most.
                words = command_words(_prefix(expression))[:1]
            else:
                words = command_words(text)
            for word in words:
                defect = _path_defect(word)
                if defect:
                    found.append(Finding(_shown(path), node.lineno, call, word, defect))
    seen, unique = set(), []
    for finding in found:
        key = (finding.path, finding.line, finding.call, finding.token)
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique


def _places(root: pathlib.Path | None) -> list[pathlib.Path]:
    """The `*.py` files to read — one tree for a fixture, the scan roots otherwise."""
    trees = [root] if root is not None else [ROOT / name for name in SCAN_ROOTS]
    return [path for tree in trees
            for path in sorted(tree.rglob("*.py")) if "__pycache__" not in str(path)]


def _shown(path: pathlib.Path) -> str:
    """A checkout-relative name where there is one, so a refusal reads like one.

    Forward slashes on purpose: this runs on Windows too, and a refusal that prints
    `tests\\unit\\x.py` is a path pasted back into a `git` command that git will not
    resolve — the same text has to be a filename on both machines.
    """
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def findings(root: pathlib.Path | None = None) -> list[Finding]:
    """Every process-call finding under `root` (the scan roots, by default)."""
    out: list[Finding] = []
    for path in _places(root):
        out.extend(_findings_in(path, path.read_text(encoding="utf-8", errors="replace")))
    return out


def summary(root: pathlib.Path | None = None) -> dict:
    """What the guard read, so that reading nothing cannot pass as reading clean."""
    files = calls = executables = 0
    for path in _places(root):
        files += 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node, _kind_name, _module, _method in _calls(tree):
            calls += 1
            executables += len(_executables_and_payloads(node))
    return {"files": files, "calls": calls, "executables": executables}


def tracked_files(root: pathlib.Path | None = None) -> list[str]:
    """Every file the repository tracks, as git names it.

    The **repository** rather than the disk, for the reason `deploy/doc_links.py`
    gives: a scratch script in the working tree is not something a clone has, and a
    rule that read the disk would report the same finding here and differently on
    the box. `git_env.git` is used so the call answers about this checkout even when
    a hook's own `GIT_DIR` is in the environment.

    `root` is for the tests of this rule: a scratch repository that really carries
    `#!/bin/bash` is the only way to observe the walk *use* the refusal.
    """
    done = git("ls-files", "-z", cwd=str(root or ROOT))
    assert done.returncode == 0, (
        "the shebang rule reads the tracked scripts through git, and git would not "
        "list them: " + (done.stderr or "").strip())
    return [name for name in done.stdout.split("\0") if name]


def shebang_of(path: pathlib.Path) -> str | None:
    """A file's `#!` line, or `None` when it does not open with one."""
    first = path.read_bytes()[:200].split(b"\n", 1)[0].decode("utf-8", "replace")
    first = first.rstrip("\r")
    return first if first.startswith("#!") else None


def shebang_findings(root: pathlib.Path | None = None) -> list[tuple[str, str]]:
    """`(path, shebang)` for every tracked script that names its interpreter by path."""
    root = root or ROOT
    out: list[tuple[str, str]] = []
    for name in tracked_files(root):
        path = root / name
        if not path.is_file():
            continue
        line = shebang_of(path)
        if line and shebang_defect(line):
            out.append((name, line))
    return out


def shebang_count(root: pathlib.Path | None = None) -> int:
    """How many tracked files open with a `#!` line, so zero findings means read."""
    root = root or ROOT
    return sum(1 for name in tracked_files(root)
               if (root / name).is_file() and shebang_of(root / name))


def gate_tests() -> list[str]:
    """The files `deploy/theme_gate.sh` runs, from its own `TESTS=` line."""
    for line in GATE.read_text(encoding="utf-8").splitlines():
        if line.startswith("TESTS="):
            return line.split("=", 1)[1].strip().strip('"').split()
    raise AssertionError("deploy/theme_gate.sh no longer lists the checks it runs")


def scratch(tmp_path: pathlib.Path, source: str) -> list[Finding]:
    """The findings for one synthetic test file, in a tree of the guard's own."""
    root = tmp_path / "tests"
    root.mkdir(parents=True, exist_ok=True)
    (root / "test_sample.py").write_text(source, encoding="utf-8")
    return findings(root)


# ── the shapes that are refused ──────────────────────────────────────────────

REFUSED = [
    ("a sequence whose first element is the interpreter",
     'import subprocess\nsubprocess.run(["/bin/bash", "x.sh"], check=True)\n'),
    ("a command string whose command is the interpreter",
     'import subprocess\nsubprocess.run("/bin/sh -c \'echo hi\'", shell=True)\n'),
    ("`env` reached by its own path",
     'import subprocess\nsubprocess.run(["/usr/bin/env", "bash", "x.sh"])\n'),
    ("`os.system`, which is a command string by definition",
     'import os\nos.system("/bin/bash x.sh")\n'),
    ("a path assembled from two literals",
     'import subprocess\nsubprocess.run(["/bin/" + "bash", "x.sh"])\n'),
    ("the interpreter behind a wrapper word",
     'import subprocess\nsubprocess.run(["sudo", "/bin/bash", "x.sh"])\n'),
    ("the interpreter behind a wrapper and its flag",
     'import subprocess\nsubprocess.run(["env", "-i", "/usr/bin/bash", "x.sh"])\n'),
    ("a literal head whose tail is runtime data",
     'import subprocess, sys\nscript = sys.argv[0]\n'
     'subprocess.run("/bin/bash " + script, check=True)\n'),
    ("a second command after a separator inside a payload",
     'import subprocess\n'
     'subprocess.run(["bash", "-c", "echo hi; /usr/bin/env bash y.sh"])\n'),
    ("a payload after `-c`, where the executable is a path",
     'import subprocess\nsubprocess.run(["bash", "-c", "/usr/bin/python3 tool.py"])\n'),
    ("a module imported as an alias",
     'import subprocess as sp\nsp.Popen(["/usr/bin/bash", "x.sh"])\n'),
    ("a method imported by name",
     'from subprocess import run\nrun(["/bin/dash", "x.sh"])\n'),
    ("`which`, handed the path it exists to look up",
     'import shutil\nBASH = shutil.which("/bin/bash") or shutil.which("bash")\n'),
    ("`which`, imported by name and handed the same path",
     'from shutil import which\nBASH = which("/usr/bin/bash")\n'),
    ("a Windows drive path, the other way round",
     'import subprocess\n'
     'subprocess.run(["C:\\\\Program Files\\\\Git\\\\bin\\\\bash.exe", "x.sh"])\n'),
    ("the same sequence, handed over as `args=`",
     'import subprocess\nsubprocess.run(args=["/bin/bash", "x.sh"])\n'),
    ("a binary chosen by `executable=`, where argv names only the script",
     'import subprocess\nsubprocess.run(["x.sh"], executable="/bin/bash")\n'),
]

ALLOWED = [
    ("the name, resolved by the platform",
     'import shutil, subprocess\nBASH = shutil.which("bash")\n'
     'if BASH:\n    subprocess.run([BASH, "x.sh"], check=True)\n'),
    ("the interpreter of the running process",
     'import subprocess, sys\nsubprocess.run([sys.executable, "tool.py"], check=True)\n'),
    ("a shebang written into a fixture, which is not a call",
     'from pathlib import Path\n'
     'Path("fake.sh").write_text("#!/usr/bin/env bash\\necho hi\\n", encoding="utf-8")\n'),
    ("a shebang inside a payload, which the shell reads as a comment",
     'import shutil, subprocess\nBASH = shutil.which("bash")\n'
     'subprocess.run([BASH, "-c", "#!/bin/sh\\ncd /tmp && echo hi"])\n'),
    ("an absolute path as an argument rather than the executable",
     'import shutil, subprocess\nBASH = shutil.which("bash")\n'
     'subprocess.run([BASH, "x.sh", "/tmp/out.json"], check=True)\n'),
    ("a bare name with arguments that are paths",
     'import subprocess\nsubprocess.run(["git", "-C", "/tmp/repo", "status"])\n'),
    ("a path mentioned where nothing runs — an assertion about content",
     'rendered = "#!/usr/bin/env bash\\n"\n'
     'assert rendered.startswith("#!/usr/bin/env bash")\n'
     'EXAMPLE = "/bin/bash is what this file must not call"\n'),
    ("a method that is not a process call",
     'import subprocess\nsubprocess.list2cmdline(["/bin/bash"])\n'),
    ("a wrapper whose executable is a name",
     'import shutil, subprocess\nBASH = shutil.which("bash")\n'
     'subprocess.run(["exec " + BASH + " x.sh"], shell=True)\n'),
    ("the same sequence by keyword, with a bare name",
     'import subprocess\nsubprocess.run(args=["bash", "x.sh"], check=True)\n'),
    ("an `executable=` that is a name",
     'import subprocess\nsubprocess.run(["x.sh"], executable="bash")\n'),
]


class TestTheRuleRefusesTheShellByPath:
    def test_the_tree_is_not_refusing_everything(self, tmp_path):
        """The control for the whole class: a file with nothing in it is clean."""
        assert scratch(tmp_path, "import subprocess\n") == []

    @pytest.mark.parametrize("label,source", REFUSED, ids=[row[0] for row in REFUSED])
    def test_it_is_refused(self, tmp_path, label, source):
        assert scratch(tmp_path, source), f"{label}: the guard read this as portable"

    def test_it_names_the_token_the_call_and_the_line(self, tmp_path):
        """A refusal a reader cannot act on is a red line with no fix in it."""
        found = scratch(tmp_path, 'import os\nos.system("/bin/bash x.sh")\n')
        assert len(found) == 1
        assert found[0].token == "/bin/bash"
        assert found[0].call == "os.system"
        assert found[0].line == 2
        assert found[0].path.endswith("test_sample.py")
        assert "absolute Unix path" in found[0].why

    def test_both_platforms_are_named_for_what_they_are(self, tmp_path):
        unix = scratch(tmp_path, 'import os\nos.system("/bin/sh x")\n')[0]
        drive = scratch(
            tmp_path,
            'import os\nos.system("C:\\\\Program Files\\\\Git\\\\bin\\\\bash.exe x")\n')[0]
        assert "Unix path" in unix.why and "box" in unix.why
        assert "drive path" in drive.why and "checkout" in drive.why

    def test_the_same_call_is_reported_once(self, tmp_path):
        """The list element and the command word are the same claim."""
        found = scratch(tmp_path, 'import subprocess\nsubprocess.run(["/bin/bash", "x"])\n')
        assert len(found) == 1, [f.token for f in found]


class TestTheRuleLeavesThePortableShapesAlone:
    @pytest.mark.parametrize("label,source", ALLOWED, ids=[row[0] for row in ALLOWED])
    def test_it_is_allowed(self, tmp_path, label, source):
        found = scratch(tmp_path, source)
        assert not found, (
            f"{label}: refused with {[f.token for f in found]} — a guard that fails "
            "on the repair is a guard that gets deleted")


class TestAScriptFindsItsInterpreterByName:
    """The shebang half, measured on its own before the tree is judged.

    A `#!` line is where a script states where its interpreter is, and every other
    tracked script here already answers `#!/usr/bin/env …` (or `#!/bin/sh`, which
    POSIX requires to exist). Four of them answered `#!/bin/bash` — the same defect
    as `subprocess.run(["/bin/bash", …])`, one line earlier in the read: true on
    the box, and a script that only runs where that path happens to exist.
    """

    @pytest.mark.parametrize("line", [
        "#!/bin/bash", "#! /bin/bash", "#!/usr/bin/python3", "#!/bin/python",
        "#!/usr/local/bin/bash", "#!/bin/zsh", "#!/usr/bin/env",
    ])
    def test_it_is_refused(self, line):
        assert shebang_defect(line), f"{line!r} was read as portable"

    @pytest.mark.parametrize("line", [
        "#!/usr/bin/env bash", "#!/usr/bin/env python3", "#!/usr/bin/env python",
        "#!/usr/bin/env -S python3 -u", "#!/bin/sh", "#!/bin/sh -e",
    ])
    def test_it_is_allowed(self, line):
        assert not shebang_defect(line), (
            f"{line!r} is portable and was refused — a guard that fails on the "
            "repair is a guard that gets deleted")

    def test_the_refusal_names_the_line_it_wants_instead(self):
        """A refusal a reader cannot act on is a red line with no fix in it."""
        why = shebang_defect("#!/bin/bash")
        assert "/bin/bash" in why and "#!/usr/bin/env bash" in why
        assert "#!/bin/sh" in shebang_defect("#!/usr/bin/python3")

    def test_a_file_without_a_shebang_is_not_a_script(self, tmp_path):
        plain = tmp_path / "notes.txt"
        plain.write_text("no shebang here\n", encoding="utf-8")
        assert shebang_of(plain) is None

    def test_the_shebang_is_read_past_a_crlf(self, tmp_path):
        """`readline` on a CRLF file hands back `#!/bin/bash\r`, which a rule that
        did not strip it would compare against `#!/bin/bash` and pass."""
        crlf = tmp_path / "script.sh"
        crlf.write_bytes(b"#!/bin/bash\r\necho hi\r\n")
        assert shebang_of(crlf) == "#!/bin/bash"
        assert shebang_defect(shebang_of(crlf))


class TestTheShebangRuleOverATrackedTree:
    """`shebang_defect` judged alone says nothing about whether the walk *uses* it.

    A repository whose scripts are all portable reports exactly what a walk that
    opened no file reports, so the reader is driven against a scratch repository —
    one that really tracks a script beginning `#!/bin/bash`, which is the finding
    the rule exists to make.
    """

    @staticmethod
    def _repo(tmp_path: pathlib.Path, scripts: dict[str, str]) -> pathlib.Path:
        git("init", "-q", cwd=str(tmp_path))
        for name, line in scripts.items():
            (tmp_path / name).write_bytes(f"{line}\necho hi\n".encode("utf-8"))
        git("add", *scripts, cwd=str(tmp_path))
        return tmp_path

    def test_a_tracked_script_with_a_path_shebang_is_refused(self, tmp_path):
        root = self._repo(tmp_path, {"s.sh": "#!/bin/bash"})
        assert shebang_findings(root) == [("s.sh", "#!/bin/bash")]

    def test_the_portable_spellings_are_not_refused(self, tmp_path):
        root = self._repo(tmp_path, {"a.sh": "#!/usr/bin/env bash",
                                     "b.sh": "#!/bin/sh"})
        assert shebang_findings(root) == []

    def test_a_script_git_does_not_track_is_not_a_claim(self, tmp_path):
        """A leftover in the working tree is not something a clone has."""
        root = self._repo(tmp_path, {"s.sh": "#!/bin/bash"})
        (root / "scratch.sh").write_bytes(b"#!/bin/bash\necho scratch\n")
        assert [name for name, _ in shebang_findings(root)] == ["s.sh"]

    def test_the_walk_says_what_it_read(self, tmp_path):
        root = self._repo(tmp_path, {"a.sh": "#!/usr/bin/env bash", "plain.txt": "x"})
        assert shebang_count(root) == 1, (
            "the floor that keeps 'no findings' honest counts the same walk")


class TestTheCommandsInACommandStringAreRead:
    """`command_words` is the one piece of shell grammar here, so it is measured."""

    def test_the_executable_of_every_command_is_taken(self):
        assert command_words("echo hi; /bin/sh x && /usr/bin/env bash y") == [
            "echo", "/bin/sh", "/usr/bin/env"]

    def test_a_wrapper_does_not_hide_the_executable_behind_it(self):
        assert command_words("sudo /bin/bash x") == ["/bin/bash"]
        assert command_words("env FOO=1 /bin/sh -c 'echo'") == ["/bin/sh"]
        assert command_words("nohup /usr/bin/env bash x") == ["/usr/bin/env"]

    def test_the_limit_around_a_wrappers_option_value_is_stated_and_held(self):
        """`sudo -u root /bin/bash` stops at `root`: an option's value is not a
        wrapper word either, and guessing which words are values is how a guard
        starts refusing argv it cannot read. The limit is written in the docstring
        rather than discovered by a false negative."""
        assert command_words("sudo -u root /bin/bash x") == ["root"]
        assert "The limit this has" in command_words.__doc__

    def test_an_argument_is_never_read_as_a_command(self):
        assert command_words("echo /bin/bash") == ["echo"]
        assert command_words("git -C /tmp/repo status") == ["git"]

    def test_a_runtime_tail_stops_the_reading_rather_than_guessing(self):
        """`"/bin/" + x + "bash"` is not the path `/bin/bash`: the pieces are
        ordered, and the unknown one ends the literal prefix rather than joining
        across it."""
        expression = ast.parse('"/bin/" + x + "bash"').body[0].value
        assert _prefix(expression) == "/bin/"
        assert _literal(expression) is None


class TestTheRealTreeIsCleanAndWasRead:
    def test_no_test_runs_a_binary_by_an_absolute_path(self):
        found = findings()
        assert not found, (
            "these run a process by a path that exists on one platform only:\n  "
            + "\n  ".join(f"{f.path}:{f.line}  {f.call}  {f.token}" for f in found)
            + "\nUse `shutil.which(\"name\")` — and skip when it is None — or "
              "`sys.executable` for Python, which is what the suites that run a "
              "script already do.")

    def test_both_trees_are_read(self):
        """The tools are half the claim: `deploy/*.py` runs from this same checkout."""
        names = {_shown(path) for path in _places(None)}
        assert "tests/conftest.py" in names and "deploy/doc_links.py" in names, (
            "one of the two trees is no longer read, so its calls are judged by "
            "nothing — a finding in an unread tree is the same as no finding")

    def test_it_read_the_whole_suite(self):
        """A guard that recognises nothing reports what a clean tree reports."""
        read = summary()
        assert read["files"] >= MIN_FILES, (
            f"only {read['files']} file(s) under tests/ were read, so 'no findings' "
            "is a statement about a tree the guard did not open")
        assert read["calls"] >= MIN_CALLS, (
            f"only {read['calls']} process call(s) were recognised, so the rule no "
            "longer reads the calls it exists to judge")
        assert read["executables"] >= MIN_EXECUTABLES, (
            f"only {read['executables']} executable position(s) were read as a "
            "literal, out of " + str(read["calls"]) + " call(s) — the rule has "
            "stopped looking where the path would be")

    def test_no_tracked_script_names_its_interpreter_by_a_path(self):
        found = shebang_findings()
        assert not found, (
            "these scripts name their interpreter by a path that exists on one "
            "platform only:\n  "
            + "\n  ".join(f"{name}  {line}" for name, line in found)
            + "\nUse `#!/usr/bin/env <name>` — the interpreter is looked up on "
              "`PATH` on both machines — or `#!/bin/sh`, which POSIX requires to "
              "be there.")

    def test_it_read_the_scripts_of_the_repository(self):
        """A rule that read no shebang reports what a clean repository reports."""
        read = shebang_count()
        assert read >= SHEBANG_MINIMUM, (
            f"only {read} tracked file(s) were read as having a `#!` line, so 'no "
            "findings' is a statement about scripts the rule did not open")

    def test_this_file_holds_no_call_of_its_own(self):
        """The guard is a test file too, and its examples must be shapes.

        Every refused shape above is fixture *source*: a string handed to a helper
        that writes it into a scratch tree, so this file contains no process call for
        the rule to trip on. Asserted rather than assumed — the alternative, a real
        failing call kept as an example, would make the guard refuse itself and be
        deleted by whoever hit it.
        """
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        assert not _findings_in(pathlib.Path(__file__), source)


class TestTheGuardIsWired:
    """A suite the hook does not run is a suite nobody runs.

    CI runs `tests/unit` as a directory, so this file is executed on every push
    whatever happens here. The pre-commit hook and the auto-deploy run the gate's own
    `TESTS=` list instead, and the gate *refuses* a release that deletes a file it
    names (`exit 3`) rather than skipping it — which is what makes naming this file
    there the difference between a guard and a habit.
    """

    def test_the_gate_runs_it(self):
        assert "tests/unit/test_shell_path_portability.py" in gate_tests(), (
            "the guard is not in the gate's TESTS=, so a commit that hardcodes a "
            "shell path is refused by nothing until CI, and the box runs no check")

    def test_the_gate_covers_the_list_it_runs(self):
        gate = GATE.read_text(encoding="utf-8")
        armament = [line for line in gate.splitlines() if line.startswith("ARMAMENT=")]
        assert armament and "$TESTS" in armament[0], (
            "the armament list stopped including the test files, so deleting this "
            "one would be a warning rather than a refusal")
