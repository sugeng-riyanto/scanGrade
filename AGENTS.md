# ScanGrade — AI Context File

## Stack
- **Backend**: Flask 3.x + Supabase (Python Client)
- **Frontend**: Tailwind CSS + HTMX + Chart.js + PDF.js
- **Auth**: Supabase Auth (JWT via Service Key for backend)
- **Deploy**: Gunicorn + ngrok (dev) / VPS (prod)
- **Testing**: pytest + Playwright

## Project Structure
```
app/
├── __init__.py          # Flask app factory
├── config.py            # Dev/Prod config classes
├── routes/              # Blueprints
│   ├── auth.py          # Login/register/me
│   ├── exam.py          # CRUD ujian + PDF upload
│   ├── admin.py         # Dashboard admin
│   ├── teacher.py       # Builder + grader UI
│   ├── student.py       # Take exam + results
│   ├── api.py           # Anti-cheat endpoints
│   ├── publish.py       # Publish scores
│   └── webhook.py       # Midtrans/Fonnte callbacks
├── services/            # Business logic
├── models/              # Supabase query helpers
├── utils/               # Decorators, security, helpers
└── templates/           # Jinja2 + Tailwind
```

## Key Rules
- **Anti-Cheat**: Frontend `visibilitychange` + debounce 1500ms; backend validates timestamps ±5min, rate limit 1 log/2s. Penalty always computed server-side.
- **RLS**: Service key for backend operations; anon key for public reads only.
- **CORS**: Allow localhost + ngrok URL (dynamic via env).
- **No secrets in .env committed** — use `.env.example` for templates.
