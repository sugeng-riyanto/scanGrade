"""What deleting a subscription plan would take with it.

Why this is not just a count in the view
----------------------------------------
``subscription_plans`` is named by two other tables, both declared in migration 012
as ``INTEGER REFERENCES subscription_plans(id)`` with **no** ``ON DELETE`` clause:

* ``school_subscriptions.plan_id`` — which plan a school is (or was) on;
* ``payment_transactions.plan_id`` — which plan a payment attempt was for.

So the database refuses the delete outright, and the route used to hand the operator
the raw payload it raised: measured on the running box against plan 7, held by two
*pending* transactions, the page printed
``Gagal: {'code': '23503', 'details': 'Key (id)=(7) is still referenced...'}``.
Nothing said which rows held it, how many there were, or what to do instead — and the
answer that exists is not "delete anyway": it is **deactivate** the plan, which stops
offering it while leaving the ledger readable.

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

#: ``(table, key in the usage dict)`` — the tables that name a plan.
PLAN_HOLDERS = (
    ("school_subscriptions", "subscription"),
    ("payment_transactions", "payment"),
)

#: ``key -> (Indonesian, English)`` label for the sentence. Named here rather than in
#: the route so the page, the flash and the tests all say the same words.
HOLDER_LABELS = {
    "subscription": ("langganan sekolah", "school subscriptions"),
    "payment": ("transaksi pembayaran", "payment transactions"),
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
