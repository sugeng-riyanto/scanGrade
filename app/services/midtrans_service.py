import uuid
import json
import secrets
import string
from datetime import datetime, timezone, timedelta
from flask import current_app

try:
    import midtransclient
    _HAS_MIDTRANS = True
except ImportError:
    _HAS_MIDTRANS = False
    midtransclient = None


def _load_midtrans_config():
    supabase = current_app.extensions["supabase"]
    try:
        res = supabase.table("midtrans_settings").select("*").limit(1).execute()
        if res.data:
            return res.data[0]
    except Exception:
        pass
    return {}


def _load_plan(plan_id):
    supabase = current_app.extensions["supabase"]
    res = supabase.table("subscription_plans").select("*").eq("id", plan_id).single().execute()
    return res.data if res.data else None


def generate_order_id(school_id):
    ts = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
    short = school_id[:8] if school_id else "XXXX"
    return f"SG-{short}-{ts}-{secrets.randbelow(9000)+1000}"


def generate_activation_code():
    chars = string.ascii_uppercase + string.digits
    return "SG-" + "-".join("".join(secrets.choice(chars) for _ in range(4)) for _ in range(3))


def create_snap_transaction(school_id, plan_id, school_name, school_email):
    if not _HAS_MIDTRANS:
        return None, "Paket midtransclient belum terinstall. Jalankan: pip install midtransclient"

    cfg = _load_midtrans_config()
    if not cfg or not cfg.get("server_key") or not cfg.get("client_key"):
        return None, "Midtrans belum dikonfigurasi oleh Super Admin"

    plan = _load_plan(plan_id)
    if not plan:
        return None, "Plan tidak ditemukan"
    if plan.get("duration_days", 0) == 0:
        label = "Selamanya"
    else:
        label = plan.get("duration_label", f"{plan['duration_days']} Hari")

    snap = midtransclient.Snap(
        is_production=cfg.get("is_production", False),
        server_key=cfg["server_key"],
        client_key=cfg["client_key"],
    )

    order_id = generate_order_id(school_id)
    base_price = int(float(plan["price"]))
    adjusted_price, tier_label = calculate_plan_price(base_price, school_id)
    total_with_fee, fee_info = calculate_total_with_fee(adjusted_price)
    gross = total_with_fee

    item_name = f"ScanGrade - {plan['name']}"
    if fee_info["fee_flat"] > 0 or fee_info["fee_percent"] > 0:
        item_name += f" + biaya admin"

    param = {
        "transaction_details": {
            "order_id": order_id,
            "gross_amount": gross,
        },
        "item_details": [
            {
                "id": str(plan_id),
                "price": adjusted_price,
                "quantity": 1,
                "name": item_name,
            },
        ],
        "customer_details": {
            "first_name": school_name[:32] if school_name else "Sekolah",
            "email": (school_email or "").strip() or "srphysics04@gmail.com",
        },
    }

    # Add fee as a separate line item if any
    fee_total = fee_info["fee_percent_amount"] + fee_info["fee_flat"]
    if fee_total > 0:
        param["item_details"].append({
            "id": "admin_fee",
            "price": fee_total,
            "quantity": 1,
            "name": f"Biaya admin ({fee_info['note']})",
        })

    try:
        response = snap.create_transaction(param)
    except Exception as e:
        current_app.logger.error(f"Midtrans create_transaction error: {e}")
        return None, f"Gagal membuat transaksi: {str(e)}"

    token = response.get("token", "")
    redirect_url = response.get("redirect_url", "")

    supabase = current_app.extensions["supabase"]
    supabase.table("payment_transactions").insert({
        "school_id": school_id,
        "plan_id": plan_id,
        "order_id": order_id,
        "gross_amount": gross,
        "status": "pending",
        "snap_token": token,
        "snap_redirect_url": redirect_url,
    }).execute()

    return {
        "token": token,
        "redirect_url": redirect_url,
        "order_id": order_id,
        "gross_amount": gross,
        "base_amount": adjusted_price,
        "fee_info": fee_info,
    }, None


