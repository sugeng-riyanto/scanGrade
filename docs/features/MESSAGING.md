# Pesan — matriks CRUD, badge, dan yang sengaja tidak ada

Halaman untuk **keenam peran** adalah satu: `app/templates/shared/comms.html`
(`/comms`, `/teacher/comms`, `/student/comms`, `/admin-sekolah/comms`,
`/super-admin/comms`, `/principal/comms`, `/vice-principal/comms`).

RBAC pengiriman (siapa boleh menulis ke siapa, dan pasangan yang timbal balik)
ada di `AGENTS.md` § *Pertukaran Pesan — RBAC Matrix*. Berkas ini mencatat
**CRUD di dalam percakapan** — dan batasnya.

Sumber kebenaran: `app/routes/api.py`.

## Endpoint

| Aksi | Endpoint | Siapa |
|------|----------|-------|
| Daftar kontak | `GET /api/percakapan/contacts` | login; hanya peran yang **timbal balik** dengan peran sendiri (`_conversation_roles`), dan hanya sekolah sendiri kecuali `super_admin` |
| Cari penerima | `GET /api/percakapan/recipients?q=` | sama |
| Buka percakapan | `POST /api/percakapan` | login; penerima harus ada, perannya ada di daftar, **entri balikannya juga ada**, dan (kecuali `super_admin`) sekolahnya sama |
| Arsipkan | `PUT /api/percakapan/<id>` | peserta |
| Hapus percakapan | `DELETE /api/percakapan/<id>` | `super_admin` |
| Baca pesan | `GET /api/percakapan/<id>/pesan` | peserta; **membaca berarti menandai sudah dibaca** (`mark_pesan_read`) |
| Kirim pesan | `POST /api/percakapan/<id>/pesan` | peserta, dan pasangan peran masih sah |
| Edit pesan | `PUT /api/pesan/<id>` | **penulisnya saja** — `super_admin` pun ditolak |
| Hapus pesan (*unsend*) | `DELETE /api/pesan/<id>` | penulisnya, atau `super_admin` |
| Badge belum dibaca | `GET /api/percakapan/<id>/unread-count` | **peserta saja** |

Dua aturan tinggal di satu fungsi masing-masing, karena itulah bentuk cacatnya
sebelum ini:

* **`_conversation_for(supabase, percakapan_id, uid)`** — satu-satunya penjaga
  partisipasi. Percakapan yang tidak ada → **404**; percakapan orang lain →
  **403** (jawaban yang sengaja tidak membedakan mana yang benar, supaya uuid
  tidak bisa dipakai menebak). Sebelum ini endpoint badge tidak memeriksa sama
  sekali: hitungan belum-dibaca percakapan orang lain bisa dibaca dengan
  menyebut uuid-nya.
* **`_unread_count(supabase, percakapan_id, uid)`** — satu-satunya aritmetika
  badge: pesan **masuk**, **belum dibaca**, **belum dihapus**. Dipakai daftar
  percakapan, daftar kontak, dan endpoint badge, karena dua dari tiga permukaan
  itu dulu berbeda soal pesan yang sudah dihapus (lihat *Cacat* di bawah).

## Urutan daftar kontak

`last_message_at` menurun: percakapan terbaru di atas, kontak yang belum pernah
dihubungi di bawah. Kuncinya satu nilai (`last_message_at or ""`), sebab kunci
tuple + `reverse=True` membalik **seluruh** tuple dan menaruh orang yang belum
pernah dihubungi di puncak daftar.

## Yang sengaja tidak ada

* **`sembunyikan` (sembunyikan-per-pembaca).** Percakapan hanya punya satu kolom
  `pesan.is_deleted`, yaitu **hapus-untuk-semua**; menyembunyikan untuk satu
  pembaca butuh satu baris per pembaca. `message_hides` yang dibuat migrasi 021
  menempel pada tabel `notifications` yang sudah pensiun dan **bukan** milik
  `pesan`, jadi jangan menghitungnya sebagai fitur percakapan.
* **Jendela waktu untuk edit.** Penulis boleh mengedit pesannya kapan saja;
  barisnya ditandai `is_edited` dan halaman menampilkan *diedit*.
* **Pemberitahuan langsung saat lawan bicara mengedit/menghapus.** Baris baru
  terbaca saat daftar pesan diambil (poll di halaman), bukan lewat push.
* **`super_admin` menulis ulang pesan orang lain.** Ia boleh **menghapus** pesan
  siapa pun (moderasi) tetapi tidak boleh mengeditnya — mengedit adalah perbuatan
  penulisnya di bawah namanya sendiri.

## Penjaga

`tests/unit/test_messaging_rbac_matrix.py` (**67 uji**) memaku seluruh tabel di
atas untuk keenam peran, dengan fake Supabase lokal yang benar-benar menyaring
(termasuk `or_()` dan `rpc("mark_pesan_read")`): matriks harfiah per peran
dibandingkan ke `_conversation_roles` **dan** ke jawaban endpoint, tiap pasangan
timbal balik bisa dibuat dan tiap pasangan lain ditolak 403, non-peserta ditolak
di kirim/baca/arsip/badge, edit hanya penulisnya, hapus penulisnya atau
`super_admin`, badge tidak menghitung pesan terhapus, membaca mengosongkan badge,
urutan kontak, serta sweep literal `/api/...` di halaman terhadap `app.url_map`
supaya halaman tidak pernah memanggil rute yang tidak ada.

`.freebuff/mutate_messaging_rbac.py` menyuntikkan keempat cacatnya kembali dan
**4/4 tertangkap**.
