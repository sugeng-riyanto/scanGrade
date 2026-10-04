"""Two caches whose only job is to stop paying for the same row twice.

A page in this app issues up to a dozen Supabase round-trips, and each one costs
roughly 100-165 ms from this deployment, so the cheapest performance work
available is *not making the same query again*. Two lifetimes are needed:

``memo()`` — this request only.
    Two code paths on one page want the same row (the dashboard read the
    student's profile three times, and counted teacher assignments twice), and
    the second read must not be another round-trip. The store lives in ``g`` and
    an ``after_request`` hook drops it, so one request's data cannot bleed into
    the next. The cache this replaces keyed a *module-level* dict by
    ``id(request)`` — which grew without bound in a long-lived worker and could
    hand a request another request's row whenever CPython reused the address.

``ttl()`` — a couple of minutes, across requests, and across workers when Redis
    is up. For rows that are identical for everyone in a school: its name, its
    logo, whether the whiteboard feature is on, the list of its classes. Five
    hundred students then cost one query per TTL instead of five hundred — and
    500 of anything is where a 1 vCPU box runs out of room.

Both readers fail open. If the cache is unreachable or raises, the caller simply
does the real query, so a cache problem costs latency and never breaks a page.

Anything that *writes* one of these rows must call the matching ``invalidate_*``
so the next reader sees it. The TTLs here are minutes, which is too long for a
super admin toggling a feature flag to wait out.
"""
import logging

from flask import g, has_request_context

from app.utils.kv_cache import cache_delete, cache_get, cache_set

logger = logging.getLogger(__name__)

# How long a row that is the same for a whole school may be reused. Long enough
# that a classroom full of students shares one query, short enough that an
# administrative change is not stuck behind it. Writes invalidate explicitly.
SCHOOL_TTL = 120
CLASS_TTL = 300
ASSIGNMENT_TTL = 60


def memo(key, factory):
    """Run ``factory`` at most once per request for ``key``."""
    if not has_request_context():
        return factory()
    store = getattr(g, "_req_memo", None)
    if store is None:
        store = {}
        g._req_memo = store
    if key not in store:
        store[key] = factory()
    return store[key]


def ttl(key, seconds, factory):
    """``factory``'s value for ``seconds``, shared by every request that asks."""
    try:
        cached = cache_get(key)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("req_cache get failed for %s: %s", key, e)
        cached = None
    if cached is not None:
        return cached
    value = factory()
    try:
        cache_set(key, value, seconds)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("req_cache set failed for %s: %s", key, e)
    return value


def invalidate(*keys):
    """Drop cached rows by key (call from every writer of those rows)."""
    for key in keys:
        try:
            cache_delete(key)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("req_cache delete failed for %s: %s", key, e)


# ── The rows a whole school shares ──────────────────────────────────────────


def school_key(school_id):
    return f"school:{school_id}"


def school_row(school_id, columns="name, npsn, logo_url, features, status"):
    """One school row, reused by every student and teacher in it.

    Header text, the logo and the whiteboard feature flag all come from this one
    row, so the three separate selects the pages used to make collapse into one
    query per school per TTL.
    """
    if not school_id:
        return {}

    def load():
        from app.utils.supabase_client import get_supabase
        try:
            return (get_supabase().table("schools").select(columns)
                    .eq("id", school_id).single().execute().data) or {}
        except Exception as e:
            logger.debug("school_row(%s) failed: %s", school_id, e)
            return {}

    return ttl(school_key(school_id), SCHOOL_TTL, load)


def school_features(school_id):
    """The feature flags out of the school row, always as a dict."""
    import json

    feats = (school_row(school_id) or {}).get("features") or {}
    if isinstance(feats, str):
        try:
            feats = json.loads(feats)
        except (ValueError, TypeError):
            feats = {}
    return feats if isinstance(feats, dict) else {}


def invalidate_school(school_id):
    invalidate(school_key(school_id), subject_count_key(school_id),
               school_classes_key(school_id), school_subjects_key(school_id))


def class_key(class_id):
    return f"class:{class_id}"


def class_row(class_id, columns="name, grade_level"):
    """A class row, shared by every student in that class."""
    if not class_id:
        return {}

    def load():
        from app.utils.supabase_client import get_supabase
        try:
            return (get_supabase().table("classes").select(columns)
                    .eq("id", class_id).single().execute().data) or {}
        except Exception as e:
            logger.debug("class_row(%s) failed: %s", class_id, e)
            return {}

    return ttl(class_key(class_id), CLASS_TTL, load)


def invalidate_class(class_id):
    invalidate(class_key(class_id), class_subjects_key(class_id))


def subject_count_key(school_id):
    return f"subjcount:{school_id}"


