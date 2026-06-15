# Whiteboard — Papan Tulis Digital Real-Time

> **Status:** Planned | **Target:** v1.1.0
> **Dependency:** Flask-SocketIO, Eventlet, Redis Pub/Sub

---

## Overview

Fitur papan tulis digital real-time untuk pembelajaran interaktif. Guru dan murid bisa berkolaborasi di satu canvas secara langsung — guru menjelaskan, murid melihat, dan bisa meminta izin untuk ikut menganotasi.

**Posisi:** Sub-project independen yang menempel di dashboard guru & murid. Tidak mengganggu sistem ujian online yang sudah ada.

---

## UX Flow

### Teacher Side

```
┌──────────────────────────────────────────────────────────────────┐
│  [Nama Papan Tulis]        [👥 26/30 siswa online]   [Bagikan]  │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌────────┐  ┌──────────────────────────────────────────────┐   │
│  │ TOOLBAR │  │                                              │   │
│  │ ✏️ Pen  │  │           CANVAS UTAMA                      │   │
│  │ 🧹 Eraser│  │                                              │   │
│  │ 🔤 Text │  │   Background: PDF/Slide/Image               │   │
│  │ 🖍️ High- │  │   + Anotasi guru (real-time broadcast)      │   │
│  │   light  │  │   + Anotasi murid (jika diizinkan)          │   │
│  │ 🟦 Shapes│  │                                              │   │
│  │ 🎨 Color │  │                                              │   │
│  │ ↩️ Undo  │  │                                              │   │
│  │ ➡️ Redo  │  │                                              │   │
│  │ 🗑️ Clear │  │                                              │   │
│  │ 🔦 Laser │  │                                              │   │
│  └────────┘  └──────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────── STUDENT PANEL ─────────────────────────┐ │
│  │                                                             │ │
│  │  🟢 Fokus    👤 Budiman         [✋ Minta] [✅ Ijinkan]      │ │
│  │  🔴 Pindah   👤 Siti            [✋ Minta] [✅ Ijinkan]      │ │
│  │  🟢 Fokus    👤 Andi  ◀️ [Sedang Menganotasi]  [⛔ Revoke]  │ │
│  │  🟡 Layar    👤 Dewi            [—]                          │ │
│  │     penuh                                                      │ │
│  │                                                               │ │
│  │  [ 🔒 Kunci Semua | ✅ Ijinkan Semua | 🔄 Reset Izin ]       │ │
│  └───────────────────────────────────────────────────────────────┘
└──────────────────────────────────────────────────────────────────┘
```

### Student Side

```
┌──────────────────────────────────────────────────────────────────┐
│  [← Keluar]  [Papan Tulis: Matematika]          [🟢 Stabil]  │
│                                [✋ Minta Izin Anotasi]          │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                                                          │   │
│  │           CANVAS (VIEW-ONLY) ─── Sebelum izin            │   │
│  │                                                          │   │
│  │           CANVAS (FULL TOOLBAR) ─── Setelah izin         │   │
│  │                                                          │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  👁️ Guru sedang melihat  |  ✅ Fokus aktif              │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

---

## Fitur Kunci

| Fitur | Deskripsi | Target |
|-------|-----------|--------|
| **Canvas Drawing** | Pen, eraser, text, highlight, shapes, undo/redo, color picker, stroke width | MVP |
| **Laser Pointer** | Lingkaran merah real-time di semua layar murid tanpa coretan permanen | MVP |
| **Quick Reaction** | Kirim emoji (👍 ❓ 🚀) tanpa perlu izin — muncul di panel guru | MVP |
| **Timer Overlay** | Guru set countdown — muncul di canvas semua peserta | MVP |
| **Slide Navigator** | PDF multi-halaman → prev/next + thumbnail sidebar | MVP |
| **Snapshot Momen** | Simpan momen canvas → thumbnail di list archive | MVP |
| **PDF Export** | Gabung background + anotasi → download PDF final | MVP |
| **Permission** | Request → approve/deny/revoke per murid + bulk actions | MVP |
| **Anti-Cheat Soft** | Track blur/visibility/fullscreen — info ke guru, bukan hukuman | MVP |
| **Background Upload** | PDF/slide/image → konversi ke canvas background | MVP |

---

## Permission Flow

```
Murid: [View Only] ─klik "Minta Izin"─► [Menunggu...] ─guru approve─► [Full Toolbar]
                                          │                            │
                                          └─guru deny─► [View Only]    │
                                                                    [Annotating]
                                                                        │
                                                          └─guru revoke─► [View Only]
