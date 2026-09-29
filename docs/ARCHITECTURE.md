# Architecture

## High-Level Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Browser   │────▶│  Flask App (WSGI) │────▶│    Supabase     │
│  Tailwind   │     │  Gunicorn x4      │     │  PostgreSQL +   │
│  Alpine.js  │     │  Jinja2 Templates │     │  Auth + Storage │
│  HTMX       │     │  REST API         │     │  (Supabase SaaS)│
└─────────────┘     └──────────────────┘     └─────────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │   Sentry     │
                    │  (Errors)    │
                    └──────────────┘
```

### Key Design Decisions

- **Monolith (not microservices)**: Single Flask app for simplicity; no network overhead between components
- **No Celery**: All operations synchronous; CSV import is inline (chunked 100 rows for memory efficiency)
- **Supabase service key for DB ops**: Bypasses RLS (simpler queries); security via `@require_school_access` decorator
- **Supabase anon key for auth**: JWT verification via Supabase Auth API
- **The database client is wrapped once, and it is where a dropped connection is answered**: Supabase keeps an HTTP connection alive and closes it, and the client writes the next query into the dead socket — `httpx.RemoteProtocolError: Server disconnected` on a query that is correct (measured: 4 of 10 identical reads). `app/utils/supabase_retry.py` wraps the client in `create_app`, so every `get_supabase().table(...).execute()` in the app is covered without touching 679 call sites. Three rules hold there: a **read** (GET/HEAD, decided from the builder's own `http_method` at `execute()` time) is retried; a **write is never re-sent**, because a dropped reply does not say whether the statement ran and re-sending an insert applies it twice; and a dropped reply on a **PATCH or DELETE whose filter names the rows it meant** is settled by one read — the rows are read back and the write is reported as done only if they carry the patch. Everything the read cannot settle keeps failing as before: an insert (no filter), an unfiltered write (would need a table-wide read), a patch that moves the column it filters on (the filter no longer matches, so the read would call a landed write lost), and any genuine `APIError`, which is the server answering. `value_matches` compares timestamps and JSON columns by value rather than by text, because `…+00:00` and `…Z` are the same instant and comparing them as strings would report a landed write as lost.

## Component Flow

### Exam Creation
```
Teacher → /teacher/exams/new → Form → POST → 
  exams table INSERT (teacher_id, school_id, settings) →
  Redirect to exam detail
```

### Student Taking Exam
```
Student → /student/exams/<id> → 
  Alpine.js component (timer, canvas, anti-cheat) →
  Auto-save via /api/student/sync-draft every 20s →
  Manual submit → POST /student/exams/<id>/submit →
  submissions table INSERT/UPDATE
```

### Grading (Teacher)
```
Teacher → /teacher/grade/<submission_id> →
  View answers →
  Manual score override OR AI grading →
  POST /api/grade/ai-suggest → LLM API → score + feedback →
  submissions.teacher_feedback UPDATE
```

### Anti-Cheat Violation
```
Tab switch → 1.5s timer → handleViolation() →
  sendBeacon /api/violation/log →
  Server: INSERT violation_logs + UPDATE submissions.penalty →
  If max violations: auto-submit exam
```

### Subscription/Payment
```
Admin → /admin-sekolah/subscription →
  Select plan → Midtrans Snap widget →
  Payment success → Midtrans webhook → 
  Generate activation code → INSERT payment_transactions →
  Redeem code → UPDATE school_subscriptions
```

## File Structure
```
app/
├── __init__.py          # App factory, blueprints, extensions
├── config.py            # Configuration classes
├── errors.py            # Custom exception classes
├── decorators/
│   ├── security.py      # @require_school_access
│   └── subscription.py  # @require_subscription
├── handlers/
│   └── error_handlers.py # Flask error handlers
├── middlewares/
│   └── auth.py          # Alias for security decorator
├── models/
│   └── supabase_queries.py
├── routes/
│   ├── auth.py, admin.py, admin_sekolah.py
│   ├── teacher.py, student.py, api.py
│   ├── exam.py, super_admin.py
│   ├── public.py, students.py
│   └── publish.py, webhook.py, tools.py
├── services/
│   ├── ai_service.py, omr_service.py
│   ├── midtrans_service.py, pdf_service.py
│   ├── subscription_service.py
│   └── export_service.py, audit_service.py
├── templates/           # Jinja2 templates
└── utils/
    ├── auth.py          # Decorators, supabase clients
    ├── logger.py        # Structured JSON logging
    ├── security.py      # CSRF, sanitization
    └── rate_limiter.py  # Rate limiting middleware
```

## Data Isolation

Every data table has a `school_id` foreign key → `schools.id`. Access is controlled at 3 levels:
1. **RLS Policies** (Supabase — bypassed by service key)
2. **`@require_school_access` decorator** (Flask — verifies school_id match)
3. **Query filters** (`.eq("school_id", sid)` in every query)