def school_subject_count(school_id):
    """How many subjects a school has assigned to teachers, for the dashboard.

    The page asked this question twice with the *same* filter (the second result
    overwrote the first), and it is the same number for every student in the
    school — so it belongs in the shared cache, not in the request.
    """
    if not school_id:
        return 0

    def load():
        from app.utils.supabase_client import get_supabase
        try:
            res = (get_supabase().table("teacher_assignments")
                   .select("id", count="exact").eq("school_id", school_id).execute())
            return res.count or 0
        except Exception as e:
            logger.debug("school_subject_count(%s) failed: %s", school_id, e)
            return 0

    return ttl(subject_count_key(school_id), ASSIGNMENT_TTL, load)


def invalidate_subject_count(school_id):
    invalidate(subject_count_key(school_id))


# What a teacher opening a whiteboard changes, and how long a class may keep
# showing the old list. Short: a board appearing on the class dashboard is the
# point of the feature, and the page itself carries a 30 s Cache-Control.
BOARD_TTL = 30


def boards_key(class_id):
    return f"boards:{class_id}"


def active_whiteboards_for(class_id, school_id):
    """The class's live whiteboards — one query for the whole class."""
    if not class_id or not school_id:
        return []

    def load():
        from app.utils.supabase_client import get_supabase
        try:
            return (get_supabase().table("whiteboards")
                    .select("id,title,status,created_at")
                    .eq("class_id", class_id)
                    .eq("school_id", school_id)
                    .eq("status", "active")
                    .order("created_at", desc=True)
                    .limit(5).execute().data) or []
        except Exception as e:
            logger.debug("active_whiteboards_for(%s) failed: %s", class_id, e)
            return []

    return ttl(boards_key(class_id), BOARD_TTL, load)


def invalidate_boards(class_id):
    invalidate(boards_key(class_id))


# A school's classes and subjects change when an admin edits them and then stay
# put for the rest of the term, which is why these may sit in the cache longer
# than a feature flag. Both are identical for every teacher in the school.


def school_classes_key(school_id):
    return f"classes:{school_id}"


def school_classes(school_id):
    """The school's classes, for the pickers a teacher page renders."""
    if not school_id:
        return []

    def load():
        from app.utils.supabase_client import get_supabase
        try:
            return (get_supabase().table("classes").select("id, name, grade_level")
                    .eq("school_id", school_id).order("name").execute().data) or []
        except Exception as e:
            logger.debug("school_classes(%s) failed: %s", school_id, e)
            return []

    return ttl(school_classes_key(school_id), CLASS_TTL, load)


def school_subjects_key(school_id):
    return f"subjects:{school_id}"


def school_subjects(school_id):
    """The school's active subjects."""
    if not school_id:
        return []

    def load():
        from app.utils.supabase_client import get_supabase
        try:
            return (get_supabase().table("subjects").select("id, name, code")
                    .eq("school_id", school_id).eq("is_active", True)
                    .order("name").execute().data) or []
        except Exception as e:
            logger.debug("school_subjects(%s) failed: %s", school_id, e)
            return []

    return ttl(school_subjects_key(school_id), CLASS_TTL, load)


def class_subjects_key(class_id):
    return f"classsubjects:{class_id}"


def closed_subjects_for_class(school_id, class_id):
    """The subject ids this class has switched **off**.

    A subject is offered by every class unless a `class_subjects` row says it is
    not (see `subject_levels.mapped_class_ids`); this reads only the closed rows
    for one class, so the answer is the short list of exceptions rather than a
    count of rows.
    """
    if not school_id or not class_id:
        return set()

    def load():
        from app.utils.supabase_client import get_supabase
        try:
            rows = (get_supabase().table("class_subjects")
                    .select("subject_id, is_active")
                    .eq("school_id", school_id).eq("class_id", class_id)
                    .execute().data) or []
            return [str(r["subject_id"]) for r in rows
                    if r.get("subject_id") and not r.get("is_active", True)]
        except Exception as e:
            logger.debug("closed_subjects_for_class(%s) failed: %s", class_id, e)
            return []

    return set(ttl(class_subjects_key(class_id), CLASS_TTL, load))


def subjects_for_class(school_id, class_id):
    """The school's active subjects that one class actually offers.

    This is the question the pupil dashboard's "Mapel" card asks: scoped to the
    pupil's own school (NPSN) and to the class the school admin put them in. The
    class a `class_subjects` row names already carries the school year
    (`classes.school_year_id`, migration 046), so the pair is year-scoped by the
    class it belongs to and needs no year of its own — which is why a pupil in a
    past year's class is counted against that class's offering, not this year's.
    """
    subjects = school_subjects(school_id)
    closed = closed_subjects_for_class(school_id, class_id)
    if not closed:
        return subjects
    return [s for s in subjects if str(s.get("id")) not in closed]


def invalidate_class_subjects(class_id):
    invalidate(class_subjects_key(class_id))


def class_teachers_key(class_id):
    return f"classteachers:{class_id}"


