# Panduan Upload Petik API v1.15.0 — iPhone

## 1. Backend API

1. Unduh `Petik-Universal-API-v1.15.zip`.
2. Buka aplikasi Files, masuk ke Downloads, lalu ketuk ZIP untuk mengekstraknya.
3. Buka folder hasil ekstrak dan pilih seluruh isi di dalamnya. Jangan mengunggah ZIP sebagai satu file.
4. Buka repository `taupik15/petik-universal-api` di GitHub.
5. Unggah seluruh isi ZIP ke root repository, pertahankan folder `tests/`.
6. Commit perubahan.
7. Buka Render service `petik-universal-api-masx`, pilih Manual Sync, lalu deploy commit terbaru.
8. Tunggu deployment selesai, kemudian buka `https://petik-universal-api-masx.onrender.com/health`.

Versi yang diharapkan: `1.15.0`. Periksa `youtubeRequestPolicy`:

- `retries: 0`
- `extractorRetries: 0`
- `fragmentRetries: 0`
- `fileAccessRetries: 0`
- `concurrentFragments: 1`

## 2. Website

1. Unduh `Petik-Website-YouTube-v1.15.zip` dan ekstrak.
2. Buka repository `taupik15/petik`.
3. Unggah `index.html` ke root, lalu unggah file `assets/petik-youtube-v1_15.js` ke folder `assets/`.
4. Jangan menghapus atau mengganti `assets/petik-app-v3_7.js`, file CSS, ataupun file asset lain.
5. Commit perubahan website.
6. Buka `https://taupik15.github.io/petik/` dan refresh halaman. Bila versi lama masih tersimpan, tutup tab dan buka ulang halaman.

## 3. Tes setelah deployment

1. Pastikan `/health` menampilkan versi `1.15.0` dan `youtubeRequestPolicy` sesuai angka di atas.
2. Coba satu tautan video YouTube publik satu kali.
3. Jika hasilnya HTTP 429, jangan kirim ulang berulang kali. Tunggu sesuai header `Retry-After`/cooldown.
4. Kirim hasil `/health`, respons `/api/inspect`, serta log Render untuk percobaan tersebut.
5. Jika inspect berhasil menemukan format, lanjut tes MP4 dan MP3 secara terpisah.

Catatan: perubahan ini tidak menjanjikan YouTube akan mengizinkan egress Render. Tes offline tidak menggantikan tes live di service pengguna.
