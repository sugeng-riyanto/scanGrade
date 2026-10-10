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
| Auth (login) | 300/minute (in-view backstop) + 8 failed / 15 min | Per IP + per account (identifier) |
| Register | 10/10 minutes | Per IP |
| API | 30/minute | Per IP |
| OMR Scan | 20/minute | Per user |
| Upload | 10/5 minutes | Per IP |
| Default | 60/minute | Per IP |

### Satu pintu login

`/auth/sign-in` adalah satu-satunya halaman yang memeriksa password; `/auth/login`
dan `/auth/login-user` adalah alias yang meneruskan (GET) atau tetap memproses
login (POST). Identifier yang sama — email, NISN, atau NIP — dicocokkan terhadap
seluruh peran, dan tab pada halaman tidak pernah dipakai untuk mempersempit
pencarian, sehingga tidak ada "pintu yang salah" yang bisa ditolak.

Penolakan selalu satu kalimat generik yang sama untuk identifier tidak dikenal,
password salah, dan peran yang tidak dikenal aplikasi ini. Yang sengaja **berbeda**
hanya kegagalan infrastruktur (baris profil yang tidak terbaca *dan* tidak ada peran
di metadata akun): kalimat sementara, dan — ini bagian pentingnya — percobaan itu
**tidak** memakan jatah percobaan akun, supaya lonjakan rate limit di sisi kami
tidak mengunci sebuah sekolah selama 15 menit.

Rate limiting tidak dilonggarkan:

- ketiga URL yang memeriksa password memakai backstop `300 per minute` per IP **di
dalam view**, plus penghitung **per akun** (`check_account_limit("login_failed",
  identifier)`, 8 percobaan / 15 menit) yang dikunci pada identifier yang diketik —
  bukan pada IP — supaya satu sekolah di balik satu NAT tidak saling mengunci;
- ketiganya juga ada di `_exact_exempt` middleware rate limiter, sebab bucket grup
  per-IP di middleware bersifat **tambahan**, bukan pengganti: halaman yang tidak
  dikecualikan akan dihitung dua kali untuk perbuatan yang sama sementara aliasnya
  dihitung sekali. `tests/unit/test_sign_in_merged.py` menjaga ketiganya tetap sama.

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
