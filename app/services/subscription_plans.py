"""What deleting a subscription plan would take with it.

Why this is not just a count in the view
----------------------------------------
``subscription_plans`` is named by **three** other tables, all declaring
``plan_id INTEGER REFERENCES subscription_plans(id)`` with **no** ``ON DELETE``
clause — two in migration 012, and a third in ``_COMPLETE_SETUP.sql``:

* ``school_subscriptions.plan_id`` — which plan a school is (or was) on;
* ``payment_transactions.plan_id`` — which plan a payment attempt was for;
* ``invoices.plan_id`` — which plan an invoice was raised for.

So the database refuses the delete outright, and the route used to hand the operator
the raw payload it raised: measured on the running box against plan 7, held by two
*pending* transactions, the page printed
``Gagal: {'code': '23503', 'details': 'Key (id)=(7) is still referenced...'}``.
Nothing said which rows held it, how many there were, or what to do instead — and the
answer that exists is not "delete anyway": it is **deactivate** the plan, which stops
offering it while leaving the ledger readable.

The third table was itself the same defect one turn later: the list held two of the
three, so a plan an invoice names read as *free* — the route skipped the detach, issued
the delete, and the database raised exactly the payload above. The list is therefore
pinned to the SQL by ``test_the_holder_list_covers_them_all``, which reads every
``REFERENCES subscription_plans`` out of ``supabase/`` rather than trusting this
comment; :func:`plan_failure_sentence` is the second line of defence for a reference
no file declares, so a refusal always names its table instead of quoting a payload.

Two decisions shape this module
-------------------------------
**A read that failed is not a count of zero.** ``delete_class`` already draws this
line ("we could not tell" and "nobody is in it" are different answers, and only the
second may be deleted without asking). Here it matters more, not less: a plan whose
holders could not be counted is exactly the plan whose delete will raise.

**The page needs this for every row, so it must not cost a query per row.** A
seven-plan catalogue would be fourteen round trips, on a box where one is ~150 ms, so
:func:`plan_usage_map` answers all of them from two queries.
"""

import re

#: ``(table, key in the usage dict)`` — every table that names a plan. Pinned to the
#: schema by a test, because a list one entry short is how the delete raised: the code
#: asked two tables, the plan was held by the third, and the FK spoke instead.
PLAN_HOLDERS = (
    ("school_subscriptions", "subscription"),
    ("payment_transactions", "payment"),
    ("invoices", "invoice"),
)

#: ``key -> (Indonesian, English)`` label for the sentence. Named here rather than in
#: the route so the page, the flash and the tests all say the same words.
HOLDER_LABELS = {
    "subscription": ("langganan sekolah", "school subscriptions"),
    "payment": ("transaksi pembayaran", "payment transactions"),
    "invoice": ("faktur", "invoices"),
}


def used_total(usage: dict) -> int:
    """How many rows hold a plan, or ``0`` when the count could not be taken.

    Deliberately numeric and blunt: callers that need to *act* on the difference
    between zero and unknown ask :func:`usage_confirmation_needed`, which does not
    flatten the two.
    """
    return sum(usage.get(key, 0) or 0 for _table, key in PLAN_HOLDERS)


def plan_usage(supabase, plan_id) -> dict:
    """``{"subscription": n, "payment": m, "measured": bool}`` for one plan.

    Counts are read with ``count="exact"`` so the rows never travel. ``measured`` is
    ``False`` when either read raised — never silently zero.
    """
    usage = {key: 0 for _table, key in PLAN_HOLDERS}
    usage["measured"] = True
    for table, key in PLAN_HOLDERS:
        try:
            res = (supabase.table(table).select("id", count="exact")
                   .eq("plan_id", plan_id).execute())
            usage[key] = res.count or 0
        except Exception:
            usage["measured"] = False
    return usage


def plan_usage_map(supabase, plan_ids) -> dict:
    """``{plan_id: usage}`` for every plan, from two queries.

    The list page draws a mark on each card, so one query per plan per table is a
    cost the operator pays on every load. Unknown or unmeasurable plans map to a
    ``measured: False`` usage rather than being absent, because a card that finds no
    entry must not read as "nothing holds this".
    """
    ids = [pid for pid in (plan_ids or []) if pid is not None]
    if not ids:
        return {}
    usage = {pid: {key: 0 for _table, key in PLAN_HOLDERS} for pid in ids}
    for pid in ids:
        usage[pid]["measured"] = True
    for table, key in PLAN_HOLDERS:
        try:
            rows = (supabase.table(table).select("plan_id")
                    .in_("plan_id", ids).execute().data or [])
        except Exception:
            for pid in ids:
                usage[pid]["measured"] = False
            continue
        for row in rows:
            pid = row.get("plan_id")
            if pid in usage:
                usage[pid][key] += 1
    return usage


