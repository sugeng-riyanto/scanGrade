# Activation Codes & Subscription

## Format Kode Aktivasi

`SG-XXXX-XXXX-XXXX` (3 grup 4 karakter alfanumerik uppercase)

Contoh: `SG-A4B2-C7D1-E9F3`

## Cara Mendapatkan

### 1. Pembelian Online (Midtrans)
1. Admin sekolah pilih paket di halaman **Langganan**
2. Klik **Beli Sekarang**
3. Bayar via Midtrans (transfer bank, VA, QRIS, dll)
4. Setelah sukses → kode aktivasi otomatis tergenerate
5. Kode bisa langsung ditukar

### 2. Aktivasi Tunai (oleh Super Admin)
1. Super Admin buka **Kelola Kode Aktivasi**
2. Pilih sekolah berdasarkan NPSN
3. Klik **Generate** atau **Aktivasi Tunai**
4. Kode siap digunakan

## Cara Menukarkan

1. Admin sekolah buka menu **Langganan**
2. Klik **Tukar Kode Aktivasi**
3. Masukkan kode (contoh: SG-A4B2-C7D1-E9F3)
4. Klik **Tukarkan**
5. Langganan aktif sesuai durasi paket

## Tier & Batasan

| Tier | Ujian/Tahun | Siswa | AI Grading | Durasi |
|------|-------------|-------|------------|--------|
| **Trial** (gratis) | 5 | 100 | ❌ | 14 hari |
| **Starter** | 10 | 500 | ❌ | 1-12 bulan |
| **Pro** | ∞ | ∞ | ✅ | 1-12 bulan |
| **Enterprise** | ∞ | ∞ | ✅ | Kustom |

## Cara Tier Ditentukan

Tier dibaca dari **apa yang sekolah beli**, berurutan:

1. `tier` yang tersimpan di baris langganan, bila salah satu dari `basic`/`pro`/`enterprise`;
2. paket yang dirujuk langganan → **durasi paket**: ≤180 hari = `basic`,
   181–1095 hari = `pro`, >1095 hari atau `Selamanya` (0) = `enterprise`;
3. peta id paket bawaan, bila baris paketnya sudah tidak ada;
4. rentang langganan (`subscription_end − subscription_start`), bila paketnya hilang;
5. `basic` — **tier berbayar terendah** — untuk langganan `active` yang tidak
   menyimpan apa pun.

Baris berstatus `trial`/`trial_expired` tetap `trial`. **Langganan berbayar tidak
pernah resolve ke `trial`**: dulu setiap baris `plan_id IS NULL` (termasuk yang
`active`, hasil aktivasi tunai) dibaca sebagai trial, sehingga sekolah yang sudah
membayar pun terjepit kuota **5 ujian/tahun**. Aktivasi tunai dan penukaran kode
kini menyimpan `plan_id` + `tier`; paket yang dipilih online tapi dibayar tunai
diambil dari transaksi terakhir sekolah itu.

## Paket Berlangganan

| Paket | Harga | per Bulan |
|-------|-------|-----------|
| 1 Bulan | Rp59.000 | Rp59.000 |
| 3 Bulan | Rp149.000 | Rp49.667 |
| 6 Bulan | Rp249.000 | Rp41.500 |
| 1 Tahun | Rp399.000 | Rp33.250 |
| 2 Tahun | Rp699.000 | Rp29.125 |
| 3 Tahun | Rp949.000 | Rp26.361 |
| 5 Tahun | Rp1.399.000 | Rp23.317 |
| 7 Tahun | Rp1.799.000 | Rp21.417 |
| Selamanya | Rp2.499.000 | - |

## Scaled Pricing

Super Admin dapat mengaktifkan scaled pricing:
- Harga dasar disesuaikan dengan jumlah siswa
- Tier: 0-100, 101-250, 251-500, 500+ siswa
- Setiap tier punya harga berbeda

## Configurasi oleh Super Admin

1. Buka **Pengaturan Harga**
2. Pilih model: **Flat** (harga tetap) atau **Scaled** (per tier siswa)
3. Atur durasi trial
4. Atur biaya admin (flat + persentase)
