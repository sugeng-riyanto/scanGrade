# Development Guide

## Project Structure

```
scanGrade/
├── app/                    # Flask application
│   ├── __init__.py         # App factory (create_app)
│   ├── config.py           # Configuration classes
│   ├── errors.py           # Custom exceptions
│   ├── decorators/         # @require_school_access, @require_subscription
│   ├── handlers/           # Error handlers
│   ├── middlewares/        # Auth middleware aliases
│   ├── models/             # Supabase query helpers
│   ├── routes/             # Blueprints (auth, teacher, student, admin, api...)
│   ├── services/           # Business logic (omr, ai, midtrans, export...)
│   ├── templates/          # Jinja2 HTML templates
│   ├── static/             # JS (Alpine), images
│   └── utils/              # Auth, logger, rate limiter, CSRF
├── supabase/migrations/    # SQL migration files
├── docs/                   # Documentation
├── tests/                  # Pytest test files
├── deploy/                 # Systemd service + deploy script
├── manage.py               # Demo data management CLI
├── wsgi.py                 # Gunicorn entry point
└── requirements.txt        # Python dependencies
```

## Coding Conventions

- **Python**: PEP8, snake_case, type hints where helpful
- **JavaScript**: camelCase, Alpine.js reactive properties
- **HTML**: Jinja2 templates, Tailwind CSS utility classes
- **No comments in code** — keep it readable through clear naming
- **No emojis in code** (only in user-facing messages)

## How to Add a New Route

1. Create a new blueprint file in `app/routes/` or add to existing one
2. Register in `_register_blueprints()` in `app/__init__.py`
3. Create template in `app/templates/` (if rendering HTML)
4. Add `@require_school_access` decorator for school-scoped routes

Example:
```python
# app/routes/my_new_feature.py
from flask import Blueprint, render_template
from app.utils.auth import guru_required, get_supabase
from app.decorators.security import require_school_access

my_bp = Blueprint("my", __name__, url_prefix="/my")

@my_bp.route("/<resource_id>")
@guru_required
@require_school_access("exams", "resource_id")
def my_view(resource_id):
    return render_template("my/view.html")
```

## How to Add a New Service

1. Create file in `app/services/`
2. Import in route file
3. Services should be stateless functions, not classes

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_error_handling.py -v

# Run with coverage
pytest tests/ --cov=app --cov-report=term-missing
```

## Debugging

- Use `app.logger.info()` for debug messages (structured JSON)
- Browser: F12 → Console for JavaScript errors
- Flask debug mode: `flask run --debug` (auto-reloads on changes)
