#!/usr/bin/env python3
"""Is the committed tailwind.css what the templates actually produce?

`app/static/css/tailwind.css` is committed rather than built on the box, and that
is the right trade here: the app is served by nginx with a one-year cache and the
URL is content-hashed (`asset_v()`), so a rebuild does reach a browser, and the
production box has 1 vCPU and 957 MB to spend on students rather than on Node.

It has one failure mode, and it is silent:

    a template gains a utility, `npm run css:build` is not run, and the class is
    simply not in the stylesheet that ships.

Nothing errors. Every page answers 200. The element keeps whatever its ancestor
gave it — which is how two of the three "Log in" buttons on /demo shipped as
white text on a white box, and how a `min-w-[...]` that was never generated left
a table to squeeze instead of scroll.

tests/unit/test_tailwind_class_names.py already catches that for the *colour*
utilities a class-bearing attribute or a script names. This is the general check:
it rebuilds the stylesheet with the project's own `css:build` command and compares
the result, so every utility is covered — including arbitrary values, modifiers
and the ones only JavaScript names.

What it compares is the CSS a browser applies, with comments removed, and that is
load-bearing rather than tidy. `--minify` keeps Tailwind's banner, so the artefact
names the toolchain that built it:

    /*! tailwindcss v3.4.17 | MIT License | https://tailwindcss.com */

This box has 3.4.17 and the production checkout has 3.4.19, both matching the
their lockfiles, and they produce the same 882 classes and byte-identical rules.
Comparing the files raw would call the production stylesheet stale and refuse
every release from then on — a false alarm from the one check that is supposed to
be about real regressions. So the comparison ignores the banner (and says so when
it moved), and it ignores line endings, because Git stores LF and checks out CRLF
on Windows: a gate has to give the same answer on both.

Exit codes follow the other gates:

  0  the committed stylesheet is exactly what these templates produce
  1  it is stale: the release would serve a stylesheet its own templates outgrow
  2  could not measure — no node, no node_modules, the build failed, or no
     `css:build` script to copy. Never a verdict on the release, and never a
     rollback: a broken checker must not be able to reject good code.

Usage:
    python deploy/css_freshness.py              # check: 0 / 1 / 2
    python deploy/css_freshness.py --json       # machine-readable
    python deploy/css_freshness.py --rebuild    # write the fresh build in place

No gate ever passes --rebuild. Writing the stylesheet is a decision someone makes
after reading why it is stale, not something a release does to itself.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_FRESH = 0
EXIT_STALE = 1
EXIT_CANNOT_RUN = 2

REPO = Path(__file__).resolve().parent.parent
CSS_PATH = REPO / "app" / "static" / "css" / "tailwind.css"
CONFIG_PATH = REPO / "tailwind.config.js"
PACKAGE_JSON = REPO / "package.json"
TEMPLATES = REPO / "app" / "templates"

# The CLI entry points, in the order a checkout is likely to have them. The first
# is the package's own entry (`node_modules/tailwindcss/lib/cli.js` is a two-line
# shim onto the real one) and the second is the bin stub npm writes.
CLI_CANDIDATES = (
    "node_modules/tailwindcss/lib/cli.js",
    "node_modules/.bin/tailwindcss",
    "node_modules/.bin/tailwindcss.cmd",
)

BUILD_TIMEOUT = 180

# A class name in minified CSS: `.mt-4{`, `.lg\:block{`, `.min-w-\[480px\]{`. The
# escapes are Tailwind's own, so the backslash stays in the name and the two
# files are compared in the same alphabet.
_UTILITY = re.compile(r"\.((?:[A-Za-z0-9_-]|\\.)+)")

_COMMENT = re.compile(r"/\*.*?\*/", re.S)

# Tailwind's own banner, which `--minify` keeps because it starts with `/*!`.
_BANNER = re.compile(r"/\*!(.*?)\*/", re.S)


def strip_comments(text: str) -> str:
    """Remove CSS comments, keeping everything the browser actually applies.

    This is not cosmetic. `--minify` preserves Tailwind's banner, so it carries
    the *toolchain version* into the artefact:

        /*! tailwindcss v3.4.17 | MIT License | https://tailwindcss.com */

    and a checkout whose node_modules is a patch release newer produces the same
    882 classes and byte-identical rules with `v3.4.19` in that line. Comparing
    the files raw would then report a *stale* stylesheet that is not stale — on
    the one machine that matters, where the deploy would refuse every release
    from then on. Stripping comments first makes the comparison about what the
    app serves, which is what was wrong in the first place.

    (The banner is also why `_UTILITY` must not run on the raw text: it would
    read `.4.17` out of `v3.4.17` and report a utility that does not exist.)
    """
    return _COMMENT.sub("", text)


def comparable(blob: bytes) -> str:
    """The part of a stylesheet two builds can be compared on.

    Line endings first, because Git stores LF and checks out CRLF on Windows and
    a gate has to give the same answer on both. Then comments, for the reason
    above.
    """
    return strip_comments(blob.decode("utf-8", "replace").replace("\r\n", "\n"))


def banner_of(blob: bytes) -> str:
    """The toolchain banner, for a note — never for the verdict."""
    found = _BANNER.search(blob.decode("utf-8", "replace"))
    return " ".join(found.group(1).split()) if found else ""


def utilities(text: str) -> set[str]:
    """Every class name the stylesheet defines. Used for the diagnosis only."""
    return set(_UTILITY.findall(text))


def build_command(out_path: Path) -> list[str]:
    """The project's own `css:build`, with only the output path changed.

    Read out of package.json on purpose. Hardcoding `--minify` here would make
    this check and the build it is checking two different commands, and the day
    one of them changes the gate would go on measuring the other.

    Raises CannotRun rather than guessing when the script is not there.
    """
    try:
        scripts = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["scripts"]
    except (OSError, KeyError, ValueError) as exc:
        raise CannotRun(f"cannot read scripts from package.json ({exc})") from exc

    command = scripts.get("css:build")
    if not command:
        raise CannotRun("package.json has no 'css:build' script to check against")

    tokens = shlex.split(command)
    # Drop the runner wrapper (`npx tailwindcss ...`) — the CLI is invoked
    # directly below, so the wrapper is the one part that must not be copied.
    while tokens and (
        tokens[0] in ("npx", "npm", "node", "yarn", "pnpm")
        or tokens[0].endswith("tailwindcss")
    ):
        tokens.pop(0)

    for index, token in enumerate(tokens):
        if token in ("-o", "--output") and index + 1 < len(tokens):
            tokens[index + 1] = str(out_path)
            break
    else:
        tokens += ["-o", str(out_path)]
    return tokens


class CannotRun(Exception):
    """The checker cannot produce an answer. Never a verdict on the release."""


def find_node() -> str:
    for candidate in ("node", "nodejs"):
        found = shutil.which(candidate)
        if found:
            return found
    raise CannotRun("no node on PATH - the stylesheet cannot be rebuilt here")


def find_cli() -> Path:
    for relative in CLI_CANDIDATES:
        candidate = REPO / relative
        if candidate.exists():
            return candidate
    raise CannotRun(
        "no tailwind CLI in node_modules - run `npm install` in the checkout"
    )


def build(out_path: Path) -> None:
    """Rebuild the stylesheet into `out_path` with the project's own command."""
    node = find_node()
    cli = find_cli()
    args = [node, str(cli), *build_command(out_path)]
    try:
        proc = subprocess.run(
            args,
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise CannotRun(f"the build did not finish in {BUILD_TIMEOUT}s") from exc
    except OSError as exc:
        raise CannotRun(f"could not run the build ({exc})") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = " | ".join(detail[-3:]) if detail else "no output"
        raise CannotRun(f"the build exited {proc.returncode}: {tail}")
    if not out_path.exists():
        raise CannotRun("the build reported success but wrote no file")


def short_path(path: Path) -> str:
    """A path as it should be printed: repo-relative when it is inside the repo.

    Falling back to the absolute path rather than raising keeps this in the
    category of a message. A report that cannot be produced because one of its
    paths is unusual is worse than a report naming a long path.
    """
    try:
        return path.relative_to(REPO).as_posix()
    except ValueError:
        return path.as_posix()


def where_used(names: set[str], limit: int = 6) -> list[str]:
    """Name the templates that would render a utility the stylesheet lacks.

    The gate's whole output is a decision someone has to make, and "which page
    asked for this" is the difference between a one-minute fix and a search.
    """
    found: list[str] = []
    for name in sorted(names):
        # `.lg\:block` in CSS is `lg:block` in a template.
        literal = name.replace("\\", "")
        for template in sorted(TEMPLATES.rglob("*.html")):
            try:
                if literal in template.read_text(encoding="utf-8"):
                    # Report the template's spelling of the class, not CSS's:
                    # `.lg\:block` is a thing nobody can find in a template.
                    found.append(f".{literal} ({short_path(template)})")
                    break
            except OSError:
                continue
        if len(found) >= limit:
            break
    return found


def compare(committed: bytes, fresh: bytes) -> list[str]:
    """Why the committed stylesheet is not the one these templates produce.

    [] means it is. The reasons are written for whoever reads the journal on the
    box at 2am: what is missing, from where, and the one command that fixes it.
    """
    committed_text = comparable(committed)
    fresh_text = comparable(fresh)
    if committed_text == fresh_text:
        return []

    missing = utilities(fresh_text) - utilities(committed_text)
    extra = utilities(committed_text) - utilities(fresh_text)

    reasons = [
        f"the committed stylesheet ({len(committed)} bytes) is not the one these "
        f"templates produce ({len(fresh)} bytes)"
    ]
    if missing:
        reasons.append(
            f"{len(missing)} utility name(s) the templates use are not generated: "
            + ", ".join(where_used(missing))
        )
    if extra:
        reasons.append(
            f"{len(extra)} utility name(s) are in the stylesheet but no template "
            "asks for them: "
            + ", ".join("." + name for name in sorted(extra)[:8])
        )
    if not missing and not extra:
        # Same names, different bytes: a config or content-glob change, not a
        # template edit. Saying so stops the reader hunting for a missing class.
        reasons.append(
            "every utility name matches, so the difference is not a missing class "
            "- it is a change to tailwind.config.js, the content globs, or the "
            "order the rules were generated in"
        )
    return reasons


def run(rebuild: bool = False) -> tuple[int, dict, list[str]]:
    """Return (exit code, machine-readable verdict, human-readable lines)."""
    report: dict = {
        "css": short_path(CSS_PATH),
        "committed_bytes": None,
        "fresh_bytes": None,
        "verdict": "cannot_measure",
    }
    if not CSS_PATH.exists():
        return EXIT_CANNOT_RUN, report, [
            f"{short_path(CSS_PATH)} does not exist - there is nothing to "
            "compare, and a page with no stylesheet is not a pass. "
            "Run `npm run css:build`."
        ]

    committed = CSS_PATH.read_bytes()
    report["committed_bytes"] = len(committed)

    with tempfile.TemporaryDirectory(prefix="sg-css-") as tmp:
        out = Path(tmp) / "tailwind.css"
        try:
            build(out)
        except CannotRun as exc:
            return EXIT_CANNOT_RUN, report, [str(exc)]
        # A build that reports success and writes nothing is not evidence, and it
        # is the same shape of silence this whole check exists to find. The
        # deploy's snapshot step learned it too: a command's exit code is not the
        # artefact.
        try:
            fresh = out.read_bytes()
        except OSError as exc:
            return EXIT_CANNOT_RUN, report, [
                f"the build wrote no stylesheet to compare ({exc})"
            ]
    report["fresh_bytes"] = len(fresh)

    reasons = compare(committed, fresh)
    if not reasons:
        report["verdict"] = "fresh"
        lines = [
            f"tailwind.css is in sync with the templates ({len(committed)} bytes)"
        ]
        # Same stylesheet, different bytes: say why, because a rebuild that
        # changes nothing anyone can point at is the sort of thing that gets
        # "fixed" by committing it to make a diff go away.
        old, new = banner_of(committed), banner_of(fresh)
        if committed != fresh and old != new:
            lines.append(
                f"byte-identical after the toolchain banner, which moved: "
                f"{old or 'none'} -> {new or 'none'}"
            )
        return EXIT_FRESH, report, lines

    if rebuild:
        CSS_PATH.write_bytes(fresh)
        report["verdict"] = "rebuilt"
        report["rebuild_bytes"] = len(fresh)
        return EXIT_FRESH, report, [
            f"tailwind.css was stale and has been rebuilt ({len(fresh)} bytes) - "
            "commit it"
        ]
    report["verdict"] = "stale"
    return EXIT_STALE, report, reasons + [
        "fix: npm run css:build   (then commit app/static/css/tailwind.css)"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Is the committed tailwind.css what the templates produce?"
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="write the fresh build in place instead of reporting (never a gate)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--quiet", action="store_true", help="print nothing on success")
    args = parser.parse_args(argv)

    try:
        code, report, lines = run(rebuild=args.rebuild)
    except CannotRun as exc:                      # pragma: no cover - defensive
        code, report, lines = EXIT_CANNOT_RUN, {"verdict": "cannot_measure"}, [str(exc)]

    if args.json:
        print(json.dumps({**report, "exit": code, "reasons": lines}, indent=1))
        return code

    if args.quiet and code == EXIT_FRESH:
        return code

    # Plain ASCII on purpose: this output is relayed into the deploy journal and
    # onto consoles that are not always UTF-8, where an em-dash arrives as a
    # replacement character. The i18n tool learned the same lesson first.
    label = {
        EXIT_FRESH: "css freshness: OK",
        EXIT_STALE: "css freshness: STALE",
        EXIT_CANNOT_RUN: "css freshness: CANNOT MEASURE",
    }[code]
    print(f"{label} - {lines[0]}")
    for line in lines[1:]:
        print(f"    {line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
