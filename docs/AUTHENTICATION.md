# ScanGrade Authentication Architecture

This document describes the authentication implementation in the current application code.

## 1. Components

- **Supabase Auth**: identity provider and password authentication.
- **Flask auth routes**: `/auth/login`, `/auth/login-user`, registration, and activation.
- **Auth utilities**: `app/utils/auth.py` extracts tokens, resolves sessions, refreshes access tokens, applies identity to Flask `g`, and enforces role requirements.
- **Role decorators**: `@super_admin_required`, `@admin_sekolah_required`, `@guru_required`, `@murid_required`, plus compatibility aliases.
- **School boundary check**: `@require_school_access` verifies that the requested resource belongs to the signed-in user's school.
- **Supabase RLS**: database-level policies provide an additional authorization boundary.
- **CSRF/rate limiting/session controls**: protect state-changing requests, authentication endpoints, and long-lived sessions.

## 2. Login Request Flow

### Admin / Super Admin

```
Browser
  -> GET /auth/login
  -> POST /auth/login (email, password)
  -> _sign_in_with_retry()
  -> Supabase Auth sign_in_with_password()
  -> read profiles(role, status, school_id)
  -> reject pending or wrong role
  -> set access_token cookie
  -> set refresh_token cookie
  -> set session_start cookie
  -> redirect to role dashboard
```

### Teacher / Student

```
Browser
  -> GET /auth/login-user
  -> POST /auth/login-user
  -> optional NISN/NIP -> resolve account email
  -> _sign_in_with_retry()
  -> Supabase Auth sign_in_with_password()
  -> read profiles(role, status)
  -> reject pending or wrong role
  -> set access_token + refresh_token + session_start
  -> redirect to /teacher/dashboard or /student/dashboard
```

## 3. Credentials and Tokens

The browser submits an email/password to the Flask server. Flask passes those credentials to Supabase Auth.

On successful login, ScanGrade stores:

- `access_token`: HttpOnly cookie, one-day lifetime.
- `refresh_token`: HttpOnly cookie, seven-day lifetime.
- `session_start`: timestamp cookie used by the application's absolute-session timeout logic.

The cookie flags are:

- `HttpOnly=true`
- `SameSite=Lax`
- `Secure=true` in production

The application can also accept an API-style `Authorization: Bearer <token>` header. Cookie authentication is the normal browser flow.

The Flask application's own `FLASK_SECRET_KEY` is separate from Supabase access/refresh tokens. It is used for Flask application/session features, not as the Supabase JWT.

## 4. Authenticated Request Flow

Protected views use `@login_required`.

```
Request
  -> _extract_token()
       |-- Authorization: Bearer ...
       \-- access_token cookie
  -> _session_for(token)
       -> short-lived auth session cache lookup
       -> if cache miss: Supabase Auth get_user(token)
       -> read profiles(role, school_id, status, class_id)
  -> _apply_session(...)
       -> g.user_id
       -> g.user_email
       -> g.user_role
       -> g.user_school_id
       -> g.user_class_id
       -> g.user_status
  -> role/status/session-timeout checks
  -> route handler
```

The resolved identity is cached briefly (30 seconds by default) under a SHA-256-derived cache key. The JWT `exp` claim is checked locally so an expired token cannot remain usable merely because it is present in the cache.

## 5. Access Token Refresh

When token resolution fails, `login_required` attempts:

```
refresh_token cookie
  -> Supabase Auth refresh_session()
  -> new access_token
  -> update Flask g.*
  -> continue request
```

The refreshed token is also marked internally so the response layer can replace the browser's access-token cookie.

## 6. Role Authorization

Role authorization happens after authentication.

```python
@guru_required
def teacher_page():
    ...
```

The role decorator wraps `@login_required`, reads `g.user_role`, and returns HTTP 403 (or a role dashboard redirect for browser requests) when the role is not allowed.

Current normalized roles:

- `super_admin`
- `admin_sekolah`
- `guru`
- `murid`

Legacy names such as `admin`, `teacher`, and `student` are normalized for compatibility.

## 7. School-Level Authorization

Role checks answer **who may perform an operation**. They do not, by themselves, prove that the requested row belongs to the user's school.

