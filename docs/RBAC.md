# ScanGuide — Role-Based Access Control (RBAC) & Interaction Flows

## 1. Role Hierarchy

```
super_admin        (Super Admin — antar-sekolah)
    │
    └── admin_sekolah    (Admin Sekolah — 1 sekolah)
            │
            ├── guru          (Guru — mengajar)
            │
            └── murid         (Siswa — mengerjakan ujian)
```

| Role | Tujuan | Dibuat oleh | Login di |
|------|--------|-------------|----------|
| `super_admin` | Mengelola semua sekolah + pengguna sistem | Via Supabase Console | `/auth/login` |
| `admin_sekolah` | Mengelola 1 sekolah (guru, siswa, kelas, mapel) | Register mandiri (perlu approval) | `/auth/login` |
| `guru` | Membuat ujian, mengoreksi, melihat hasil | Di-import oleh admin_sekolah | `/auth/login_user` |
| `murid` | Mengerjakan ujian, melihat nilai | Di-import oleh admin_sekolah | `/auth/login_user` |

---

## 2. Authentication Flow

### 2.1. Login Admin (super_admin & admin_sekolah)
```
Browser → /auth/login → POST (email + password)
  → Supabase Auth sign_in_with_password()
  → Cek profile.status (pending → redirect /auth/activate)
  → Cek role (super_admin/admin_sekolah saja)
  → Set cookies: access_token (24h) + refresh_token (7d)
  → Redirect ke /admin/dashboard
```

### 2.2. Login Guru/Murid
```
Browser → /auth/login_user → POST (email + password)
  → Supabase Auth sign_in_with_password()
  → Cek profile.status (pending → redirect /auth/activate)
  → Cek role (guru/murid saja)
  → Set cookies + redirect ke dashboard masing-masing
```

### 2.3. Register Admin Sekolah
```
Browser → /auth/register → POST (NPSN, sekolah, WA, jabatan, email, password)
  → Buat user di Supabase Auth (role: admin_sekolah, status: pending)
  → Buat school_registration_requests (status: pending)
  → Tampilkan halaman sukses
  → Super admin approve via /admin/registration-requests
  → Email/WA dikirim kode aktivasi
  → Admin sekolah aktivasi via /auth/activate
```

### 2.4. JWT Verification (setiap request)
```
Request → login_required decorator
  → Extract token dari cookie/Bearer header
  → Supabase Auth get_user(token)
  → Fetch profile dari profiles table (role, status, school_id)
  → Set g.user_id, g.user_role, g.user_school_id
  → Cek user_status (pending → redirect)
```

---

## 3. Akses Peran (Route Protection)

| Route | super_admin | admin_sekolah | guru | murid |
|-------|:-----------:|:-------------:|:----:|:-----:|
| `/admin/dashboard` | ✅ | ✅ | ❌ | ❌ |
| `/admin/users` | ✅ | ❌ | ❌ | ❌ |
| `/admin/teachers` | ✅ | ❌ | ❌ | ❌ |
| `/admin/students` | ✅ | ❌ | ❌ | ❌ |
| `/admin/classes` | ✅ | ❌ | ❌ | ❌ |
| `/admin/exams` | ✅ | ❌ | ❌ | ❌ |
| `/admin/school` | ✅ | ❌ | ❌ | ❌ |
| `/admin/registration-requests` | ✅ | ❌ | ❌ | ❌ |
| `/admin/compliance` | ✅ | ❌ | ❌ | ❌ |
| `/admin-sekolah/*` | ❌ | ✅ | ❌ | ❌ |
| `/teacher/dashboard` | ❌ | ❌ | ✅ | ❌ |
| `/teacher/exams/*` | ❌ | ❌ | ✅ | ❌ |
| `/teacher/grade/*` | ❌ | ❌ | ✅ | ❌ |
| `/teacher/results` | ❌ | ❌ | ✅ | ❌ |
| `/teacher/analytics` | ❌ | ❌ | ✅ | ❌ |
| `/teacher/scan` | ❌ | ❌ | ✅ | ❌ |
| `/student/dashboard` | ❌ | ❌ | ❌ | ✅ |
| `/student/exams/*` | ❌ | ❌ | ❌ | ✅ |
| `/student/results` | ❌ | ❌ | ❌ | ✅ |
| `/api/*` | ✅ | ✅ | ✅ | ✅ |
| `/publish/*` | ❌ | ❌ | ✅ | ❌ |

