# 02 — Akses, undangan dan pengawas pengganti

## Hak server

| Aktor | Ruang sekolah | Kontrol | Presensi/ekspor |
| --- | --- | --- | --- |
| Super Admin | Sesuai scope sekolah yang dipilih dan tercatat | Pengelolaan berwenang | Sesuai scope dan audit |
| Admin sekolah | Hanya sekolahnya | ON/OFF, mulai/akhiri, tugaskan/ambil alih | Seluruh sesi sekolahnya |
| Guru | Ujian miliknya/meeting yang dipandu | Host; bisa dibatasi kontrak dan assignment | Sesi miliknya/penugasannya |
| Pengawas pengganti | Penugasan eksplisit, berjangka, oleh admin | Hanya ujian/sesi yang ditugaskan | Sesi yang ditugaskan |
| Murid | Ujian yang memang boleh diikuti/meeting yang diundang | Media sendiri, raise hand | Tidak mengunduh daftar peserta sekolah |
| Peserta umum | Hanya meeting biasa lewat undangan umum | Sesuai admission host | Tidak ada ekspor presensi kolektif |

Jangan memberi semua guru akses ke semua kamera/ujian di sekolah. Status admin juga
bukan izin lintas sekolah. Hak principal/vice_principal mengikuti RBAC repository,
tidak diperluas menjadi admin hanya karena dapat membaca hasil ujian.

## Pengambilalihan yang aman

1. Admin membuka sesi dari dashboard dan memilih Ambil alih / Tugaskan pengawas.
2. Server memverifikasi school, assignment, entitlement dan tahun ajaran.
3. Simpan perubahan pengawas melalui transaksi/compare-and-swap, beserta aktor,
   pengawas lama/baru, waktu UTC dan alasan.
4. Room/token/track permissions harus mengikuti pengawas aktif. Implementasi saat
   ini mengikat kamera murid ke creator: mengganti created_by saja **tidak cukup**.
5. Pilih dan buktikan protokol: rebind izin SFU dan seluruh publisher secara aman,
   atau tutup ruang lama yang sudah dihapus di provider, lalu rejoin ruang pengganti.
   Jangan membuat dua pengawas tak berwenang tetap menerima kamera.
6. Transfer tidak menghabiskan trial/sesi berbayar dua kali, tidak mereset deadline,
   dan tidak menambah batas peserta. Jika provider gagal, transfer dilaporkan pending/
   blocked dan cleanup dicatat tahan restart; jangan melaporkan takeover sukses palsu.
7. Klien lama kehilangan kontrol; murid mendapat nama pengawas baru dan status reconnect.
   Tidak menyentuh jawaban, timer atau nilai.

## Undangan

| Scope | Tujuan | Pagar akses |
| --- | --- | --- |
| Kelas | Anggota kelas sekolah yang dipilih | Membership server, bukan class_id URL |
| Individu | User yang ditunjuk | Cocokkan subject akun dan scope sekolah |
| Umum | Meeting biasa | Link/kode acak berumur terbatas, admission host |
| Pengawas | Akun staf yang ditugaskan | Link tidak memberi hak host tanpa assignment |

Undangan punya id, room, issuer, audience, expiry, revoked_at dan audit. Simpan hash
token; jangan mengembalikan secret pada API daftar undangan. Cabut link dan tolak
expired/replay yang tidak diizinkan. Link umum tidak membuka direktori murid/kelas
atau halaman ujian. Untuk tamu tanpa akun, gunakan identitas tamu server dan admission;
jangan memalsukan row profiles atau menganggap display name sebagai identitas siswa.

Tombol Salin link dan Bagikan via WhatsApp membuka compose yang dapat ditinjau.
Jangan kirim otomatis, mengimpor nomor telepon, atau membuka direktori lintas sekolah.
Partisipan dapat melihat undangan yang berlaku di akunnya. Notifikasi in-app harus
idempotent dan tidak mengirim ulang setiap reconnect.
