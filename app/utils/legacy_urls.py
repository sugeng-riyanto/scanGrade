"""The admin URLs that moved, and the page that owns each of them now.

`/admin/*` is the panel this app had before it served more than one school: it
reads `profiles`, `classes` and `exams` with no school filter, and it renders the
`admin/*.html` layout the school admin had before `/admin-sekolah` existed. Both
prefixes stayed routable, so `/tools/device-preview` listed the same page twice —
two "Dashboard" buttons in the school-admin section, two "Students", two
"Teachers", two "Classes", two "Messages". A human reading the preview had to
hover a `title` tooltip to tell them apart, and a phone cannot hover.

**One page, one URL.** The legacy paths answer **308 Permanent Redirect**, which
matters because it *preserves the method*: a bookmarked form POST is re-sent to
the page that owns it instead of being turned into a GET that drops its body.

The canonical side is the role prefix that owns the content:

* `/admin-sekolah/*` for the pages that are school-scoped (classes, students,
  teachers, the school profile) — that is also the prefix every navigation link
  and the deploy smoke test already use;
* `/super-admin/*` for the pages that read the whole platform (the global
  dashboard, all users, all exams, messages, the audit log).

This table has two readers on purpose: `create_app` registers a rule per entry,
and `app/services/device_preview.py` reads the *same* keys to exclude the legacy
paths from its list. A path therefore cannot be redirected and still be
advertised as a page — the two cannot drift, because there is only one of them.
"""

# legacy URL -> the URL that owns the page now. Every target is asserted against
# the app's own URL map by `tests/unit/test_canonical_admin_urls.py`, so a rename
# cannot leave a 308 pointing into nothing.
LEGACY_ADMIN_PAGES = {
    "/admin/dashboard":       "/super-admin/dashboard",
    "/admin/users":           "/super-admin/users",
    "/admin/exams":           "/super-admin/exams",
    "/admin/comms":           "/super-admin/comms",
    "/admin/compliance/logs": "/super-admin/logs",
    "/admin/classes":         "/admin-sekolah/classes",
    "/admin/students":        "/admin-sekolah/students",
    "/admin/teachers":        "/admin-sekolah/teachers",
    "/admin/school":          "/admin-sekolah/profile",
}

# Why the device preview does not render them, as the (Indonesian, English) pair
# that page prints. A moved URL is not a page a viewer can look at, and it is not
# silently missing either — it says where it went.
LEGACY_PAGE_REASON = (
    "URL lama — dialihkan permanen ke halaman yang sekarang memilikinya",
    "a legacy URL — permanently redirected to the page that owns it now",
)


def legacy_endpoint(path: str) -> str:
    """``/admin/compliance/logs`` -> ``legacy_admin_compliance_logs``.

    Explicit rather than derived from the closure's name: `add_url_rule` uses the
    endpoint to key `view_functions`, and nine closures all called ``move`` would
    be nine names for one function.
    """
    return "legacy_" + path.strip("/").replace("/", "_").replace("-", "_")
