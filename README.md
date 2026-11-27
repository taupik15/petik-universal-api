# Petik Universal Extractor API

Backend Petik v1.6.2 berbasis FastAPI, yt-dlp, gallery-dl, curl-cffi (fallback Instagram), Deno/EJS, HTTPX, dan FFmpeg. API mendukung dua pola:

- Native Petik: `POST /api/inspect` memeriksa metadata, format video/audio, dan galeri foto; `POST /api/download` memproses video/audio.
- Galeri foto: `POST /api/gallery/download` mengunduh indeks foto terpilih sebagai file individual atau satu ZIP.
- Cobalt-compatible: `GET /` menampilkan daftar layanan dan `POST /` mengembalikan `status: tunnel` agar frontend lama masih dapat bekerja.

## Deploy lewat Render

1. Buat atau buka repository GitHub `petik-universal-api`.
2. Unggah seluruh isi ZIP backend ini ke root repository (bukan file ZIP-nya).
3. Pastikan `Dockerfile`, `app.py`, `requirements.txt`, dan `render.yaml` berada langsung di root.
4. Di Render, buka Blueprint `petik-universal-api`, lalu jalankan Manual Sync / Apply jika diminta.
5. Tunggu deploy berstatus **Live**.
6. Tes `https://NAMA-LAYANAN.onrender.com/health`; versi yang baru harus menampilkan `1.6.2`, `galleryDlAvailable: true`, serta `galleryDlVersion` yang terdeteksi.
7. Tes URL root `/`; daftar `cobalt.services` harus memuat layanan yang ditawarkan.

## Platform dan batas dukungan

Backend mengenali YouTube, Instagram, Facebook, TikTok, X/Twitter, Reddit, Vimeo, Pinterest, SoundCloud, Telegram publik, Threads publik, Snapchat Spotlight, Dailymotion, dan LinkedIn. Pengenalan tautan tidak menjamin semua postingan berhasil diekstrak; beberapa platform hanya best-effort melalui yt-dlp dan belum punya fallback khusus. Situs dapat mengganti API, membatasi IP server, membutuhkan login, atau mengubah proteksi.

Batasan khusus:

- YouTube dapat tetap menolak IP server Render atau meminta autentikasi. Deno dipasang karena yt-dlp memerlukan JavaScript runtime dan EJS untuk menyelesaikan tantangan JavaScript YouTube; Deno membantu, tetapi tidak mengatasi semua pembatasan server-side.
- Snapchat Spotlight adalah best-effort; extractor yt-dlp pernah dilaporkan gagal akibat perubahan Next.js data pada beberapa tautan.
- Telegram hanya mendukung postingan channel/grup publik, misalnya `t.me/nama_channel/123`. Tautan privat, invite, chat pribadi, konten protected, dan `t.me/c/...` tidak didukung.
- Threads: galeri foto/carousel memakai resolver halaman publik tanpa login. Resolver mengambil JSON yang dirender pada halaman post dan memilih post berdasarkan shortcode. Ini rapuh terhadap perubahan HTML/JSON Meta; jika payload tidak lagi tampil untuk pengunjung publik, ekstraksi akan gagal dengan aman. Foto dan video tetap dipisahkan.
- Instagram: galeri foto mencoba gallery-dl, lalu resolver logged-out GraphQL terbaru dan JSON halaman publik menggunakan curl-cffi. Tidak memakai akun, username/password, atau cookie sesi pengguna. Resolver ini tetap best-effort: Meta dapat mengganti query internal, menampilkan login wall, memberi rate limit, atau membatasi IP server.
- Facebook: galeri foto mencoba gallery-dl, lalu resolver HTML publik yang membaca metadata Open Graph dan URL CDN gambar yang tertanam di halaman. Ini paling sesuai untuk foto yang ditampilkan langsung oleh halaman publik; album/carousel tidak dijamin terambil lengkap bila Facebook tidak menyertakan semua URL di HTML.
- X/Twitter, Pinterest, dan Reddit: galeri tetap menggunakan gallery-dl. Extractor upstream dapat meminta login, cookie, atau OAuth pada sebagian konten/versi platform. Petik tidak mengirim cookie akun pengguna; jika sumber menolak akses tanpa login, unduhan memang tidak akan berhasil.
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

