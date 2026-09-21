"""Which schools are the *demo* schools.

The platform has two lives at once: a real school uses it, and the same instance
carries the seeded demo used for /demo, the tutorials and a sales walk-through.
A destructive button labelled "reset demo data" has to know the difference — it
did not, and deleted every school's rows instead (see ``reset_demo_data``).

The definition already exists: the seed in ``manage.py`` names the demo schools
and their NPSNs, and ``reset_demo_passwords`` has always read that list to know
which accounts to touch. This module exposes the same list as *NPSNs* so a
database query can scope itself to it, rather than duplicating the names in a
second place that can drift from the seed.

NPSN is the key to scope on because it is the one field an operator also types
by hand on ``/super-admin/reset-school-data`` — so "the demo schools" and "the
school you named" are addressed the same way.
"""
import logging

logger = logging.getLogger(__name__)

#: The seeded demo schools' NPSNs, used when ``manage.py`` cannot be imported
#: (a frozen deploy, or a test that stubs the app without the repo root on the
#: path). Kept in step with ``manage.DEMO_SCHOOLS`` by
#: ``tests/unit/test_demo_school_scope.py``.
FALLBACK_NPSNS = ("99887711", "99887722", "99887733")


def demo_school_npsns() -> list[str]:
    """The demo schools' NPSNs, from the seed where it can be read.

    ``manage`` is imported lazily for the same reason ``reset_demo_passwords``
    does it: the module builds the whole app at import time, so importing it
    from a request path would be circular.
    """
    try:
        from manage import DEMO_SCHOOLS
    except Exception:                       # pragma: no cover - import-shape only
        logger.debug("manage.DEMO_SCHOOLS unavailable; using the fallback NPSNs")
        return list(FALLBACK_NPSNS)
    npsns = [str(s.get("npsn")).strip() for s in DEMO_SCHOOLS if s.get("npsn")]
    return npsns or list(FALLBACK_NPSNS)
