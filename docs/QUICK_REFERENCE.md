# Quick Reference

## Common Commands

| Command | Description |
|---------|-------------|
| `python wsgi.py` | Start development server |
| `flask run --debug` | Start with auto-reload |
| `pytest tests/ -v` | Run all tests |
| `python manage.py seed --exam` | Seed demo data |
| `python manage.py reset` | Full reset (delete + re-seed) |
| `python manage.py generate-csv` | Generate sample CSV |
| `pip install -r requirements.txt` | Install dependencies |

## Directory Layout

```
F:\opencode\ScanGrade\scanGrade\
├── app/
│   ├── __init__.py          # App factory
│   ├── config.py            # Config
│   ├── errors.py            # Exceptions
│   ├── decorators/          # @require_school_access, @require_subscription
│   ├── handlers/            # Error handlers
│   ├── routes/              # Blueprint files
│   ├── services/            # Business logic
│   ├── templates/           # Jinja2 HTML
│   └── utils/               # Auth, logger, rate limiter
├── supabase/
│   └── migrations/          # SQL migration files
├── docs/                    # Documentation
├── tests/                   # Pytest tests
├── deploy/                  # Systemd service + deploy script
└── requirements.txt
```

## Key Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_URL` | ✅ | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | ✅ | Service role key (DB ops) |
| `SUPABASE_ANON_KEY` | ✅ | Anon key (auth) |
| `FLASK_SECRET_KEY` | ✅ | Session signing key |
| `SENTRY_DSN` | ❌ | Error tracking |
| `MIDTRANS_SERVER_KEY` | ❌ | Payment gateway |

## Common Code Patterns

**Route with decorator:**
```python
@teacher_bp.route("/exams/<exam_id>", methods=["GET"])
@subscription_write_required
@teacher_or_admin_required
@require_school_access("exams", "exam_id")
def exam_detail(exam_id):
    ...
```

**Supabase query:**
```python
supabase = get_supabase()
data = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
```

**Render template:**
```python
return render_template("teacher/exam_form.html", exam=exam, subjects=subjects)
```

**Custom error:**
```python
from app.errors import NotFoundError
raise NotFoundError("Ujian", exam_id)
```
