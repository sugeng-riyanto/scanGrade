"""School-license management; all callers must pass the Super Admin door."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import re

from app.services import scanmeet_packages as packages

log = logging.getLogger(__name__)
LICENSES = "scanmeet_school_entitlements"
REQUESTS = "scanmeet_plan_requests"


def _one(query):
    rows = query.limit(2).execute().data or []
    if len(rows) > 1:
        raise ValueError("NPSN tidak unik. Periksa data sekolah terlebih dahulu.")
    return rows[0] if rows else None


def dashboard(db, args, renderer):
    q = str(args.get("q") or "").strip()[:80]
    npsn = str(args.get("npsn") or "").strip()
    try:
        page = max(1, min(10000, int(args.get("page", 1))))
    except (ValueError, TypeError):
        page = 1
    school = license_row = None
    rows, pending, error = [], [], ""
    total = 0
    try:
        if npsn:
            if not re.fullmatch(r"[0-9]{8}", npsn):
                raise ValueError("NPSN harus terdiri dari delapan digit.")
            school = _one(db.table("schools").select("id,npsn,name").eq("npsn", npsn))
            if school:
                raw = _one(db.table(LICENSES).select("*").eq("school_id", school["id"]))
                license_row = packages.describe(raw)
                rows = [{**school, "license": license_row}]
                total = 1
            else:
                error = "Sekolah dengan NPSN ini belum terdaftar."
        else:
            query = db.table("schools").select("id,npsn,name", count="exact")
            if q:
                if re.fullmatch(r"[0-9]{8}", q):
                    query = query.eq("npsn", q)
                else:
                    query = query.ilike("name", "%" + q.replace("%", "").replace("_", "") + "%")
            answer = query.order("name").range((page - 1) * 20, page * 20 - 1).execute()
            schools = answer.data or []
            total = answer.count or 0
            ids = [s["id"] for s in schools]
            licenses = (db.table(LICENSES).select("*").in_("school_id", ids).execute().data or []) if ids else []
            by_id = {r["school_id"]: packages.describe(r) for r in licenses}
            rows = [{**s, "license": by_id.get(s["id"], packages.describe(None))} for s in schools]
        # Requests are independent of the selected school. A missing migration is
        # surfaced as unavailable, never rendered as an empty successful inbox.
        try:
            pending = db.table(REQUESTS).select("id,school_id,plan_code,note,created_at").eq("status", "pending").order("created_at").limit(20).execute().data or []
            request_schools = (db.table("schools").select("id,npsn,name").in_("id", list({r["school_id"] for r in pending})).execute().data or []) if pending else []
            lookup = {s["id"]: s for s in request_schools}
            pending = [{**r, "school": lookup.get(r["school_id"]), "plan": packages.plan(r["plan_code"])} for r in pending]
            requests_available = True
        except Exception:
            requests_available = False
    except Exception as exc:
        log.warning("ScanMeet license list unavailable: %s", type(exc).__name__)
        error = str(exc) if isinstance(exc, ValueError) else "Data lisensi belum dapat dimuat. Muat ulang atau periksa migrasi paket ScanMeet."
        requests_available = False
    summary = {key: sum(r["license"]["effective_status"] == key for r in rows)
               for key in ("active", "trial", "expired", "inactive", "archived")}
    selected_plan = (license_row or {}).get("plan_code") or "trial"
    if selected_plan == "trial" and (license_row or {}).get("trial_started_at"):
        selected_plan = "kelas"
    for item in pending:
        if item["id"] == args.get("request_id") and school and item["school_id"] == school["id"]:
            selected_plan = item["plan_code"]
    return renderer("super_admin/scanmeet_schools.html", npsn=npsn, school=school,
                    license=license_row, rows=rows, pending=pending, q=q, page=page,
                    total=total, pages=max(1, (total + 19) // 20), summary=summary,
                    plans=packages.PLANS, error=error, requests_available=requests_available,
                    request_id=str(args.get("request_id") or ""), selected_plan=selected_plan)


def revoke_rooms(db, school_id, config):
    from app.services import live_cleanup, livekit_gateway
    revoked, pending = [], []
    sessions = db.table("scan_live_sessions").select("*").eq("school_id", school_id).eq("status", "active").execute().data or []
    for session in sessions:
        # Queue before the network call: a process dying mid-call leaves a retry.
        try:
            live_cleanup.enqueue(db, session, reason="school_license_off", error="license revoked")
        except Exception:
            log.exception("ScanMeet revocation could not persist cleanup debt")
            pending.append(session["id"])
            continue  # leave active for the expiry sweep; license is already OFF
        ended = db.table("scan_live_sessions").update({"status": "ended"}).eq("id", session["id"]).eq("school_id", school_id).eq("status", "active").execute().data or []
        if not ended:
            continue
        revoked.append(session["id"])
        try:
            livekit_gateway.end_room(config, session["room_name"])
        except Exception:
            pending.append(session["id"])
    return revoked, pending


def mutate(db, payload, actor_id, config, audit):
    npsn = str(payload.get("npsn") or "").strip()
    if not re.fullmatch(r"[0-9]{8}", npsn):
        return {"error": "NPSN harus terdiri dari delapan digit."}, 400
    action = payload.get("action")
    if action not in ("activate", "extend", "revoke", "archive"):
        return {"error": "Tindakan lisensi tidak tersedia."}, 400
    try:
        school = _one(db.table("schools").select("id,npsn,name").eq("npsn", npsn))
        if not school:
            return {"error": "Sekolah tidak ditemukan."}, 404
        school_id = school["id"]
        old = _one(db.table(LICENSES).select("*").eq("school_id", school_id))
        expected = str(payload.get("version") or "")
        if expected != str((old or {}).get("updated_at") or ""):
            return {"error": "Lisensi telah berubah. Muat ulang sebelum menyimpan."}, 409
        stamp = datetime.now(timezone.utc).isoformat()
        if action in ("activate", "extend"):
            if action == "activate":
                active = db.table("scan_live_sessions").select("id").eq("school_id", school_id).eq("status", "active").limit(1).execute().data or []
                if active:
                    return {"error": "Akhiri sesi pengawasan aktif sebelum mengganti paket. Ujian tidak perlu diakhiri."}, 409
            verified = str(payload.get("payment_verified") or "").lower() in ("true", "on", "1")
            entry = packages.activation(old, payload.get("plan_code"), action, verified)
        else:
            if old is None:
                return {"error": "Sekolah ini belum memiliki lisensi."}, 404
            entry = {"enabled": False, "status": "disabled", "valid_until": None}
            if action == "archive":
                entry["archived_at"] = stamp  # retain trial history and audit trail
        request_id = str(payload.get("request_id") or "").strip()
        if request_id:
            request_row = _one(db.table(REQUESTS).select("id,plan_code").eq("id", request_id).eq("school_id", school_id).eq("status", "pending"))
            if not request_row or request_row["plan_code"] != payload.get("plan_code"):
                return {"error": "Permintaan paket telah berubah atau bukan milik sekolah ini."}, 409
        entry.update(approved_by=actor_id, approved_at=stamp, updated_at=stamp)
        if old:
            changed = db.table(LICENSES).update(entry).eq("school_id", school_id).eq("updated_at", old["updated_at"]).execute().data or []
            if not changed:
                return {"error": "Lisensi telah berubah. Muat ulang sebelum menyimpan."}, 409
        else:
            db.table(LICENSES).insert({**entry, "school_id": school_id}).execute()
        audit("update" if old else "create", LICENSES, school_id,
              old_data=old, new_data={**entry, "action": action}, user_id=actor_id)
        if request_id:
            db.table(REQUESTS).update({"status": "approved", "updated_at": stamp}).eq("id", request_id).eq("school_id", school_id).eq("status", "pending").execute()
        revoked, pending = ([], [])
        if action in ("revoke", "archive"):
            revoked, pending = revoke_rooms(db, school_id, config)
        return {"ok": True, "message": "Perubahan lisensi tersimpan.",
                "revoked": revoked, "cleanup_pending": pending}, 202 if pending else 200
    except ValueError as exc:
        return {"error": str(exc)}, 400
    except Exception:
        log.exception("ScanMeet license mutation unavailable")
        return {"error": "Perubahan belum dapat dikonfirmasi. Muat ulang untuk memeriksa status sebelum mencoba lagi."}, 503
