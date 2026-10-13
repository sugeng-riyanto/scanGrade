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

### Setiap penulis memanggilnya — tidak ada yang menyalin aritmetikanya

Aritmetika ini dulu ditulis ulang di empat tempat: pintu kumpul (force-submit), jalur
`POST /api/student/submit`, jalur hitung-ulang nilai satu ujian, dan
`app/services/deadline_service.py` yang menutup kertas setelah jam ujian habis. Tiga di
antaranya **menghilangkan batas `min(raw, 100)`**, jadi kertas yang "dihitung ulang" bisa
memunculkan angka yang tidak akan pernah ditampilkan laporan mana pun. Keempatnya kini
memanggil `final_score.breakdown` (diimpor sebagai `final_breakdown`), dan
`tests/unit/test_final_score_single_source.py` gagal begitu salah satunya menulis
aritmetika itu sendiri lagi.

Permukaan yang hanya memegang baris `submissions` (kartu hasil per kelas, dan render apa
pun yang tidak diberi gambar lengkap) memakai `final_score.display_parts(submission)`
lewat global Jinja `score_parts`. Ia membalik aritmetika tersimpan
(`raw = final + penalty`, eksak selama lantai 0 tidak kena) dan **tidak pernah mengarang
skor mentah** untuk kertas yang lantainya kena — kertas seperti itu melaporkan penalti
yang dibebankan padanya, batas yang dicerminkan nilainya.

### Nilai akhir disebut **sekali** per lembar

Setiap halaman hasil menyatakan nilai akhir **satu kali**, sebagai angka utama
(headline); baris di bawahnya menunjukkan **jalan hitungnya** — Skor Mentah, Total
Penalti, dan jumlah pelanggaran diterapkan/diabaikan — memakai kosakata yang sama dengan
panel tinjau guru (`teacher/submission_violations.html`). Sebelumnya nilai akhir muncul
**dua kali** di kartu hasil dan di PDF murid (angka utama *dan* satu sel di baris
ringkasan), sehingga satu-satunya angka yang diperiksa pembaca justru yang tercetak dua
kali. `tests/unit/test_final_score_single_source.py` menjaga kedua sisi: labelnya muncul
sekali, dan jalan hitungnya muncul.

### Lembar cetak menyatakan jalan hitungnya, dan angkanya rekonsiliasi

Lembar hasil ujian (`teacher/print_exam_report.html`) dan kartu hasil
(`print/report_card.html`) menyatakan rumusnya sendiri, jadi **setiap baris harus
rekonsiliasi**: `Skor Mentah - Total Penalti = Nilai Akhir`. Kolom yang dulu ada di
sana memuat **skor objektif** (`submissions.score`), dan itu tidak pernah
rekonsiliasi — esai adalah nilai guru dan tidak masuk hitungan kolom itu, sehingga
pada ujian campuran `Skor MCQ - Penalti` bukan nilai akhir. Kolom itu sekarang
memuat **Skor Mentah** yang diperoleh dari mark scheme.

Satu pembaca melayani seluruh angkatan:
`penalty_service.working_for_exam(supabase, exam, rows)` membaca log pelanggaran
**satu kali** untuk seluruh sitting, membangun mark scheme **satu kali**, lalu
memberi setiap baris `raw`, `penalty`, `applied`, dan `recorded` — tanpa query per
murid (kelas 30 tetap tiga pembacaan, bukan 30). `final_score.display_parts`
(global Jinja `score_parts`) memakai `raw` yang sudah ditempelkan bila ada, dan
hanya jatuh ke `final + penalty` bila tidak: sebuah kertas yang kena lantai 0
**tidak bisa** dipulihkan skor mentahnya dari baris, jadi lembar tidak mengarang
angka.

Skor objektif tidak hilang dari aplikasi: layar koreksi guru
(`teacher/grade_detail.html`) tetap menampilkannya sebagai `MCQ:`; yang berubah
hanya lembar cetak, yang harus menjumlahkan.

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