#: Fields `handle_payment_notification` reads off a parsed Midtrans status. The
#: simulation path builds a status with exactly these, so both paths hand the rest
#: of the function the same shape.
_STATUS_FIELDS = ("order_id", "transaction_status", "fraud_status", "payment_type",
                  "transaction_time", "settlement_time", "va_numbers",
                  "permata_va_number", "bill_key", "biller_code", "store",
                  "payment_code")


def _simulation_enabled():
    """True only where a payment may be faked: tests and staging. Never here —
    ``ProductionConfig`` sets ``PAYMENT_SIMULATION = False`` as a class attribute,
    so no `.env` on a live box can turn it on."""
    return bool(current_app.config.get("PAYMENT_SIMULATION"))


def _simulated_status(notification_dict):
    """A parsed-notification status built from the callback itself.

    A real Midtrans notification carries the same fields the parse returns, so
    simulation only replaces the network round-trip, not the decisions taken on
    its result.
    """
    return {key: notification_dict.get(key) for key in _STATUS_FIELDS}


def handle_payment_notification(notification_dict):
    if _simulation_enabled():
        status = _simulated_status(notification_dict)
    else:
        if not _HAS_MIDTRANS:
            current_app.logger.error("midtransclient not installed")
            return False

        cfg = _load_midtrans_config()
        if not cfg or not cfg.get("server_key"):
            current_app.logger.error("Midtrans not configured for notification handling")
            return False

        snap = midtransclient.Snap(
            is_production=cfg.get("is_production", False),
            server_key=cfg["server_key"],
            client_key=cfg.get("client_key", ""),
        )

        try:
            status = snap.transaction.notification(notification_dict)
        except Exception as e:
            current_app.logger.error(f"Midtrans notification parse error: {e}")
            return False

    order_id = status.get("order_id", "")
    transaction_status = status.get("transaction_status", "")
    fraud_status = status.get("fraud_status", "")
    payment_type = status.get("payment_type", "")
    transaction_time = status.get("transaction_time", "")
    settlement_time = status.get("settlement_time", "")

    current_app.logger.info(f"Midtrans notification: order={order_id}, status={transaction_status}, fraud={fraud_status}")

    supabase = current_app.extensions["supabase"]

    tx_res = supabase.table("payment_transactions").select("*").eq("order_id", order_id).limit(1).execute()
    if not tx_res.data:
        current_app.logger.warning(f"Transaction not found: {order_id}")
        return False
    tx = tx_res.data[0]

    is_success = (transaction_status == "settlement" or transaction_status == "capture") and fraud_status != "deny"
    is_expired = transaction_status == "expire"
    is_failed = transaction_status in ("deny", "cancel", "failure")

    # Midtrans retries a notification until it is acknowledged, so the same
    # settlement can arrive more than once. Activating again would retire the just-made
    # subscription (`status='replaced'`), write a second invoice and mint a second
    # activation code — for one payment. The transaction's own prior status is the
    # idempotency key, read before this notification overwrites it.
    already_settled = tx.get("status") == "success"

    # Store payment details (VA numbers, etc.)
    details = {}
    va = status.get("va_numbers")
    if va:
        details["va_numbers"] = va
    permata_va = status.get("permata_va_number")
    if permata_va:
        details["permata_va"] = permata_va
    bill_key = status.get("bill_key")
    if bill_key:
        details["bill_key"] = bill_key
        details["biller_code"] = status.get("biller_code", "")
    store = status.get("store")
    if store:
        details["store"] = store
    payment_code = status.get("payment_code")
    if payment_code:
        details["payment_code"] = payment_code

    update_data = {
        "status": "success" if is_success else ("expired" if is_expired else "failure"),
        "payment_type": payment_type,
        "payment_details": details,
    }
    if transaction_time:
        try:
            dt = datetime.fromisoformat(transaction_time.replace("Z", "+00:00"))
            update_data["transaction_time"] = dt.isoformat()
        except:
            pass
    if settlement_time:
        try:
            dt = datetime.fromisoformat(settlement_time.replace("Z", "+00:00"))
            update_data["settlement_time"] = dt.isoformat()
        except:
            pass

    supabase.table("payment_transactions").update(update_data).eq("id", tx["id"]).execute()

    if is_success and not already_settled:
        _activate_subscription(tx["school_id"], tx["plan_id"], order_id, supabase)
    elif is_success and already_settled:
        current_app.logger.info(
            "Duplicate settlement for order %s ignored - already activated", order_id)

    return True