def _teacher_map(rows, names, year_name):
    """``{subject_id: [teacher name, …]}`` from assignment rows and an id→name map.

    Pure, so the two rules that matter can be read and tested without a database:
    the **active-pair rule** (`teacher_assignments.row_is_active` — `status` and
    the year) decides which rows name a teacher at all, and a row whose teacher
    has no resolved name is dropped rather than printing a blank beside the
    subject. Two teachers on one subject are both named, once each.
    """
    from app.services import teacher_assignments as ta
    out: dict[str, list[str]] = {}
    for row in rows or []:
        if not ta.row_is_active(row, year_name):
            continue
        subject_id = row.get("subject_id")
        teacher_id = row.get("teacher_id")
        if not subject_id or not teacher_id:
            continue
        name = names.get(str(teacher_id))
        if not name:
            continue
        bucket = out.setdefault(str(subject_id), [])
        if name not in bucket:
            bucket.append(name)
    for bucket in out.values():
        bucket.sort()
    return out


def teachers_for_class(school_id, class_id):
    """The teacher assigned to each subject one class takes, in one cached read.

    The pupil dashboard names the teacher beside each subject, and every pupil in
    the class needs the same answer — so this is read once per class (like
    `subjects_for_class`) rather than once per pupil. The pairs are the admin's
    own `teacher_assignments` rows for this school and class, filtered by the one
    active-pair rule the rest of the app uses, and the names come from a single
    `profiles` read. Fails to an empty map, which the card reads as "no teacher
    named yet" rather than a blank.
    """
    if not school_id or not class_id:
        return {}

    def load():
        from app.utils.supabase_client import get_supabase
        from app.services import teacher_assignments as ta
        try:
            supabase = get_supabase()
            rows = (supabase.table("teacher_assignments")
                    .select("teacher_id, subject_id, status, school_year")
                    .eq("school_id", school_id).eq("class_id", class_id)
                    .execute().data) or []
            year = ta.active_school_year(supabase, school_id)
            teacher_ids = sorted({str(r["teacher_id"]) for r in rows
                                  if r.get("teacher_id")})
            names = {}
            if teacher_ids:
                profs = (supabase.table("profiles").select("id, full_name")
                         .in_("id", teacher_ids).execute().data) or []
                names = {str(p["id"]): (p.get("full_name") or "") for p in profs}
            return _teacher_map(rows, names, (year or {}).get("name"))
        except Exception as e:
            logger.debug("teachers_for_class(%s) failed: %s", class_id, e)
            return {}

    return ttl(class_teachers_key(class_id), ASSIGNMENT_TTL, load)


def teacher_assignments_key(teacher_id, school_id):
    return f"tassign:{teacher_id}:{school_id}"


def teacher_assignments_for(teacher_id, school_id):
    """One teacher's **active** class+subject assignments, names embedded.

    Filtered by the one rule the rest of the app uses
    (`teacher_assignments.row_is_active`): `status='active'` and the active school
    year. Reading every row — as this did — put a pair the admin had **removed**
    (soft-closed `inactive`) back on the teacher's own dashboard, which is the
    exact opposite of what the admin's save meant.
    """
    if not teacher_id or not school_id:
        return []

    def load():
        from app.utils.supabase_client import get_supabase
        from app.services import teacher_assignments as ta
        try:
            supabase = get_supabase()
            rows = (supabase.table("teacher_assignments")
                    .select("*, classes(id, name, grade_level), subjects(id, name, code)")
                    .eq("teacher_id", teacher_id)
                    .eq("school_id", school_id)
                    .execute().data) or []
            year = ta.active_school_year(supabase, school_id)
            year_name = (year or {}).get("name")
            return [r for r in rows if ta.row_is_active(r, year_name)]
        except Exception as e:
            logger.debug("teacher_assignments_for(%s) failed: %s", teacher_id, e)
            return []

    return ttl(teacher_assignments_key(teacher_id, school_id), ASSIGNMENT_TTL, load)


def invalidate_teacher_assignments(teacher_id, school_id, class_ids=None):
    """Call from every writer of a teacher_assignments row.

    An added or removed assignment moves three cached things: the teacher's own
    list, the school's subject count that every student dashboard reads, and the
    per-class teacher map the pupil's card names subjects from. A teacher who has
    just been assigned a class must see it immediately, so the write invalidates
    rather than waiting out the TTL. `class_ids` is optional because some callers
    (a teacher editing their own list) know only the teacher; those rely on the
    short TTL, while the admin's matrix editor passes the classes it touched.
    """
    if teacher_id and school_id:
        invalidate(teacher_assignments_key(teacher_id, school_id))
    if school_id:
        invalidate(subject_count_key(school_id))
    for class_id in (class_ids or []):
        if class_id:
            invalidate(class_teachers_key(class_id))
