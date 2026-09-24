# Pricing & Subscription Plans

## Free Trial

Setiap sekolah baru mendapatkan trial gratis (bawaan **14 hari**, diatur di
`/super-admin/trial-settings`) dengan paket Starter:
- 5 ujian
- 100 siswa
- Tanpa AI grading

### Satu tempat yang menentukan panjangnya

Panjang trial dibaca lewat `app/services/trial_settings.py` — `get_trial_days()` —
dan **tidak ada tempat lain yang boleh memutuskan angka itu**. Sebelumnya tidak
begitu: alur persetujuan pendaftaran sekolah (pintu masuk hampir semua sekolah)
menulis `14` sebagai literal, sehingga halaman pengaturan trial tidak berpengaruh
pada satu pun trial yang diklaimnya atur. Kini tiga tempat yang butuh angka ini —
persetujuan pendaftaran, aktivasi tunai oleh super admin, dan kalimat di halaman
langganan sekolah — membaca satu nilai yang sama, dan tanggal berakhirnya
dihitung dari pembacaan yang sama (`days_until()`), sebab baris yang menyatakan 30
hari tapi berakhir dalam 14 lebih buruk daripada salah satu kesalahan itu sendiri.

Halaman `/super-admin/trial-settings` kini CRUD yang sebenarnya di balik RBAC
super admin: **read** (nilai yang berlaku, apakah itu override atau bawaan, siapa
mengubahnya dan kapan, plus riwayat perubahan dari audit log), **create/update**
(satu baris, divalidasi 0–365 hari, penolakan menyebut batasnya), dan **delete**
(menghapus override sehingga bawaan berlaku lagi — satu-satunya "D" yang jujur
untuk tabel pengaturan tunggal). Nilai `0` adalah kebijakan ("tanpa trial"),
bukan nilai kosong, jadi ia tidak jatuh kembali ke bawaan.

`trial_settings` tidak punya keunikan, dan ini bukan kekhawatiran teoretis:
**diukur di produksi, tabelnya berisi 15 baris** — 14 di antaranya benih dari
`012_subscription_system.sql` yang `ON CONFLICT DO NOTHING`-nya tanpa target
sehingga menyisipkan ulang setiap kali setup dijalankan. Angka yang pernah
diatur operator (30 hari, 22 September) justru berada di baris yang **id-nya
terkecil**, sementara `.limit(1)` tanpa urutan mengembalikan benih bulan Juni:
halaman berkata 30, persetujuan sekolah memberi 14. Karena itu baris yang berlaku
ditentukan oleh **tulisan manusia yang terakhir** (`updated_at` desc, `id`
sebagai pemecah seri, dengan `NULLS LAST` sebab Postgres menaruh NULL di *depan*
untuk `DESC`) — bukan baris dengan id terbesar, yang menjawab pertanyaan berbeda
dan salah untuk tabel ini. `supabase/migrations/034_trial_settings_singleton.sql`
menghapus baris ganda dengan aturan yang **sama** (kalau berbeda, menerapkan
migrasi akan mengubah pengaturannya) lalu menambahkan indeks unik pada konstanta
`true`; kode tetap benar tanpa migrasi itu, dan halaman `/super-admin/trial-settings`
**melaporkan** jumlah baris lebihnya alih-alih merapikannya sendiri (menghapus
baris di dalam sebuah GET adalah penulisan yang tidak diminta siapa pun).

## Available Plans

| No | Paket | Durasi | Harga | per Bulan |
|----|-------|--------|-------|-----------|
| 1 | 1 Bulan | 30 hari | Rp59.000 | Rp59.000 |
| 2 | 3 Bulan | 90 hari | Rp149.000 | Rp49.667 |
| 3 | 4 Bulan | 120 hari | Rp179.000 | Rp44.750 |
| 4 | 6 Bulan | 180 hari | Rp249.000 | Rp41.500 |
| 5 | 1 Tahun | 365 hari | Rp399.000 | Rp33.250 |
| 6 | 2 Tahun | 730 hari | Rp699.000 | Rp29.125 |
| 7 | 3 Tahun | 1.095 hari | Rp949.000 | Rp26.361 |
| 8 | 5 Tahun | 1.825 hari | Rp1.399.000 | Rp23.317 |
| 9 | 7 Tahun | 2.555 hari | Rp1.799.000 | Rp21.417 |
| 10 | Selamanya | - | Rp2.499.000 | - |

## Pricing Models

### Flat Pricing
Harga tetap per paket, tidak tergantung jumlah siswa.

### Scaled Pricing
Harga disesuaikan dengan jumlah siswa aktif:
- Tier 1: 0-100 siswa
- Tier 2: 101-250 siswa
- Tier 3: 251-500 siswa
- Tier 4: 500+ siswa

## Admin Fee

Untuk pembayaran via Midtrans, ada biaya admin:
- **Flat**: Rp4.000 per transaksi
- **Persentase**: 0% (dapat diatur oleh Super Admin)

## Upgrade & Renewal

- Upgrade kapan saja (biaya proporsional)
- Perpanjangan sebelum masa berlaku habis
- Aktivasi via kode yang dikirim setelah pembayaran