def usage_confirmation_needed(usage: dict) -> bool:
    """True when the delete would break a reference — or could not be measured."""
    return used_total(usage) > 0 or not usage.get("measured", False)


#: The table a foreign-key refusal names, in either of the shapes Postgres and
#: supabase-py hand it over: ``Key (id)=(7) is still referenced from table "x".``
#: inside ``details``, or ``... on table "x"`` inside the constraint sentence.
_REFERENCED_TABLE = re.compile(r"(?:from|on)\s+table\s+[\"']?([\w.]+)", re.I)

#: A payload dict that reached us as text — the shape the old page printed.
_LOOKS_LIKE_A_PAYLOAD = re.compile(r"^\s*\{[\s\S]*'code'")


def _text_of(exc: BaseException) -> str:
    """Everything the driver said, whichever attribute or string it used."""
    parts = [str(getattr(exc, name, "") or "") for name in ("details", "message")]
    parts.append(str(exc))
    return " ".join(part for part in parts if part)


def plan_failure_sentence(exc: BaseException, lang: str = "id") -> str:
    """One sentence for a write the database refused, naming the table if it can.

    The reported failure was ``Gagal: {'code': '23503', 'details': 'Key (id)=(7) is
    still referenc`` — the payload, truncated at 60 characters, with the table it
    named cut off. A refusal this page cannot explain is worse than no page: the
    operator cannot tell a plan that is still wanted from one that is merely stuck.

    So the table is read *out of the refusal* rather than looked up in the list
    above, which means a reference no file in this repository declares is still named
    instead of becoming a mystery. Nothing here quotes a driver payload.
    """
    text = _text_of(exc)
    table = _REFERENCED_TABLE.search(text)
    if table:
        name = table.group(1).strip(".")
        if lang == "en":
            return (f"The database refused this because table {name} still references "
                    f"the plan. Detach that reference (or deactivate the plan "
                    f"instead) and try again.")
        return (f"Database menolak karena tabel {name} masih merujuk paket ini. "
                f"Lepas rujukan itu (atau nonaktifkan paketnya saja) lalu coba lagi.")

    # A driver error with no reference to read is still worth its own words — unless
    # those words are a payload, which is the thing being removed here.
    spoke = "" if _LOOKS_LIKE_A_PAYLOAD.search(text) else " ".join(text.split())[:160]
    if lang == "en":
        tail = f" The database said: {spoke}" if spoke else ""
        return ("The plan could not be saved — the database refused the change. Try "
                "again; if it keeps failing, deactivate the plan instead of "
                f"deleting it.{tail}")
    tail = f" Database berkata: {spoke}" if spoke else ""
    return ("Paket tidak bisa disimpan — database menolak perubahannya. Coba lagi; "
            "bila terus gagal, nonaktifkan paketnya alih-alih menghapusnya."
            f"{tail}")


def usage_message(usage: dict, lang: str = "id") -> str:
    """The sentence shown when a plan cannot simply be deleted.

    Carries the counts, names the two kinds of holder, and names the way out. It
    must never contain a database error: the number is what makes the next decision
    possible, and a Postgres code is not.
    """
    if not usage.get("measured", False):
        if lang == "en":
            return ("How many rows still reference this plan could not be read, so it "
                    "is not known what deleting it would detach — try again, or "
                    "deactivate the plan instead.")
        return ("Berapa baris yang masih merujuk paket ini tidak bisa dibaca, jadi "
                "tidak diketahui apa yang akan ikut terlepas bila dihapus — coba "
                "lagi, atau nonaktifkan paketnya saja.")

    parts = []
    for _table, key in PLAN_HOLDERS:
        count = usage.get(key, 0) or 0
        if not count:
            continue
        id_label, en_label = HOLDER_LABELS[key]
        label = en_label if lang == "en" else id_label
        parts.append(f"{count} {label}")

    holders = " dan ".join(parts) if lang != "en" else " and ".join(parts)
    if lang == "en":
        return (f"This plan is still referenced by {holders}. Deleting it detaches "
                f"them — the rows stay, they only lose the plan — so repeat to "
                f"confirm. To simply stop offering it, deactivate it instead.")
    return (f"Paket ini masih dirujuk {holders}. Menghapusnya akan melepas rujukan "
            f"itu — barisnya tetap ada, hanya kehilangan paketnya — ulangi untuk "
            f"menghapus. Bila hanya ingin berhenti menawarkannya, nonaktifkan saja.")
