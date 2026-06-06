# ScanGrade — Product Requirements Document (PRD)

## 1. Product Overview

**ScanGrade** adalah platform koreksi ujian digital berbasis web yang mendukung scan OMR via kamera, koreksi esai dengan AI similarity scoring + teacher override, anti-cheat, analytics dashboard, dan export hasil ke XLSX/PDF.

**Target pengguna:**
- Super Admin — mengelola seluruh sistem dan sekolah
- Admin Sekolah — mengelola sekolah, guru, siswa, kelas, mata pelajaran
- Guru — membuat ujian, mengoreksi, melihat hasil
- Siswa — mengerjakan ujian, melihat nilai

**Tech Stack:** Flask 3.1, Supabase (PostgreSQL + Auth + Storage), Tailwind CSS, Alpine.js, HTMX, Chart.js

---

## 2. Target Audience

| Role | Jumlah (estimasi) | Kebutuhan utama |
|------|-------------------|-----------------|
| Super Admin | 1-5 | Manajemen semua sekolah, approval registrasi |
| Admin Sekolah | 1-3 per sekolah | Manajemen guru, siswa, kelas, mata pelajaran, tahun ajaran |
| Guru | 10-50 per sekolah | Buat ujian, koreksi esai, upload PDF, export hasil |
| Siswa | 100-500 per sekolah | Kerjakan ujian (offline-first), lihat nilai |

**Concurrent target:** 500 siswa per sesi ujian

---

## 3. Feature Requirements

### 3.1 Authentication & Authorization (P0)
- [x] Login/register via email (Supabase Auth)
- [x] Role-based access: super_admin, admin_sekolah, guru, murid
- [x] JWT token via cookie + Authorization header
- [x] Auto-create profile on signup (trigger)
- [x] Role-based RLS di Supabase

### 3.2 Multi-School Management (P0)
- [x] Tabel schools, school_years, registration_codes
- [x] Admin sekolah mengelola data sekolahnya sendiri
- [x] Super admin melihat semua sekolah
- [x] Import guru/siswa via Excel

### 3.3 Exam Builder (P0)
- [x] Buat ujian dengan judul, mapel, durasi, passing score
- [x] Tambah soal MCQ (A-E) dan Essay (teks + canvas)
- [x] Upload PDF soal (convert ke page images)
- [x] Set answer key + bobot nilai per soal
- [x] Atur anti-cheat: tab switch penalty, fullscreen, block copy-paste
- [x] Atur randomize questions & options
- [x] Atur watermark nama siswa
- [x] Kalkulator ilmiah (opsional)

### 3.4 Student Exam Experience (P0)
- [x] Offline-first: localStorage + sync ke server
- [x] Countdown timer + auto-submit
- [x] Drawing tools: pen, line, eraser, text
- [x] Measurement tools: ruler 30cm, protractor 0-180°, set square 10cm, compass
- [x] Scientific calculator (jika diaktifkan)
- [x] Anti-cheat: tab switch detection with graduated penalty
- [x] Watermark nama siswa
- [x] MCQ options A-E + canvas corat-coret per soal

### 3.5 Grading & Review (P0)
- [x] Guru mengoreksi esai dengan canvas overlay di atas PDF
- [x] Teacher tools: pen, eraser, text box (multi-font), ruler, protractor, triangle
- [x] Skor per soal + komentar
- [x] Override final score
- [x] Auto-save + publish nilai
- [x] Retraction request approval

### 3.6 Results & Analytics (P1)
- [x] Dashboard guru: rata-rata, jumlah siswa, distribusi nilai
- [x] Chart.js: distribusi nilai, soal tersulit, per-band stats
- [x] Export XLSX (openpyxl)
- [x] Export PDF (ReportLab)
- [x] Export bubble sheet (LJK)

### 3.7 Anti-Cheat (P0)
- [x] Tab switch detection via visibilitychange
- [x] Graduated penalty: 1st=warning, 2nd=-N, 3rd=-2N, 4th+=-3N
- [x] Auto-submit on max violations
- [x] Block right-click, copy-paste
- [x] Watermark overlay
- [x] Rate-limited violation logging