For school-scoped resources, ScanGrade also uses:

```python
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def exam_detail(exam_id):
    ...
```

The second decorator resolves the resource's `school_id` and compares it with `g.user_school_id`. A mismatch returns HTTP 403.

This is intentionally backed by Supabase RLS as a second enforcement layer.

## 8. Database RLS

Supabase PostgreSQL tables are protected with Row-Level Security policies. Policies use authenticated identity information such as `auth.uid()` and, in the newer schema/policies, helper functions for role and school resolution.

This means the application has two relevant authorization layers:

```
Flask authentication / RBAC
        +
resource school-scope checks
        +
Supabase RLS
```

A bug in one layer should not automatically expose every row.

## 9. Registration and Activation

School-admin registration is deliberately separate from ordinary login:

```
POST /auth/register
  -> create Supabase Auth user
  -> create/update profiles row
  -> create school_registration_requests row (pending)
  -> wait for super-admin approval
  -> activation code issued
  -> POST /auth/activate
  -> mark registration activated
  -> set profile status = active
```

Pending accounts are redirected to the activation flow and cannot enter protected application pages.

### Issued credentials (a login card's password is a one-time password)

A school does not ask its pupils to register — it issues them credentials. `app/services/login_cards.py`
writes a random password for each account through the admin API and sets
`profiles.must_change_password = true`. The address printed beside it comes from Supabase Auth
(the value `/auth/login-user` matches), with `profiles.email` — the mirror column migration 040 adds —
as the fallback when the auth listing cannot be read.

Until that password is replaced, the account is admitted but not let in: `login_required` reads
`g.must_change_password` and redirects every page to `/auth/change-password`. The exempt list is
exactly the way out a locked reader needs — the change page itself (or the redirect loops), both
login doors, logout, `/static/`, and `/api/` (a school reprints cards whenever it likes, including
mid-sitting, and blocking an autosave would lose real answered work for a rule about what the reader
*sees*, not what they save).

`/auth/change-password` is the one page reached **with** a session, and `base.html` chooses its
layout from `{% if g.user_id %}`: the authenticated branch renders `{% block content %}`, the other
`{% block content_noauth %}`. This page therefore fills **both**, out of one `body()` macro — it used
to fill only `content_noauth`, so the reader it exists for (signed in, password still the printed
one) was handed the chrome over an empty `main`: no form, nothing to submit, and no sentence saying
why. Reported as "masih kosong dan belum berhasil"; guarded in `tests/unit/test_change_password_page.py`
by rendering the real template with a request context, plus a sweep that every view behind a
`*_required` decorator fills `content` in a page that extends `base.html`.

It proves the **current** password rather than trusting the open session, writes
the new one through the admin API, records `must_change_password = false` and `password_changed_at`,
then destroys the session and lands the reader on their own login door — nothing else in the app can
prove the new password works, so a fresh sign-in is the only honest end of the flow. Both writes are
deliberately not one try block: the password is the change, the record only *says* it happened, so a
database that cannot hold the record must not report a landed change as failed. `password_change_record()`
in `app/utils/auth.py` is the one place that record is written, and both routes (this page and the
reset by code) share it.

The page is now **reachable from the chrome every role shares**: `base.html` carries it once in the
sidebar's `<!-- User -->` block (a key icon, with a translated tooltip) and once in the top-right
account menu (a translated label), so all six roles — `super_admin`, `admin_sekolah`, `principal`,
`vice_principal`, `guru`, `murid` — find it in one click in the language they are reading. It was not
linked anywhere before: route, template and rule all existed, and the only way in was typing the URL,
which is how a page that works gets reported as a page that does not exist. The guard is
`tests/unit/test_password_change_reachable.py`: the two doors, their translated labels, that the route
carries `@login_required` alone (a role decorator would lock one role out of its own credential), and
that a change made by each of the six roles writes `profiles.id` for the **session's own**
`g.user_id` through Supabase's admin API — one code path and one `where` clause, so one role's change
cannot land on another's row.

