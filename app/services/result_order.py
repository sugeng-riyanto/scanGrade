"""Class labels and hand-back order for result documents."""
import re


def class_names(supabase, profiles, school_id):
    ids = sorted({p["class_id"] for p in profiles if p.get("class_id")})
    if not ids or not school_id:
        return {}
    rows = (supabase.table("classes").select("id,name").eq("school_id", school_id)
            .in_("id", ids).execute().data or [])
    return {row["id"]: row["name"] for row in rows if row.get("name")}


def class_key(class_name):
    parts = re.split(r"(\d+)", class_name.casefold())
    natural = tuple(int(part) if i % 2 else part for i, part in enumerate(parts))
    return not class_name, natural


def key(class_name, student_name, identifier):
    return class_key(class_name), not student_name, student_name.casefold(), str(identifier or "")


def arrange(supabase, submissions, school_id):
    profiles = [row.pop("profiles", None) or {} for row in submissions]
    labels = class_names(supabase, profiles, school_id)
    for row, profile in zip(submissions, profiles):
        row["student_name"] = profile.get("full_name") or row.get("student_name") or ""
        row["class_name"] = labels.get(profile.get("class_id"), "")
    return sorted(submissions, key=lambda row: key(
        row["class_name"], row["student_name"], row.get("id")))
