# ScanGrade - AI Agent Context

## Project Overview
Sistem koreksi ujian digital dengan fitur:
- Scan lembar jawaban pilihan ganda via kamera (seperti ZipGrade)
- Koreksi esai dengan AI similarity scoring + teacher override
- Anti-cheat: deteksi tab switch dengan penalty configurable
- Analytics dashboard interaktif untuk guru
- Export hasil ke XLSX/PDF + publish ke siswa via login/WA

## Tech Stack
| Layer | Technology | Notes |
|-------|-----------|-------|
| Backend | Flask 3.x | App factory pattern, blueprints |
| Language | Python 3.10+ | Type hints wajib |
| Database | Supabase PostgreSQL | Row Level Security (RLS) |
| Auth | Supabase Auth | Email/password + JWT |
| Storage | Supabase Storage | Bucket: exam-pdfs, student-answers |
| Frontend | Tailwind CSS + HTMX | Vanilla JS, no React/Vue |
| Charts | Chart.js 4.x | Untuk analytics dashboard |
| PDF | ReportLab + pdf.js | Export laporan + viewer soal |
| Excel | openpyxl | Export hasil ujian |
| Queue | Celery + Redis | Untuk notifikasi async |
| Tunnel | ngrok | Development exposure |

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

## Security & Best Practices
### Environment Variables
- **WAJIB**: Jangan commit file .env ke GitHub!
- Copy .env.example ke .env dan isi nilai sebenarnya

### Row Level Security (RLS) Rules
- **profiles**: User hanya bisa baca/update profil sendiri
- **exams**:
  - Guru: CRUD exam yang teacher_id = auth.uid()
  - Siswa: READ only exam dengan status='active' + punya access code
- **submissions**:
  - Guru: READ/WRITE submission untuk exam miliknya
  - Siswa: READ only submission sendiri DAN is_published=true
- **violation_logs**:
  - Backend: INSERT via service key
  - Guru: READ logs untuk exam miliknya
- **exam_access_codes**:
  - Guru: CREATE codes untuk exam miliknya
  - Siswa: READ only code miliknya yang belum used

## Anti-Cheat Implementation Rules
- **Frontend deteksi**: visibilitychange + debounce 1500ms
- **Logging**: navigator.sendBeacon untuk reliability saat tab ditutup
- **Server validation**: Cek timestamp ±5 menit, rate limit 1 log/2 detik
- **Penalty calculation**: SELALU di backend, jangan percaya frontend
- **False positive mitigation**: Ignore blur <500ms, toleransi iOS +1 violation

## Code Style
- Python: Type hints + Google docstring
- Flask blueprints dengan prefix URL
- Error handling di tiap endpoint
- Supabase client via app.extensions

## Key Rules
- **Anti-Cheat**: Frontend `visibilitychange` + debounce 1500ms; backend validates timestamps ±5min, rate limit 1 log/2s. Penalty always computed server-side.
- **RLS**: Service key for backend operations; anon key for public reads only.
- **CORS**: Allow localhost + ngrok URL (dynamic via env).
- **No secrets in .env committed** — use `.env.example` for templates.