`/auth/login-user` admits `guru`, `murid`, `principal` and `vice_principal`; `/auth/login` admits the two
admin roles. A reset by code — `/auth/forgot-password` → `/auth/verify-reset-code` →
`/auth/set-new-password` — is a password change like any other and clears the same flag, and the
lookup is role-agnostic: it recognises an account by recovery phone, then NISN (pupil) or
`employee_id` (teacher), then the `profiles.email` mirror, then the paged auth listing as a last
resort. The mirror is the one that matters for the two oversight roles: a principal or vice
principal has no `students`/`teachers` row, and the issued address on their card is all they usually
know. Both login doors link to `/auth/forgot-password` — the pupil/teacher door served four of the
six roles and, until it carried the link, those four could reset only by typing the URL.

### Saving the new password (the browser's side)

`/auth/change-password` carries **no** `autocomplete="off"` — that attribute is the signal that
suppresses the browser's "save this password?" offer, and the one change the page exists to make is
exactly the credential a browser should remember. Its three fields are marked `current-password`
and `new-password` so the browser can tell which one to store; `/auth/set-new-password`,
`/auth/reset-password` and `/auth/register` mark their fields `new-password` for the same reason.
Guarded in `tests/unit/test_password_onboarding.py`, which reads the form and input tags rather than
the file — the page's own comment explains the attribute it removed.

### The identity the mail wears

Every outbound message resolves through one place (`app/services/smtp_settings.py`): the stored
mailbox first, the environment as fallback, and a blank `smtp_from` becomes
`ScanGrade <scangrade9@gmail.com>` so the `From` header is not a bare Gmail address, which is the
shape a school's filter reads as bulk mail. A `Reply-To` is set unconditionally, defaulting to
`noreply@scangrade.web.id`: a reset code is a one-way message, and leaving the header empty makes a
mail client answer the sending mailbox, which nobody watches. An operator can override both on
`/super-admin/email-settings`. The credential itself is *not* in the repository — it must be a Gmail
App Password stored on that page (or in `SMTP_PASSWORD`), and the page's "Send a test" action is how
the path is proven before a user relies on it.

The **body** is one module for every user-facing mail (`app/services/email_bodies.py`),
and it was not: the reset code was a bare string with the code framed in box-drawing
characters (`┌─────┐`), a super-admin password reset and a payment receipt were bare
paragraphs, and the school-activation mail was its own orange HTML blob. Each body now
carries an Indonesian section and an English one, ships a complete HTML document **and**
a plain-text alternative in one `multipart/alternative` (so a client that refuses HTML
still reads the code, and a one-part HTML mail is what spam filters score highest), and
escapes the name and the code rather than concatenating them into the layout — a name
comes from an import sheet, so `<b>Budi</b>` is a name. One sender does the sending
(`smtp_settings.send`): `notification_service.send_email` used to open its own
`smtplib` connection with its own `From`/`Reply-To`, which is a second answer to every
question the resolver answers, and only one of them was receiving fixes.

The **headers below the body** are what decide whether any of it is read
(`smtp_settings.send`): `Date` and a `Message-ID` whose domain belongs to the sending mailbox, so a
filter can age the message and does not read it as forged or replayed; `Auto-Submitted:
auto-generated` and `X-Auto-Response-Suppress: All` (RFC 3834), the standard way to say
"transactional, no human wrote it", which keeps a reset code out of the *bulk* folder and stops
vacation responders answering it; and `Importance: high` + `X-Priority` on the mails whose reader
cannot wait — a reset code, an activation code, a new password from super admin, a payment receipt
(`important=True` at those four call sites) while the internal deploy alerts stay unmarked, because a
flag every mail carries means nothing. `Precedence: bulk` is deliberately **never** set: it is the
header people add believing it means "bulk mail", and it is what routes a transactional message to
the bulk tab. None of this is visible in a rendered preview, which is why it was missing; the guard
is `tests/unit/test_email_delivery_headers.py`, and `.freebuff/mutate_email_delivery.py` injects
each absence and each misuse to prove it bites.

