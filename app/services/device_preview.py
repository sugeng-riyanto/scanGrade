"""Every main page, at the widths a phone actually has.

A responsive defect is usually invisible to a test and obvious to a human
looking at 320 px: a table that squeezes instead of scrolling, a toolbar that
wraps to three lines, a card whose padding eats its own heading. `test_mobile_layout.py`
catches the classes of defect it can name; this renders the pages so the others
can be *seen*.

The list is derived from the app's own URL map rather than typed out, so a page
added tomorrow appears in the preview without anyone remembering to add it here —
the same "drop it in and the page grows a row" property the capacity page has.
What is excluded is excluded by name, with the reason, because a silent omission
is how a preview starts lying about what it covers.
"""
import re
from itertools import chain

# The widths a student's phone actually reports. 375 is the common Android/iPhone
# CSS width, 320 is the narrowest phone still in a classroom, and 768 is exactly
# Tailwind's `md` breakpoint — the one width where a layout is supposed to have
# switched to its tablet arrangement.
DEVICE_WIDTHS = (320, 375, 768)

DEVICE_NAMES = {
    320: ("Telepon kecil", "Small phone"),
    375: ("Ponsel standar", "Common phone"),
    768: ("Tablet — tepat di titik henti md", "Tablet — exactly the md breakpoint"),
}

# Not pages, and each for its own reason. `SKIP_EXACT` is matched against the
# whole path, `SKIP_PREFIX` against the start of it. Every reason is an
# `(Indonesian, English)` pair because the page *prints* them: an exclusion list
# is the honest half of a preview, and half of it in one language only is a page
# that explains itself to one reader.
SKIP_EXACT = {
    "/": None,  # never skipped; the landing page is the first thing to check
    "/auth/logout": ("mengakhiri sesi — tiga bingkai saja akan mengeluarkan Anda",
                     "it ends the session — three frames of it sign the viewer out"),
    "/auth/me": ("JSON, bukan halaman", "JSON, not a page"),
    "/health": ("titik akhir mesin", "machine endpoint"),
    "/metrics": ("titik akhir mesin", "machine endpoint"),
    "/monitor": ("artefak cetak, dibebaskan dari aturan seluler",
                 "print artefact, exempt from the mobile rule"),
}

SKIP_PREFIX = {
    "/api/": ("JSON", "JSON"),
    "/static/": ("aset", "an asset"),
    "/webhook/": ("callback penyedia layanan", "a provider callback"),
    "/debug/": ("internal yang dijaga login, bukan halaman yang dibaca peran mana pun",
                "an internal guarded on login, not a page a role reads"),
    "/loaderio-": ("verifikasi uptime", "uptime verification"),
    "/print/": ("artefak cetak", "print artefact"),
    "/teacher/results/print": ("artefak cetak", "print artefact"),
    "/teacher/export/": ("unduhan berkas", "a file download"),
    "/students/export": ("unduhan berkas", "a file download"),
    "/students/template": ("unduhan berkas", "a file download"),
    "/admin/students/export": ("unduhan berkas", "a file download"),
    "/admin/teachers/export": ("unduhan berkas", "a file download"),
    "/admin-sekolah/export": ("unduhan berkas", "a file download"),
    "/admin-sekolah/download-template/": ("unduhan berkas", "a file download"),
    "/super-admin/demo-settings/data": ("JSON untuk formulir pengaturan",
                                        "JSON for the settings form"),
    "/admin/school/data": ("JSON untuk formulir pengaturan", "JSON for the settings form"),
    "/api/account/": ("JSON untuk panel hak subjek data", "JSON for the rights panel"),
    "/admin-sekolah/payment/": ("butuh id transaksi di query", "needs a transaction id in the query"),
    # JSON endpoints that hang off a role's own prefix rather than /api/, which is
    # exactly where a "just list every route" approach goes wrong: the URL looks
    # like a page.
    "/teacher/api/": ("JSON (status wizard AI)", "JSON (the AI wizard's status)"),
    "/admin-sekolah/generate-email": ("JSON (pratinjau email untuk formulir impor)",
                                      "JSON (an email preview for the import form)"),
    "/exam/": ("cetak biru ujian lama menjawab JSON", "the legacy exam blueprint answers JSON"),
}

# A route with a `<placeholder>` has no URL a frame can load. It is not a
# non-page, so it gets its own reason rather than being dropped in silence.
NEEDS_AN_ID = ("butuh id di URL — buka dari halaman daftarnya",
               "needs an id in the URL — open it from its list page")

# Which section a page belongs to, in the order a viewer wants them: the public
# front door first, then each role's own pages. `None` means an exact match.
SECTION_RULES = (
    ("public", ("Publik", "Public"), (
        (None, "/"), (None, "/pricing"), (None, "/privacy"), (None, "/terms"),
        (None, "/demo"), (None, "/capacity"),
        ("/guide/", None), ("/tutorial/", None),
    )),
    ("auth", ("Pintu masuk", "Entry"), (("/auth/", None),)),
    ("student", ("Murid", "Student"), (("/student/", None), ("/wb/student", None))),
    ("teacher", ("Guru", "Teacher"), (("/teacher/", None), ("/wb/teacher", None))),
    ("admin", ("Admin Sekolah", "School admin"), (
        ("/admin-sekolah/", None), ("/admin/", None), ("/students/", None),
    )),
    ("super", ("Super Admin", "Super admin"), (("/super-admin/", None),)),
    ("tools", ("Alat", "Tools"), (("/tools/", None),)),
)