Folder `tests/` berisi 56 tes offline untuk validasi platform, endpoint inspect dan URL preview progresif, deteksi direct-media URL, parser CDN dengan ekstensi query, parser galeri Threads/Instagram, keamanan host CDN dan redirect, indeks carousel campuran, endpoint download, kompatibilitas Cobalt, tunnel file, format MP3, filter durasi, dan kualitas video. Tes tiruan tidak membuktikan keberhasilan unduhan langsung dari situs sumber.


## Perubahan v1.6.2 (perbaikan Instagram dan Facebook publik)

- Memutakhirkan resolver Instagram anonim agar mengikuti query logged-out `PolarisLoggedOutDesktopWWWPostRootContentQuery`: shortcode dikonversi ke media ID numerik, token LSD/CSRF dibaca dari sesi anonim, dan respons `xig_polaris_media.if_not_gated_logged_out` dinormalisasi.
- Memperbaiki konfigurasi TLS impersonation `curl-cffi`: fingerprint browser diberikan saat membuat `Session(impersonate="chrome")`, bukan dengan mengisi atribut sesi sesudahnya.
- Menambahkan fallback Instagram dari JSON halaman publik ketika respons GraphQL tidak menyediakan objek media.
- Memperluas parser Instagram untuk `carousel_media`, `image_versions2`, `display_resources`, serta media video di sela-sela foto; poster video tidak dianggap foto.
- Menambahkan fallback Facebook publik berbasis metadata Open Graph dan URL CDN gambar yang tertanam di HTML halaman, tanpa login atau cookie akun.
- Menambahkan unduhan langsung dari CDN Instagram/Facebook yang sudah lolos allowlist HTTPS, validasi redirect, MIME/magic bytes, dan batas ukuran. `gallery-dl` tetap menjadi jalur awal dan fallback unduhan.
- Menambahkan status resolver di `/health` dan pesan lebih jelas ketika Instagram hanya mengembalikan error karena tidak ada stream video dan tidak ada data foto publik yang berhasil dibaca.
- Mempertahankan resolver Threads yang sudah ada serta fitur video/audio dan galeri frontend.
- Menambahkan tes offline untuk query GraphQL terbaru, fallback Facebook, unduhan CDN, batas host, dan jalur API; tes fixture bukan bukti live extraction dari Meta.

## Perubahan yang diwarisi dari v1.6.1

- Parser CDN membaca ekstensi gambar dari path maupun parameter query (`format`, `fm`, `ext`, `mime_type`, dan nama file), sambil menolak URL HTTP atau URL yang tidak bisa dipastikan sebagai gambar.
- Batas indeks galeri mengakomodasi carousel campuran foto/video.
- Threads menggunakan resolver halaman publik untuk URL `/share/`, termasuk pemilihan foto dan download dengan pembatasan host CDN.
- Instagram sebelumnya telah memiliki fallback GraphQL anonim; pada v1.6.2, query tersebut diperbarui agar sesuai pola logged-out extractor saat ini.
- Dailymotion dan LinkedIn dikenali saat validasi tautan dan diserahkan ke yt-dlp; ini bukan jaminan semua jenis post berhasil.

## Perubahan yang diwarisi dari v1.5

- yt-dlp tidak lagi dikunci ke satu versi, dan diperbarui otomatis tiap container hidup (matikan dengan `YTDLP_AUTO_UPDATE=0`). `/health` menampilkan `ytdlpVersion`.
- Unduhan bersamaan default 2 dan batas 40 permintaan per 5 menit. Kalau Render Free kehabisan memori (out of memory di log), kembalikan `MAX_CONCURRENT_DOWNLOADS` ke 1.
- Pratinjau punya slot dan batas sendiri.