def _activate_subscription(school_id, plan_id, order_id, supabase):
    plan = _load_plan(plan_id) if plan_id else None
    code = generate_activation_code()
    now = datetime.now(timezone.utc)

    # What the school bought, written down rather than left for the next reader to
    # guess. Without this the row carries `plan_id=None`/`tier='trial'` and
    # `get_tier_for_school` caps a paying school at the free trial's 5 exams.
    from app.services.subscription_service import (CASH_DEFAULT_DAYS,
                                                   subscription_end,
                                                   tier_for_duration_days,
                                                   tier_for_manual_activation)

    # One expiry rule for both write paths (`subscription_service.subscription_end`):
    # the plan's own length, the documented year for an activation that named no
    # plan, and no end at all for `0` (Selamanya).
    plan_days = plan.get("duration_days") if plan else None
    duration_days = plan_days if plan_days is not None else CASH_DEFAULT_DAYS
    sub_end = subscription_end(now, plan_days)
    tier = (tier_for_duration_days(plan_days) if plan_days is not None
            else tier_for_manual_activation())

    # Update payment transaction with activation code
    supabase.table("payment_transactions").update({
        "activation_code": code,
    }).eq("order_id", order_id).execute()

    # Deactivate any existing active subscription for this school
    supabase.table("school_subscriptions").update({
        "status": "replaced",
    }).eq("school_id", school_id).eq("status", "active").execute()

    # Insert new subscription
    sub_res = supabase.table("school_subscriptions").insert({
        "school_id": school_id,
        "plan_id": plan_id,
        "tier": tier,
        "status": "active",
        "subscription_start": now.isoformat(),
        "subscription_end": sub_end.isoformat() if sub_end else None,
        "activation_code": code,
    }).execute()

    # Activate school
    supabase.table("schools").update({"status": "active"}).eq("id", school_id).execute()

    # The write gate caches "is this school active" — drop it so the school can
    # write immediately instead of waiting out the cache TTL.
    invalidate_school_active(school_id)

    # Send activation email to admin
    from app.services import smtp_settings
    try:
        admin = supabase.table("profiles").select("id, full_name, phone").eq("school_id", school_id).eq("role", "admin_sekolah").limit(1).execute().data
        if admin:
            admin_email = None
            try:
                au = current_app.extensions["supabase_auth"].admin.get_user_by_id(admin[0]["id"])
                admin_email = au.user.email
            except:
                pass
            recovery_email = admin[0].get("phone", "")
            recipient = recovery_email if "@" in recovery_email else admin_email
            if recipient:
                # One body for every user-facing mail (`app/services/email_bodies.py`).
                # The receipt used to be a bare paragraph with the activation code
                # buried in prose; it is the one mail a school may forward to its own
                # finance office, so it is laid out like a receipt and bilingual.
                from app.services import email_bodies

                mail = email_bodies.payment_success(
                    name=admin[0].get("full_name") or "Admin Sekolah",
                    plan_name=(plan.get("name") if plan else None) or "Langganan",
                    starts=now.strftime("%d %B %Y"),
                    ends=sub_end.strftime("%d %B %Y") if sub_end else "Selamanya",
                    login_url="https://scangrade.web.id/admin-sekolah/dashboard")
                ok, err = smtp_settings.send(
                    recipient, mail["subject"], mail["html"], html=True,
                    text=mail["text"], important=True)
                if ok:
                    current_app.logger.info(f"Activation email sent to {recipient}")
                else:
                    current_app.logger.warning(f"Activation email not sent to {recipient}: {err}")
    except Exception as e:
        current_app.logger.error(f"Failed to send activation email: {e}")

    # The receipt and the log line are side effects of the activation, and both
    # used to sit *after* `return code` — so no activation ever left one behind.
    # Production held three invoices, all demo fixtures; a school that paid could
    # not show its finance office what it paid for. The invoice is written here,
    # before the return, and a failure to write it is logged, never raised: the
    # school is already activated and must not be rolled back over a receipt.
    try:
        _generate_invoice(supabase, school_id, plan_id, order_id, now, duration_days)
    except Exception as e:
        current_app.logger.error(f"Invoice creation error: {e}")

    current_app.logger.info(f"Subscription activated for school {school_id}, plan={plan_id}, code={code}")

    return code