### Decorators (digunakan di routes)

| Decorator | Roles yang diizinkan |
|-----------|---------------------|
| `@super_admin_required` | `super_admin` |
| `@admin_sekolah_required` | `admin_sekolah` |
| `@guru_required` | `guru` |
| `@murid_required` | `murid` |
| `@admin_required` | `super_admin`, `admin` |
| `@teacher_required` | `guru`, `teacher` |
| `@teacher_or_admin_required` | `guru`, `admin_sekolah`, `admin`, `teacher` |
| `@login_required` | Semua role yang sudah login |

---

## 4. Database RLS Policies (Supabase)

| Table | super_admin | admin_sekolah | guru | murid |
|-------|:-----------:|:-------------:|:----:|:-----:|
| `schools` | ALL | SELECT own | SELECT own | SELECT own |
| `profiles` | ALL | SELECT own_school + UPDATE own_school | SELECT murid + UPDATE own | SELECT own |
| `exams` | ALL | SELECT own_school | CRUD own + SELECT active | SELECT active+published |
| `submissions` | ALL | SELECT own_school | SELECT own_exam + UPDATE own_exam | SELECT own+published, INSERT own |
| `classes` | ALL | ALL own_school | SELECT own_school | SELECT own_school |
| `subjects` | ALL | ALL own_school | SELECT own_school | SELECT own_school |
| `teachers` | ALL | SELECT own_school | SELECT own | ❌ |
| `students` | ALL | SELECT own_school | SELECT own_school | SELECT own |
| `teacher_assignments` | ALL | ALL own_school | INSERT own + SELECT own | ❌ |
| `violation_logs` | ❌ | ❌ | SELECT own_exam | ❌ |
| `audit_logs` | ALL | ❌ | ❌ | ❌ |

---

## 5. Interaction Flows

### 5.1. Admin School → Import Guru & Siswa
```
admin_sekolah login → /admin-sekolah/dashboard
  ├── /admin-sekolah/teachers → Import Excel / Tambah manual
  │     → supabase.auth.admin.create_user() (role: guru)
  │     → Insert ke profiles + teachers table
  │     → Generate password (random 12 char)
  │
  └── /admin-sekolah/students → Import Excel / Tambah manual
        → supabase.auth.admin.create_user() (role: murid)
        → Insert ke profiles + students table  
        → Generate password
```

### 5.2. Guru → Membuat Ujian
```
guru login → /teacher/dashboard
  → /teacher/exams/new → Exam form
      ├── Isi: judul, mapel, durasi, passing score, deskripsi
      ├── Atur jumlah soal + tipe (MCQ / Essay)
      ├── Upload PDF soal → convert ke page images (PyMuPDF)
      ├── Set answer key + bobot nilai
      ├── Atur anti-cheat (tab switch penalty, watermark, dll)
      ├── Atur randomize questions/options
      ├── Izinkan kalkulator (opsional)
      → Save → status: draft
  → /teacher/exams/{id}/publish-exam → status: active + is_published: true
```

