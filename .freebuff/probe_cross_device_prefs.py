"""Does a theme/language picked on one device reach the same student's next device?

Two sessions, no shared cookies and no browser storage at all — so the only way the
second can show the choice is if it came from the student's profile on the server.
That is the whole claim of the cross-device preference feature, and a unit test
against a fake client cannot make it.

The values it writes are deliberately the **opposite** of what a fresh device is
already showing. Asserting `lang == 'en'` on a box whose default is `en` would pass
with the feature entirely removed, which is the sort of proof that proves nothing.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe .freebuff/probe_cross_device_prefs.py

It signs in as a demo student, and leaves the profile as it found it (the raw
`profiles.preferences` value is read first and written back at the end). If the
column itself is missing it says so and stops: no preference can persist, and
reporting a link failure it cannot fix would be the wrong answer.
"""
from __future__ import annotations

import json
import os
import re
import sys

import requests
from dotenv import load_dotenv

load_dotenv(".env")

BASE = os.environ.get("SG_BASE", "http://127.0.0.1:5000").rstrip("/")
EMAIL = os.environ.get("SG_STUDENT", "siswa1_smp@scan-grade.app")
PASSWORD = os.environ.get("SG_STUDENT_PASSWORD", "demo123")
PAGE = "/student/dashboard"

SUPA = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SECRET_KEY") or ""

CSRF = re.compile(r'name="csrf-token"\s+content="([^"]+)"')
PREFS = re.compile(r"window\.__sgPrefs=(\{.*?\});", re.S)
HTML_LANG = re.compile(r'<html lang="([^"]+)"')
DARK_CLASS = re.compile(r'<html lang="[^"]*"[^>]*class="([^"]*)"')

ok = True


def check(label, condition, extra=""):
    global ok
    ok = ok and bool(condition)
    print(f"  {'OK  ' if condition else 'FAIL'} {label}{(' — ' + extra) if extra else ''}")
    return bool(condition)


def admin_headers():
    return {"apikey": KEY, "Authorization": f"Bearer {KEY}"}


def user_id_for(email: str) -> str | None:
    """The auth id behind an email, so the profile row can be read by hand."""
    for page in range(1, 26):
        response = requests.get(
            f"{SUPA}/auth/v1/admin/users",
            params={"page": str(page), "per_page": "1000"},
            headers=admin_headers(), timeout=90)
        response.raise_for_status()
        users = response.json().get("users", [])
        if not users:
            return None
        for user in users:
            if (user.get("email") or "").lower() == email.lower():
                return user["id"]
        if len(users) < 1000:
            return None
    return None


def raw_prefs(uid: str):
    """`(exists, value)` — the stored column, or `(False, None)` if it is absent."""
    response = requests.get(
        f"{SUPA}/rest/v1/profiles",
        params={"select": "preferences", "id": f"eq.{uid}"},
        headers=admin_headers(), timeout=60)
    if response.status_code == 400 and "does not exist" in response.text:
        return False, None
    response.raise_for_status()
    rows = response.json()
    return True, (rows[0].get("preferences") if rows else None)


def write_raw_prefs(uid: str, value):
    requests.patch(
        f"{SUPA}/rest/v1/profiles",
        params={"id": f"eq.{uid}"},
        headers={**admin_headers(), "Content-Type": "application/json",
                 "Prefer": "return=minimal"},
        data=json.dumps({"preferences": value}), timeout=60)


def login() -> requests.Session:
    """One device: its own cookie jar, nothing carried over from another."""
    session = requests.Session()
    page = session.get(f"{BASE}/auth/login-user", timeout=60)
    match = CSRF.search(page.text)
    if not match:
        raise SystemExit(f"no CSRF token on /auth/login-user — is {BASE} the app?")
    response = session.post(
        f"{BASE}/auth/login-user",
        data={"email": EMAIL, "password": PASSWORD, "_csrf_token": match.group(1)},
        headers={"X-CSRF-Token": match.group(1)},
        allow_redirects=False, timeout=60)
    landed = response.headers.get("Location", "")
    if response.status_code not in (301, 302, 303) or "dashboard" not in landed:
        raise SystemExit(
            f"login as {EMAIL} did not land on a dashboard "
            f"(HTTP {response.status_code}, {landed!r})")
    return session