def _generate_invoice(supabase, school_id, plan_id, order_id, now, duration_days):
    """Generate an invoice for a successful payment or cash activation."""
    import random
    ts = now.strftime("%y%m%d%H%M%S")
    inv_num = f"INV-{now.year}-{ts}-{random.randint(100,999)}"

    tx = supabase.table("payment_transactions").select("gross_amount, payment_type, status, activation_code").eq("order_id", order_id).single().execute()
    tx_data = tx.data or {}
    amount = tx_data.get("gross_amount") or 0
    payment_method = tx_data.get("payment_type") or "cash"
    activation_code = tx_data.get("activation_code") or ""

    plan_name = ""
    if plan_id:
        p = supabase.table("subscription_plans").select("name, duration_days").eq("id", plan_id).single().execute()
        if p.data:
            plan_name = p.data.get("name", "")
            duration_days = p.data.get("duration_days", duration_days)

    period_end = now + timedelta(days=duration_days) if duration_days > 0 else None

    supabase.table("invoices").insert({
        "invoice_number": inv_num,
        "school_id": school_id,
        "order_id": order_id,
        "plan_id": plan_id,
        "amount": amount,
        "status": "paid",
        "payment_method": payment_method,
        "period_start": now.isoformat(),
        "period_end": period_end.isoformat() if period_end else None,
        "paid_at": now.isoformat(),
        "due_at": (now + timedelta(days=7)).isoformat(),
        "notes": f"Langganan {plan_name}" if plan_name else "Aktivasi akun",
        "activation_code": activation_code,
    }).execute()


def get_school_subscription(school_id):
    supabase = current_app.extensions["supabase"]
    res = supabase.table("school_subscriptions") \
        .select("*, subscription_plans(name, duration_label, duration_days)") \
        .eq("school_id", school_id) \
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()
    if res.data:
        sub = res.data[0]
        # Check if expired
        if sub["status"] == "active" and sub.get("subscription_end"):
            end = sub["subscription_end"]
            if isinstance(end, str):
                end = datetime.fromisoformat(end[:19] if "T" in end else end)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            if end < datetime.now(timezone.utc):
                supabase.table("school_subscriptions").update({"status": "expired"}).eq("id", sub["id"]).execute()
                sub["status"] = "expired"
        # Check trial end
        elif sub["status"] == "trial" and sub.get("trial_end"):
            end = sub["trial_end"]
            if isinstance(end, str):
                end = datetime.fromisoformat(end[:19] if "T" in end else end)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            if end < datetime.now(timezone.utc):
                supabase.table("school_subscriptions").update({"status": "trial_expired"}).eq("id", sub["id"]).execute()
                sub["status"] = "trial_expired"
        return sub
    return None


