# Petik Universal Extractor API v1.10.0

Backend Petik berbasis FastAPI, yt-dlp, gallery-dl, curl-cffi untuk resolver publik Meta, Deno/EJS, BgUtils PO Token Provider, HTTPX, dan FFmpeg. Rilis ini memperbaiki alur YouTube dengan percobaan client bertahap, diagnosa aman untuk alur PO Token, serta fallback saat client utama ditolak, tanpa layanan/API berbayar. Fitur galeri campuran foto + video dari v1.8.0 tetap dipertahankan.

## Fitur dan kontrak API

- `POST /api/inspect`: membaca metadata video/audio dan galeri media campuran. Respons galeri baru memakai `mediaItems[]` dengan `id`, `type` (`photo` atau `video`), `url`, `directUrl`, `thumbnail`, `extension`, `filename`, `sourceIndex`, dan durasi video jika tersedia. Respons juga menyertakan `mediaCounts.total`, `mediaCounts.photos`, `mediaCounts.videos` serta `galleryLimit`.
- `photoItems[]` tetap disediakan sebagai alias kompatibilitas foto-only untuk frontend lama. ID di `mediaItems` adalah ID yang harus dipakai pada endpoint unduhan campuran.
- `POST /api/gallery/download`: menerima `url`, array `indices` berisi ID dari `mediaItems`, dan `zip`. `zip: true` mengembalikan ZIP campuran `petik-media.zip`; `zip: false` mengembalikan daftar file untuk dikirim satu per satu. Respons berisi `count`, `failedCount`, dan `failures`; pada unduhan langsung Meta, satu item gagal tidak membatalkan item lain yang berhasil.
- `POST /api/download`: alur unduh video/audio tunggal tetap memakai pemilih kualitas lama.
- `GET /health`: menampilkan versi API, ketersediaan gallery-dl, resolver media campuran, status provider PO Token YouTube, kecocokan versi plugin/server, dan batas jumlah/ukuran media.
- `GET /` dan `POST /` tetap menyediakan kompatibilitas Cobalt yang sudah ada.

Contoh ringkas respons galeri:

```json
{
  "status": "ready",
  "mediaItems": [
    {"id": 1, "type": "photo", "url": "https://cdn.example/1.jpg", "sourceIndex": 1},
    {"id": 2, "type": "video", "url": "https://cdn.example/2.mp4", "sourceIndex": 2}
  ],
  "mediaCounts": {"total": 2, "photos": 1, "videos": 1},
  "galleryLimit": 20
}
```

Contoh itu hanya menjelaskan bentuk respons; bukan URL media yang benar-benar diekstrak.

## Deploy lewat Render

1. Buka repository GitHub `petik-universal-api`.
2. Ekstrak ZIP backend v1.10.0, lalu unggah semua isinya langsung ke root repository (jangan unggah file ZIP sebagai satu file).
3. Pastikan `Dockerfile`, `app.py`, `start.sh`, `requirements.txt`, `render.yaml`, `README.md`, dan folder `tests/` berada di root.
4. Commit perubahan dari GitHub.
5. Di Render, buka Blueprint Petik lalu jalankan **Manual Sync / Apply**.
6. Setelah layanan berstatus Live, buka endpoint `/health`. Versi yang diharapkan `1.10.0`, `mixedMediaAvailable: true`, dan `galleryDlAvailable: true` bila gallery-dl berhasil terpasang. Untuk YouTube, pastikan `youtubePoTokenProviderReady: true`, `youtubePoTokenPluginVersion: "2.0.2"`, dan `youtubePoTokenServerVersion: "2.0.2"`.
7. Jika `youtubePoTokenProviderReady` bernilai `false`, lihat Render Logs. Backend tetap dapat melayani platform lain, tetapi Petik tidak akan memaksa klien YouTube `mweb` tanpa provider yang cocok.
8. Buka `/` untuk memeriksa daftar platform dan layanan.


## YouTube PO Token dan strategi fallback (v1.10.0)

