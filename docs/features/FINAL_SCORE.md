# Nilai Akhir — one arithmetic, and the working shown

Halaman yang menampilkannya: laporan cetak guru (`/teacher/results/print`), kartu
hasil (`print/report_card.html`), halaman & PDF hasil murid
(`/student/results/<id>`), dasbor/gradebook (`/teacher/students`), dan ekspor
XLSX/PDF/CSV. Sumber kebenarannya: **`app/services/final_score.py`**.

## Formula

```
Nilai Akhir = max(0, min(Skor Mentah, 100) - Penalti Diterapkan)
```

- **Skor Mentah** — poin berbobot dari jawaban: `app/services/mark_scheme.py`
  menormalkan skema per tipe soal tepat ke 100, dan `app/services/question_types.py`
  memberi `earned_points` per tipe (termasuk *part credit* untuk menjodohkan /
  mengurutkan / pilihan ganda kompleks). Nilai guru per soal (`teacher_feedback.scores`,
  persen dari bobot soal itu) ditambahkan di atasnya.
- **Penalti** — `app/services/anti_cheat_service.py` menghitungnya dari **tangga
  bertingkat** atas pelanggaran yang *dihitung* (`PENALIZED_VIOLATION_TYPES`:
  `tab_switch`, `fullscreen_exit`, `focus_lost`). Pelanggaran jenis lain tetap
  tercatat tetapi tidak dihitung.
- **`max(0, …)`** — penalti boleh menghapus nilai, tidak pernah membalikkannya.

## Satu sumber kebenaran

`final_score.breakdown(raw, penalty)` adalah satu-satunya tempat pengurangan
dilakukan. `exam_scoring.rescore` memanggilnya, sehingga *preview* perubahan kunci
jawaban dan angka yang benar-benar disimpan memakai aritmetika yang sama. Modul ini
juga mengembalikan **komponen**, bukan hanya jawaban akhir — supaya permukaan yang
menampilkan nilai bisa memperlihatkan jalan hitungnya, bukan satu angka tanpa konteks:

```python
final_score.breakdown(earned, penalty)   # {"raw": 88.5, "penalty": 12.0, "final": 76.5}
final_score.penalty_breakdown(events, base=5)   # per-pelanggaran: posisi, nilai, status
final_score.for_submission(raw, penalty, events, base=base)
```

## Kenapa per-posisi, bukan satu angka total

`penalty_breakdown` memberi tiap pelanggaran nilai menurut **posisinya** di tangga
(1 = peringatan/0, 2 = `penalty_per_violation`, 3 = 2×, 4+ = 3×), lalu menjumlahkan
hanya yang berstatus `applied`. Menjumlahkan posisi — bukan menghitung ulang tangga
dari total — adalah yang membuat satu pelanggaran yang diabaikan bisa dikurangi tanpa
me-level-ulang pelanggaran lain yang tidak disentuh guru. Dengan semua pelanggaran
`applied`, jumlahnya persis sama dengan `calculate_graduated_penalty` — jadi angka
historis tidak bergeser.

## Keputusan per-pelanggaran — siapa yang mengubah angka itu

Sampai perubahan ini pelanggaran hanya punya satu angka agregat (`submissions.penalty`),
jadi guru tidak bisa meninjau pelanggaran satu per satu dan angka penalti tidak bisa
diturunkan ulang dari apa pun. Sekarang keputusan guru disimpan **per pelanggaran**
dan penalti selalu **diturunkan** dari daftar itu oleh
`app/services/penalty_service.py`:

```
penalti = Σ nilai_tangga(posisi) untuk setiap pelanggaran berstatus "applied"
```

- **Tempat penyimpanannya** adalah `submissions.answers["_violation_decisions"]`,
  sebuah peta `{violation_id: {status, reason, decided_by, decided_at, previous}}` —
  kontainer yang **sama** dengan `_penalty_appeal`, sehingga tidak ada dua wadah yang
  bisa tidak sinkron. Tidak ada tabel/kolom baru, jadi tidak ada migrasi.
- **Ketiadaan baris = `applied`.** Percobaan lama tidak punya peta keputusan sama
  sekali, dan membacanya sebagai *semua diterapkan* mempertahankan angka historisnya
  persis. Tidak ada backfill karena tidak ada yang perlu di-backfill.
- **Hanya dua status**: `applied` / `ignored`. Pengurangan sebagian diungkapkan dengan
  mengabaikan *sebagian* pelanggaran, bukan dengan angka kedua — sebab delta tidak
  bisa dihitung ulang oleh tangga, dan satu angka yang tidak bisa diturunkan ulang
  adalah asal mula dua kebenaran.
- **`penalty_service.recompute(submission_id)`** adalah satu-satunya penulis: ia
  membaca event + keputusan, menghitung ulang *skor mentah* lewat `exam_scoring.rescore`
  (dengan penalti dinolkan), lalu menulis `penalty`, `violations`, dan `final_score`.
  Karena `rescore` memakai `final_score.breakdown`, menampilkan dan menyimpan tidak bisa
  berbeda.
- **`penalty_service.inspect()`** mengembalikan gambar yang sama **tanpa menulis**,
  dipakai panel guru dan setiap permukaan yang menampilkan jalan hitungnya.

Siapa yang boleh mengubah: **guru pemilik ujian** dan **admin_sekolah** sekolahnya
(`can_manage_exam`) — admin yang bukan pemilik tercatat "atas nama guru [X]" di
`audit_logs`. **Kepala sekolah & wakil kepala sekolah hanya membaca**
(`can_read_exam`): pengawasan mereka adalah membaca, dan memberi mereka wewenang
menimpa nilai guru lain akan melanggar garis yang sudah ditetapkan `exam_access`.

## Riwayat tidak berubah tanpa tindakan

Perubahan status penalti adalah **tindakan sadar** guru/admin. Tidak ada migrasi atau
perilisan yang menghitung ulang nilai percobaan yang sudah ada atas inisiatifnya
sendiri; sebuah nilai hanya bergerak ketika seseorang memutuskan sesuatu tentang
pelanggarannya. Karena ketiadaan keputusan berarti *diterapkan*, perilisan ini pun
tidak menggerakkan satu pun nilai lama.

## Alur banding menyatu dengan keputusan

Murid mengajukan banding (`POST /api/student/penalty-appeal`) yang disimpan di
`submissions.answers["_penalty_appeal"]` (`status`: `pending` → `approved`/`rejected`,
`penalty_reduction`), dan guru menanganinya di `/teacher/penalty-appeals`, dengan tautan
ke panel tinjau per pelanggaran (`/teacher/submission/<id>/violations`).

**Menyetujui banding = mengabaikan seluruh pelanggaran kertas itu**, dan itu dijalankan
oleh `penalty_service.set_all(…, "ignored")` — bukan lagi dengan mengurangi angka
penalti secara manual. Dengan begitu angka yang dibayar murid selalu bisa diturunkan
ulang dari daftar keputusan, dan tidak ada delta tersembunyi. Guru yang ingin
mengabulkan sebagian saja mengabaikan pelanggaran tertentu di panel tinjau, lalu
menyetujui banding.
