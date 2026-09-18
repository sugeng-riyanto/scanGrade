"""The demo surface: which items show, in what order.

`school_settings.demo_settings` is one JSON blob, and it answers two questions for
two different pages:

* **which** demo items are switched on — the four role cards on ``/demo`` and the
  three tutorial buttons in the landing page hero;
* **in what order** they appear, which a super admin now arranges on
  ``/super-admin/demo-settings``.

Both answers live here rather than in each template, because "the checkbox did not
hide it" is what happens when two pages read the same flag their own way: the
landing page read ``demo_tutorial_guru`` while `/demo` read ``demo_guru``, and a
third reading in the page being edited is a third chance to disagree. The pages now
loop over `demo_items(...)`, so a switched-off item cannot be rendered by
forgetting a flag — it is not in the list.

Two groups, not one, because the items are rendered on two pages that have nothing
else in common: `roles` is the card stack on `/demo`, `tutorials` is the button row
on the landing page. Reordering one must not shuffle the other.
"""
import json

# The items of each group, in the order they appear when nothing is stored. This
# is the *canonical* order: the one a missing, partial or hand-edited blob falls
# back to, and the one that decides where a newly added item lands.
ROLE_ITEMS = ("demo_super_admin", "demo_admin_sekolah", "demo_guru", "demo_murid")
TUTORIAL_ITEMS = ("demo_tutorial_guru", "demo_tutorial_siswa", "demo_tutorial_admin")

# group -> (json key holding its order, its canonical items, the form field the
# settings page posts that order in)
GROUPS = {
    "roles": ("role_order", ROLE_ITEMS, "role_order"),
    "tutorials": ("tutorial_order", TUTORIAL_ITEMS, "tutorial_order"),
}

# The master switch for the tutorial row. An item that has no flag of its own
# follows this one, which is how the page has always read it.
TUTORIAL_MASTER = "demo_tutorial"

_VALID_KEYS = {group: frozenset(items) for group, (_, items, _) in GROUPS.items()}


def order_key(group) -> str:
    """The json key inside the blob that holds this group's order."""
    return GROUPS[group][0]


def as_mapping(settings) -> dict:
    """The blob as a dict, whatever Supabase handed over.

    The column is jsonb, and a value written with `json.dumps` arrives as a JSON
    *string* — the same trap `question_types` and `class_ids` have each fallen
    into. A string that does not parse is treated as "nothing stored", which is
    the answer that shows the demo to everybody rather than to nobody.
    """
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except (json.JSONDecodeError, TypeError):
            settings = {}
    return settings if isinstance(settings, dict) else {}


def clean_order(settings, group) -> list[str]:
    """The stored order for this group, made safe to loop over.

    Keeps only this group's own keys (a stale name from an older release, or one
    pasted into the database, must not become a row), removes duplicates (a key
    listed twice would render its card twice), and appends whatever the stored
    order left out in canonical order — so an item added to the code later still
    appears without anyone editing a blob, and a blob that predates ordering keeps
    working exactly as it did.
    """
    _, canonical, _ = GROUPS[group]
    stored = as_mapping(settings).get(GROUPS[group][0]) or []
    if isinstance(stored, str):                    # "a,b,c" from a form field
        stored = [part.strip() for part in stored.split(",")]
    order = []
    for key in stored:
        if key in _VALID_KEYS[group] and key not in order:
            order.append(key)
    order.extend(key for key in canonical if key not in order)
    return order


def demo_order(settings, group) -> list[str]:
    """Every item of this group, in the stored order — what the settings page draws.

    Deliberately *not* `demo_items`: that one drops what is switched off, and the
    page you switch an item back on from must still contain it. Ordering and
    visibility are separate questions, and the settings page asks only the first.
    """
    return clean_order(settings, group)


def demo_link_on(settings) -> bool:
    """Is the ``Demo`` link offered? Its master switch, and unset means yes.

    The one flag that is not a row on the settings page: it is the master toggle
    at the top, which gates the whole demo surface.
    """
    return bool(as_mapping(settings).get("demo_enabled", True))


def effective_flags(settings) -> dict:
    """Every flag as the *pages* read it, which is what the settings page paints.

    The blob is sparse on purpose (a missing key means "unset", and unset has a
    different answer per group), so the checkboxes used to open blank while the
    landing page showed all three tutorial buttons — the switches disagreed with
    the thing they switch. Seeding the form from this dict makes the page the
    honest picture: what is ticked is what a visitor sees.
    """
    flags = {}
    for group, (_, items, _) in GROUPS.items():
        for key in items:
            flags[key] = is_on(settings, group, key)
    flags["demo_enabled"] = demo_link_on(settings)
    return flags


def is_on(settings, group, key) -> bool:
    """Does this item's own checkbox say so?

    An **unset** checkbox is the answer that has to be got right, and the two
    groups have always answered it differently:

    * a role card is shown when the blob says so — with one exception, the
      *empty* blob, where every demo is shown (a school that has never opened this
      page gets the full demo, which is what `{% if not ds or ds.<key> %}` meant);
    * a tutorial button follows its own flag when it has one and the master
      `demo_tutorial` when it does not.
    """
    blob = as_mapping(settings)
    if not blob:
        return True
    if group == "roles":
        return bool(blob.get(key))
    return bool(blob.get(key, blob.get(TUTORIAL_MASTER, True)))


def demo_items(settings, group) -> list[str]:
    """The visible items of this group, in the stored order.

    What the templates loop over. Nothing here decides *where* an item is drawn —
    each page still owns its own markup — only that it is drawn, and in which
    position.
    """
    return [key for key in clean_order(settings, group) if is_on(settings, group, key)]


def order_from_form(form, group) -> list[str]:
    """Read this group's order out of the settings form, sanitised.

    The page posts the row order as one comma-separated field, built from the DOM
    after the operator moved rows around — so the value is whatever a browser (or
    someone with a console) sent, not a list this code chose. Only this group's
    keys survive, in the order given; `clean_order` completes them when the page
    is read back, so this deliberately does not repeat that rule here.
    """
    _, _, field = GROUPS[group]
    raw = form.get(field, "")
    parts = [part.strip() for part in raw.split(",")] if isinstance(raw, str) else []
    seen = []
    for key in parts:
        if key in _VALID_KEYS[group] and key not in seen:
            seen.append(key)
    return seen
