# Security Documentation

## Authentication

- **JWT via Supabase Auth**: Tokens are stored in `access_token` cookie
- **Two clients**: `get_supabase()` (service key — bypasses RLS) for data ops, `get_auth_client()` (anon key) for auth
- **Session**: HttpOnly cookies, SameSite=Lax, Secure in production

## Authorization Decorators

| Decorator | Role | Description |
|-----------|------|-------------|
| `@login_required` | Any authenticated | Checks JWT + fetches profile |
| `@super_admin_required` | super_admin | Full access |
| `@admin_sekolah_required` | admin_sekolah | School management |
| `@guru_required` | guru | Exam creation/grading |
| `@murid_required` | murid | Take exams only |
| `@teacher_or_admin_required` | guru + admin | Combined |

## Data Isolation (School Scoping)

Every table has a `school_id` UUID foreign key. The `@require_school_access` decorator verifies the user's `school_id` matches the resource's `school_id`:

```python
@teacher_bp.route("/exams/<exam_id>")
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def exam_detail(exam_id):
    ...
```

Applied to 25+ routes across teacher and admin_sekolah blueprints.

## Row-Level Security (Supabase)

22 tables have RLS enabled with school-scoped policies. Helper functions:
- `public._is_role(role)` — checks authenticated user's role
- `public._user_school_id()` — returns authenticated user's school_id

Policy per table documented in `docs/SECURITY_RLS_MATRIX.md`.

## Rate Limiting

Two layers:
1. **Custom middleware** (`app/utils/rate_limiter.py`): Redis-backed with memory fallback
2. **Flask-Limiter**: `@limiter.limit("5 per minute")` on auth routes

| Group | Limit | Scope |
|-------|-------|-------|
| Auth (login) | 5/minute | Per IP |
| Register | 10/10 minutes | Per IP |
| API | 30/minute | Per IP |
| OMR Scan | 20/minute | Per user |
| Upload | 10/5 minutes | Per IP |
| Default | 60/minute | Per IP |

## File Upload Security

- Extension whitelist: `.jpg`, `.jpeg`, `.png` (images)
- MIME type validation via `python-magic`
- Image integrity check via Pillow `verify()`
- EXIF data stripped (removes GPS/metadata)
- Max file size: 20MB (images), 50MB (PDF via Flask config)

## CSRF Protection

- `generate_csrf_token()` injects token into Jinja2 globals
- `csrf_required` decorator validates on POST/PUT/DELETE
- `X-CSRF-Token` header auto-injected by HTMX/AJAX requests

## Error Handling

- `sentry_sdk` captures 100% errors, 10% performance traces
- Structured JSON logging (timestamp, level, message, extra context)
- User-facing messages in Bahasa Indonesia via custom exception classes

## Security Headers

- `SESSION_COOKIE_HTTPONLY = True`
- `SESSION_COOKIE_SAMESITE = "Lax"`
- `X-Response-Time-ms` performance header (internal)
