# 05 — Studio kamera, third view dan rekaman lokal

## Kamera meeting biasa

Ikuti Jumpa: preview lobby; pilihan kamera/mic/speaker bila didukung; kualitas Otomatis,
Hemat Data, Jernih; audio-only; peredam bising. ScanMeet menampilkan 360p/720p/1080p
hanya bila paket dan perangkat mendukung. Resolusi adalah target/max constraint,
bukan jaminan kamera benar-benar memberi 1080p. Tampilkan kualitas aktual bila tersedia.

Efek lokal opsional: blur/segmentasi, chroma key green screen (pilih warna, toleransi,
spill/tepi), background kantor/kelas/perpustakaan/studio, unggahan gambar, brightness,
contrast dan warmth; preview, reset dan Off. Chroma key fisik berbeda dari segmentasi
AI tanpa layar hijau. Aset harus legal, lokal dan branding ScanMeet. Jangan menyalin
merek Zoom/Meet/Teams atau menjanjikan kualitas algoritme yang identik.

Batasi dimensi/ukuran upload, validasi content type sebenarnya, jangan menerima SVG
aktif/URL bebas yang mencemari canvas/CORS. Efek di perangkat pengguna, capability
check WebGL/canvas/CPU, turunkan kualitas/fps pada perangkat lemah; OFF fallback.
Track hasil efek yang diterbitkan harus sama dengan preview/recording. Saat reset/
keluar, hentikan track, worker, object URL dan kamera lama. Mode ujian menolak efek
penyamaran termasuk bila klien memalsukan setting.

## Tampilan ketiga ala OBS

Halaman operator dengan Preview dan Program. Scene: grid, speaker + slides, slides
penuh, title/intermission. Preview tidak mengubah Program sampai Take/transition.
Program adalah output bersih tanpa tombol, chat privat, token atau notifikasi OS.
Clean feed receive-only untuk OBS eksternal, dengan akses operator yang sama ketat.
Bukan integrasi OBS otomatis/virtual camera; browser source membutuhkan akses yang
sesuai, bukan memasukkan cookie/key rahasia ke URL publik.

Mixer audio memakai track meeting yang berwenang, termasuk suara host, peserta dan
presentation audio yang benar-benar tersedia. Hindari feedback/duplikasi audio lokal.
Pengawas ujian/recorder tidak boleh mendapat jalur umum untuk melihat kamera murid.
Recorder role tidak dapat publish media atau mengubah room. Consumer baru harus
mematuhi admission, role, expiry dan revocation; jangan memberi room-admin token.

## Rekaman lokal

Hanya host/co-host dengan izin eksplisit/admin. OFF secara default. Sebelum mulai:
jelaskan peserta yang direkam, tujuan, persetujuan, perangkat tujuan, kemampuan audio
serta format yang tersedia. Tampilkan indikator recording dan durasi untuk semua
peserta, termasuk yang baru join/rejoin; simpan audit start/stop/actor/consent tanpa
mengunggah video. Berhenti ketika hak dicabut/room berakhir. Indikator aplikasi
bukan janji dapat mencegah peserta merekam sendiri melalui aplikasi eksternal.

MediaRecorder merekam komposisi Program beserta audio mixer; MIME dipilih memakai
isTypeSupported dan tes playback. Jangan hanya merekam kamera host atau mengklaim
file MP4 jika browser menghasilkan WebM. Start/pause/resume/stop; finalisasi dan
download lokal; nama file bersih berisi sesi/tanggal, tanpa data sensitif berlebih.

Cegah buffer RAM tak terbatas. Simpan chunk bertahap melalui kemampuan storage yang
tersedia dan manifest urutan/format/timestamp. Kuota storage harus dicek best effort,
write error ditampilkan, final chunk ditunggu sebelum menyebut file tersimpan.
**Chunk MediaRecorder tidak selalu dapat diputar sendiri; timeslice tidak presisi.**
Crash recovery hanya diklaim jika gabung/container dan playback terbukti; jika tidak,
label rekaman parsial dan sediakan ekspor diagnostik tanpa rahasia. Segment file yang
playable harus sengaja difinalisasi per segmen dengan gap yang terukur.

Disk penuh, close tab, sleep, lock screen, izin dicabut, browser crash, audio hilang,
network reconnect, perubahan scene/track, pause lama harus diuji. beforeunload tidak
menjamin menyelamatkan rekaman. Mobile yang tidak mendukung mendapat fallback jelas.
Tidak ada server recording, egress recorder berbayar atau upload otomatis ke cloud.
Local recording menghemat penyimpanan/upload rekaman, bukan menghapus biaya trafik SFU.
