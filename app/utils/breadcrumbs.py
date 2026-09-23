"""The trail in the topbar: where the reader is, and the way back.

The chrome has shown a breadcrumb since the layout was written, and it was built
from the URL in the template: the path split on `/`, a small dictionary of route
words, and `title()` for everything else. Four things were wrong with it, and all
four are visible in a single release.

* **The first word was the URL's prefix, not the reader.** `/teacher/analysis/<id>`
  is a page a *super admin* and a *school admin* open through the cross-role doors
  (`can_manage_exam` allows both), and the trail called all three of them
  "Teacher". It names the reader's own area now — the same area the sidebar is
  built from — so the first crumb and the menu cannot disagree about who is here.
* **The home link went to a page that does not exist any more.** It was
  `/{admin|teacher|student}/dashboard`, and `/admin/dashboard` is a *legacy* URL
  that 308s to `/super-admin/dashboard` (see `app/utils/legacy_urls.py`) — so a
  school admin's one way back ran through two redirects and a refused page before
  landing on their own dashboard. The area crumb is the reader's own dashboard.
* **Most words were not words.** `analysis`, `retractions`, `penalty-appeals`,
  `scan`, `comms` and forty more were absent from the dictionary, so the crumb was
  `title()` of the URL — an English word in an Indonesian interface, in the one
  piece of chrome that follows the language toggle. The vocabulary lives in the
  template (where the coverage gate can count it, and where every other string the
  reader sees is written); `tests/unit/test_breadcrumbs.py` holds it complete.
* **Nothing was navigable but the last page.** A trail you cannot walk back along
  is a label. A crumb is a link now — when, and only when, the path it stands for
  is a **GET page the app actually serves**. `/teacher/analysis` (the parent of
  the item analysis) is not a route, so it is a word and not a link, and
  `/teacher/results/download.csv` is a file, not a place.

What is dropped is as much of the design as what is kept: an identifier
(`len >= 32`, or six digits and up) is not a place — the trail used to print a
submission UUID in the header of every dynamic page — and neither is a machine
endpoint (`api/…`, the `*-data` readbacks, the read-status counters), an artifact
(`download*`, `*.pdf`, `*.csv`, `*.xlsx`, `*.html`), or a login door, which never
renders this chrome at all.

The words are the template's, the structure is here: this module decides *which*
crumbs a path has, where each one points, and which is the page being read.
"""

from __future__ import annotations

from typing import Any, Iterable

#: role -> (the area word's key, the page that area starts at).
#:
#: The key is what the template looks the (Indonesian, English) pair up by. The
#: four pages are the four dashboards, read from the routes that serve them rather
#: than from the sidebar's links, because this is the one place a wrong address is
#: silent: a dead crumb looks exactly like a live one.
AREAS: dict[str, tuple[str, str]] = {
    "super_admin": ("super_admin", "/super-admin/dashboard"),
    "admin_sekolah": ("admin_sekolah", "/admin-sekolah/dashboard"),
    "guru": ("guru", "/teacher/dashboard"),
    "murid": ("murid", "/student/dashboard"),
}

#: The role words. A crumb never prints one, wherever it appears in the path,
#: because the area crumb already stands for the reader's own area: at the front of
#: a role's address (`/teacher/…`), behind a module mounted under one
#: (`/wb/teacher/whiteboard`), and as the audience of a public page
#: (`/tutorial/murid`). `admin` is the legacy prefix, which 308s into the two
#: canonical ones.
PREFIXES = frozenset({"super-admin", "admin-sekolah", "admin", "teacher",
                      "student", "guru", "murid"})

#: Segments that are never a place in a trail, in four groups. Each group *is* a
#: reason, and a segment belongs to exactly one of them (a test asserts the groups
#: are disjoint) — because this list is the only thing between a new page and a
#: crumb that reads "Api" or a UUID, and a segment dropped for a reason nobody can
#: name is a segment dropped by accident.
#:
#: A *machine endpoint*: the body is JSON, an asset, or an ops probe the reader
#: reaches from a script rather than from a link.
MACHINE = frozenset({
    "api", "ai", "class", "me", "count", "read-status", "unread-count",
    "members", "slides", "snapshots", "ops", "can-annotate", "cheat-data",
    "proctoring-data", "wizard-status", "check-pdf", "contacts", "task",
    "violation", "status", "transaction", "evidence", "data", "school",
    # The box's own probes, which are read outside the app: `/monitor` parses a
    # log file, `/health` and `/metrics` answer JSON, `/static` serves assets, and
    # `/debug/exam/<id>` answers the exam row as JSON for a developer.
    "health", "metrics", "processes", "monitor", "static", "debug",
})

#: An *artifact*: a download is a file, not a page. The crumb for
#: `/teacher/results/download.xlsx` is the results page it came from, and the
#: `/r/<token>` reader publishes PDFs and spreadsheets the same way.
ARTIFACT = frozenset({
    "csv", "pdf", "xlsx", "image", "export", "export-data", "bubble-sheet",
    "template", "public", "r",
    # `/teacher/analysis/<exam>/learners.zip` is thirty children's files in one
    # archive: a second artifact with one name, dropped for the same reason
    # `download.xlsx` is — the crumb is the report page it was built from.
    "learners.zip",
})

#: A *login door*. `/auth/login` and its siblings render a different chrome
#: entirely (`content_noauth`) and never show a trail; `auth` is here because it is
#: the mount point those doors live under, so the trail has nothing to say about
#: either half of the address.
DOORS = frozenset({
    "auth", "login", "login-user", "logout", "forgot-password", "register",
    "activate", "verify-reset-code", "recover", "success", "failure",
    "reset-password",
})

