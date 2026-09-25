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
