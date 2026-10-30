# LMS Content Spec — Guru x Mapel x Kelas x Tahun Ajaran + Drive Embed

> Agent: baca ini + `LMS_ROADMAP_3_BULAN.md` sebelum coding.
> Stabil yang disentuh: `teacher_assignments`, `year_lock`, `exam_preview` (pola saja).

## 1. Tujuan

Guru mengelola materi (video, dokumen, link) per mapel per kelas per tahun ajaran.
Semua file berupa **referensi Google Drive (embed)**, bukan blob di VPS.

## 2. Skema (migrasi `080_lms_content.sql`)

```sql
-- semua tabel: id UUID PK default gen_random_uuid(), created_at/updated_at
content_folders (
  id, school_id UUID NOT NULL REFERENCES schools(id),
  subject_id UUID NOT NULL, class_id UUID NOT NULL,
  school_year_id UUID NOT NULL, teacher_id UUID NOT NULL,
  title TEXT NOT NULL, parent_id UUID NULL, position INT DEFAULT 0,
  is_published BOOL DEFAULT false
);
content_items (
  id, folder_id UUID NOT NULL, school_id UUID NOT NULL,
  type TEXT NOT NULL, -- video | doc | link | assignment | quiz_ref
  title TEXT NOT NULL, drive_file_id TEXT NULL, drive_embed_url TEXT NULL,
  mime_type TEXT NULL, duration_min INT NULL,
  body_md TEXT NULL, -- untuk tugas/link
  ref_exam_id UUID NULL, -- untuk quiz_ref -> exams.id
  is_published BOOL DEFAULT false, publish_at TIMESTAMPTZ NULL, position INT DEFAULT 0
);
content_views (
  student_id UUID NOT NULL, item_id UUID NOT NULL, school_id UUID NOT NULL,
  progress_pct INT DEFAULT 0, completed_at TIMESTAMPTZ NULL,
  UNIQUE(student_id, item_id)
);
content_versions (
  id, item_id UUID NOT NULL, version INT NOT NULL,
  changed_by UUID NOT NULL, snapshot JSONB NOT NULL
);
```

RLS: service-role bypass (seperti sekarang) + enforce di Python.
Index: `(school_id, subject_id, class_id, school_year_id)` di folders;
`(folder_id, position)` di items.

## 3. Otorisasi tulis (WAJIB)

- Fungsi tunggal: `content_access.can_write(db, user_id, role, school_id, class_id, subject_id)`
  - `guru`: `teacher_assignments` aktif di (class, subject, school, tahun aktif) ATAU pemilik folder.
  - `admin_sekolah`: semua folder sekolahnya.
  - `principal/vice`: read-only.
- Route menolak SEBELUM baca murid (seperti `exam-roster` 403 sebelum SELECT).
- Tahun tutup (`year_lock`): tulis ditolak dengan pesan "arsip".

## 4. Google Drive embed

- Simpan hanya `drive_file_id`. Embed URL dibangun server:
  - doc/slide: `https://docs.google.com/{type}/d/{id}/preview`
  - file umum: `https://drive.google.com/file/d/{id}/preview`
- Publish: `drive.permissions.create(role=reader, type=group/domain sesuai kelas)`.
  Implementasi awal boleh per-email murid kelas (batch), lalu optimasi ke grup.
- Unpublish: delete permission.
- Render: cek `drive.files.get(id)` dulu; gagal -> pesan "Hubungi guru" bukan iframe kosong.
- CSP: tambah `frame-src https://drive.google.com https://docs.google.com` (jangan `*`).

## 5. Route + template

- `GET/POST /teacher/contents` — filter dropdown TA x Mapel x Kelas (dropdown kosong = tutup, bukan tampil semua).
- `GET /teacher/contents/<id>/edit`, `POST .../publish`, `POST .../clone`
- `GET /student/learn` — kartu modul + progress bar + tombol resume.
- `GET /teacher/contents/preview/<id>` — reuse pola `exam_preview` (render template murid, tanpa tulis `content_views`).
- Semua string `t('...','...')`, tabel ≥4 kolom pakai `overflow-x-auto + min-w-[...]`.

## 6. Clone tahun ajaran + kenaikan kelas

- `POST /admin-sekolah/contents/clone-year {from_year, to_year}` — copy folders+items,
  `is_published=false`, catat `cloned_from`.
- `POST /admin-sekolah/students/promote` — massal naik `class_id` + tulis `student_enrollment`.

## 7. Acceptance criteria (agent harus buktikan)

- [ ] Guru A tidak bisa tulis folder kelas yang tidak dia ampu (403, 0 row ditulis).
- [ ] Murid sekolah lain 403 sebelum ada SELECT murid.
- [ ] Tulis ke tahun tutup ditolak.
- [ ] Unpublish mencabut akses Drive (probe `files.get` sebagai murid = 404).
- [ ] Clone menghasilkan folder baru, tidak mengubah arsip.
- [ ] Test: `tests/unit/test_lms_content.py` (otorisasi, clone, drive-url builder).
