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


def handle_payment_notification(notification_dict):
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

    update_data = {
        "status": "success" if is_success else ("expired" if is_expired else "failure"),
        "payment_type": payment_type,
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

    if is_success:
        _activate_subscription(tx["school_id"], tx["plan_id"], order_id, supabase)

    return True


def _activate_subscription(school_id, plan_id, order_id, supabase):
    plan = _load_plan(plan_id)
    if not plan:
        current_app.logger.error(f"Plan {plan_id} not found for activation")
        return

    code = generate_activation_code()
    now = datetime.now(timezone.utc)

    duration_days = plan.get("duration_days", 0)
    if duration_days == 0:
        sub_end = None
    else:
        sub_end = now + timedelta(days=duration_days)

    # Update payment transaction with activation code
    supabase.table("payment_transactions").update({
        "activation_code": code,
    }).eq("order_id", order_id).execute()

    # Deactivate any existing active subscription for this school
    supabase.table("school_subscriptions").update({
        "status": "replaced",
    }).eq("school_id", school_id).eq("status", "active").execute()

    # Insert new subscription
    supabase.table("school_subscriptions").insert({
        "school_id": school_id,
        "plan_id": plan_id,
        "status": "active",
        "subscription_start": now.isoformat(),
        "subscription_end": sub_end.isoformat() if sub_end else None,
        "activation_code": code,
    }).execute()

    current_app.logger.info(f"Subscription activated for school {school_id}, plan={plan_id}, code={code}")


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


def is_school_active(school_id):
    """Check if a school has active subscription or is still in trial period."""
    if not school_id:
        return False
    sub = get_school_subscription(school_id)
    if not sub:
        # No subscription record yet → assume active (new school, not yet tracked)
        return True
    return sub["status"] == "active" or sub["status"] == "trial"


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