### 5.3. Siswa → Mengerjakan Ujian
```
siswa login → /student/exams
  → Klik ujian → /student/exams/{id}
      → Cek access code (jika diperlukan)
      → Tampilkan agreement modal + aturan
      → START → timer mulai
      
      → Offline-first:
          ├── Jawaban → localStorage (instant)
          ├── Light sync → server setiap 20 detik
          └── Canvas sync → server setiap 60 detik
          
      → Tools:
          ├── Pena / Garis / Hapus / Teks
          ├── Ruler 30cm (transparan, fixed scale)
          ├── Protractor 0-180° (1° accuracy)
          ├── Set Square 10cm
          ├── Compass (circle drawing)
          └── Scientific calculator (jika diaktifkan)
          
      → Anti-cheat:
          ├── Tab switch detection (1.5s delay)
          ├── Graduated penalty (1st=warning, 2nd=-base, 3rd=-2×, 4th+=-3×)
          ├── Auto-submit on max violations
          ├── Block right-click, copy-paste
          └── Watermark nama siswa
          
      → Submit → POST /student/exams/{id}/submit
```

### 5.4. Guru → Mengoreksi Esai
```
guru login → /teacher/grading
  → Lihat submission pending
  → /teacher/grade/{submission_id}
      → Lihat PDF soal + jawaban siswa (canvas overlay)
      → Tools koreksi:
          ├── Pena (warna bisa diatur)
          ├── Eraser
          ├── Text box (multi-font, multi-size)
          ├── Ruler, Protractor, Set Square
          └── Kalkulator ilmiah
      → Beri skor per soal + komentar
      → Simpan (auto-save setiap 15 detik)
      → Publish nilai
```

### 5.5. Export Hasil
```
guru → /teacher/results?exam_id={id}
  ├── Export XLSX: summary + per-question answers
  ├── Export PDF: per-student report + canvas drawings
  └── Cetak LJK (bubble sheet)
```

---

## 6. API Endpoints

| Endpoint | Method | Auth | Deskripsi |
|----------|--------|------|-----------|
| `/api/violation/log` | POST | Cookie | Log tab-switch violation |
| `/api/student/sync-draft` | POST | Cookie | Sync draft jawaban |
| `/api/grade/auto-save/{id}` | POST | Cookie | Auto-save grading |
| `/api/grade/batch` | POST | Guru | Batch grading |
| `/api/scan/process` | POST | Guru | Process OMR scan |
| `/auth/me` | GET | Cookie | Current user info |
| `/auth/set-timezone` | POST | Cookie | Set timezone cookie |

---

## 7. Security Measures

| Measure | Implementasi |
|---------|-------------|
| **CSRF Protection** | Token via meta tag + auto-inject ke semua form/fetch |
| **Rate Limiting** | In-memory + Redis support (30 auth/mnt, 60 default/mnt) |
| **PDF Validation** | Magic bytes `%PDF`, size limit 50MB, page count check |
| **Password Strength** | Min 6 char, require letter + number (register) |
| **Session Timeout** | access_token: 24h, refresh_token: 7d |
| **Cookie Security** | `httponly=True`, `samesite=Lax`, `Secure` di production |
| **JWT Verification** | On every request via `login_required` decorator |
| **RLS** | Row-level security di semua tabel Supabase |
| **Audit Log** | Semua operasi CRUD tercatat di `audit_logs` |
| **Input Sanitization** | HTML escaping, UUID/email/NISN validation |

---

## 8. Flow Diagram (Text)

```
                    ┌───────────────────┐
                    │   Landing (/auth)  │
                    └────────┬──────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
       /auth/login     /auth/login_user  /auth/register
       (super_admin,   (guru, murid)     (admin_sekolah)
        admin_sekolah)      │                │
              │              │                ▼
              ▼              ▼          Pending approval
         /admin/*       /teacher/*        by super_admin
                        /student/*             │
                                               ▼
                                          /auth/activate
                                          (activation code)
                                               │
                                               ▼
                                          /admin-sekolah/*
                                               │
                                          Import guru/siswa
                                               │
                                          ┌────┴────┐
                                          ▼         ▼
                                       /teacher/*   /student/*
                                       (guru)       (murid)
```
