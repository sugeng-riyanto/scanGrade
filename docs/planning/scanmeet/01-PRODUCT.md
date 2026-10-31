# 01 — Produk dan keputusan tetap

## Dua mode yang benar-benar berbeda

| Aturan | Ujian | Meeting biasa |
| --- | --- | --- |
| ON/OFF | Per ujian, server berwenang; default OFF | Host membuat/memulai ruang tersendiri |
| Kamera | Wajib untuk pengawasan setelah izin | Pilihan peserta dari lobby |
| Mikrofon | Mati; izin pengawas + tindakan peserta untuk bicara | Pilihan lobby; default Jumpa ON setelah izin, host bisa mute |
| Antar peserta | Kamera murid hanya ke pengawas berwenang | Peserta melihat sesuai layout/jenis ruang |
| Chat | OFF | Host dapat mengaktifkan/nonaktifkan |
| Berbagi layar | OFF | Host/co-host; peserta jika diizinkan host |
| Efek/latar | Tidak menyamarkan kamera; efek kosmetik OFF | Studio kamera opsional |
| Perekaman | Dilarang pada aplikasi/token/recorder view | Lokal dahulu, host/admin berwenang |
| Kehadiran | Terpisah dari nilai dan penalti | Presensi otomatis per sesi |
| Kegagalan media | Alert pengawas; ujian tetap berjalan | Reconnect, bukan membuat peserta baru |

Kamera/mikrofon tidak boleh otomatis menyala saat guru mematikan ScanMeet.
Izin OS/browser tetap diperlukan, tidak dapat dilewati oleh aplikasi. Setting tersimpan
ON adalah preferensi, bukan izin menangkap media sebelum pengguna bergabung.

## Kapasitas dan penawaran yang telah diputuskan

- Maksimum **40 orang termasuk host/pengawas**, bukan 40 murid + guru.
- Trial: gratis, satu sesi 45 menit, masa aktivasi 7 hari, sekali per sekolah.
- Premium bulanan: Rp49.000, 30 hari, maksimum 120 menit/sesi.
- Premium tahunan: Rp399.000, 365 hari, maksimum 120 menit/sesi.
- Premium tanpa batas jumlah sesi selama masa berlaku; kontrak baru satu ruang aktif
  bersamaan per sekolah. Jangan mengubah kontrak lama secara diam-diam.
- Aktivasi manual Super Admin; default OFF; tidak ada tagihan otomatis atau upgrade
  supplier. Kapasitas provider dan biaya participant-minute harus diverifikasi.
- Status entitlement mencakup trial/aktif/kedaluwarsa/ditangguhkan. Batas durasi,
  peserta, concurrency dan expiry ditegakkan di server, bukan timer UI saja.
- Meeting baru memakai anggaran concurrency dan entitlement sekolah yang sama sampai
  ada keputusan produk berbeda. Jangan membuat jalur meeting untuk menghindari kuota.
- Jangan menyalin tier kapasitas Jumpa 50 orang atau tier UMUM secara otomatis.

## Non-goal saat ini

Tidak membangun APK/iOS pada paket ini. Tidak menyimpan video di server, membuka
penagihan otomatis, menjanjikan pengganti OBS lengkap, atau menjamin semua fitur
media identik pada semua browser. Webinar >40 peserta memerlukan keputusan baru.
Paket ini mendeskripsikan target; yang benar-benar tersedia harus dibuktikan dari kode.