```

Guru bisa:
- ✅ Approve/deny per-murid
- ✅ Bulk approve semua yang request
- ✅ Bulk revoke semua
- ✅ Revoke individual

---

## Anti-Cheat Strategy

Anti-cheat di whiteboard bersifat **soft (informasi, bukan hukuman)** — berbeda dengan exam yang langsung blokir:

| Trigger | Deteksi | Aksi Server | Status di Panel Guru |
|---------|---------|-------------|----------------------|
| Pindah tab | `visibilitychange` | Log + warning | 🔴 Pindah tab |
| Alt+Tab / Klik luar | `blur` | Log | 🟡 Tidak fokus |
| Koneksi putus | WebSocket close | Status offline | ⚪ Putus koneksi |
| Heartbeat timeout | No ping >30s | Auto-mark offline | ⚪ Offline |
| Layar tidak penuh | `fullscreenchange` | Log | 🟡 Layar tidak penuh |

**Fullscreen = opsional.** Murid bisa buka buku/modul di samping. Guru bisa *request* murid untuk fullscreen (ada tombol), dan statusnya terlihat di panel guru.

---

## Arsitektur

```
┌─────────────┐     WebSocket      ┌──────────────┐
│   Guru      │◄──────────────────►│  Redis Pub   │
│  (Browser)  │                    │  /Sub        │
└─────────────┘                    └──────┬───────┘
        ▲                                │
        │ WebSocket                       │
        ▼                                ▼
┌─────────────┐                    ┌──────────────┐
│   Murid 1   │◄──────────────────►│  Gunicorn    │
│  (Browser)  │                    │  Workers     │
└─────────────┘                    └──────────────┘
        ▲
        │
