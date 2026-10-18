"""Mutation check for `deploy/doc_links.py` — the rules a page and its copy are held to.

The tool is small and every clause in it exists because something was measured
first, so each mutation here removes exactly one of those clauses and asks
`tests/unit/test_doc_links.py` to go red:

* the three shapes that are **not** claims — a data file, a runtime artifact, a
  branch template — because a guard that fails on `feature/description` is a guard
  that gets deleted;
* the gone-window, both ways: read from a whole line and 470 of `AGENTS.md`'s
  references go exempt (a bullet there is a paragraph), and read through the
  reference's own name and a file called `test_gone.py` exempts itself;
* the two structural exemptions, the exception list held in both directions, and
  the three exit codes.

Every mutation names the test it is expected to turn red, and the harness refuses
to report a baseline it could not establish — a green suite before the run is what
makes "caught" mean something.
"""
from __future__ import annotations

from tests.mutation.harness import main

GUARDS = ["tests/unit/test_doc_links.py"]

MUTATIONS = [

    ('a missing reference resolves anyway',
     'deploy/doc_links.py',
     '                elif resolvable:\n                    ref.state = "resolved"',
     '                elif True:\n                    ref.state = "resolved"'),

    ('a fenced block is read as prose',
     'deploy/doc_links.py',
     '        if fenced:\n            continue',
     '        if fenced and False:\n            continue'),

    ('a data file is asserted',
     'deploy/doc_links.py',
     '    if extension_of(path) in KNOWN_EXT:\n        return True',
     '    if True:\n        return True'),

    ('the gone words are read from the whole line',
     'deploy/doc_links.py',
     '                near = (above + line[max(0, start - NEAR): start] + " "\n'
     '                        + line[end:end + NEAR])',
     '                near = line'),

    ('the reference reads its own name as the sentence',
     'deploy/doc_links.py',
     '                near = (above + line[max(0, start - NEAR): start] + " "\n'
     '                        + line[end:end + NEAR])',
     '                near = line[max(0, start - NEAR): end + NEAR]'),

    ('an exception whose file exists is kept',
     'deploy/doc_links.py',
     '        elif resolve(repo, doc, path, index=index):\n'
     '            report.stale.append((where, path, "the path is tracked again — drop the exception"))',
     '        elif resolve(repo, doc, path, index=index):\n            pass'),

    ('an exception the page stopped naming is kept',
     'deploy/doc_links.py',
     '        if (doc, path) not in named:',
     '        if False:'),

    ('a tree with no documents is reported clean',
     'deploy/doc_links.py',
     '    if not report.docs:\n        raise Measure(f"no tracked markdown under {repo}")',
     '    if not report.docs:\n        return report'),

    ('the scratch corner is asserted',
     'deploy/doc_links.py',
     '                if rel.startswith(PLANNING) or path.startswith(SCRATCH):',
     '                if rel.startswith(PLANNING):'),

    ('planning pages are asserted',
     'deploy/doc_links.py',
     '                if rel.startswith(PLANNING) or path.startswith(SCRATCH):',
     '                if path.startswith(SCRATCH):'),

    ('an unresolved reference still exits zero',
     'deploy/doc_links.py',
     '        print_offenders(report)\n        return 1',
     '        print_offenders(report)\n        return 0'),

    # ── the refusal's own text: the window under the reference ──────────────
    # A refusal that prints `doc:line [path] reference` and nothing else is a
    # lookup; these five break the half that makes the fix one edit away.

    ('the snippet is not printed at all',
     'deploy/doc_links.py',
     '        if ref.context:\n'
     '            # The page\'s own words, so the fix is one edit away rather than a line\n'
     '            # number to go and look up.\n'
     '            print(f"        {printable(ref.context)}", file=sys.stderr)\n',
     ''),

    ('the tail clip cuts mid-word',
     'deploy/doc_links.py',
     '    cut = text[:limit]\n'
     '    space = cut.rfind(" ")\n'
     '    return cut[:space] if space > 0 else cut',
     '    return text[:limit]'),

    ('the head clip cuts mid-word',
     'deploy/doc_links.py',
     '        cut = text[-limit:]\n'
     '        space = cut.find(" ")\n'
     '        return cut[space + 1:] if space != -1 else cut',
     '        return text[-limit:]'),

    ('a window is built for a reference nobody refuses',
     'deploy/doc_links.py',
     '                ref = Ref(rel, number, kind, raw)\n                ref.path = path',
     '                ref = Ref(rel, number, kind, raw)\n'
     '                ref.path = path\n'
     '                ref.context = context_for(line, start, end)'),

    ('the window leaves the reference out of it',
     'deploy/doc_links.py',
     '    return " ".join(part for part in (head, squash(line[start:end]), tail) if part)',
     '    return " ".join(part for part in (head, tail) if part)'),

    ("this repo's punctuation is escaped, not folded",
     'deploy/doc_links.py',
     '    return _safe("".join(FOLD.get(char, char) for char in text))',
     '    return _safe(text)'),

    ('a malformed exception record is skipped',
     'deploy/doc_links.py',
     '        if len(parts) < 3 or not all(p.strip() for p in parts[:3]):',
     '        if False:'),

    # ── the copy nothing executes: docstrings, comments, messages ────────────
    # `app/services/exam_report.py` cited `docs/features/ANALYSIS.md` and nothing
    # noticed; a deploy script's `FAILED:` line is the same rot where a reader is
    # already having a bad day. Each anchor below removes one half of that
    # reading: the files it opens, the shapes it reads in them, and the three
    # reasons a line there is not a claim.

    ('a shell script is not read at all',
     'deploy/doc_links.py',
     '    if rel.endswith((".sh", ".ps1")):\n        return "shell"',
     '    if rel.endswith((".sh", ".ps1")):\n        return ""'),

    ('a template comment is not read at all',
     'deploy/doc_links.py',
     '    if rel.endswith(".html") and rel.startswith(TEMPLATED):\n        return "template"',
     '    if rel.endswith(".html") and rel.startswith(TEMPLATED):\n        return ""'),

    ('an html comment is not read, only a jinja one',
     'deploy/doc_links.py',
     '    for match in re.finditer(r"\\{#(.*?)#\\}|<!--(.*?)-->", source, re.S):',
     '    for match in re.finditer(r"\\{#(.*?)#\\}", source, re.S):'),

    ('a bare path in a docstring is not a claim',
     'deploy/doc_links.py',
     '    if not markdown:\n        for match in BARE.finditer(line):',
     '    if False:\n        for match in BARE.finditer(line):'),

    ('a fixture tree is read as this checkout',
     'deploy/doc_links.py',
     '    if rel.startswith(("tests/",) + SCRATCH):',
     '    if rel.startswith(SCRATCH):'),

    ('a line that opens with a command is a claim',
     'deploy/doc_links.py',
     '    return [(number, line, bool(COMMAND.match(line)))',
     '    return [(number, line, False)'),

    ('every line of a python file is a snippet',
     'deploy/doc_links.py',
     '    return [(number, line, bool(COMMAND.match(line)))',
     '    return [(number, line, True)'),

    ('the copy may not say it is an example',
     'deploy/doc_links.py',
     '                elif not page and _example(near):',
     '                elif not page and False:'),

    ('a wrapped message is read as one line only',
     'deploy/doc_links.py',
     '                above = "" if page else previous[-NEAR:] + " "',
     '                above = ""'),

    # ── the heading a link names ─────────────────────────────────────────────
    # `[x](b.md#the-cases)` is two claims: the page, which was always checked, and
    # the heading, which was not. A renamed section left a link that opens a page
    # at the top and says nothing about what the reader came for.

    ('a fragment a link names is not read',
     'deploy/doc_links.py',
     '    if kind != "link" or not fragment or LINE_ANCHOR.match(fragment):',
     '    if True:'),

    ('the slug keeps the punctuation it should drop',
     'deploy/doc_links.py',
     '    kept = "".join(ch for ch in text.strip().lower()\n'
     '                   if ch.isalnum() or ch in "-_ ")',
     '    kept = text.strip().lower()'),

    ('a repeated heading is not suffixed',
     'deploy/doc_links.py',
     '        out.add(base if not count else f"{base}-{count}")',
     '        out.add(base)'),

    ('a heading inside a fence is an anchor',
     'deploy/doc_links.py',
     '        if fenced:\n            continue\n        match = HEADING.match(line)\n        if not match:',
     '        if False:\n            continue\n        match = HEADING.match(line)\n        if not match:'),

    ('an anchor the page writes itself is not read',
     'deploy/doc_links.py',
     '    out.update(match.group(1) for match in EXPLICIT_ANCHOR.finditer(text))',
     '    pass'),

    ('the fragment is compared as it is written',
     'deploy/doc_links.py',
     '                fragment = unquote(fragment)\n                local = kind == "link"',
     '                fragment = fragment\n                local = kind == "link"'),

    ('a same-page anchor is not a link',
     'deploy/doc_links.py',
     '                local = kind == "link" and not target_name and bool(fragment)',
     '                local = False'),

    ('a heading is looked for on the wrong page',
     'deploy/doc_links.py',
     '    page = rel if local else (page_named(repo, rel, path, index) if path else None)',
     '    page = rel'),

    ('the summary forgets the copy it read',
     'deploy/doc_links.py',
     '    return (f"doc links: {len(report.docs)} document(s) and "\n'
     '            f"{len(report.sources)} file(s) of comment copy, {report.checked} "',
     '    return (f"doc links: {len(report.docs)} document(s) and "\n'
     '            f"{0} file(s) of comment copy, {report.checked} "'),
]

if __name__ == "__main__":
    raise SystemExit(main("mutate_doc_links", GUARDS, MUTATIONS))