# The tail of the list. A page that matches no rule above still shows up here,
# because a page missing from the preview is a page nobody looks at.
FALLBACK = ("other", ("Lainnya", "Other"))

ACRONYMS = {
    "ai": "AI", "api": "API", "csv": "CSV", "omr": "OMR", "pdf": "PDF",
    "pdp": "PDP", "xlsx": "XLSX", "wb": "Whiteboard", "uu": "UU",
}


def _words(slug: str) -> str:
    """``ai-settings`` -> ``AI settings``."""
    return " ".join(ACRONYMS.get(w.lower(), w.capitalize())
                    for w in re.split(r"[-_]", slug) if w)


def page_label(url: str, depth: int = 1) -> str:
    """``/teacher/ai-settings`` -> ``AI settings``.

    Derived rather than typed, for the same reason the list is: a new page must
    arrive with a readable name and no edit here. *depth* says how many trailing
    segments make the name — two when the last one alone is ambiguous
    (``/admin/dashboard`` is "Admin / Dashboard").
    """
    parts = [p for p in (url or "").split("/") if p]
    if not parts:
        return "Landing"
    names = [_words(part) for part in parts[-max(1, depth):]]
    return " / ".join(n for n in names if n) or "Landing"


def _unique_labels(pages: list) -> list:
    """Every page gets a name that is unique *inside its section*.

    The last URL segment alone gave the school-admin section two buttons reading
    "Dashboard" (`/admin/dashboard` and `/admin-sekolah/dashboard`) and two
    reading "Import" — and the only thing telling them apart was a `title`
    tooltip, which a phone cannot hover. So a name that collides is extended with
    the segment before it: "Admin / Dashboard". Only the colliding pages pay the
    length; a page whose name is already unique keeps its short one.
    """
    remaining = list(pages)
    depth = 1
    while remaining and depth <= 4:
        counts = {}
        for page in remaining:
            page["label"] = page_label(page["url"], depth)
            key = (page["section"], page["label"])
            counts[key] = counts.get(key, 0) + 1
        remaining = [p for p in remaining
                     if counts[(p["section"], p["label"])] > 1]
        depth += 1
    return pages


def skip_reason(url: str):
    """Why this URL is not a page as an ``(id, en)`` pair, or ``None`` when it is one."""
    if url in SKIP_EXACT:
        return SKIP_EXACT[url]
    for prefix, reason in SKIP_PREFIX.items():
        if url.startswith(prefix):
            return reason
    return None


def _section_for(url: str):
    for section_id, names, rules in SECTION_RULES:
        for prefix, exact in rules:
            if prefix is not None and url.startswith(prefix):
                return section_id, names
            if exact is not None and url == exact:
                return section_id, names
    return FALLBACK


def preview_pages(app) -> list:
    """Every parameterless GET page, grouped, in a stable order.

    Parameterless on purpose: a frame needs a URL it can load, and
    ``/teacher/exams/<id>`` has no id to give it. Those pages are reached through
    the list page they belong to, which is in the list.
    """
    seen = set()
    pages = []
    for rule in app.url_map.iter_rules():
        if "GET" not in (rule.methods or set()):
            continue
        url = str(rule.rule)
        if "<" in url or ">" in url:
            continue
        if url in seen or skip_reason(url):
            continue
        seen.add(url)
        section_id, names = _section_for(url)
        pages.append({
            "section": section_id,
            "section_en": names[1],
            "section_id_name": names[0],
            "url": url,
            "label": page_label(url),
        })
    pages.sort(key=lambda p: (p["section"], p["url"]))
    # After the sort, so two pages that collide are still the ones relabelled.
    return _unique_labels(pages)


def preview_sections(app) -> list:
    """The pages above, grouped into the sections the page renders as tabs."""
    order = list(chain((rule[0] for rule in SECTION_RULES), (FALLBACK[0],)))
    names = {rule[0]: rule[1] for rule in SECTION_RULES}
    names[FALLBACK[0]] = FALLBACK[1]
    grouped = {sid: [] for sid in order}
    for page in preview_pages(app):
        grouped.setdefault(page["section"], []).append(page)
    return [
        {"id": sid, "en": names[sid][1], "id_name": names[sid][0], "pages": grouped[sid]}
        for sid in order if grouped.get(sid)
    ]


def preview_exclusions(app) -> list:
    """Every URL this preview does *not* render, with the reason.

    A preview is a claim about what was looked at, so the URLs it leaves out are
    part of the page rather than a comment in this file: an omission nobody can
    read is how a preview starts lying by accident. The `/` entry in `SKIP_EXACT`
    has `None` as its reason (it means "this one is never skipped"), so it is not
    an exclusion and drops out here.
    """
    seen = set()
    exclusions = []
    for rule in app.url_map.iter_rules():
        if "GET" not in (rule.methods or set()):
            continue
        url = str(rule.rule)
        if url in seen:
            continue
        reason = NEEDS_AN_ID if ("<" in url or ">" in url) else skip_reason(url)
        if not reason:
            continue
        seen.add(url)
        exclusions.append({"url": url, "reason_id": reason[0], "reason_en": reason[1]})
    exclusions.sort(key=lambda e: e["url"])
    return exclusions