Rilis ini memakai **BgUtils PO Token Provider 2.0.2** dengan dua komponen yang versinya dipasangkan: plugin Python `bgutil-ytdlp-pot-provider==2.0.2` dan server HTTP BgUtils 2.0.2. Docker membangun server dari tag upstream `2.0.2`, memasang dependensi Deno, lalu `start.sh` menjalankan server pada `127.0.0.1:4416`. Server token tidak dibuka sebagai port publik dan tidak menambah layanan Render terpisah.

Untuk permintaan YouTube, Petik menjalankan client secara berurutan, bukan mengaktifkan banyak client sekaligus: `mweb` (jika BgUtils sehat dan versi plugin/server cocok), `web_safari`, `web_embedded`, lalu `tv`. Setiap client dicoba dalam sesi yt-dlp terpisah; percobaan berikutnya hanya dijalankan ketika error menunjukkan bot-check, PO Token, HTTP 403, atau format tidak tersedia. Hasil format DRM dan SABR-only tidak dipilih sebagai media unduhan. `web_embedded` hanya dapat bekerja pada video yang mengizinkan embedding; `tv` merupakan fallback terakhir karena formatnya dapat terbatas. Provider BgUtils dipasang pada `mweb` dan `web_safari` jika token diminta.

Untuk membuktikan apakah provider benar-benar dipanggil, logger yt-dlp v1.10 mengamati baris registry `PO Token Providers` dan `Generating a ... PO Token`. `/health` menyediakan `youtubeDiagnostics` berisi client yang dicoba, hasil aman tiap percobaan, `providerAdvertised`, dan `tokenGenerationObserved`. Diagnostik tidak menyimpan URL sumber, cookies, visitor data, signed media URL, atau nilai PO Token. `youtubePoTokenProviderReady` tetap hanya memverifikasi ping dan kecocokan versi; indikator token generation merupakan bukti runtime yang lebih kuat, tetapi bukan jaminan ekstraksi atau unduhan akan berhasil.

Deno juga dipakai untuk EJS challenge solver yt-dlp. Paket `yt-dlp[default]` menyediakan dependensi EJS, sedangkan runtime Deno harus memenuhi versi minimum pada panduan EJS yt-dlp yang digunakan saat rilis ini.

**Batas penting:** PO Token bukan jaminan bypass. YouTube tetap dapat membatasi IP pusat data/Render, meminta verifikasi, mengubah challenge, atau mengembalikan format yang tidak dapat diunduh. BgUtils sendiri memperingatkan bahwa penyediaan token tidak menjamin lolos dari 403 atau bot checks. Konten privat, berbayar, atau yang memerlukan akun tidak otomatis menjadi dapat diakses. Petik tidak meminta password, cookies, atau token akun YouTube.

Provider alternatif `yt-dlp-getpot-wpc` juga dievaluasi. Dokumentasi upstream menyebutnya sebagai alternatif/fallback yang masih eksperimental dan memerlukan Chrome/Chromium untuk mencetak token melalui browser. Provider ini tidak dimasukkan ke image v1.10 agar tidak menambah browser headless dan beban runtime; BgUtils tetap menjadi provider utama. BgUtils dan yt-dlp adalah proyek open-source; tidak ada API berbayar atau biaya provider tambahan pada implementasi ini. Namun penggunaan Render Free tetap tunduk pada kuota, bandwidth, waktu build, penghentian saat idle, dan batas resource Render. Render menyebut instance Free memiliki RAM 512 MB pada halaman pricing dan dapat spin down setelah 15 menit tidak menerima trafik.

## Platform dan batas dukungan

API mengenali YouTube, Instagram, Facebook, TikTok, X/Twitter, Reddit, Vimeo, Pinterest, SoundCloud, Telegram publik, Threads publik, Snapchat Spotlight, Dailymotion, dan LinkedIn. Pengenalan tautan bukan jaminan extractor dapat membaca semua postingan. Hasil bergantung pada dukungan extractor upstream, tipe posting, akses publik tanpa login, dan pembatasan IP dari platform.

Resolver galeri campuran yang diterapkan:

