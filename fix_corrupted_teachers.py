"""One-time fix: hapus guru dengan nama corrupted (Tahun Ajaran sebagai nama)."""
import os, sys, re
sys.path.insert(0, os.path.dirname(__file__))
from app import create_app
from app.utils.supabase import get_supabase

app = create_app()
with app.app_context():
    supabase = get_supabase()
    # Cari guru yang namanya mirip tahun ajaran (YYYY/YYYY atau YYYY-YYYY)
    corrupted = supabase.table("profiles").select("id, full_name, school_id").eq("role", "guru").execute().data or []
    to_delete = [p for p in corrupted if re.match(r"^\d{4}[/-]\d{4}$", p.get("full_name", ""))]
    if not to_delete:
        print("Tidak ada guru corrupted ditemukan.")
        sys.exit(0)
    print(f"Menemukan {len(to_delete)} guru corrupted:")
    for p in to_delete:
        print(f"  {p['id']} - {p['full_name']} (school={p['school_id']})")
    for p in to_delete:
        uid = p["id"]
        try:
            supabase.table("teachers").delete().eq("id", uid).execute()
            supabase.table("profiles").delete().eq("id", uid).execute()
            supabase.auth.admin.delete_user(uid)
            print(f"  OK hapus {uid}")
        except Exception as e:
            print(f"  GAGAL hapus {uid}: {e}")
    print("Selesai.")
