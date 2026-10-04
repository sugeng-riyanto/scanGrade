# Audit: third-party error text shown to operators

An operator (school admin, super admin, school official) should never be shown a
library's generic error text where a sentence they can act on exists. This is the
inventory of the places that can, and the decision for each: **transient** (a
retry or a "try again" is right) or **permanent** (the input or the permission is
wrong and repeating changes nothing).

The *how* already lives in one place — `app/utils/failure.py`, `failure.sentence()`:

| The exception | What the operator is shown | Why |
|---|---|---|
| anything with `user_message` (a `ScanGradeException`, `OfficialError`, `AccountNotCreated`) | its own sentence | that sentence exists because the technical one is wrong for a reader |
| `httpx.TransportError` (a lost reply) | `LOST_REPLY` — *outcome unknown, check before repeating* | a dropped connection cannot say whether the write ran, and re-sending an insert is how one row becomes two |
| `postgrest.exceptions.APIError` | `SERVER_REFUSED` + the Postgres code | the JSON body is a developer's; the code names the refusal to whoever supports the school |
| anything else | the exception's own text, one bounded line | a sentence invented for an unanticipated failure would hide the only clue |

This audit is the *classification* half: which library texts are transient, and
which call sites were still bypassing `failure.sentence()` for account surfaces.

## Transience decisions

| Library / surface | Generic text (example) | Transient? | Decision / handling |
|---|---|---|---|
| **GoTrue — admin create** (`auth.admin.create_user`) | `Database error creating new user`, `Database error saving new user` | **Yes** | already retried (`app/utils/auth_retry.py`) and recorded (`app/utils/auth_health.py`); shown in the creator's own words, never GoTrue's. Pinned by `tests/unit/test_operator_error_text.py`. |
| **GoTrue — admin update/delete** (`auth.admin.update_user_by_id`, `delete_user`) | `Database error updating user` / generic database error | **Yes** (same rolled-back transaction) | retried-and-shown like create. The demo-password repair now routes through `failure.sentence`; the legacy per-teacher/student reset still shows raw text (see *Remaining*). |
| **httpx transport** (Supabase/PostgREST over the network) | `Server disconnected`, `Read timeout` | **Ambiguous** | a lost reply is *not* a failure — the write may have landed. `failure.sentence` returns `LOST_REPLY`; `supabase_retry` settles writes where it can. Never auto-retried blindly. |
| **PostgREST** — a server answer | `invalid input syntax for type uuid: "None"`, `23505`, `PGRST…` | **No** | deterministic refusal; repeating changes nothing. Shown as `SERVER_REFUSED (code)`. |
| **PostgREST** — connection refused / 5xx | `503`, `connection reset` | **Yes** (but see transport) | the transport layer's classification applies. |
| **Redis** (draft lock, rate limiter) | `Error 10061 connecting to localhost` | **Yes** | `app/utils/lock_health.py` records the fallback and the status page shows it; the app falls back rather than failing. |
| **openpyxl** (`.xlsx` import) | `File is not a zip file`, `Bad magic number` | **No** | the upload is the wrong file; a retry uploads the same bytes. Wrapped with "Gagal membaca file: …". |
| **Pillow / PDF / OCR** (scan, media) | `cannot identify image file`, decode errors | **No** | the image is bad, not the server. Wrapped as "Gambar tidak valid atau corrupt" / "Gagal memproses PDF". |
| **AI providers** | provider `5xx` / rate-limit text | **Yes** | `AIProcessingError` carries a `user_message` ("Coba beberapa saat lagi"), so it is shown in its own words. |
| **Task queue / Celery broker** | `OperationalError` (broker down) | **Yes** | the OMR routes fall back to synchronous processing when enqueueing fails. |

## The account surfaces that fell back to raw text

All three are account-creation (or account-repair) surfaces, so all three were
showing GoTrue's/PostgREST's text where the shared creator already carried a
`user_message`. Fixed in this change:

- `app/routes/admin.py` — the two legacy importers (`import_students`,
  `import_teachers`) per-row errors now use `failure.sentence(e)`. This is the
  same GoTrue class as the original report: a transient create that exhausted its
  retries told the operator "Database error creating new user" instead of the
  creator's sentence.
- `app/routes/admin_sekolah.py` — the subject importer's per-row error and the
  subject-list read error now use `failure.sentence(e)`. (The student/teacher
  import row errors already used `getattr(e, "user_message", str(e))`.)
- `app/routes/super_admin.py` — the demo-password repair's per-email password
  error, the auth-user list error, and the "flag not cleared" warning now use
  `failure.sentence(e)`. The password update is GoTrue, so it is the same class.

Pinned by `tests/unit/test_operator_error_text.py` (classifications + the call
sites) and a driving test in `tests/unit/test_legacy_importers.py` (a transient
create, exhausted, shows the creator's words and not GoTrue's).

## The operator surfaces, now fully routed

The *Remaining* inventory above was closed. Every operator-facing route module
(`admin.py`, `admin_sekolah.py`, `super_admin.py`) now sends its failures through
`failure.sentence`, so a PostgREST refusal reaches the school admin or the super
admin as `SERVER_REFUSED (23505)` and never as the JSON body.

What changed, by class:

- **PostgREST writes** shown raw in JSON/flash — `app/routes/admin.py` (class
  create, registration approve/reject/delete), `app/routes/admin_sekolah.py`
  (class delete, teacher/official/student delete and reset, several JSON
  endpoints), `app/routes/super_admin.py` (feature toggle, Midtrans settings).
  Decision: **permanent** when it is a PostgREST `APIError`; the code now travels.
- **GoTrue legacy resets** (`app/routes/admin.py` `admin_reset_teacher_password`,
  `admin_reset_student_password`, and the school-admin equivalents): decision
  **transient**; routed like the repair, so a transient create shows the creator's
  sentence rather than GoTrue's generic text.
- **openpyxl** raw text in the two legacy importers and the class/official
  list reads: wrapped with `failure.sentence` so the operator gets one bounded
  line rather than a driver traceback fragment.

The rule is held absolutely rather than per-site: `tests/unit/test_operator_raw_postgrest.py`
scans the three modules and fails on any `jsonify`/`flash`/`refuse`/`back` call that
interpolates `str(e)`, `{e}`, `str(exc)` or `{exc}`. A new write that stringifies
its own exception fails the guard rather than waiting for the next audit.

Two audiences are deliberately *not* in that scan: `app/routes/api.py` (the
announcement and conversation endpoints a pupil or teacher uses) and
`app/routes/teacher.py` (media/AI). Their failures are read by a different person
and are tracked separately; the operator rule is scoped to the people who act on
a platform failure.

## Honest limits

- The transport-vs-server split is made by exception *type* (`httpx` /
  `postgrest`), not by parsing the text; a library that changes its exception
  hierarchy would fall to the "unknown" branch (raw text), which is the safe
  default but would need this table revisited.
- The operator guard is a source scan of the three operator modules. A new
  *operator* module that stringifies its own error would need adding to that list;
  the guard names its files explicitly rather than discovering them, so the list is
  a thing to keep current. (Student/teacher surfaces are out of scope by design.)
