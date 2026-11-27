# Petik Universal Extractor API

Backend Petik v1.6.0 berbasis FastAPI, yt-dlp, gallery-dl, Deno/EJS, dan FFmpeg. API mendukung dua pola:

- Native Petik: `POST /api/inspect` memeriksa metadata, format video/audio, dan galeri foto; `POST /api/download` memproses video/audio.
- Galeri foto: `POST /api/gallery/download` mengunduh indeks foto terpilih sebagai file individual atau satu ZIP.
- Cobalt-compatible: `GET /` menampilkan daftar layanan dan `POST /` mengembalikan `status: tunnel` agar frontend lama masih dapat bekerja.

## Deploy lewat Render

1. Buat atau buka repository GitHub `petik-universal-api`.
2. Unggah seluruh isi ZIP backend ini ke root repository (bukan file ZIP-nya).
3. Pastikan `Dockerfile`, `app.py`, `requirements.txt`, dan `render.yaml` berada langsung di root.
4. Di Render, buka Blueprint `petik-universal-api`, lalu jalankan Manual Sync / Apply jika diminta.
5. Tunggu deploy berstatus **Live**.
6. Tes `https://NAMA-LAYANAN.onrender.com/health`; versi yang baru harus menampilkan `1.6.0` dan `galleryDlAvailable: true`.
7. Tes URL root `/`; daftar `cobalt.services` harus memuat layanan yang ditawarkan.

## Platform dan batas dukungan

Backend mengenali YouTube, Instagram, Facebook, TikTok, X/Twitter, Reddit, Vimeo, Pinterest, SoundCloud, Telegram publik, Threads publik, serta Snapchat Spotlight. Pengenalan tautan tidak menjamin semua postingan berhasil diekstrak. Situs dapat mengganti API, membatasi IP server, membutuhkan login, atau mengubah proteksi.

Batasan khusus:

- YouTube dapat tetap menolak IP server Render atau meminta autentikasi. Deno dipasang karena yt-dlp memerlukan JavaScript runtime dan EJS untuk menyelesaikan tantangan JavaScript YouTube; Deno membantu, tetapi tidak mengatasi semua pembatasan server-side.
- Snapchat Spotlight adalah best-effort; extractor yt-dlp pernah dilaporkan gagal akibat perubahan Next.js data pada beberapa tautan.
- Telegram hanya mendukung postingan channel/grup publik, misalnya `t.me/nama_channel/123`. Tautan privat, invite, chat pribadi, konten protected, dan `t.me/c/...` tidak didukung.
- Threads hanya untuk postingan publik. Meta dapat mengubah struktur halaman kapan saja.
- Media privat atau yang perlu login/cookies tidak didukung. Galeri foto memakai gallery-dl untuk Instagram, Facebook, X/Twitter, Pinterest, dan Reddit; dukungan aktual tetap bergantung pada extractor dan akses publik. Threads belum memiliki dukungan gallery-dl foto khusus dalam paket ini.
- Galeri menampilkan maksimal 20 foto per proses, batas 20 MiB per foto dan 60 MiB total secara default. File ZIP disimpan sementara. Untuk iPhone, ZIP lebih praktis untuk banyak foto; mode non-ZIP mengirim file satu per satu.
- Batas video/audio default 140 MiB, durasi 60 menit, dua proses media bersamaan, dan 40 permintaan per IP dalam lima menit.
- Render Free dapat tidur setelah tidak aktif; permintaan pertama bisa lambat. Filesystem-nya sementara/ephemeral, sehingga berkas unduhan disimpan sementara dan otomatis kedaluwarsa.

## Urutan API di frontend Petik

1. API khusus yang diisi pengguna (jika ada).
2. API Petik sendiri: `https://petik-universal-api-masx.onrender.com`.
3. Cobalt API sebagai cadangan.

## Variabel lingkungan

`PUBLIC_BASE_URL`, `ALLOWED_ORIGINS`, `MAX_FILE_MB`, `MAX_DURATION_SECONDS`, `FILE_TTL_SECONDS`, `MAX_CONCURRENT_DOWNLOADS`, `RATE_LIMIT_COUNT`, `RATE_LIMIT_WINDOW_SECONDS`, dan `TEMP_DIR`.

## Tes

Folder `tests/` berisi tes offline untuk validasi platform, endpoint inspect dan URL preview progresif, deteksi direct-media URL, endpoint download, kompatibilitas Cobalt, tunnel file, format MP3, filter durasi, dan kualitas video. Tes tiruan tidak membuktikan keberhasilan unduhan langsung dari situs sumber.


## Perubahan v1.6.0 (berbasis API v1.5)

- Menambahkan `gallery-dl==1.32.21` sebagai extractor foto open-source; tidak memakai Apify atau API berbayar.
- `/api/inspect` kini dapat mengembalikan `photoItems` dan mempertahankan opsi video/audio yang ditemukan. Jika yt-dlp gagal pada tautan foto, galeri masih dapat menjadi hasil pemeriksaan.
- `POST /api/gallery/download` menerima `url`, `indices`, dan `zip`. Pilihan ZIP menghasilkan satu file; tanpa ZIP mengembalikan daftar file individual.
- Memasang batas item, ukuran foto, total ukuran, validasi indeks, timeout, penamaan file, dan pembersihan sementara.
- `/health` menampilkan `galleryDlAvailable`, `galleryDlVersion`, dan `maxGalleryItems`. Jika `galleryDlAvailable` false, foto tidak dapat diekstrak sampai dependency berhasil terpasang pada deploy.

## Perubahan yang diwarisi dari v1.5

- yt-dlp tidak lagi dikunci ke satu versi, dan diperbarui otomatis tiap container hidup (matikan dengan `YTDLP_AUTO_UPDATE=0`). `/health` menampilkan `ytdlpVersion`.
- Unduhan bersamaan default 2 dan batas 40 permintaan per 5 menit. Kalau Render Free kehabisan memori (out of memory di log), kembalikan `MAX_CONCURRENT_DOWNLOADS` ke 1.
- Pratinjau punya slot dan batas sendiri.