#: A segment that *mounts* a module rather than naming a place: `/wb/teacher/whiteboard`
#: is the whiteboard, and `wb` is only where the module lives, so the trail reads
#: "Teacher › Whiteboard" instead of "Teacher › Wb Teacher Whiteboard".
MODULES = frozenset({"wb"})

#: A page that never renders this chrome at all: a standalone HTML document, or
#: the load tester's verification file.
NO_CHROME = frozenset({"loaderio-51ecf273210e88abe9f24d4eb2dba2a8.html"})

#: (why, which segments). The prose is the guard's documentation *and* the reason a
#: reader gets for a segment that is not a place — a test refuses an empty one.
GROUPS: tuple[tuple[str, frozenset[str]], ...] = (
    ("a machine endpoint — JSON, an asset, or an ops probe", MACHINE),
    ("a file rather than a page — the crumb is the page it came from", ARTIFACT),
    ("a login door, which renders a different chrome and shows no trail", DOORS),
    ("a module's mount point, whose page is named by the segment under it", MODULES),
    ("a document that renders no chrome at all", NO_CHROME),
)

#: Every literal segment the app serves as a GET route is either named in
#: `app/templates/base.html` or dropped by this list, and a test asserts exactly
#: that — so a new page cannot quietly arrive without a name.
IGNORED = frozenset().union(*(segments for _why, segments in GROUPS))


def _is_identifier(segment: str) -> bool:
    """A row id is not a place.

    The rule the chrome has always used: a UUID is 36 characters and a row id is
    a run of digits. Every word in the vocabulary is far shorter.
    """
    return len(segment) >= 32 or (segment.isdigit() and len(segment) >= 6)


def _ignored(segment: str) -> bool:
    return (segment in IGNORED
            or segment.startswith("download")
            or segment.startswith("loaderio-"))


def names_a_page(segments: list[str], index: int) -> bool:
    """Is the *role word* at `index` naming a page rather than the area?

    A role word is normally the area — dropped, because the area crumb already
    stands for it. In front of an identifier it is not: one route in this app uses
    it that way, `/teacher/analysis/<exam>/report/student/<student>`, and that
    page is one learner's report. Dropping the word there would leave the trail
    claiming the class report is the page being read, and offering a link back to
    it from the top of the page it is already ignoring.
    """
    return index + 1 < len(segments) and _is_identifier(segments[index + 1])


def names_a_place(segment: str) -> bool:
    """Does this segment earn a crumb?

    The single predicate the trail walks by, exposed because it is also what the
    guard reads: `tests/unit/test_breadcrumbs.py` asks it about every literal
    segment of every GET rule the app registers, and insists that each one it
    accepts is *named* in the template. A test that re-implemented the rule could
    pass while the trail quietly stopped dropping an id.

    Role words are not asked about here: `PREFIXES` drops them, and it is read
    before this in `trail()` because it is a *label* rule rather than a "is this a
    place" rule — the reader's own area, which the area crumb stands for.
    """
    return not (_is_identifier(segment) or _ignored(segment))


def _serves(path: str) -> bool:
    """Is `path` a **GET** page this app answers?

    Asked of the application's own URL map rather than of a list kept here, which
    is what makes the answer stay true: a crumb is a link only while the address
    behind it exists, and the method matters — `/teacher/exams/<id>/publish` is a
    POST, so a trail that linked to it would hand the reader a form submission.

    No application context (a template rendered outside a request) means no
    links: an unknown address is not asserted to be one.
    """
    try:
        from flask import current_app
        adapter = current_app.url_map.bind(
            current_app.config.get("SERVER_NAME") or "localhost")
    except RuntimeError:
        return False
    try:
        adapter.match(path, method="GET")
        return True
    except Exception:
        return False


def trail(path: str | None, role: str | None = None,
          segments: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """The crumbs for `path`, in order, as the chrome renders them.

    Each crumb is ``{"kind", "key", "path", "href", "current"}``: `key` is what
    the template looks a (Indonesian, English) pair up by — the role for the area
    crumb, the URL segment for a page crumb — and `href` is ``None`` for a word
    that is not a link (the page being read, and any parent the app does not serve
    as a GET page).

    *segments* is for the callers that already have them (a test, or a caller that
    parsed a path of its own); the template passes the request path.
    """
    if segments is None:
        cleaned = (path or "/").split("?", 1)[0].split("#", 1)[0]
        segments = [part for part in cleaned.strip("/").split("/") if part]

    area = AREAS.get(role or "")
    crumbs: list[dict[str, Any]] = []

    walk = ""
    pages: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        walk += "/" + segment
        if segment in PREFIXES:
            # The area, which the area crumb already stands for — unless it is the
            # one role word this app uses as a page name (`names_a_page`).
            if not names_a_page(segments, index):
                continue
        elif not names_a_place(segment):
            continue
        if area and walk == area[1]:
            # The reader's own dashboard: the area crumb *is* this page, so a
            # second word for it would be a link to where they already are.
            continue
        pages.append({"kind": "page", "key": segment, "path": walk,
                      "href": None, "current": False})

    if area:
        crumbs.append({
            "kind": "area", "key": area[0], "path": area[1],
            # The area is a link back to the reader's own dashboard *unless* the
            # dashboard is the page being read — in which case the trail is one
            # crumb long and it is the current one.
            "href": None if not pages else area[1],
            "current": not pages,
        })

    for crumb in pages:
        crumb["href"] = crumb["path"] if _serves(crumb["path"]) else None
    if pages:
        # The page being read is the deepest segment that names anything, and that
        # is the last crumb — every id, download and machine endpoint behind it is
        # dropped, and the one role word this app uses as a page name is printed.
        pages[-1]["current"] = True
        pages[-1]["href"] = None
    crumbs.extend(pages)
    return crumbs