def read_page(session: requests.Session, url: str):
    response = session.get(url, timeout=90)
    text = response.text
    prefs = PREFS.search(text)
    lang = HTML_LANG.search(text)
    return {
        "status": response.status_code,
        "prefs": json.loads(prefs.group(1)) if prefs else None,
        "lang": lang.group(1) if lang else None,
        "class": DARK_CLASS.search(text).group(1) if DARK_CLASS.search(text) else None,
        "error_page": "<title>Error - ScanGrade</title>" in text,
    }


def main() -> int:
    if not (SUPA and KEY):
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_KEY missing from .env")

    uid = user_id_for(EMAIL)
    print(f"signed-in identity: {EMAIL} ({uid})\n")
    if not uid:
        raise SystemExit(f"no auth user for {EMAIL}")

    exists, original = raw_prefs(uid)
    print("=" * 78)
    if not exists:
        print("profiles.preferences is ABSENT from the database this app can reach.")
        print("The column arrives with migration 036, and nothing can be stored")
        print("without it: `_fetch_session` falls back to the legacy columns, so")
        print("`g.user_prefs` is always empty and a write has nowhere to land.")
        print("Applying it is one idempotent ADD COLUMN, but the running process")
        print("remembers the first failed read, so it also has to be restarted.")
        return 1

    print(f"stored profile preferences at the start: {original!r}")
    try:
        # ── Device B, before any change: what does a fresh login see? ──────────
        print("\n-- device B (fresh cookies) BEFORE the change --------------------")
        before = read_page(login(), f"{BASE}{PAGE}")
        check("B renders the student dashboard", before["status"] == 200
              and not before["error_page"], f"HTTP {before['status']}")
        check("B carries a server-seeded preference object",
              isinstance(before["prefs"], dict), repr(before["prefs"]))
        print(f"     B sees prefs={before['prefs']!r} lang={before['lang']!r}")

        # Choose the value B is NOT already showing, so a pass cannot come from
        # the defaults: only the stored profile can produce the opposite.
        size = (before["prefs"] or {})
        target_theme = "dark" if size.get("theme") != "dark" else "light"
        target_lang = "en" if before["lang"] != "en" else "id"
        target = {"theme": target_theme, "lang": target_lang}
        check("the target differs from what B already shows, so the proof bites",
              target_theme != size.get("theme") and target_lang != before["lang"],
              f"before={size.get('theme')!r}/{before['lang']!r} -> target={target!r}")

        # ── Device A: the student changes theme and language ──────────────────
        print(f"\n-- device A (its own cookies) sets {target_theme} + {target_lang} "
              + "-" * 12)
        session_a = login()
        page = session_a.get(f"{BASE}{PAGE}", timeout=90)
        token = CSRF.search(page.text)
        if not token:
            raise SystemExit("no CSRF token on the student dashboard")
        response = session_a.post(
            f"{BASE}/api/ui-preferences", json=target,
            headers={"X-CSRF-Token": token.group(1)}, timeout=90)
        check("A's write is accepted", response.status_code == 200,
              f"HTTP {response.status_code} {response.text[:120]!r}")
        if response.status_code == 200:
            stored = response.json().get("preferences") or {}
            check("A's response echoes the merged set",
                  stored.get("theme") == target_theme
                  and stored.get("lang") == target_lang, repr(stored))

        # The same device must not render the old choice back after the write.
        again = read_page(session_a, f"{BASE}{PAGE}")
        check("A's own next page shows the new choice (not undone by a cache)",
              (again["prefs"] or {}).get("theme") == target_theme
              and again["lang"] == target_lang,
              f"prefs={again['prefs']!r} lang={again['lang']!r}")

        # ── Device C, after the change: a different device entirely ───────────
        print("\n-- device C (fresh cookies, no storage) AFTER the change --------")
        after = read_page(login(), f"{BASE}{PAGE}")
        check("C renders the student dashboard", after["status"] == 200
              and not after["error_page"], f"HTTP {after['status']}")
        check("C is seeded with the theme chosen on A",
              (after["prefs"] or {}).get("theme") == target_theme,
              repr(after["prefs"]))
        check("C declares the language chosen on A (not its own default)",
              after["lang"] == target_lang,
              f"got {after['lang']!r}, default was {before['lang']!r}")
        print(f"     C sees prefs={after['prefs']!r} lang={after['lang']!r} "
              f"class={after['class']!r}")
        print("     (the head script turns that `theme` into the dark class "
              "before first paint; a page class is not server-rendered)")
    finally:
        write_raw_prefs(uid, original)
        print(f"\nrestored profiles.preferences to {original!r}")

    print("\n" + ("PASS — the choice crossed devices" if ok else "FAIL — it did not"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
