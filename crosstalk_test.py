"""Cross-session race test.

All logins share one Supabase auth client, and sign_in_with_password mutates
that client's session state. If concurrent greenlets can interleave, a request
could be served with another account's identity — catastrophic for an exam
platform. This logs in every account concurrently, then asks /auth/me (which
echoes g.user_id) and asserts it matches the account that signed in.

Usage: PYTHONPATH=/opt/scangrade .venv/bin/python crosstalk_test.py [rounds]
"""
import asyncio
import re
import sys
from collections import Counter

import httpx
from dotenv import load_dotenv

load_dotenv(".env")
BASE = "https://scangrade.web.id"
CSRF = re.compile(r'name="csrf-token"\s+content="([^"]+)"')
PASSWORD = "demo123"
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 3


def roster():
    from app.config import get_config
    from supabase import create_client
    cfg = get_config()
    svc = create_client(cfg.SUPABASE_URL, cfg.SUPABASE_SERVICE_KEY)
    profiles = {r["id"]: r for r in
                svc.table("profiles").select("id, role").execute().data}
    out = []
    for u in svc.auth.admin.list_users():
        email = getattr(u, "email", "") or ""
        if not email.endswith("@scan-grade.app"):
            continue
        pr = profiles.get(u.id)
        if pr and pr.get("role") in ("murid", "guru"):
            out.append({"id": u.id, "email": email, "role": pr["role"]})
    return out


async def one(acct, state):
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as c:
            page = await c.get(f"{BASE}/auth/login-user")
            tok = CSRF.search(page.text).group(1)
            r = await c.post(f"{BASE}/auth/login-user",
                             data={"email": acct["email"], "password": PASSWORD,
                                   "_csrf_token": tok},
                             headers={"X-CSRF-Token": tok})
            state[f"login {r.status_code}"] += 1
            me = await c.get(f"{BASE}/auth/me", headers={"Accept": "application/json"})
            state[f"me {me.status_code}"] += 1
            if me.status_code != 200:
                return
            # Only a 200 is evidence; a throttled call proves nothing either way.
            state["verified"] += 1
            got = (me.json() or {}).get("user_id")
            if got != acct["id"]:
                state["mismatch"].append((acct["email"], acct["id"], got))
    except Exception as e:
        state[f"exc {type(e).__name__}"] += 1


async def main():
    accounts = roster()
    total = len(accounts) * ROUNDS
    print(f"{len(accounts)} accounts, {ROUNDS} concurrent rounds each ({total} logins)")
    state = Counter()
    state["mismatch"] = []
    for i in range(ROUNDS):
        if i:
            # Let the app's per-identity window drain so results are not just 429s.
            await asyncio.sleep(65)
        await asyncio.gather(*(one(a, state) for a in accounts))
        print(f"  round {i + 1}: verified={state['verified']} mismatches={len(state['mismatch'])}")

    print("\ncounters:", {k: v for k, v in state.items() if k != "mismatch"})
    print(f"IDENTITY MISMATCHES: {len(state['mismatch'])} of {state['verified']} verified sessions")
    for m in state["mismatch"][:10]:
        print("   expected", m[0], m[1], "-> got", m[2])


asyncio.run(main())
