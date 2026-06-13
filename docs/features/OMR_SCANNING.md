# OMR Scanning — Cara Kerja

## Apa itu OMR?

OMR (Optical Mark Recognition) adalah teknologi untuk mendeteksi jawaban yang ditandai (biasanya pensil 2B) pada lembar jawaban. ScanGrade menggunakan OpenCV untuk memproses foto lembar jawaban.

## Pipeline Pemrosesan

```
Upload Foto → Validasi (format/ukuran) → Strip EXIF → Load → Deskew 
→ Adaptive Threshold → Denoise → Deteksi Mark → Perspective Correct 
→ Deteksi Bubble → Scoring
```

### 1. Upload & Validasi
- Format: `.jpg`, `.jpeg`, `.png`
- Maks: 20MB
- MIME type & integritas gambar diverifikasi
- EXIF (metadata GPS) otomatis dihapus

### 2. Preprocessing
- **Deskew**: Memperbaiki foto miring menggunakan `cv2.minAreaRect`
- **Adaptive Threshold**: CLAHE + `ADAPTIVE_THRESH_GAUSSIAN_C` untuk menangani foto gelap/terang
- **Denoise**: MORPH_OPEN/CLOSE + medianBlur

### 3. Deteksi Mark Registrasi
Sistem mencari 4+ titik hitam di sudut lembar sebagai referensi geometri.

### 4. Perspective Correct
Transformasi perspektif untuk mendapatkan tampilan top-down (850x1100 px).

### 5. Deteksi Bubble
Menggunakan posisi grid LJK (Lembar Jawaban Komputer) yang telah dikalibrasi:
- 25 soal per kolom
- 5 opsi (A/B/C/D/E)
- Jarak bubble 6.5mm horizontal, 8.5mm vertikal

### 6. Confidence Score
Setiap bubble dihitung fill ratio-nya (OTSU + adaptive threshold). Confidence 0.0-1.0:
- **>0.7**: High confidence
- **0.6-0.7**: Medium confidence
- **<0.6**: Needs review (flag `needs_review: true`)

## Best Practices

| Faktor | Rekomendasi |
|--------|-------------|
| Pencahayaan | Cukup, merata, tidak ada bayangan |
| Posisi | Lembar penuh terlihat, rata |
| Resolusi | Min 720p, recommend 1080p |
| Pensil | 2B, bulatan penuh tidak setengah |
| Background | Kontras (putih/hijau) |

## Troubleshooting

| Masalah | Solusi |
|---------|--------|
| "Tanda registrasi tidak ditemukan" | Foto ulang, pastikan 4 sudut terlihat |
| Confidence rendah | Scan ulang dengan kualitas lebih baik |
| Jawaban tidak terbaca | Lembar kotor/hapus pensil |
| Error processing | File corrupt, upload ulang |
