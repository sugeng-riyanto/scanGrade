# 04 — Presensi otomatis dan ekspor

## Sumber kebenaran

Kehadiran berasal dari join/leave media yang terverifikasi di server (webhook bertanda
tangan + rekonsiliasi). Membuka URL/lobby atau klik tombol bukan bukti hadir.
Heartbeat klien hanya petunjuk koneksi, bukan bukti otoritatif satu-satunya.

Model usulan (sesuaikan tabel yang sudah ada, jangan membuat duplikat):
- attendance_event: school/session/user atau guest subject, unique provider event id,
  event kind, provider time, received time, connection identity, validation status.
- attendance_interval: join, leave, leave source, confidence, connection id.
- attendance_summary: first_join, last_leave, total_seconds, reconnect_count,
  media warning counts, presence decision, threshold version.
- attendance_correction: original/new decision, reason, actor, UTC timestamp,
  version dan audit; tidak menimpa raw event.

Server menjaga scope FK sekolah/sesi/subjek. Unik per orang per sesi; user berganti
perangkat atau tab tetap satu orang. Gabungkan union interval yang overlap agar
jumlah detik tidak ganda; clamp pada mulai/akhir sesi. Duplikat dan event terlambat/
out-of-order tidak menggandakan durasi. Event dengan leave hilang direkonsiliasi:
beri estimated/unknown, jangan mengarang waktu hadir pasti. Rebuild summary harus
idempotent dari event asli. Tamu tidak dicocokkan ke murid berdasarkan nama.

Tampilkan waktu WIB; simpan UTC. Gunakan waktu server/provider, bukan jam perangkat.
Definisi hadir/terlambat memakai ambang yang admin tetapkan dan ditampilkan pada
laporan. Default tanpa keputusan admin: laporkan durasi/status teknis, jangan memberi
label absen sebagai hukuman. Terputus dan kamera OFF adalah kolom terpisah.

## Laporan profesional

PDF: logo sekolah berizin, nama sekolah, judul/jenis sesi, kelas, tanggal WIB, pengawas,
nomor laporan, waktu pembuatan, ringkasan, legenda estimasi/koreksi, tabel per peserta,
header berulang, nomor halaman. A4 portrait untuk ringkasan; landscape untuk tabel
lebar. Nama panjang, 40 peserta, multi-halaman, light print dan karakter Indonesia
harus teruji. Jangan cetak token room atau rahasia. QR verifikasi hanya bila endpoint
verifikasi dibangun dan berizin; jangan membuat QR dekoratif yang mengklaim validasi.

XLSX: sheet Ringkasan, Daftar Hadir, Riwayat Koneksi dan Koreksi jika ada. Header tetap,
filter, lebar kolom wajar, tipe tanggal/durasi benar, legenda WIB, total tidak dihitung
ganda. Escape formula injection pada nama/judul yang diawali =,+,-,@; hyperlink user
juga divalidasi. Hanya kolom perlu, tanpa secret dan tanpa telepon secara default.

Ekspor dicek server setiap permintaan dan unduhan; admin sekolah scope sekolah,
guru/pengawas hanya sesi berwenang. Tautan file private dan berumur pendek. Catat
aktor, sesi, filter, waktu dan format. Jangan simpan hasil sensitif di public bucket.
Retensi presensi dan audit ditetapkan pemilik sebelum pilot, bukan angka hukum
rekaan; implementasikan purge terjadwal dan kontrol akses yang dapat diuji.