- **Instagram:** mencoba resolver logged-out GraphQL / JSON halaman publik untuk menemukan urutan foto dan video, lalu gallery-dl sebagai fallback. Poster video tidak dianggap foto. Tidak menggunakan cookie sesi akun.
- **Threads:** membaca JSON post publik dan menyeleksi posting sesuai shortcode/canonical URL; tiap lampiran foto atau video ditampilkan mengikuti urutan JSON sumber. Pengunduhan langsung hanya ke host CDN Meta yang diizinkan.
- **Facebook:** menggunakan satu manifest gallery-dl untuk urutan lampiran bila tersedia. Jika manifest tidak ada, fallback gambar publik tetap dibatasi pada metadata Open Graph dan subtree lampiran yang terkait ID post/foto target. Petik tidak memindai semua URL CDN dalam HTML karena itu pernah memasukkan rekomendasi, avatar, ikon, dan media posting lain. Deteksi video album Facebook tetap best-effort bergantung pada hasil extractor.
- **X/Twitter, Pinterest, dan Reddit:** menggunakan manifest URL gallery-dl untuk menggabungkan URL gambar dan file video langsung yang dikenali. Stream HLS/DASH terpisah (`.m3u8`/`.mpd`) tidak dipasarkan sebagai satu file direct; extractor harus menyediakan file video yang dapat diunduh.
- **Platform lain:** tetap memakai alur video/audio tunggal yt-dlp. Petik tidak menampilkan galeri multi-item yang belum memiliki jalur unduhan dan pemetaan ID yang aman.

Batasan yang perlu diperhatikan:

- Konten privat, dihapus, paywalled, age-gated, atau yang membutuhkan login tidak didukung. Petik tidak meminta password, session cookie, token akun, OAuth pengguna, atau login media sosial.
- Instagram Story dan Facebook Story bukan bagian fitur galeri post ini dan tidak dijanjikan tersedia.
- Meta dapat mengganti struktur GraphQL, JSON halaman, CDN, atau membatasi IP Render. Jika data media tidak tersedia secara publik, resolver akan gagal dengan pesan yang jelas.
- Beberapa URL CDN bersifat sementara/kedaluwarsa. Link Direct hanya ditampilkan saat extractor menyediakan URL berkas yang valid; link tersebut dapat berhenti berlaku mengikuti masa berlaku dari sumber.
- File video yang berupa stream terpisah atau URL tanpa file progresif yang jelas tidak ditampilkan sebagai link direct palsu.

## Batas ukuran dan performa

Default Render di `render.yaml`:

| Batas | Default |
|---|---:|
| Item galeri per proses | 20 foto/video gabungan |
| Sumber attachment yang dipindai | Maksimal 40 nomor sumber |
| Satu foto | 20 MiB |
| Satu video galeri | 140 MiB |
| Total satu unduhan galeri | 300 MiB |
| Video/audio tunggal | 140 MiB |
| Durasi video/audio tunggal | 60 menit |
| Unduhan bersamaan | 2 proses |
| Batas permintaan | 40 per IP dalam 5 menit |

Pengunduh CDN menulis potongan data langsung ke file sementara agar tidak menampung seluruh video besar di memori. File sementara memakai filesystem Render yang bersifat ephemeral dan kedaluwarsa otomatis. ZIP memakai penyimpanan tanpa kompresi ulang agar video tidak diproses ulang.

## Variabel lingkungan

`PUBLIC_BASE_URL`, `ALLOWED_ORIGINS`, `MAX_FILE_MB`, `MAX_DURATION_SECONDS`, `FILE_TTL_SECONDS`, `MAX_CONCURRENT_DOWNLOADS`, `RATE_LIMIT_COUNT`, `RATE_LIMIT_WINDOW_SECONDS`, `TEMP_DIR`, `BGUTIL_POT_PROVIDER_ENABLED`, `BGUTIL_POT_PROVIDER_URL`, `BGUTIL_PROVIDER_PORT`, `BGUTIL_PROVIDER_START_TIMEOUT`, `MAX_GALLERY_ITEMS`, `MAX_GALLERY_FILE_MB`, `MAX_GALLERY_VIDEO_MB`, dan `MAX_GALLERY_TOTAL_MB`.

## Tes dan batas verifikasi

Folder `tests/` berisi 91 tes offline untuk parser Instagram/Threads campuran, deteksi URL gallery-dl, urutan `sourceIndex`, pemetaan ID unduhan, ZIP yang berisi foto dan video, unduhan CDN streaming, partial failure, validasi host/redirect, endpoint inspect/download, health, kompatibilitas Cobalt, preview, MP3, durasi, dan kualitas. Tes ini memvalidasi kontrak dan perilaku lokal; tes offline bukan bukti bahwa setiap tautan live dari tiap platform akan berhasil diekstrak.