def get_pricing_config():
    supabase = current_app.extensions["supabase"]
    try:
        res = supabase.table("school_settings").select("pricing_config").eq("id", 1).single().execute()
        if res.data and res.data.get("pricing_config"):
            return res.data["pricing_config"]
    except Exception:
        pass
    return {"model": "flat", "tiers": []}


def get_student_count_for_school(school_id):
    supabase = current_app.extensions["supabase"]
    try:
        res = supabase.table("profiles").select("id", count="exact").eq("role", "murid").eq("school_id", school_id).execute()
        return res.count or 0
    except Exception:
        return 0


def calculate_plan_price(plan_base_price, school_id=None):
    """Adjust plan price based on active pricing model.
    Returns (adjusted_price, pricing_label)."""
    config = get_pricing_config()
    if config.get("model") != "scaled" or not school_id:
        return plan_base_price, "flat"

    student_count = get_student_count_for_school(school_id)
    tiers = config.get("tiers", [])
    if not tiers:
        return plan_base_price, "flat"

    # Find matching tier
    multiplier = 1.0
    tier_name = ""
    for t in sorted(tiers, key=lambda x: x.get("min", 0)):
        t_min = t.get("min", 0)
        t_max = t.get("max", 999999)
        if t_min <= student_count <= t_max:
            multiplier = float(t.get("multiplier", 1.0))
            tier_name = t.get("name", "")
            break

    adjusted = round(plan_base_price * multiplier, -3)  # round to nearest 1000
    return max(adjusted, plan_base_price), tier_name


def invalidate_school_active(school_id):
    """Forget a cached subscription gate — call whenever a school's
    subscription changes (payment webhook, admin approval) so the write gate
    reacts immediately instead of waiting out the cache TTL."""
    if not school_id:
        return
    from app.utils.kv_cache import cache_delete
    cache_delete(f"school_active:{school_id}")


def is_school_active(school_id):
    """Check if a school has active subscription or is still in trial period.

    This gates every write route, so it is cached briefly: the underlying query
    is an embedded join costing ~165 ms per call. The cache is per school, and
    60 s of staleness is harmless for a billing gate that flips at most a few
    times a month (payments invalidate it explicitly).
    """
    if not school_id:
        return False

    from app.utils.kv_cache import cache_get, cache_set

    key = f"school_active:{school_id}"
    cached = cache_get(key)
    if cached is not None:
        return bool(cached)

    sub = get_school_subscription(school_id)
    if not sub:
        # No subscription record yet → assume active (new school, not yet tracked)
        active = True
    else:
        active = sub["status"] == "active" or sub["status"] == "trial"
    cache_set(key, active, 60)
    return active


def get_payment_fee_config():
    """Get the admin fee configuration (biaya admin yang dibebankan ke pelanggan)."""
    supabase = current_app.extensions["supabase"]
    try:
        res = supabase.table("school_settings").select("payment_fee_config").eq("id", 1).single().execute()
        if res.data and res.data.get("payment_fee_config"):
            return res.data["payment_fee_config"]
    except Exception:
        pass
    return {"fee_percent": 0, "fee_flat": 4000, "fee_note": "Biaya admin Rp 4.000 (transfer bank)"}


def calculate_total_with_fee(base_amount):
    """Calculate total amount including admin fee.
    Returns (total, fee_breakdown) where fee_breakdown explains the fee."""
    fee_cfg = get_payment_fee_config()
    fee_pct = float(fee_cfg.get("fee_percent", 0))
    fee_flat = float(fee_cfg.get("fee_flat", 0))
    pct_amount = round(base_amount * fee_pct / 100)
    total = round(base_amount + pct_amount + fee_flat)
    return total, {
        "base": base_amount,
        "fee_percent": fee_pct,
        "fee_percent_amount": pct_amount,
        "fee_flat": fee_flat,
        "total": total,
        "note": fee_cfg.get("fee_note", ""),
    }
