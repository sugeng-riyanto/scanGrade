"""Exercise supervision races on a disposable, loopback-only PostgreSQL database.

No Supabase credentials or external network targets are used. The database must be empty
and named scanmeet_supervision_ci*. CI destroys its PostgreSQL container after
this test. This complements PGlite; it does not sign off LiveKit or real devices.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
import os
from pathlib import Path
import threading
from uuid import uuid4

import psycopg2

ROOT = Path(__file__).resolve().parents[1]


def settings(environ=None):
    env = os.environ if environ is None else environ
    host = env.get("PGHOST", "")
    database = env.get("PGDATABASE", "")
    if host not in ("127.0.0.1", "::1", "localhost") or not database.startswith("scanmeet_supervision_ci"):
        raise ValueError("Requires a disposable scanmeet_supervision_ci* database on loopback")
    # libpq can honor environment values even when most fields are explicit.
    # In particular PGHOSTADDR can redirect an otherwise safe-looking hostname.
    if any(env.get(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGPASSFILE")):
        raise ValueError("Refusing libpq connection overrides for disposable SQL checks")
    return dict(host=host, hostaddr="127.0.0.1" if host == "localhost" else host,
                dbname=database, sslmode="disable", options="", passfile=os.devnull, port=int(env.get("PGPORT", "5432")),
                user=env.get("PGUSER", "postgres"), password=env.get("PGPASSWORD", ""),
                connect_timeout=5)


def connection(*, service=False):
    conn = psycopg2.connect(**settings())
    with conn.cursor() as cursor:
        cursor.execute("SET statement_timeout='12s'; SET lock_timeout='8s'")
        if service:
            cursor.execute("SET ROLE service_role")
    return conn


def scalar(cursor, sql, args=()):
    cursor.execute(sql, args)
    return cursor.fetchone()[0]


def bootstrap():
    with closing(connection()) as conn:
        with conn, conn.cursor() as cursor:
            count = scalar(cursor, "SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('public','auth')")
            if count:
                raise ValueError("Refusing to bootstrap a database containing application tables")
            cursor.execute("""
                CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS;
                CREATE SCHEMA auth; CREATE TABLE auth.users(id uuid PRIMARY KEY,email text);
                CREATE TABLE public.schools(id uuid PRIMARY KEY,name text,npsn text);
                CREATE TABLE public.profiles(id uuid PRIMARY KEY REFERENCES auth.users(id),
                    school_id uuid REFERENCES public.schools(id),role text,full_name text);
                CREATE TABLE public.exams(id uuid PRIMARY KEY,school_id uuid REFERENCES public.schools(id),
                    teacher_id uuid NOT NULL REFERENCES public.profiles(id),title text);
            """)
            for prefix in ("070", "071", "073", "074", "075", "076", "077", "079", "079"):
                paths = list((ROOT / "supabase/migrations").glob(prefix + "_*.sql"))
                if len(paths) != 1:
                    raise ValueError("Expected one migration for " + prefix)
                cursor.execute(paths[0].read_text(encoding="utf-8"))
            # Supabase's core schema grants service_role table access. This
            # minimal fixture models that, without granting a browser role.
            cursor.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO service_role")
        try:
            with conn.cursor() as cursor:
                cursor.execute((ROOT / "tests/scanmeet_supervision_sql_checks.sql").read_text(encoding="utf-8"))
        finally:
            conn.rollback()
        with conn.cursor() as cursor:
            assert scalar(cursor, "SELECT count(*) FROM public.schools") == 0
    print("PASS native PostgreSQL migration, sequential RPCs and rollback")


@dataclass
class Fixture:
    school: str
    owner: str
    admin: str
    deputy: str
    exam: str
    exam2: str


def fixture():
    f = Fixture(*(str(uuid4()) for _ in range(6)))
    with closing(connection()) as conn, conn, conn.cursor() as c:
        c.execute("INSERT INTO schools(id,name) VALUES(%s,'ZZ native concurrency fixture')", (f.school,))
        for uid, role in ((f.owner, "guru"), (f.admin, "admin_sekolah"), (f.deputy, "guru")):
            c.execute("INSERT INTO auth.users(id,email) VALUES(%s,%s)", (uid, uid + "@example.invalid"))
            c.execute("INSERT INTO profiles(id,school_id,role) VALUES(%s,%s,%s)", (uid, f.school, role))
        for eid in (f.exam, f.exam2):
            c.execute("INSERT INTO exams(id,school_id,teacher_id,title,scan_live_enabled) VALUES(%s,%s,%s,'ZZ race',true)", (eid, f.school, f.owner))
        c.execute("""INSERT INTO scanmeet_school_entitlements
            (school_id,enabled,status,valid_until,plan_code,session_limit_minutes,participant_limit,session_allowance,concurrent_room_limit)
            VALUES(%s,true,'trial',now()+interval '2 hours','trial',45,40,5,1)""", (f.school,))
    return f


def grant(c, f, *, revoke=False, version=0):
    return scalar(c, "SELECT scanmeet_supervisor_assignment(%s,%s,%s,now()+interval '1 hour',%s,'native race',%s)",
                  (f.exam, f.admin, f.deputy, version, revoke))


def start(c, f, exam=None):
    return scalar(c, "INSERT INTO scan_live_sessions(school_id,exam_id,created_by,room_name) VALUES(%s,%s,%s,%s) RETURNING id",
                  (f.school, exam or f.exam, f.owner, "sg_" + uuid4().hex))


def begin(c, f, sid, target=None):
    return scalar(c, "SELECT scanmeet_begin_handover(%s,%s,%s,%s,1,'native race')",
                  (f.exam, f.admin, sid, target or f.deputy))


def race(first, second):
    """Independent server connections start together; errors roll back fully."""
    barrier = threading.Barrier(2, timeout=10)
    def run(operation):
        with closing(connection(service=True)) as conn:
            try:
                barrier.wait()
                with conn, conn.cursor() as c:
                    return {"ok": operation(c)}
            except psycopg2.Error as exc:
                conn.rollback()
                return {"error": exc.diag.message_primary}
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(run, first), pool.submit(run, second)
        return a.result(timeout=20), b.result(timeout=20)


def one_winner(results, error):
    assert sum("ok" in r for r in results) == 1, results
    assert [r["error"] for r in results if "error" in r] == [error], results


def prepare(f):
    with closing(connection(service=True)) as conn, conn, conn.cursor() as c:
        grant(c, f)
        return start(c, f)


def claim(c, job):
    token = uuid4().hex
    c.execute("UPDATE scan_live_cleanup_jobs SET claim_token=%s,claimed_at=clock_timestamp() WHERE handover_id=%s AND status='pending'", (token, job))
    assert c.rowcount == 1
    return token


def finish(c, job, token):
    # Provider deletion is represented by the cleanup claim. These SQL checks
    # do not create, delete or claim to observe any actual LiveKit room.
    return scalar(c, "SELECT scanmeet_finish_handover(%s,%s)", (job, token))


def check_grant_race():
    f = fixture()
    one_winner(race(lambda c: grant(c, f), lambda c: grant(c, f)), "scanmeet_version_conflict")
    with closing(connection(service=True)) as conn, conn.cursor() as c:
        assert scalar(c, "SELECT count(*) FROM scanmeet_supervisor_audit WHERE exam_id=%s", (f.exam,)) == 1
    print("PASS concurrent assignments: one version winner and one audit")


def check_room_reservation_race():
    f = fixture()
    one_winner(race(lambda c: start(c, f), lambda c: start(c, f, f.exam2)), "scanmeet_school_room_busy")
    with closing(connection(service=True)) as conn, conn.cursor() as c:
        assert scalar(c, "SELECT sessions_used FROM scanmeet_school_entitlements WHERE school_id=%s", (f.school,)) == 1
    print("PASS simultaneous rooms: one reservation and one charge")


def check_transfer_races():
    for duplicate in (False, True):
        f = fixture()
        sid = prepare(f)
        results = race(lambda c: begin(c, f, sid), lambda c: begin(c, f, sid, f.deputy if duplicate else f.admin))
        if duplicate:
            assert all("ok" in r for r in results), results
            assert results[0]["ok"]["handover_id"] == results[1]["ok"]["handover_id"], results
        else:
            one_winner(results, "scanmeet_handover_busy")
        with closing(connection(service=True)) as conn, conn.cursor() as c:
            assert scalar(c, "SELECT count(*) FROM scanmeet_handover_jobs WHERE session_id=%s", (sid,)) == 1
            assert scalar(c, "SELECT count(*) FROM scan_live_cleanup_jobs WHERE session_id=%s", (sid,)) == 1
            assert scalar(c, "SELECT status||':'||transfer_state FROM scan_live_sessions WHERE id=%s", (sid,)) == "active:closing"
            assert scalar(c, "SELECT sessions_used FROM scanmeet_school_entitlements WHERE school_id=%s", (f.school,)) == 1
        print("PASS " + ("duplicate" if duplicate else "competing") + " concurrent handover: one durable operation")


def check_finish_races():
    f = fixture()
    sid = prepare(f)
    with closing(connection(service=True)) as conn, conn, conn.cursor() as c:
        job = begin(c, f, sid)["handover_id"]
        token = claim(c, job)
    results = race(lambda c: finish(c, job, token), lambda c: finish(c, job, token))
    assert all(r.get("ok", {}).get("state") == "completed" for r in results), results
    with closing(connection(service=True)) as conn, conn.cursor() as c:
        assert scalar(c, "SELECT media_version FROM scan_live_sessions WHERE id=%s", (sid,)) == 2
        assert scalar(c, "SELECT sessions_used FROM scanmeet_school_entitlements WHERE school_id=%s", (f.school,)) == 1
    print("PASS concurrent finalizers: rotate once, no additional charge")


def check_revoke_race():
    f = fixture()
    sid = prepare(f)
    with closing(connection(service=True)) as conn, conn, conn.cursor() as c:
        job = begin(c, f, sid)["handover_id"]
        token = claim(c, job)
    results = race(lambda c: finish(c, job, token), lambda c: grant(c, f, revoke=True, version=1))
    assert all("ok" in r for r in results), results
    # Either revocation cancels the transfer, or it queues cleanup of the new
    # generation. Acknowledging every queued deletion must leave no active room.
    with closing(connection(service=True)) as conn, conn, conn.cursor() as c:
        c.execute("SELECT id FROM scanmeet_handover_jobs WHERE session_id=%s AND state='pending'", (sid,))
        for (pending,) in c.fetchall():
            finish(c, pending, claim(c, pending))
        assert scalar(c, "SELECT status FROM scan_live_sessions WHERE id=%s", (sid,)) == "ended"
        assert not scalar(c, "SELECT scanmeet_supervisor_authorized(%s,%s)", (f.exam, f.deputy))
        assert scalar(c, "SELECT sessions_used FROM scanmeet_school_entitlements WHERE school_id=%s", (f.school,)) == 1
    print("PASS revoke/finalize race: no revived supervisor or double charge")


def main():
    settings()  # Refuse before connecting or writing anything.
    bootstrap()
    for check in (check_grant_race, check_room_reservation_race, check_transfer_races,
                  check_finish_races, check_revoke_race):
        check()
    print("PASS native PostgreSQL supervision concurrency checks")


if __name__ == "__main__":
    main()
