# Petik Universal Extractor API

Backend Petik v1.6.1 berbasis FastAPI, yt-dlp, gallery-dl, curl-cffi (fallback Instagram), Deno/EJS, HTTPX, dan FFmpeg. API mendukung dua pola:

- Native Petik: `POST /api/inspect` memeriksa metadata, format video/audio, dan galeri foto; `POST /api/download` memproses video/audio.
- Galeri foto: `POST /api/gallery/download` mengunduh indeks foto terpilih sebagai file individual atau satu ZIP.
- Cobalt-compatible: `GET /` menampilkan daftar layanan dan `POST /` mengembalikan `status: tunnel` agar frontend lama masih dapat bekerja.

## Deploy lewat Render

1. Buat atau buka repository GitHub `petik-universal-api`.
2. Unggah seluruh isi ZIP backend ini ke root repository (bukan file ZIP-nya).
3. Pastikan `Dockerfile`, `app.py`, `requirements.txt`, dan `render.yaml` berada langsung di root.
4. Di Render, buka Blueprint `petik-universal-api`, lalu jalankan Manual Sync / Apply jika diminta.
5. Tunggu deploy berstatus **Live**.
6. Tes `https://NAMA-LAYANAN.onrender.com/health`; versi yang baru harus menampilkan `1.6.1`, `galleryDlAvailable: true`, serta `galleryDlVersion` yang terdeteksi.
7. Tes URL root `/`; daftar `cobalt.services` harus memuat layanan yang ditawarkan.

## Platform dan batas dukungan

Backend mengenali YouTube, Instagram, Facebook, TikTok, X/Twitter, Reddit, Vimeo, Pinterest, SoundCloud, Telegram publik, Threads publik, Snapchat Spotlight, Dailymotion, dan LinkedIn. Pengenalan tautan tidak menjamin semua postingan berhasil diekstrak; beberapa platform hanya best-effort melalui yt-dlp dan belum punya fallback khusus. Situs dapat mengganti API, membatasi IP server, membutuhkan login, atau mengubah proteksi.

Batasan khusus:

- YouTube dapat tetap menolak IP server Render atau meminta autentikasi. Deno dipasang karena yt-dlp memerlukan JavaScript runtime dan EJS untuk menyelesaikan tantangan JavaScript YouTube; Deno membantu, tetapi tidak mengatasi semua pembatasan server-side.
- Snapchat Spotlight adalah best-effort; extractor yt-dlp pernah dilaporkan gagal akibat perubahan Next.js data pada beberapa tautan.
- Telegram hanya mendukung postingan channel/grup publik, misalnya `t.me/nama_channel/123`. Tautan privat, invite, chat pribadi, konten protected, dan `t.me/c/...` tidak didukung.
- Threads: galeri foto/carousel memakai resolver halaman publik tanpa login. Resolver mengambil JSON yang dirender pada halaman post dan memilih post berdasarkan shortcode. Ini rapuh terhadap perubahan HTML/JSON Meta; jika payload tidak lagi tampil untuk pengunjung publik, ekstraksi akan gagal dengan aman. Foto dan video tetap dipisahkan.
- Instagram: galeri foto mencoba gallery-dl, lalu fallback GraphQL anonim menggunakan curl-cffi untuk postingan publik. Fallback ini mengikuti pendekatan request anonim yang juga dipakai proyek open-source DownSnap; tidak memakai akun, username/password, atau cookie sesi pengguna. Sesi HTTP anonim hanya dipakai untuk mengambil token CSRF publik sementara. GraphQL `doc_id` dapat berubah dan Instagram dapat menampilkan login wall atau membatasi IP server, sehingga ini best-effort.
- Facebook, X/Twitter, Pinterest, dan Reddit: galeri memakai gallery-dl. Extractor upstream dapat meminta login, cookie, atau OAuth pada sebagian konten/versi platform. Petik tidak mengirim cookie akun pengguna; jika sumber menolak akses tanpa login, unduhan memang tidak akan berhasil.
- Tidak ada API komersial, Apify, token API pihak ketiga, atau login akun media sosial yang ditambahkan. Story Instagram/Facebook bukan bagian dari galeri post dan tidak dijanjikan tersedia.
- Dailymotion dan LinkedIn sekarang dikenali saat validasi tautan dan diserahkan ke yt-dlp; ini bukan jaminan semua jenis post LinkedIn/Dailymotion berhasil.
- Media privat, paywalled, age-gated, atau yang hanya dapat dibuka dengan sesi login tidak didukung.
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

Folder `tests/` berisi 44 tes offline untuk validasi platform, endpoint inspect dan URL preview progresif, deteksi direct-media URL, parser CDN dengan ekstensi query, parser galeri Threads/Instagram, keamanan host CDN dan redirect, indeks carousel campuran, endpoint download, kompatibilitas Cobalt, tunnel file, format MP3, filter durasi, dan kualitas video. Tes tiruan tidak membuktikan keberhasilan unduhan langsung dari situs sumber.


## Perubahan v1.6.1 (revisi berbasis API v1.6)

- Memperbaiki parser URL galeri agar membaca ekstensi gambar dari path maupun parameter query CDN (`format`, `fm`, `ext`, `mime_type`, dan nama file), sambil tetap menolak URL HTTP atau URL yang tidak bisa dipastikan sebagai gambar.
- Memperbaiki batas indeks galeri untuk carousel campuran: indeks sumber foto/video kini boleh sampai `MAX_GALLERY_SOURCE_ITEMS`, bukan hanya jumlah foto yang terlihat.
- Menambahkan resolver foto/carousel Threads dari JSON halaman publik; URL postingan `/share/` dapat dipadankan melalui canonical `og:url`. Unduhan foto Threads memakai host CDN allowlist, validasi MIME/magic bytes, batas ukuran, dan redirect yang dibatasi.
- Menambahkan fallback galeri Instagram tanpa sesi akun: gallery-dl dicoba dahulu, lalu GraphQL publik anonim dengan `curl-cffi`. Fallback ini best-effort karena GraphQL internal dapat berubah atau dibatasi.
- Menambahkan pengenalan domain Dailymotion, `dai.ly`, dan LinkedIn.
- Menambahkan HTTPX sebagai dependensi eksplisit dan `curl-cffi` untuk fallback Instagram.
- Memperbaiki `/health` untuk melaporkan versi gallery-dl melalui metadata paket jika CLI tidak menuliskan versi ke stdout.
- Memperjelas pesan error ekstraksi agar status login wall, pembatasan, URL tidak didukung, dan timeout tidak seluruhnya berubah menjadi pesan generik.
- Video/audio dan antarmuka galeri yang ada tetap dipertahankan.

## Perubahan yang diwarisi dari v1.5

- yt-dlp tidak lagi dikunci ke satu versi, dan diperbarui otomatis tiap container hidup (matikan dengan `YTDLP_AUTO_UPDATE=0`). `/health` menampilkan `ytdlpVersion`.
- Unduhan bersamaan default 2 dan batas 40 permintaan per 5 menit. Kalau Render Free kehabisan memori (out of memory di log), kembalikan `MAX_CONCURRENT_DOWNLOADS` ke 1.
- Pratinjau punya slot dan batas sendiri.
