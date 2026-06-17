# ScanGrade — Grading UX Plan

## Vision
Buat guru dan murid nyaman: koreksi essay cepat (manual + AI), lihat hasil jelas, gak ribet.

---

## 1. Teacher Grading Dashboard (`/teacher/exams/<id>/results`)

### Saat Ini
- Tabel daftar siswa + skor MCQ + status
- Tombol "Koreksi" per siswa → `grade_detail.html`
- Export XLSX, PDF, LJK
- Fitur question grader (`/teacher/exams/<id>/grade-question`)

### Target UX
| Elemen | Status | Target |
|--------|--------|--------|
| Tabel siswa | ✅ Ada | Filter by kelas, search nama, sort skor |
| Status per siswa | ✅ | Draft/Submitted/Graded/Published |
| Tombol Koreksi | ✅ | `grade_detail.html` |
| Question grader | ✅ | `/grade-question` — nilai per soal semua siswa |
| Batch AI grade | ✅ | Tombol "AI Grade All" di question grader |
| Skor langsung di tabel | ⏳ | Tampilkan skor essay + total tanpa klik detail |
| Filter siswa belum dikoreksi | ❌ | Cepat lihat siapa aja yang essay-nya belum dinilai |

---

## 2. Grade Detail (`/teacher/exams/<exam_id>/grade/<submission_id>`)

### Layout
```
┌─────────────────────────────────────────────┐
│  Header: Nama siswa, Skor sementara         │
├────────────────┬────────────────────────────┤
│  Sidebar soal  │  Area kerja utama           │
│  (daftar soal) │  ┌─ PDF page image ───────┐ │
│                │  │  Student canvas overlay │ │
│                │  │  Teacher canvas overlay │ │
│                │  │  Tools (pen, ruler dll) │ │
│                │  └────────────────────────┘ │
│                │  ┌─ Skor & Komentar ───────┐ │
│                │  │  [Skor] [Komentar]      │ │
│                │  │  [AI Suggest] [Simpan]  │ │
│                │  └────────────────────────┘ │
└────────────────┴────────────────────────────┘
```

### Saat Ini
- Tab MCQ + Essay di header
- Student canvas (opacity 0.65, pointer-events:none)
- Teacher canvas (bisa annotate)
- Tools: pen, eraser, text, ruler, protractor, triangle, compass
- Score + comment per question
- Auto-save score & comment ke server

### Target UX
| Fitur | Status | Target |
|-------|--------|--------|
| Lihat jawaban MCQ | ✅ | Highlight benar/salah + skor otomatis |
| Lihat canvas siswa | ✅ | Student-canvas overlay (opacity 0.65) |
| Anotasi guru | ✅ | Teacher-canvas + drawing tools |
| Skor + komentar per soal | ✅ | Input skor, text komentar |
| Auto-save skor | ⏳ | Simpan otomatis saat input berubah (debounce) |
| Navigasi soal cepat | ⏳ | Sidebar soal dengan status (belum/telah dinilai) |
| Toggle student canvas | ❌ | Show/hide student drawing biar gak numpuk |
| Zoom PDF page | ❌ | Perbesar halaman untuk lihat detail |

---

## 3. Manual Essay Correction

### Flow
```
1. Guru buka grade_detail → lihat jawaban essay siswa
2. Baca jawaban di textarea (essay_text) atau lihat canvas (essay_canvas)
3. Input skor (0 - max_score)
4. Tulis komentar (opsional)
5. Simpan (auto-save tiap 3 detik + manual)
6. Lanjut ke soal berikutnya
```

### Saat Ini
- Textarea untuk jawaban essay_text
- Canvas untuk jawaban essay_canvas
- Input skor + komentar
- Tombol "Simpan" (sebenarnya auto-save via `scheduleSave()`)

### Target UX
| Fitur | Status | Target |
|-------|--------|--------|
| Lihat teks jawaban siswa | ✅ | Textarea besar dengan scroll |
| Lihat canvas jawaban siswa | ✅ | Student-canvas overlay |
| Input skor | ✅ | Number input dengan max_score |
| Input komentar | ✅ | Textarea komentar |
| Auto-save (debounce) | ⏳ | `scheduleSave()` saves every 3s if dirty |
| Keyboard shortcut | ❌ | Enter = next soal, Shift+Enter = prev soal |
| Rubrik penilaian | ✅ | Ditampilkan di sebelah komentar |
| Riwayat perubahan skor | ❌ | Log perubahan skor (audit trail) |

---

## 4. AI Essay Correction

### Flow
```
1. Guru setup API key di /teacher/ai/settings (1x)
2. Buka grade_detail → klik "AI Suggest"
3. AI baca: soal + jawaban siswa + rubrik + max_score
4. AI return: suggested_score + feedback_text
5. Guru review: Terima/Tolak/Edit
6. Kalau diterima → auto-fill score + comment
```

### Saat Ini
- 4 provider AI: Groq, Gemini, OpenAI, DeepSeek
- API key management: add, test, activate, delete
- Prompt template: editable (soal, jawaban, rubrik, max_score)
- `POST /api/grade/ai-suggest` → return `{score, feedback}`
- Tombol "AI Suggest" di grade_detail

