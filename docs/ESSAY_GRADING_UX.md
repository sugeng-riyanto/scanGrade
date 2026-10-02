# Mengoreksi esai — mode, pintasan, dan alat bantu

Halaman koreksi esai guru dibuat untuk pekerjaan yang panjang: satu soal yang sama,
dinilai untuk puluhan sampai ratusan murid. Karena itu ia punya tiga hal yang
membuat sesi berjam-jam tetap tertahankan: **skor yang selalu terlihat**, **bank
komentar yang dipakai berulang**, dan **penanda tinjau ulang**.

## Dua mode

| Halaman | Mode | Kapan dipakai |
| --- | --- | --- |
| `/teacher/grade-question/<exam_id>/<n>` | **per soal** — satu nomor soal, semua murid berurutan | Mode utama. Guru menilai dengan standar soal yang sama sepanjang antrean, jadi tidak lupa kriteria saat sampai murid ke-150. |
| `/teacher/grading/<exam_id>` | **per murid** — satu kertas, semua soalnya | Kasus khusus: melihat keseluruhan jawaban satu anak (mis. permintaan orang tua, kecurigaan). |

## Pintasan papan tik (mode per soal)

| Tombol | Aksi |
| --- | --- |
| `↓` / `j` | Murid berikutnya |
| `↑` / `k` | Murid sebelumnya |
| `1`–`9` | Skor 10–90 untuk murid yang sedang disorot, lalu lanjut otomatis |
| `0` | Skor 100, lalu lanjut otomatis |
| `Enter` | Simpan & lanjut ke murid berikutnya |
| `F` | Tandai / lepas tanda **tinjau ulang** untuk soal yang sedang dibuka |
| `B` | Fokus ke kolom komentar baru (bank) |
| `Esc` | Keluar dari kolom input |

Skor tersimpan otomatis: setiap kali guru berpindah murid, nilai dan komentar murid
sebelumnya ditulis lebih dulu — tidak ada tombol simpan terpisah yang bisa lupa
diklik di tengah antrean.

## Bank komentar

Kesalahan yang sama muncul di banyak kertas. Bank menyimpan komentar **seorang
guru** (bukan sekolah — dua guru menilai dengan kata-kata berbeda), diurutkan dari
yang paling sering dipakai.

- **Menerapkan**: klik satu komentar di panel bank. Komentarnya ditambahkan ke
  feedback murid yang sedang dibuka dan, jika komentar itu punya *pengurangan*
  terlampir, nilainya dikurangi sekaligus — menandai dan mengurangi jadi satu aksi.
- **Membuat baru**: tulis di kolom "Komentar baru", isi pengurangan opsional (mis.
  `-5`), lalu `Enter` atau tombol **Bank**. Komentar langsung tersedia untuk murid
  berikutnya.
- Server menolak komentar kosong dan pengurangan di luar rentang −100…100
  **sebelum menulis apa pun**. Guru hanya bisa menerapkan komentar miliknya sendiri
  (403 untuk milik guru lain).

## Penanda tinjau ulang

Tombol **Tinjau ulang** (atau `F`) menandai satu soal dari satu kertas untuk
dibuka lagi nanti — kasus ragu, jawaban ambigu, atau perlu didiskusikan dengan guru
lain. Penanda adalah **satu baris per (kertas, soal)**: menandai dua kali tetap
satu penanda. Flag hanya bisa dipasang pada kertas yang memang milik guru tersebut
(dijaga `_guard_submission`).

## Jejak audit nilai

Setiap perubahan nilai dicatat — nilai lama, nilai baru, siapa, kapan — ke
`grading_audit_log`. Yang **tidak** dicatat: menyimpan ulang kertas yang tidak
disentuh, karena riwayat perubahan yang tidak pernah terjadi lebih buruk daripada
tidak ada riwayat. Ini yang bisa dibaca bila orang tua atau murid mempertanyakan
kenapa sebuah nilai berubah.
