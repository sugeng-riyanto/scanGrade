# Rotating a leaked secret

## Why this document exists

`deploy/bootstrap.sh` used to write the box's `.env` with the Supabase
**service-role** JWT and a `FLASK_SECRET_KEY` spelled out in the script. That
script is in a public repository. The service-role key is the one credential the
whole design leans on row-level security to survive — it bypasses every policy —
so anyone who read the repository could act as the backend: read every table,
write any row, and forge a violation (the record the penalty is computed from).

The script no longer carries them (commit `4c3e3ce`), but **the value is still in
the repository's history**, so the key must be treated as compromised. A forward
fix does not un-leak a secret; rotation does.

Verified once, by comparing SHA-256 digests rather than printing either value:

- the box's `SUPABASE_SERVICE_KEY` **is** the committed key — production is running
  on the leaked credential, so this is not a stale one;
- the box's `FLASK_SECRET_KEY` is **not** the committed value — that one had
  already been changed, so its leak is historical.

The box's `.env` was also mode `664` (group- and other-readable); it is now `600`.

## Rotating the Supabase service key

Do this **outside a live sitting**. Rotating the key requires a restart, and a
restart mid-exam is the one interruption this system is built to avoid. Check the
journal first:

```bash
ssh root@<box> 'journalctl -u scangrade --since "15 min ago" --no-pager | grep -cE "/student/exams|/api/student/sync|/student/heartbeat"'
```

A count above zero means pupils are working now — wait.

1. In the Supabase dashboard for the project, generate a new `service_role` key
   (Project Settings → API). Keep the old one active for the moment.
2. Write the new value into the box's `.env` (never into the repository):
   ```bash
   ssh root@<box>
   cd /opt/scangrade
   cp .env .env.bak-$(date +%F)
   # edit the SUPABASE_SERVICE_KEY line by hand; do not paste it into a shell history
   chmod 600 .env
   ```
3. Restart the app and the worker, then confirm the box is healthy:
   ```bash
   systemctl restart scangrade scangrade-worker
   curl -s http://127.0.0.1:8000/health
   ```
4. Only once `/health` is `ok`, **revoke the old key** in the dashboard. That is
   what makes the value in git history worthless.
5. Confirm the box no longer answers with the old key (it must be refused):
   the new key's digest in `.env` must differ from the committed one.

## Rotating the Flask secret (optional)

`FLASK_SECRET_KEY` signs the CSRF token kept in the session. Rotating it does not
sign pupils out — authentication is a Supabase JWT in its own cookie — but any
page already open holds a CSRF token signed with the old secret until it is
reloaded, so a POST from a mid-exam page would be refused with `403`. Rotate it in
the same quiet window, not during a sitting:

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'   # the new value
# set FLASK_SECRET_KEY=… in /opt/scangrade/.env, then:
systemctl restart scangrade scangrade-worker
```

The box's current value is not the leaked one, so this rotation is housekeeping
rather than an incident response.

## What guards this now

- `tests/unit/test_no_committed_secrets.py` scans every tracked file for a JWT
  whose payload claims `service_role` (long enough to be a real key, so the fake
  fixture in `app/config.py` is not a false finding) and refuses a literal Flask
  secret. It runs **inside `deploy/theme_gate.sh`**, which the VPS auto-deploy
  runs as a hard gate before reloading the app and the pre-commit hook runs on a
  commit: a release that carries such a value is refused and rolled back, rather
  than only failing when somebody happens to run the unit suite.
- `ProductionConfig.validate()` refuses to boot when `FLASK_SECRET_KEY` is unset
  or still the repository's placeholder, so a box that loses the line fails loudly
  instead of signing sessions with a public string.
- `tests/unit/test_operational_surface.py` keeps `/metrics` a super-admin read and
  `/health` open (the deploy runner's own probe has no session).