## Urutan API di frontend Petik

1. API khusus yang diisi pengguna (jika ada).
2. API Petik Render: `https://petik-universal-api-masx.onrender.com`.
3. API Cobalt eksternal sebagai cadangan video/audio yang sudah ada.

## Riwayat rilis

### v1.10.0 — YouTube Client Fallback dan Diagnostik PO Token

- Mengganti konfigurasi satu client `mweb` menjadi percobaan berurutan `mweb` → `web_safari` → `web_embedded` → `tv` (mweb hanya bila BgUtils siap).
- Menggunakan sesi yt-dlp terpisah per client agar kegagalan satu client tidak membatalkan percobaan fallback.
- Memprioritaskan client yang baru berhasil untuk URL yang sama selama 15 menit agar proses inspect lalu download konsisten.
- Memeriksa ketersediaan format yang dapat diunduh dan menolak format yang ditandai DRM atau SABR-only.
- Menambahkan logger yt-dlp yang hanya mencatat registry provider dan peristiwa pembuatan PO Token tanpa mencetak URL, cookie, atau token.
- Menambahkan `youtubeDiagnostics` dan `youtubeClientFallbacks` ke `/health`.
- Memperjelas pesan kegagalan bot-check/IP, PO Token, dan format yang tidak tersedia.
- Mengevaluasi `yt-dlp-getpot-wpc`; tidak dipasang karena membutuhkan Chrome/Chromium dan menambah runtime browser pada deployment Free.
- Mempertahankan BgUtils 2.0.2, Deno/EJS, resolver sosial, galeri mixed media, preview, Direct Link, dan MP3.

### v1.9.0 — YouTube PO Token Provider

- Menambahkan BgUtils PO Token Provider HTTP 2.0.2 dan plugin yt-dlp 2.0.2 dengan versi yang dipasangkan.
- Menjalankan provider di container yang sama, terikat ke `127.0.0.1:4416`, bukan endpoint publik.
- Menambahkan `start.sh` untuk startup provider, pengecekan `/ping`, dan fallback agar API sosial tetap hidup bila provider tidak siap.
- Mengaktifkan klien YouTube `mweb` hanya ketika server dan plugin sehat serta versinya cocok.
- Menambahkan status provider dan versi plugin/server ke `/health`.
- Menambahkan pesan error khusus untuk kegagalan PO Token dan pembatasan IP YouTube.
- Mempertahankan resolver galeri campuran, Facebook post-scoped resolver, Instagram/Threads publik, Direct Link, preview, dan MP3 dari v1.8.0.

### v1.8.0 — Mixed Media Gallery

- Menyatukan parser galeri sebagai `mediaItems[]` untuk foto dan video.
- Mempertahankan `photoItems[]` sebagai alias kompatibilitas.
- Menggabungkan manifest gallery-dl satu kali sehingga posisi foto/video tidak dihitung dari dua daftar terpisah.
- Memetakan ID antarmuka ke nomor sumber gallery-dl sebelum unduhan agar item pilihan tidak bergeser.
- Mendukung ZIP foto + video, Direct Link per item, dan unduhan Meta secara streaming ke disk.
- Jika satu item unduhan langsung gagal, item lain tetap diproses dan respons melaporkan `failedCount` / `failures`.
- Menambahkan batas ukuran khusus video dan batas total galeri yang terukur.
- Mempertahankan validasi host CDN, redirect HTTPS, validasi MIME/magic bytes, dan pembatasan akses publik.

### Fitur terdahulu

- v1.7.0: `directUrls.video` dan `directUrls.audio` terpisah dari proses unduhan Petik.
- v1.6.3: perbaikan Facebook post-scoped agar gambar rekomendasi/halaman lain tidak ikut masuk.
- v1.6.2: resolver Instagram logged-out GraphQL/page JSON dan pengetatan unduhan CDN.
- v1.6.1: parser ekstensi dari query URL, galeri Threads publik, dan pemetaan nomor carousel.
