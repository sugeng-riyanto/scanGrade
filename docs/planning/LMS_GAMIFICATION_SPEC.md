# LMS Gamification Spec — Keren tapi Mendidik

> Agent: XP/badge TIDAK PERNAH mengubah nilai rapor langsung.
> Nilai rapor hanya dari gradebook. XP adalah motivasi, bukan currency nilai.

## 1. Ekonomi XP (server-side, jangan percaya client)

```
XP = round(skor_pct * difficulty_weight * speed_bonus)
difficulty_weight: mudah 1.0, sedang 1.5, sukar 2.0 (dari Rasch bank soal)
speed_bonus: 1.0 normal, 1.2 jika < 50% durasi (quiz saja, bukan exam)
```

- Tabel `student_xp_events (id, school_id, student_id, subject_id, source TEXT,
  xp INT, reason TEXT)` — append-only, tidak ada update.
- Level per mapel: `level = floor(sqrt(total_xp/100)) + 1`.
- Streak: `student_streaks (student_id, last_active_date, streak_days)` —
  +1 jika aktivitas (view/quiz) hari berbeda berurutan, reset jika bolong.

## 2. Badge (12 awal)

Master Pecahan, Penakluk UTS, Pantang Menyerah (retry 3x), Pembaca Rajin
(10 materi selesai), Tepat Waktu (5 tugas sebelum deadline), Asisten Guru
(peer-review OMR), Kartu Tokoh (selesaikan modul sejarah/bahasa).

Tabel `student_badges (student_id, badge_code, awarded_at)` UNIQUE(student, badge).

## 3. Kompetisi sehat

- Liga Kelas: leaderboard mingguan per (class, subject), reset Senin 00 WIB.
  Tampilkan top 5 + "kamu #N". Tidak ada leaderboard lintas sekolah.
- Boss Battle: 1 soal sulit 10 menit per bab; jika ≥80% kelas benar -> semua +50 XP.
- Quiz Live: guru dari `/teacher/quiz-live`, murid join via kode, skor realtime
  via Socket.IO (reuse `whiteboard_socket` pola, room per quiz).
- Misi Remedial: nilai < KKM buka 3 micro-quiz + 1 video; selesai -> nilai maks = KKM.
- Petualangan OMR: peer-grading LJK kertas dapat XP (manfaatkan OMR unik ScanGrade).
- Kartu Tokoh Indonesia: selesaikan modul -> kartu + fakta 2 kalimat (bisa cetak stiker).

## 4. Larangan keras

- XP tidak bisa ditukar nilai. Tidak ada lootbox berbayar. Tidak ada DM antar-murid
  sebagai hadiah. Semua event bawa `school_id` (isolasi tenant).

## 5. Acceptance

- [ ] Hitung XP 2x request sama tidak double (idempotency key per submission).
- [ ] Murid sekolah lain tidak muncul di leaderboard.
- [ ] Remedial tidak pernah menaikkan nilai di atas KKM.
- [ ] Test: `tests/unit/test_lms_game.py`.
