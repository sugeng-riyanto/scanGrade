# Setup Guide — ScanGrade

## Prerequisites
- Python 3.12+
- Supabase account (free tier)
- Git

## Local Development Setup

### 1. Clone Repository
```bash
git clone https://github.com/sugeng-riyanto/scanGrade.git
cd scanGrade
```

### 2. Virtual Environment
```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# Linux/Mac
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment
```bash
cp .env.example .env
```

Edit `.env` with your Supabase credentials:
```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=eyJ... (service_role key)
SUPABASE_ANON_KEY=eyJ... (anon key)
FLASK_SECRET_KEY=generate-random-secret-here
```

### 5. Setup Database

Open Supabase SQL Editor and run:
1. `supabase/_COMPLETE_SETUP.sql` — creates all tables, RLS, triggers
2. `supabase/migrations/001_enable_rls_and_policies.sql` — RLS policies
3. `supabase/migrations/20260608_fix_rls_policies.sql` — additional RLS
4. `supabase/migrations/20260608_usage_tracking.sql` — usage & demo tables

### 6. Seed Demo Data
```bash
python manage.py seed --exam
```

This creates:
- Super Admin: `superadmin@scan-grade.app` / `superadmin123`
- 3 Schools with admin, teachers, students
- Sample exams & submissions (with `--exam`)
- Sample invoices

### 7. Start Development Server
```bash
python wsgi.py
# or
flask run --debug
```

Visit `http://127.0.0.1:5000`

## Demo Accounts

| Role | Email | Password |
|------|-------|----------|
| Super Admin | superadmin@scan-grade.app | superadmin123 |
| Admin SMP | admin_smp@scan-grade.app | demo123 |
| Admin SMA | admin_sma@scan-grade.app | demo123 |
| Admin SMK | admin_smk@scan-grade.app | demo123 |
| Guru SMP | guru_mtk_smp@scan-grade.app | demo123 |
| Siswa SMP | siswa1_smp@scan-grade.app | demo123 |

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `Redis error: Connection refused` | Safe to ignore — Flask-Limiter falls back to memory:// |
| `ImportError: No module named 'magic'` | `pip install python-magic` (Windows: `python-magic-bin`) |
| `Supabase connection refused` | Check SUPABASE_URL in .env, verify project is active |
| `Flask-Limiter not installed` | `pip install flask-limiter` |
| Port 5000 in use | `flask run --port=5001` |

## Running Tests
```bash
pytest tests/ -v
```