### 3.8 OMR Scanning (P1)
- [x] Scan lembar jawaban via kamera (enumerateDevices)
- [x] Auto-detect MCQ bubbles
- [x] Generate LJK (bubble sheet PDF)

---

## 4. User Stories

### Guru
```
Sebagai guru, saya ingin membuat ujian dengan cepat,
mengupload PDF soal, mengatur bobot nilai, dan mengaktifkan
anti-cheat — semua dalam satu form.
```
```
Sebagai guru, saya ingin mengoreksi esai siswa langsung
di atas halaman PDF dengan pena digital, penggaris, busur,
dan siku — tanpa perlu print.
```
```
Sebagai guru, saya ingin melihat analitik hasil ujian
dalam bentuk chart serta mengekspor ke XLSX/PDF.
```

### Siswa
```
Sebagai siswa, saya ingin mengerjakan ujian tanpa khawatir
koneksi internet putus — jawaban tetap tersimpan di lokal.
```
```
Sebagai siswa, saya ingin menggunakan penggaris, busur,
dan kalkulator selama ujian fisika/matematika.
```

### Admin Sekolah
```
Sebagai admin sekolah, saya ingin mengelola guru, siswa,
kelas, dan mata pelajaran dalam satu dashboard.
```

---

## 5. Technical Architecture

### Frontend
- **CSS Framework:** Tailwind CSS (CDN)
- **Reactivity:** Alpine.js v3.14
- **AJAX:** HTMX v2
- **Charts:** Chart.js 4.x
- **PDF Viewer:** pdf.js

### Backend
- **Framework:** Flask 3.1 (app factory + blueprints)
- **Database:** Supabase PostgreSQL + RLS
- **Auth:** Supabase Auth (email/password + JWT)
- **Storage:** Supabase Storage (exam-pdfs, student-answers)
- **PDF Processing:** PyMuPDF (render), ReportLab (export)
- **OMR:** OpenCV + pytesseract

### Database (Supabase)
- **Tables:** profiles, schools, classes, subjects, exams, submissions, violation_logs, teacher_assignments, audit_logs
- **RLS:** Role-based per baris (super_admin, admin_sekolah, guru, murid)
- **Triggers:** Auto-create profile on signup, auto-create teachers/students records on role change

### Arsitektur Offline-First
```
Siswa (browser)
  └─ Jawaban → localStorage (instant, 0ms)
       ├─ Light sync: MCQ + teks setiap 20 detik
       └─ Heavy sync: Canvas JPEG setiap 60 detik

Server (Flask)
  ├─ Rate limit: 3s (light), 10s (canvas)
  ├─ Per-user lock: mencegah concurrent DB writes
  └─ Dirty hash check: skip jika data tidak berubah
```

---

## 6. Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| Concurrent users | 500 siswa |
| Response time (API) | <500ms p95 |
| Offline resilience | Full offline with auto-sync |
| Storage per exam (PDF) | Max 50MB |
| Canvas data size | ~30-80KB per page (JPEG q0.4) |
| Rate limit auth | 30 req/menit |
| Rate limit API | 30 req/menit |
| Browser support | Chrome, Firefox, Edge, Safari (2 versi terakhir) |

---

## 7. Future Features (P2)
- AI similarity scoring untuk esai
- Notifikasi WhatsApp (Fonnte) untuk publish nilai
- Payment gateway (Midtrans) untuk tryout berbayar
- Celery + Redis untuk async task queue
- Screenshot blocking via Screen Capture API

---

## 8. Glossary

| Istilah | Definisi |
|---------|----------|
| LJK | Lembar Jawaban Komputer (bubble sheet) |
| OMR | Optical Mark Recognition |
| RLS | Row Level Security (Supabase) |
| MCQ | Multiple Choice Question |
| Essay | Soal uraian (teks + canvas) |
| Canvas | Area gambar digital untuk coretan/jawaban |