The password is read under either name a `.env` carries it in: `SMTP_PASSWORD`, or the spelling
Google's own page produces — `APP_PASSWORD_GMAIL` / `app_password_gmail`. A checkout was found with
`app_password_gmail=…` filled in and `SMTP_PASSWORD` commented out, and the mail path looked up the
second name only: the box held the right secret and reported itself unable to send anything, so every
reset email was skipped with a warning nobody read. The two Gmail names also have their whitespace
squeezed, because Google prints an app password in four groups (`abcd efgh ijkl mnop`) and the grouped
string authenticates as garbage; `SMTP_PASSWORD` is left exactly as written, since an ordinary SMTP
password may contain a space as a character. One helper decides this (`config.env_password`), and the
mail path, the settings page and the deploy alerts all resolve through it.

### What the mailbox actually did

`configured` is `bool(user and password)` — an *intention*. A box holding the wrong app password is
configured and unable to send a single reset, and until this existed the only trace was a
`logger.warning` in a journal that needs a shell to read: the first person to find out was the pupil
who could not get back into their account. So the sender records what happened.

`app/services/mail_ledger.py` keeps the outcome of the last twelve attempts in one `system_settings`
row (`mail_delivery`), written and read by `smtp_settings.send` — the one function every mail path
already goes through (reset code, school activation, super-admin reset, payment receipt, test send,
and any path added later). Three states, not two: `sent`, `failed` (the relay refused, and its own
words are kept, truncated) and `skipped` (there is no credential at all). They have different fixes,
and an operator who reads one as the other changes the wrong thing. `since` is the timestamp of the
first failure in the current run, kept across further attempts and cleared by the first success, so a
path broken since breakfast does not read like one that broke a minute ago.

The record is built from an allow-list of keys — outcome, recipient, subject, the relay's words, the
time — and **never the body and never a code**: a reset code in a settings row is a credential in a
place nobody audits, and the suite asserts what was *persisted* is that allow-list rather than
trusting the construction. Every function in the module swallows its own failures, deliberately: a
store that cannot be reached yields `unknown`, and a send whose ledger write fails still returns what
the relay said. A reporting feature that turns a working reset into a 500 is worse than no reporting.

Two pages read it: `/super-admin/email-settings` shows the observed state beside the credential, and
the super-admin **dashboard** carries a card only when the most recent attempt did not go out — an
operator who never opens the mail settings page is not told nothing, and a healthy box is not given a
banner, because one that is always there stops being a signal. The one failure neither page can see
without help is a reset whose body could not even be built (the exception happens before the sender
is reached); `auth.py` records that case itself. Guards: `tests/unit/test_mail_ledger.py` (21) and
`.freebuff/mutate_mail_ledger.py` (16/16 injected defects caught).

## 10. Login Throttling

Authentication calls are paced before calling Supabase Auth. The implementation can coordinate the pacing through Redis across multiple Gunicorn workers and falls back to an in-process lock when Redis is unavailable.

Credential failures are additionally counted per account, while transient/rate-limit failures are not treated as bad passwords.

This matters for a school deployment where many users may share one public IP address.

## 11. Session Lifetime

Role-specific timeouts are configured in `app/utils/auth.py`:

| Role | Idle timeout | Absolute timeout |
|---|---:|---:|
| super_admin | 15 min | 4 h |
| admin_sekolah | 30 min | 8 h |
| guru | 60 min | 12 h |
| murid | 120 min | 24 h |

The idle and absolute checks are application-level controls. Supabase token expiry/refresh remains a separate authentication mechanism.

## 12. Logout

Logout invalidates the server-side cached session derived from the access token and clears the browser's authentication cookies. The cached-session invalidation prevents a recently logged-out token from continuing to authenticate through the short cache window.

## 13. Security Controls Around Auth

Authentication is combined with:

- CSRF validation for state-changing requests.
- HttpOnly/SameSite cookies.
- Secure cookies in production.
- Login/register/API rate limits.
- Audit logging for login/register/activation events.
- No-store caching headers on login pages.
- Supabase RLS for database isolation.

## 14. Important Design Distinction

The most important architectural distinction is:

```
Authentication
= "Who is this user?"
        |
        v
Role authorization
= "What may this role do?"
        |
        v
School/resource authorization
= "May this user access THIS row?"
        |
        v
RLS
= database-level enforcement of the same boundary
```

A route should not rely on a role check alone when the resource itself is school-scoped.
