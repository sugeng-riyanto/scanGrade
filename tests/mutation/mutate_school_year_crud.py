"""Mutation check for a school's academic years — the page that had three verbs.

The claim is that `/admin-sekolah/school-years` manages a year completely: create
it, read it, **rename and re-date it**, make it current, remove it. Every entry
below breaks one moving part of that claim and demands a red suite, because a
survivor is a hole in the guard rather than a curiosity.

The parts are the four a reader would name if asked what could go wrong here: the
refusals the create and the edit *share* (so a page cannot accept with one button
what it refuses with the other), the school filter on every write, the order that
keeps exactly one year current, the guard that stops a direct POST removing the
year the school is running in, and the template's per-row controls — which is
where the defect lived: the page offered no edit at all, and the row a school
most often needs to correct carried none of the buttons it did offer.

Three of these are *not* source readings: the refusals, the writes and the delete
refusal are exercised as behaviour by `tests/unit/test_school_year_crud.py`
against a fake PostgREST, so "the update is filtered by the school" is observed
as a filter on the request and not as a substring in the file.

Run:  python deploy/mutation_check.py --run mutate_school_year_crud   (~4 minutes)
      python deploy/mutation_check.py --check                        (anchors only)
"""
from __future__ import annotations

import sys

from tests.mutation.harness import main

GUARDS = ["tests/unit/test_school_year_crud.py", "tests/unit/test_language_toggle.py"]
NL = chr(10)
ROUTES = "app/routes/admin_sekolah.py"
TEMPLATE = "app/templates/admin_sekolah/school_years.html"

MUTATIONS = [
    # ── the refusals both forms share ────────────────────────────────────────
    ("the duplicate-name check is dropped",
     ROUTES,
     "    if name.strip().casefold() in taken:",
     "    if False:"),
    ("the duplicate check stops excluding the row being edited",
     ROUTES,
     '             for r in rows if r.get("id") != exclude_id}',
     "             for r in rows}"),

    # ── what a write is scoped to, and what it writes ───────────────────────
    ("the edited row is written without the school filter",
     ROUTES,
     '        }).eq("id", year_id).eq("school_id", sid).execute()'
     + NL + "        log_activity(",
     '        }).eq("id", year_id).execute()' + NL + "        log_activity("),
    ("the edit writes a column that is not the year's own",
     ROUTES,
     '        supabase.table("school_years").update({' + NL
     + '            "name": name, "start_date": start_date, "end_date": end_date,'
     + NL + '            "is_active": is_active,' + NL
     + '        }).eq("id", year_id).eq("school_id", sid).execute()',
     '        supabase.table("school_years").update({' + NL
     + '            "name": name, "start_date": start_date, "end_date": end_date,'
     + NL + '            "is_active": is_active, "status": "draft",' + NL
     + '        }).eq("id", year_id).eq("school_id", sid).execute()'),
    ("the edit route is no longer scoped to the school",
     ROUTES,
     '@admin_sekolah_bp.route("/school-years/<year_id>/edit", methods=["POST"])'
     + NL + "@admin_sekolah_required" + NL
     + '@require_school_access("school_years", "year_id")',
     '@admin_sekolah_bp.route("/school-years/<year_id>/edit", methods=["POST"])'
     + NL + "@admin_sekolah_required"),

    # ── exactly one year is current, and it is set in that order ────────────
    ("the create stops clearing the year that was running",
     ROUTES,
     "        try:" + NL + "            if is_active:" + NL
     + '                supabase.table("school_years").update({"is_active": False})'
       '.eq("school_id", sid).execute()' + NL
     + '            res = supabase.table("school_years").insert({',
     "        try:" + NL + "            if False:" + NL
     + '                supabase.table("school_years").update({"is_active": False})'
       '.eq("school_id", sid).execute()' + NL
     + '            res = supabase.table("school_years").insert({'),
    ("an edit stops moving the current flag off the other year",
     ROUTES,
     '        if is_active and not before.get("is_active"):',
     "        if False:"),
    ("the toggle sets the new year before it clears the old one",
     ROUTES,
     '        supabase.table("school_years").update({"is_active": False})'
     '.eq("school_id", sid).execute()' + NL
     + '        supabase.table("school_years").update({"is_active": True})'
       '.eq("id", year_id).execute()',
     '        supabase.table("school_years").update({"is_active": True})'
     '.eq("id", year_id).execute()' + NL
     + '        supabase.table("school_years").update({"is_active": False})'
       '.eq("school_id", sid).execute()'),

    # ── the running year, which a POST may not remove ───────────────────────
    ("the running year can be deleted by a direct POST",
     ROUTES,
     '    row = (supabase.table("school_years").select("is_active")' + NL
     + '           .eq("id", year_id).eq("school_id", sid).execute().data or [])'
     + NL + '    if row and row[0].get("is_active"):',
     '    row = (supabase.table("school_years").select("is_active")' + NL
     + '           .eq("id", year_id).eq("school_id", sid).execute().data or [])'
     + NL + "    if False:"),

    # ── the page's own controls, one per verb ───────────────────────────────
    ("the edit form posts to the create route",
     TEMPLATE,
     '                      action="/admin-sekolah/school-years/{{ y.id }}/edit"',
     '                      action="/admin-sekolah/school-years"'),
    ("the edit form opens empty instead of filled in",
     TEMPLATE,
     '                        <input type="text" name="name" value="{{ y.name }}" required',
     '                        <input type="text" name="name" value="" required'),
    ("the edit form forgets which year is current",
     TEMPLATE,
     'id="act_{{ y.id }}" {% if y.is_active %}checked{% endif %}',
     'id="act_{{ y.id }}"'),
    ("a control posts to a path no route serves",
     TEMPLATE,
     '                        <form method="POST" action="/admin-sekolah/school-years/'
     '{{ y.id }}/toggle" class="inline">',
     '                        <form method="POST" action="/admin-sekolah/school-years/'
     '{{ y.id }}/activate" class="inline">'),
    ("the current row's delete disappears with no word about how to get it back",
     TEMPLATE,
     '<span class="text-[11px] text-stone-400 font-medium text-right '
     'max-w-[11rem]" x-text="t(\'Sedang berjalan — aktifkan tahun lain dulu untuk '
     'menghapusnya\',\'Currently running — activate another year to delete it\')"></span>',
     '<span class="text-[11px] text-stone-400 font-medium text-right '
     'max-w-[11rem]"></span>'),
    ("the delete stops saying what goes with the year",
     TEMPLATE,
     "KKM, serta sebaran nilai bawaan tahun ini ikut terhapus.",
     "dan sebaran nilai bawaan tahun ini ikut terhapus."),
    ("a school-supplied name is interpolated into the delete handler",
     TEMPLATE,
     "@submit.prevent=\"confirm(t('Hapus tahun ajaran ini?",
     "@submit.prevent=\"confirm('Hapus tahun ajaran ' + {{ y.name }} + '?"),

    # ── and the language the page declares ──────────────────────────────────
    ("the page pins itself to Indonesian while carrying pairs",
     TEMPLATE,
     '{% extends "base.html" %}',
     "{% set content_lang = 'id' %}" + NL + '{% extends "base.html" %}'),
]

if __name__ == "__main__":
    sys.exit(main("mutate_school_year_crud", GUARDS, MUTATIONS))
