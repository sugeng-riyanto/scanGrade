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

## Riwayat tidak berubah tanpa tindakan

Perubahan status penalti adalah **tindakan sadar** guru/admin. Tidak ada migrasi atau
perilisan yang menghitung ulang nilai percobaan yang sudah ada atas inisiatifnya
sendiri; sebuah nilai hanya bergerak ketika seseorang memutuskan sesuatu tentang
pelanggarannya.

## Alur banding

Murid dapat mengajukan banding (`POST /api/student/penalty-appeal`) yang disimpan di
`submissions.answers["_penalty_appeal"]` (`status`: `pending` → `approved`/`rejected`,
`penalty_reduction`), dan guru menanganinya di `/teacher/penalty-appeals`. Keputusan
banding memakai aritmetika `final_score` yang sama saat nilai dihitung ulang.