### Target UX
| Fitur | Status | Target |
|-------|--------|--------|
| Setup API key | ✅ | Wizard 3 langkah |
| AI Suggest per soal | ✅ | Tombol di grade_detail |
| AI Grade All (batch) | ✅ | Di question grader |
| Review + Terima/Tolak | ✅ | Klik terima → auto fill |
| Edit sebelum simpan | ✅ | Bisa edit score/feedback |
| Loading indicator | ✅ | Spinner + "AI sedang menganalisis..." |
| Error handling | ✅ | Retry 3x, fallback message |
| **One-click grade** | ❌ | Klik "AI Suggest" langsung fill tanpa konfirmasi (toggle setting) |
| **Compare multiple AI** | ❌ | Minta saran dari 2 provider beda |
| **Confidence meter** | ❌ | Tampilkan confidence score dari AI (0-100%) |
| **Batch AI + Manual mix** | ❌ | AI grade all, lalu guru review satuan |

---

## 5. Question Grader (`/teacher/exams/<exam_id>/grade-question`)

### Saat Ini
- Pilih soal → lihat semua jawaban siswa untuk soal itu
- MCQ: quick-grade dengan tombol A-E
- Essay: lihat jawaban + AI grade button
- Grid layout per siswa

### Target UX
| Fitur | Status | Target |
|-------|--------|--------|
| MCQ quick-grade | ✅ | Klik A-E → simpan langsung |
| Essay view per soal | ✅ | Lihat semua jawaban siswa |
| AI grade all | ✅ | Tombol "AI Grade All" |
| **Split view: soal + jawaban** | ❌ | Soal di kiri, jawaban siswa di kanan |
| **Progress bar** | ❌ | "15/30 siswa sudah dinilai" |
| **Filter: belum dinilai** | ❌ | Sortir siswa yang essay-nya belum punya skor |

---

## 6. Student Experience

### Saat Ini
- Canvas PDF dengan overlay drawing tools
- MCQ + essay textarea
- Auto-save offline-first
- Timer + anti-cheat
- Submit + pending submit saat offline

### Target UX
| Fitur | Status | Target |
|-------|--------|--------|
| Canvas drawing tools | ✅ | Pen, line, eraser, text, ruler, protractor, compass, triangle |
| Essay textarea | ✅ | Large textarea |
| Auto-save offline | ✅ | localStorage + sync when online |
| **Responsive mobile** | ⏳ | Tools toolbar wrap, canvas scroll |
| **Font size adjust** | ❌ | Perbesar/kecilkan teks soal |
| **Highlight jawaban** | ❌ | Tandai soal yang ragu-ragu (bookmark) |
| **Sisa waktu visible** | ✅ | Countdown di navbar |
| **Nomor soal sidebar** | ✅ | 150px sidebar, selalu visible |

---

## 7. UI/UX Principles

### Warna & Layout
- Primary: blue (#3b82f6) — tombol aksi, link, highlight
- Surface: white/gray — background card, panel
- Success: green (#10b981) — benar, tersimpan, published
- Warning: amber (#f59e0b) — peringatan, pending
- Error: red (#ef4444) — salah, error, retracted
- Font: Nunito (UI), Inter (tabel), monospace (skor)
- Konsisten: tombol, card, border-radius sama di semua halaman

### Principles
1. **Less is more** — jangan tampilkan semua informasi sekaligus
2. **Progressive disclosure** — tampilkan detail saat dibutuhkan (toggle, hover, click)
3. **Feedback instan** — setiap aksi langsung ada response (loading, success, error)
4. **Offline-first** — semua fitur harus jalan walau internet lemot
5. **Keyboard-friendly** — shortcut untuk aksi umum (save, next, prev)
6. **Mobile responsive** — minimal 320px, toolbar wrap
7. **Accessibility** — kontras cukup, font size readable, label jelas

---

## 8. Iterasi Selanjutnya

### Priority Tinggi (Next Sprint)
- [ ] **Teacher: Zoom PDF page** — perbesar halaman di grade_detail dan take_exam
- [ ] **Toggle student canvas** — show/hide student drawing di grade_detail
- [ ] **Auto-save debounce** — score/comment simpan 2 detik setelah berhenti ngetik
- [ ] **Question grader: progress bar** — "15/30 siswa sudah dinilai"
- [ ] **Filter siswa belum dikoreksi** — di results table

### Priority Sedang
- [ ] **Keyboard shortcuts** — Enter=next, Shift+Enter=prev, Ctrl+S=save
- [ ] **AI Confidence meter** — tampilkan confidence score
- [ ] **Responsive mobile** — canvas toolbar wrap, sidebar collapse
- [ ] **Student: highlight/bookmark soal** — tandai soal ragu

### Priority Rendah
- [ ] **Compare multiple AI providers** — saran dari 2 AI beda
- [ ] **Audit trail skor** — log perubahan skor
- [ ] **One-click AI grade** — langsung fill tanpa konfirmasi
- [ ] **Student: font size adjust** — perbesar teks soal

---

## 9. Referensi File

| File | Kegunaan |
|------|----------|
| `app/templates/teacher/grade_detail.html` | Halaman utama koreksi per siswa |
| `app/templates/teacher/grading.html` | Question grader (per soal) |
| `app/templates/teacher/results.html` | Tabel hasil exam |
| `app/templates/student/take_exam.html` | Halaman ujian siswa |
| `app/services/ai_grading.py` | AI grading pipeline |
| `app/services/grading_service.py` | Grade essay (AI + heuristic fallback) |
| `app/services/rubric_generator.py` | Generate rubrik otomatis |
| `app/routes/teacher.py` | Route grading & koreksi |
| `app/routes/api.py` | API: ai-suggest, grade-batch |
| `docs/features/AI_GRADING.md` | Dokumentasi AI grading |
| `docs/planning/AI_ESSAY_GRADING_PLAN.md` | Planning AI grading |
