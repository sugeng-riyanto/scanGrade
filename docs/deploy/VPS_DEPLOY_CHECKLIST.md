# VPS Deploy Checklist — AI Essay Grading (Fase 1-6)

## 1. Pull Latest Code
```bash
cd /opt/scangrade
git pull
```

## 2. Install Dependencies
```bash
pip install sentence-transformers torch --no-cache-dir
# ~5 menit (download model embedding 400MB)
```

## 3. Run SQL Migration
Buka Supabase Dashboard → SQL Editor → paste & run:
```
supabase/migrations/014_ai_grading.sql
```

Atau via `psql`:
```bash
PGPASSWORD=<password> psql -h <host> -U postgres -d postgres -f supabase/migrations/014_ai_grading.sql
```

## 4. Restart Services
```bash
sudo systemctl restart scangrade
sudo systemctl restart scangrade-celery
```

## 5. Verify
```bash
# Cek service
sudo systemctl status scangrade --no-pager -n 5
sudo systemctl status scangrade-celery --no-pager -n 5

# Cek log
sudo journalctl -u scangrade -n 10 --no-pager
```

## 6. Test Flow
- [ ] Buka `/teacher/ai-settings` → Wizard API Key muncul
- [ ] Upload PDF soal baru → deteksi MCQ/Essay otomatis
- [ ] Essay grading → tombol ✨ Essay di grade_detail
- [ ] Cache → grading ulang instant (0 API call)