┌─────────────┐
│   Murid 2   │
│  (Browser)  │
└─────────────┘
```

### Struktur File

```
app/
├── routes/
│   ├── whiteboard_teacher.py      # Blueprint: /teacher/whiteboard
│   └── whiteboard_student.py      # Blueprint: /student/whiteboard
├── services/
│   └── whiteboard_service.py      # Session, permission, broadcast logic
├── templates/
│   ├── teacher/
│   │   ├── whiteboard_list.html   # Daftar papan tulis guru
│   │   └── whiteboard_canvas.html # Halaman utama canvas
│   └── student/
│       ├── whiteboard_list.html   # Daftar papan tulis murid
│       └── whiteboard_canvas.html # Halaman canvas murid
├── static/
│   └── js/
│       ├── whiteboard-canvas.js    # Operasi canvas (reuse dari OMR/ujian)
│       ├── whiteboard-websocket.js # WebSocket client
│       ├── whiteboard-slides.js    # Navigasi slide PDF
│       ├── whiteboard-reactions.js # Quick reaction emoji
│       └── whiteboard-timer.js     # Timer overlay
└── __init__.py                     # Daftarin blueprint + SocketIO init
```

### Strategi Koneksi

| Kondisi | Metode |
|---------|--------|
| Online stabil | WebSocket (flask-socketio + Redis Pub/Sub) |
| Lambat | Fetch + POST batch operation tiap 2-5 detik |
| Offline | IndexedDB queue → sync saat connect balik |
| Permission | Redis store + auto-kirim ulang saat reconnect |

---

## Database Tables

### `whiteboards`
| Kolom | Tipe | Keterangan |
|-------|------|------------|
| id | UUID | Primary key |
| school_id | UUID | FK ke schools |
| teacher_id | UUID | FK ke profiles (guru) |
| class_id | UUID | FK ke classes |
| title | VARCHAR | Judul papan tulis |
| status | VARCHAR | active / ended |
| created_at | TIMESTAMPTZ | |
| ended_at | TIMESTAMPTZ | |

### `whiteboard_members`
| Kolom | Tipe | Keterangan |
|-------|------|------------|
| id | UUID | Primary key |
| whiteboard_id | UUID | FK |
| student_id | UUID | FK ke profiles |
| can_annotate | BOOLEAN | Status izin |
| joined_at | TIMESTAMPTZ | |

### `whiteboard_slides`
| Kolom | Tipe | Keterangan |
|-------|------|------------|
| id | UUID | Primary key |
| whiteboard_id | UUID | FK |
| slide_number | INT | Urutan slide |
| background_url | TEXT | Path file lokal |
| created_at | TIMESTAMPTZ | |

### `whiteboard_ops`
| Kolom | Tipe | Keterangan |
|-------|------|------------|
| id | UUID | Primary key |
| whiteboard_id | UUID | FK |
| slide_number | INT | |
| user_id | UUID | FK |
| op_type | VARCHAR | line / text / erase / clear |
| data | JSONB | {points, color, width, ...} |
| timestamp | BIGINT | Unix ms |
| seq_number | BIGINT | Urutan operasi |

### `whiteboard_reactions`
| Kolom | Tipe | Keterangan |
|-------|------|------------|
| id | UUID | Primary key |
| whiteboard_id | UUID | FK |
| user_id | UUID | FK |
| emoji | VARCHAR | 👍 ❓ 🚀 |
| created_at | TIMESTAMPTZ | |

### `whiteboard_anti_cheat_log`
| Kolom | Tipe | Keterangan |
|-------|------|------------|
| id | UUID | Primary key |
| whiteboard_id | UUID | FK |
| user_id | UUID | FK |
| event_type | VARCHAR | blur / visibility / fullscreen / heartbeat |
| event_data | JSONB | Detail tambahan |
| created_at | TIMESTAMPTZ | |

### `whiteboard_snapshots`
| Kolom | Tipe | Keterangan |
|-------|------|------------|
| id | UUID | Primary key |
| whiteboard_id | UUID | FK |
| slide_number | INT | |
| image_url | TEXT | Path file lokal |
| created_at | TIMESTAMPTZ | |

---

## Task List Implementasi

### Phase 1: Setup & Infrastruktur

| # | Task | File |
|---|------|------|
| 1 | Tambah flask-socketio + eventlet ke requirements | `requirements.txt` |
| 2 | Init SocketIO + Redis adapter | `app/__init__.py` |
| 3 | Buat SQL migration untuk whiteboard tables | Supabase SQL |
| 4 | Buat 2 blueprint kosong & register | `whiteboard_teacher.py`, `whiteboard_student.py`, `__init__.py` |
| 5 | Ganti gunicorn worker ke eventlet | `gunicorn.conf.py` |

### Phase 2: Backend Services

| # | Task | File |
|---|------|------|
| 6 | Whiteboard Service — session CRUD | `app/services/whiteboard_service.py` |
| 7 | PDF/Image upload → canvas background | extend `pdf_service.py` |
| 8 | Operation logger — simpan drawing ops ke DB | `whiteboard_service.py` |
| 9 | Permission system — request/approve/deny/revoke | `whiteboard_service.py` |
| 10 | Snapshot saver — screenshot canvas → simpan | `whiteboard_service.py` |

### Phase 3: Real-time & Anti-Cheat

| # | Task | File |
|---|------|------|
| 11 | WebSocket events — join_room, draw, cursor_move | `whiteboard_teacher.py`, `whiteboard_student.py` |
| 12 | WebSocket events — request/approve/revoke annotate | same |
| 13 | WebSocket events — laser_pointer, timer_sync, slide_change | same |
| 14 | Anti-cheat tracker — log blur/visibility/fullscreen/heartbeat | `whiteboard_service.py` |
| 15 | Heartbeat 5s ping/pong, timeout >30s = offline | client + server |

### Phase 4: Frontend — Shared Components

| # | Task | File |
|---|------|------|
| 16 | Canvas engine — pen, eraser, text, highlight, shapes, undo/redo | `whiteboard-canvas.js` |
| 17 | Toolbar component | inline di template |
| 18 | Slide navigator — prev/next + thumbnail | `whiteboard-slides.js` |
| 19 | Quick reaction — emoji bubble 👍 ❓ 🚀 | `whiteboard-reactions.js` |
| 20 | Timer overlay — set countdown dari guru | `whiteboard-timer.js` |
| 21 | WebSocket client — connect/reconnect/send/receive | `whiteboard-websocket.js` |

### Phase 5: Frontend — Teacher Page

| # | Task | File |
|---|------|------|
| 22 | List whiteboard — tabel (judul, kelas, status, tanggal, murid aktif) | `whiteboard_list.html` |
| 23 | Create/Edit modal — judul, pilih kelas, checklist murid | modal di `whiteboard_list.html` |
| 24 | Canvas page — canvas + toolbar + student panel | `whiteboard_canvas.html` |
| 25 | Student panel — status fokus, request queue, bulk actions | component di `whiteboard_canvas.html` |
| 26 | Background upload — PDF/slide/image → canvas | `whiteboard_canvas.html` |

### Phase 6: Frontend — Student Page

| # | Task | File |
|---|------|------|
| 27 | List whiteboard — hanya yang aktif untuk kelasnya | `whiteboard_list.html` |
| 28 | Canvas page — view-only mode default | `whiteboard_canvas.html` |
| 29 | Tombol "Minta Izin Anotasi" | `whiteboard_canvas.html` |
| 30 | Full toolbar muncul saat izin granted | `whiteboard_canvas.html` |
| 31 | Auto join room + heartbeat on load | via WebSocket |

### Phase 7: Storage & Export

| # | Task | File |
|---|------|------|
| 32 | Local storage PDF di `app/static/uploads/whiteboard/` | `pdf_service.py` |
| 33 | PDF export — background + anotasi → file final | `whiteboard_service.py` |
| 34 | Download button untuk semua peserta | teacher + student template |

### Phase 8: Deploy & Polish

| # | Task | Detail |
|---|------|--------|
| 35 | NGINX — WebSocket proxy support | Periksa config |
| 36 | Load test — 1 guru + 30 murid real-time | |
| 37 | QA anti-cheat — blur, visibility, reconnect, offline | |
| 38 | Update docs/tracking.md | |

---

## Golden Rules

1. **Git branch** — Kerjakan di `feature/whiteboard`, bukan `main`
2. **Zero risk ke project utama** — Blueprint terpisah, service terpisah
3. **Mulai dari frontend dulu** — Canvas JS + HTML statis bisa dites tanpa backend
4. **Fullscreen opsional** — Murid tidak dipaksa, guru bisa request
5. **Anti-cheat soft** — Info ke guru, bukan hukuman otomatis
6. **Per-kelas dengan custom peserta** — Data murid dari DB, bisa unchecklist

---

## Catatan Penting

- **Canvas engine reuse** dari existing student exam canvas (pen, eraser, text, ruler sudah ada)
- **xhtml2pdf** tidak support `rgba()`/`opacity` — untuk PDF export perlu reportlab atau workaround
- **Redis** untuk Pub/Sub antar workers — sudah terinstall di server
- **Service key Supabase** untuk operasi backend — reuse dari existing `get_supabase()`
