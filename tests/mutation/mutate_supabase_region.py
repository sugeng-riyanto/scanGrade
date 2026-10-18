"""Mutation check for `deploy/supabase_region.py` — and for where it is called.

The guard's whole value is that a wrong fact cannot pass, and there are two ways it
could: a rule that stops biting, or a rule nothing runs. So the mutations here break
both kinds, one at a time, and ask `tests/unit/test_supabase_region.py` to go red:

* the record — a host reaching another project, a recorded site without the ref, a
  second ref-shaped string, the typo quoted outside its two files or without the
  word that calls it a typo, a region beside Supabase that is not `ap-south-1`,
  Singapore on a Supabase line, the region floor;
* the two published claims and their one source — a disclosure that spells the
  region or keeps the city by hand or renders the claim once, a capacity row typed
  in place of `database_region()`, a configured constant that moved away from the
  record, a region the place map cannot name;
* the configuration — a `.env` pointing at another project, which is the one case
  only the live half can see;
* the wiring — the gate's finding arm, and the pre-commit trigger that decides
  whether the gate runs at all (the typo shipped in `AGENTS.md`, which a filter that
  watched only templates, stylesheets, Python, SQL and `deploy/` never opened).

A survivor is a hole in the guard, and the baseline is checked first: a harness whose
guards already fail would call every mutation "caught".
"""
from __future__ import annotations

from tests.mutation.harness import main

GUARDS = ["tests/unit/test_supabase_region.py"]

MUTATIONS = [

    ('a host reaching another project resolves anyway',
     'deploy/supabase_region.py',
     '                if token != REF and not rel.startswith("tests/"):',
     '                if False:'),

    ('a recorded site that lost the ref is accepted',
     'deploy/supabase_region.py',
     '        if REF not in texts[rel]:',
     '        if False:'),

    ('a second ref-shaped string is accepted',
     'deploy/supabase_region.py',
     '                elif token != REF:',
     '                elif False:'),

    ('the typo may be quoted anywhere',
     'deploy/supabase_region.py',
     '        if rel not in OLD_QUOTING:',
     '        if False:'),

    ('the typo need not say it was one',
     'deploy/supabase_region.py',
     '            if "salah ketik" not in line:',
     '            if False:'),

    ('a region beside Supabase is not judged',
     'deploy/supabase_region.py',
     '                if match.group(0) != REGION and any(w in line for w in CLAIM_WORDS):',
     '                if False:'),

    ('the disclosure may point at Singapore',
     'deploy/supabase_region.py',
     '    if any(w in text for w in SINGAPORE):',
     '    if False:'),

    ('a scan that reads nothing reports agreement',
     'deploy/supabase_region.py',
     '    if report.region_sites < MINIMUM_REGION_SITES:',
     '    if report.region_sites < 0:'),

    ('the disclosure may render the claim once',
     'deploy/supabase_region.py',
     '    if text.count(CLAIM_VAR) != 2:',
     '    if False:'),

    ('the disclosure may spell the region itself',
     'deploy/supabase_region.py',
     '    if spelled:',
     '    if False:'),

    ('the disclosure may keep the city by hand',
     'deploy/supabase_region.py',
     '    if kept:',
     '    if False:'),

    ('the capacity row may be typed by hand',
     'deploy/supabase_region.py',
     '        if REGION in line or any(w in line for w in PLACE_WORDS):',
     '        if False:'),

    ('the capacity row need not read the claim',
     'deploy/supabase_region.py',
     '    if "database_region(" not in source:',
     '    if False:'),

    ('a constant that moved away from the record is accepted',
     'deploy/supabase_region.py',
     '    elif declared.group(1) != REGION:',
     '    elif False:'),

    ('a region with no place in the map is accepted',
     'deploy/supabase_region.py',
     '    if f\'"{REGION}":\' not in source:',
     '    if False:'),

    ('a .env pointing at another project is accepted',
     'deploy/supabase_region.py',
     '            if config.url_ref != REF:',
     '            if False:'),

    ('the gate passes a finding through',
     'deploy/theme_gate.sh',
     'if [ "$REGION_RC" -eq 1 ]; then',
     'if [ "$REGION_RC" -eq 9 ]; then'),

    ('the pre-commit hook never opens a markdown edit',
     'deploy/git-hooks/pre-commit',
     "|^.*\\.md$|^.*\\.ps1$'",
     "|^.*\\.ps1$'"),
]

if __name__ == "__main__":
    raise SystemExit(main("mutate_supabase_region", GUARDS, MUTATIONS))
