# ScanGrade

Aplikasi scanning dan grading ujian online berbasis Flask + Supabase.

## Stack
- **Backend**: Flask 3.x
- **Database**: Supabase (PostgreSQL + RLS)
- **Auth**: Supabase Auth (JWT)
- **Frontend**: Tailwind CSS + HTMX + Chart.js + PDF.js
- **Deploy**: Gunicorn + ngrok

## Setup

```bash
cp .env.example .env
# Isi .env dengan credentials Supabase
pip install -r requirements.txt
flask run
```

## Testing

```bash
pytest
```

## Dokumentasi
- `AGENTS.md` - Konteks untuk AI assistant
- `supabase/schema.sql` - Database schema
