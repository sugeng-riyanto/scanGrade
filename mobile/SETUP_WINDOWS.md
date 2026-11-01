# Instalasi di Windows

## 1. Android Studio

Unduh dari https://developer.android.com/studio. Instal dengan pengaturan Standard dan selesaikan unduhan SDK. Periksa persyaratan Windows, RAM, dan ruang disk pada versi yang akan dipasang; jangan mengasumsikan Windows 10 lama didukung semua versi terbaru.

## 2. Flutter

Ikuti instalasi manual Windows dari https://docs.flutter.dev/install: unduh SDK stable resmi, ekstrak ke folder pengguna yang dapat ditulis, misalnya C:\dev\flutter, lalu tambahkan C:\dev\flutter\bin ke PATH pengguna. Instal Git bila belum tersedia.

Di Android Studio buka pengaturan Plugins, pasang Flutter beserta Dart, lalu restart. Plugin Flutter tidak menggantikan instalasi Flutter SDK.

Buka terminal baru dan jalankan satu per satu:

```powershell
flutter --version
flutter doctor -v
flutter doctor --android-licenses
flutter doctor -v
```

Baca lisensi sebelum menyetujuinya. Selesaikan masalah bagian Android toolchain dan Android Studio. Peringatan Visual Studio untuk target Windows desktop tidak menghalangi target Android.

## 3. Buat proyek

Gunakan clone repo ScanGrade yang sudah ada. Buka terminal pada akar clone tersebut. Jalankan dari folder mobile, sehingga backend Flask tetap terpisah dari aplikasi Flutter:

```powershell
cd "$env:USERPROFILE\Documents"
flutter create --platforms=android,ios scangrade_mobile
cd scangrade_mobile
```

Pastikan subfolder scangrade_mobile belum ada sebelum menjalankan flutter create. Application ID awal bersifat sementara; tentukan identitas resmi sebelum rilis.

Dokumen Markdown tetap berada di folder mobile, satu tingkat di atas proyek Flutter. Di Android Studio gunakan Open untuk membuka folder scangrade_mobile yang baru dibuat. Jangan menjalankan flutter create di akar repo ScanGrade.

## 4. Uji di HP

Aktifkan Developer options dan USB debugging di HP Android, sambungkan USB, lalu izinkan debugging dari komputer sendiri. Instal driver USB perangkat bila dibutuhkan. Alternatif: buat emulator melalui Device Manager bila RAM dan virtualisasi memadai.

```powershell
flutter devices
flutter pub get
flutter run
```

Jika terdapat beberapa perangkat, pilih ID yang terlihat menggunakan flutter run -d ID_PERANGKAT.

## 5. Pemeriksaan dan APK

Setelah agent mengimplementasikan fitur:

```powershell
dart format --output=none --set-exit-if-changed lib test
flutter analyze
flutter test
flutter build apk --debug
```

APK debug biasanya berada di build/app/outputs/flutter-apk/app-debug.apk. Ini untuk pengujian internal. Release signing, AAB, dan distribusi toko adalah tahap berikutnya.

## iOS

Kode bersama dapat dikerjakan di Windows. Build dan pengujian iOS memerlukan lingkungan macOS dengan Xcode, baik Mac sendiri maupun runner Mac. Folder ios yang dibuat di Windows belum membuktikan aplikasi dapat berjalan di iPhone. Signing dan distribusi memerlukan konfigurasi Apple yang sesuai.
