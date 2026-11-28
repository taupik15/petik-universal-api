# Petik Universal Extractor API v1.14.0

Backend Petik berbasis FastAPI, yt-dlp, gallery-dl, curl-cffi untuk resolver publik Meta, Deno/EJS, BgUtils PO Token Provider, HTTPX, dan FFmpeg. Rilis v1.14.0 mempertahankan BgUtils sebagai konfigurasi standar, menambahkan profil WPC/Chromium eksperimental yang terpisah, memperbaiki korelasi diagnosa YouTube dan menjaga penyebab error utama agar tidak tertimpa error pembungkus. Cookies YouTube tetap opsional dan tidak ada API berbayar yang ditambahkan. Galeri campuran foto + video dan resolver sosial dipertahankan.

**Penting:** profil standar memakai `Dockerfile` dan tetap cocok untuk konfigurasi Render Free yang sudah ada. `Dockerfile.wpc` hanya untuk percobaan di lingkungan dengan resource memadai; WPC belum dibuktikan dapat mengatasi bot-check Player API atau stabil di Render Free.

## YouTube recovery dan request safety (v1.14.0)

Rilis ini memprioritaskan pengurangan permintaan berulang dan pengujian terkontrol. HTTP 429 diperlakukan sebagai sinyal untuk berhenti, bukan alasan menambah client fallback.

- **Client eksplisit dan berurutan:** `tv_samsung`, `visionos`, `web`, `mweb` (hanya bila BgUtils/WPC siap), lalu `web_embedded`. Setiap client dijalankan dalam konfigurasi yt-dlp tersendiri agar Petik dapat mencatat kegagalan dan berhenti ketika menemukan 429. `web_safari` tidak lagi menjadi jalur default karena sejak Juli 2026 HLS-nya hanya tersedia pada sesi tertentu yang tepercaya. `android_vr` sengaja tidak dipakai karena source yt-dlp saat ini mencatat semua format client tersebut mengalami HTTP 403 sejak 17 Agustus 2026. Referensi client dan argumen extractor: https://github.com/yt-dlp/yt-dlp#extractor-arguments dan https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/youtube/_base.py.
- **Penghentian fallback saat 429:** jika logger yt-dlp atau exception mengidentifikasi HTTP 429, Petik menyimpan tahap/client yang kena batas, mengaktifkan cooldown, lalu menghentikan client berikutnya pada permintaan tersebut.
- **Cooldown dan antrean:** satu proses hanya menjalankan satu pekerjaan YouTube pada satu waktu. Secara default, jarak antarpemeriksaan baru 5 detik, jarak percobaan client fallback 2 detik, dan cooldown setelah 429 adalah 300 detik. Permintaan baru selama cooldown ditolak lokal tanpa menambah request ke YouTube. Nilai hanya disimpan dalam memori proses; restart layanan akan mengatur ulang cooldown. Bila ada beberapa worker/instance, state rate-control tidak dibagi lintas instance.
- **Jeda dan cache:** `sleep_interval_requests=1` membantu mengatur jeda di dalam yt-dlp. Hasil inspect sukses disimpan dalam cache memori maksimum 64 entri selama 45 detik. Cache tidak disimpan ke disk atau dikirim melalui health. Satu hasil inspect yang baru dapat dipakai untuk mengizinkan satu aksi Simpan segera agar pengguna tidak terkena jeda lima detik di antara Ambil dan Simpan.
- **Diagnostik jaringan:** `GET /api/diagnostics/youtube-network` melakukan dua GET ke host tetap (`www.youtube.com/generate_204` dan `youtubei.googleapis.com/`) dan mengembalikan status HTTP + durasi saja. Endpoint dibatasi sekali per 120 detik dan berhenti pada target pertama yang memberi 429. Respons 2xx/3xx/4xx selain 429 hanya membuktikan host merespons; tidak membuktikan Player API atau download video berhasil. Endpoint ini tidak mengungkap alamat IP egress.
- **Endpoint/visitor data eksperimen:** `YOUTUBE_INNERTUBE_HOST` menerima hanya host yang di-allowlist (`youtubei.googleapis.com`, `www.youtube.com`, `music.youtube.com`, `studio.youtube.com`). `YOUTUBE_INNERTUBE_KEY` dibatasi format/ukuran. `YOUTUBE_PLAYER_SKIP` menerima hanya `configs`, `webpage`, `js`, `initial_data`. `YOUTUBE_VISITOR_DATA` hanya dipakai saat `player_skip` memuat `webpage,configs` dan tidak ada cookies. Pengaturan ini kosong secara default; `player_skip` dan `visitor_data` dapat mengurangi request tetapi juga dapat menghilangkan metadata/format. Gunakan hanya nilai yang diperoleh secara sah dari sesi/konfigurasi sendiri. Nilainya tidak ditampilkan pada `/health` dan tidak ditulis ke log.
- **Client tambahan:** `YOUTUBE_EXTRA_CLIENTS` hanya menerima nama dalam allowlist. `android_vr` tidak di-allowlist.
- **curl_cffi impersonation:** `YOUTUBE_HTTP_IMPERSONATE` kosong secara default. Bila diaktifkan, nilai seperti `chrome` atau `safari` diteruskan ke yt-dlp jika dukungan tersedia. Ini eksperimen terakhir saja: catatan maintainer yt-dlp mengingatkan impersonation bukan pilihan yang disarankan untuk YouTube dan dapat memperburuk hasil. Referensi: https://github.com/yt-dlp/yt-dlp/issues/17219 dan https://github.com/yt-dlp/yt-dlp#impersonation.
- **Region Render:** endpoint diagnostik membantu membandingkan akses dari suatu deployment, tetapi tidak dapat memindahkan atau mengubah IP egress. Uji region perlu dibuat sebagai deployment uji terpisah dari Render Dashboard, memakai salinan konfigurasi, lalu dibandingkan dengan video/link yang sama. Jangan pindahkan layanan utama sebelum ada hasil. Dedicated outbound IP tidak menjadi bagian paket ini.
- **Website:** patch JavaScript v1.14 menghentikan percobaan extractor website selanjutnya bila API Petik mengembalikan HTTP 429 spesifik YouTube, menjaga tombol Simpan tetap nonaktif bila tidak ada format, dan mengganti pesan generic menjadi 429/bot-check yang lebih informatif. Jika API hanya bot-check (bukan 429), fallback eksternal tetap boleh dicoba.

### Environment variables v1.14.0

| Variable | Default | Fungsi |
|---|---:|---|
| `YOUTUBE_MIN_ATTEMPT_INTERVAL_SECONDS` | `5` | Jarak minimum antar job YouTube baru |
| `YOUTUBE_CLIENT_FALLBACK_INTERVAL_SECONDS` | `2` | Jeda antar client setelah error yang boleh dicoba ulang |
| `YOUTUBE_RATE_LIMIT_COOLDOWN_SECONDS` | `300` | Cooldown lokal setelah HTTP 429 (batas 30–3600 detik) |
| `YOUTUBE_SLEEP_INTERVAL_REQUESTS` | `1` | Jeda internal request yt-dlp (0–10 detik) |
| `YOUTUBE_INFO_CACHE_TTL_SECONDS` | `45` | Cache inspect sukses dalam memori (0–180 detik) |
| `YOUTUBE_EXTRA_CLIENTS` | kosong | Client tambahan dalam allowlist |
| `YOUTUBE_INNERTUBE_HOST` | kosong | Override host Innertube yang di-allowlist |
| `YOUTUBE_INNERTUBE_KEY` | kosong | API key Innertube opsional, tidak pernah ditampilkan |
| `YOUTUBE_PLAYER_SKIP` | kosong | Eksperimen pengurangan request extractor |
| `YOUTUBE_VISITOR_DATA` | kosong | Visitor data opsional; syarat `player_skip` + tanpa cookies |
| `YOUTUBE_HTTP_IMPERSONATE` | kosong | Impersonation eksperimental; tidak direkomendasikan sebagai langkah awal |

**Verifikasi v1.14.0:** 142 tes offline lulus. Termasuk uji penghentian fallback ketika 429, cooldown, cache, allowlist, health, endpoint probe dengan respons simulasi, dan kontrak platform lama. Tes tersebut tidak bisa membuktikan akses YouTube live dari egress Render. Docker build, deploy Render, tes region, dan download MP4/MP3 nyata tetap membutuhkan pengujian setelah pengguna melakukan deploy.

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
2. Ekstrak ZIP backend v1.14.0, lalu unggah semua isinya langsung ke root repository (jangan unggah file ZIP sebagai satu file).
3. Pastikan `Dockerfile`, `Dockerfile.wpc`, `app.py`, `pot_telemetry.py`, `start.sh`, `requirements.txt`, `requirements-wpc.txt`, `render.yaml`, `README.md`, dan folder `tests/` berada di root.
4. Commit perubahan dari GitHub.
5. Di Render, buka Blueprint Petik lalu jalankan **Manual Sync / Apply**.
6. Setelah layanan berstatus Live, buka endpoint `/health`. Versi yang diharapkan `1.14.0`, `mixedMediaAvailable: true`, dan `galleryDlAvailable: true` bila gallery-dl berhasil terpasang. Untuk YouTube, pastikan `youtubePoTokenProviderReady: true`, `youtubePoTokenPluginVersion: "2.0.2"`, dan `youtubePoTokenServerVersion: "2.0.2"`.
7. Jika `youtubePoTokenProviderReady` bernilai `false`, lihat Render Logs. Backend tetap dapat melayani platform lain, tetapi Petik tidak akan memaksa klien YouTube `mweb` tanpa provider yang cocok.
8. Buka `/` untuk memeriksa daftar platform dan layanan.


## YouTube diagnosis dan recovery (v1.12.0)

Rilis ini dibuat untuk menindaklanjuti hasil v1.11 ketika provider BgUtils terdeteksi tetapi tidak ada permintaan `/get_pot`. Fokusnya adalah membuktikan tahap kegagalan, bukan menganggap IP Render pasti diblokir.

- **Diagnostik tahap ekstraksi:** logger yt-dlp mengubah baris debug menjadi label tahap aman seperti `youtube_watch_page`, `innertube_client_config`, `innertube_player_api`, `po_token_generation`, dan `youtube_bot_check`. Teks mentah, URL, query, signed URL, cookie, visitor data, dan token tidak disimpan.
- **Telemetry per percobaan:** `pot_telemetry.py` menyimpan event metadata berbatas (ID urut, waktu, status HTTP, ada/tidaknya token, dan durasi). Event tidak menyimpan body request atau respons. Endpoint `/stats` internal menampilkan event terbaru; API menghitung event setelah baseline percobaan terakhir.
- **Isolasi percobaan:** ekstraksi/unduhan YouTube diserialkan per proses API untuk mencegah permintaan bersamaan tercampur pada diagnostik provider. Platform lain tidak menggunakan lock ini. Trade-off-nya, permintaan YouTube kedua dapat menunggu sampai percobaan YouTube pertama selesai.
- **Evaluasi fallback non-PO:** opsi `android_vr` diperiksa tetapi tidak dimasukkan ke daftar aktif. Source extractor yt-dlp saat ini memberi catatan bahwa semua format client tersebut mengalami HTTP 403 sejak 17 Agustus 2026. `web_embedded` tetap hanya berlaku untuk video yang mengizinkan embedding, sedangkan `tv` masih berisiko mengembalikan SABR/DRM. HLS `web_safari` juga tidak dijamin tersedia pada sesi anonim. Karena itu tidak ada client tambahan yang dipaksakan hanya untuk memperpanjang daftar fallback.
- **IPv4:** pengaturan `YOUTUBE_FORCE_IPV4=1` tetap dipertahankan; ini memilih keluarga alamat sumber IPv4 dan tidak mengubah reputasi IP server.
- **Cookies opsional:** jika pemilik server secara sadar mengatur `YOUTUBE_COOKIES_B64`, API memvalidasi file Netscape, menulisnya hanya di filesystem sementara `/tmp` dengan mode `0600`, lalu memberi path file ke yt-dlp. Variabel ini tidak pernah ditampilkan; `/health` hanya menunjukkan boolean `youtubeCookiesConfigured`. Jangan commit file cookies ke GitHub atau membagikannya di chat. Hapus environment variable bila tidak dibutuhkan. Default tetap tanpa login.
- **WPC:** `requirements-wpc.txt` disediakan sebagai dependensi opsional versi 1.1.2. Plugin WPC memerlukan Chrome/Chromium. Paket ini tidak masuk ke `requirements.txt` dan tidak diinstal pada image standar. Render Free menyediakan 512 MB RAM dan 0,1 CPU; browser headless berisiko menghabiskan sumber daya. WPC hanya layak dievaluasi pada lingkungan yang menyediakan memori/CPU lebih besar serta binary Chrome/Chromium.
- **Pesan error:** hasil bot-check tidak lagi menyatakan IP Render diblokir sebagai fakta. Pesan menjelaskan bahwa penyebab dapat terkait tahap ekstraksi, sesi, atau reputasi IP dan perlu dibuktikan melalui diagnostik.

`/health` kini menambahkan `lastStage`, `lastErrorStage`, `stageEvents`, dan `providerAttemptEvents` pada `youtubeDiagnostics`, serta `youtubeCookiesConfigured` dan `youtubeDownloadFallbacks`. Nilai event bersifat metadata. Counter provider lifetime tetap disediakan untuk perbandingan.

**Batas verifikasi:** 129 tes offline memeriksa kontrak, klasifikasi tahap, privasi telemetry, event baseline, validasi cookies, fallback, dan regresi. Tes ini tidak membuktikan YouTube live akan menerima permintaan dari IP Render. Docker build, deploy Render, dan unduhan YouTube nyata perlu diuji setelah pengguna melakukan deploy.

## YouTube PO Token, telemetry, dan strategi recovery (v1.11.0)

Rilis ini tetap memakai **BgUtils PO Token Provider 2.0.2** dengan dua komponen yang versinya dipasangkan: plugin Python `bgutil-ytdlp-pot-provider==2.0.2` dan server HTTP BgUtils 2.0.2. Docker membangun server dari tag upstream `2.0.2`, memasang dependensi Deno, lalu `start.sh` menjalankan server pada `127.0.0.1:4416`. Server token tidak dibuka sebagai port publik dan tidak menambah layanan Render terpisah.

**Telemetry v1.11:** `pot_telemetry.py` menambahkan proxy penerus lokal pada `127.0.0.1:4417`. Plugin yt-dlp diarahkan ke proxy ini bila tersedia; proxy meneruskan permintaan ke BgUtils pada `127.0.0.1:4416`. Untuk endpoint `POST /get_pot`, proxy hanya menghitung total permintaan, status HTTP, serta apakah respons JSON memiliki nilai `poToken` non-kosong. Body request, token, visitor data, cookie, URL video, dan signed media URL tidak disimpan maupun ditulis ke log. Jika proxy tidak bisa mulai, API tetap memakai alamat BgUtils langsung.

Strategi YouTube dijalankan berurutan dalam sesi yt-dlp terpisah: `mweb` (jika BgUtils sehat dan versi plugin/server cocok), `web_safari`, `web_safari_hls` sebagai strategi HLS-only yang terpisah, `web_embedded`, lalu `tv`. `web_safari_hls` memakai player client `web_safari`, tetapi unduhan dibatasi pada format protokol HLS `m3u8` yang memiliki video dan audio sekaligus; kualitas maksimum atau batas tinggi yang dipilih tetap dihormati. Strategi ini tidak diam-diam memilih format DASH/MP4 lain dan tidak mengunduh video-only tanpa audio. `web_embedded` hanya dapat bekerja pada video yang mengizinkan embedding; `tv` merupakan fallback terakhir. Format DRM dan SABR-only tetap ditolak. Untuk audio, strategi HLS-only tidak diulang.

Dokumentasi yt-dlp yang diperiksa pada November 2026 mencatat bahwa `web_safari` dapat menawarkan HLS tanpa PO Token untuk GVS, tetapi sejak perubahan Juli 2026 format HLS hanya tersedia pada sesi tertentu yang dianggap tepercaya. Karena itu, percobaan HLS di Petik adalah jalur diagnosis/pemulihan, bukan jaminan akan tersedia untuk server anonim Render. Petik juga mengaktifkan `source_address=0.0.0.0` secara default untuk memaksa jalur IPv4 pada permintaan yt-dlp; ini membantu menghindari variasi rute IPv6, tetapi tidak mengubah reputasi IP Render.

`/health` menyediakan `youtubeDiagnostics` dengan daftar provider yang terdeteksi, strategi yang dicoba, hasil per strategi, status telemetry, jumlah permintaan provider, jumlah respons HTTP sukses/gagal, jumlah respons berisi `poToken`, timestamp terakhir, dan petunjuk akar masalah. Counter lifetime provider dan delta pada percobaan terakhir ditampilkan terpisah. `tokenGenerationObserved` hanya berarti permintaan provider teramati; `providerTokenResponsesThisAttempt` membuktikan respons `/get_pot` berisi field `poToken` non-kosong. Keduanya tidak membuktikan YouTube menerima token atau memberikan format unduhan.

Deno juga dipakai untuk EJS challenge solver yt-dlp. Paket `yt-dlp[default]` menyediakan dependensi EJS, sedangkan runtime Deno memenuhi kebutuhan JavaScript challenge solver yang dipakai image ini.

**WPC dievaluasi tetapi tidak dipasang pada image standar.** Plugin `yt-dlp-getpot-wpc` membutuhkan Chrome/Chromium untuk membuat token melalui browser headless. Menambahkan Chromium akan menambah ukuran image serta penggunaan memori pada Render Free; WPC ditampilkan sebagai status diagnosa, tetapi `runtimeEnabled` tetap `false`. Tidak ada API berbayar atau layanan eksternal berbayar yang ditambahkan.

**Batas penting:** PO Token bukan jaminan bypass. BgUtils menyatakan token tidak menjamin lolos dari HTTP 403 atau bot-check. Jika bot-check terjadi setelah proxy mengamati respons token, diagnosa akan memberi petunjuk kemungkinan pembatasan IP/session; itu bukan bukti definitif bahwa IP Render diblokir. Petik tidak menggunakan password, cookie, atau login akun YouTube. Konten privat, berbayar, atau yang memerlukan akun tetap tidak didukung.

## Platform dan batas dukungan

API mengenali YouTube, Instagram, Facebook, TikTok, X/Twitter, Reddit, Vimeo, Pinterest, SoundCloud, Telegram publik, Threads publik, Snapchat Spotlight, Dailymotion, dan LinkedIn. Pengenalan tautan bukan jaminan extractor dapat membaca semua postingan. Hasil bergantung pada dukungan extractor upstream, tipe posting, akses publik tanpa login, dan pembatasan IP dari platform.

Resolver galeri campuran yang diterapkan:

- **Instagram:** mencoba resolver logged-out GraphQL / JSON halaman publik untuk menemukan urutan foto dan video, lalu gallery-dl sebagai fallback. Poster video tidak dianggap foto. Tidak menggunakan cookie sesi akun.
- **Threads:** membaca JSON post publik dan menyeleksi posting sesuai shortcode/canonical URL; tiap lampiran foto atau video ditampilkan mengikuti urutan JSON sumber. Pengunduhan langsung hanya ke host CDN Meta yang diizinkan.
- **Facebook:** menggunakan satu manifest gallery-dl untuk urutan lampiran bila tersedia. Jika manifest tidak ada, fallback gambar publik tetap dibatasi pada metadata Open Graph dan subtree lampiran yang terkait ID post/foto target. Petik tidak memindai semua URL CDN dalam HTML karena itu pernah memasukkan rekomendasi, avatar, ikon, dan media posting lain. Deteksi video album Facebook tetap best-effort bergantung pada hasil extractor.
- **X/Twitter, Pinterest, dan Reddit:** menggunakan manifest URL gallery-dl untuk menggabungkan URL gambar dan file video langsung yang dikenali. Stream HLS/DASH terpisah (`.m3u8`/`.mpd`) tidak dipasarkan sebagai satu file direct; extractor harus menyediakan file video yang dapat diunduh.
- **Platform lain:** tetap memakai alur video/audio tunggal yt-dlp. Petik tidak menampilkan galeri multi-item yang belum memiliki jalur unduhan dan pemetaan ID yang aman.

Batasan yang perlu diperhatikan:

- Secara default Petik mengakses YouTube tanpa login dan tidak meminta password. Admin dapat menambahkan cookies YouTube miliknya sendiri secara opsional melalui secret environment variable `YOUTUBE_COOKIES_B64`; fitur ini tidak wajib, tidak menerima cookies media sosial, dan tidak menjamin semua konten privat/age-gated dapat diakses.
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

`PUBLIC_BASE_URL`, `ALLOWED_ORIGINS`, `MAX_FILE_MB`, `MAX_DURATION_SECONDS`, `FILE_TTL_SECONDS`, `MAX_CONCURRENT_DOWNLOADS`, `RATE_LIMIT_COUNT`, `RATE_LIMIT_WINDOW_SECONDS`, `TEMP_DIR`, `BGUTIL_POT_PROVIDER_ENABLED`, `BGUTIL_POT_PROVIDER_URL`, `BGUTIL_PROVIDER_PORT`, `BGUTIL_PROVIDER_START_TIMEOUT`, `BGUTIL_TELEMETRY_PORT`, `BGUTIL_TELEMETRY_URL`, `YOUTUBE_FORCE_IPV4`, `YOUTUBE_WPC_ENABLED` (harus bernilai `1` bersama plugin versi 1.1.2, Chromium, dan `YOUTUBE_POT_PROVIDER_MODE=wpc` untuk mengaktifkan WPC), `YOUTUBE_COOKIES_B64` (opsional; file cookies YouTube format Netscape yang di-Base64), `MAX_GALLERY_ITEMS`, `MAX_GALLERY_FILE_MB`, `MAX_GALLERY_VIDEO_MB`, dan `MAX_GALLERY_TOTAL_MB`.

## Tes dan batas verifikasi

Folder `tests/` berisi 142 tes offline pada v1.14.0 untuk parser Instagram/Threads campuran, deteksi URL gallery-dl, urutan `sourceIndex`, pemetaan ID unduhan, ZIP yang berisi foto dan video, unduhan CDN streaming, partial failure, validasi host/redirect, endpoint inspect/download, health, kompatibilitas Cobalt, preview, MP3, durasi, dan kualitas. Tes ini memvalidasi kontrak dan perilaku lokal; tes offline bukan bukti bahwa setiap tautan live dari tiap platform akan berhasil diekstrak.

## Urutan API di frontend Petik

1. API khusus yang diisi pengguna (jika ada).
2. API Petik Render: `https://petik-universal-api-masx.onrender.com`.
3. API Cobalt eksternal sebagai cadangan video/audio yang sudah ada.

## Riwayat rilis

### v1.12.0 — Stage Diagnostics, Attempt Telemetry, dan Recovery

- Merekam tahap ekstraksi YouTube dalam label aman tanpa menyimpan baris debug mentah.
- Menambahkan event telemetry per request BgUtils, dengan metadata terbatas dan tanpa body/token.
- Menserialkan percobaan YouTube untuk menjaga korelasi event telemetry.
- Mengevaluasi `android_vr` tetapi tidak memasukkannya ke fallback aktif karena catatan extractor yt-dlp terbaru menunjukkan semua format client ini mengalami HTTP 403.
- Menambahkan dukungan cookies YouTube opsional melalui secret environment variable Base64, dengan file sementara mode `0600`; default tetap tanpa login.
- Menambahkan `requirements-wpc.txt` opsional, tetapi tidak memasang Chrome/WPC pada Render Free.
- Membuat pesan bot-check lebih akurat dan tidak menyimpulkan blokir IP tanpa bukti.
- Mempertahankan BgUtils, Deno/EJS, mixed media, resolver sosial, preview, Direct Link, dan MP3.
- Menambahkan tes offline untuk diagnostik tahap, isolasi event, privasi, cookies, dan fallback.

### v1.11.0 — YouTube Provider Telemetry, HLS Recovery, dan IPv4

- Menambahkan `pot_telemetry.py`, proxy penerus BgUtils yang hanya terikat ke loopback.
- Mengukur permintaan `/get_pot`, status HTTP, dan respons yang berisi field `poToken` tanpa menyimpan token/body.
- Menggunakan telemetry proxy otomatis bila siap; fallback langsung ke BgUtils bila proxy gagal dimulai.
- Memisahkan strategi `web_safari_hls` dari percobaan `web_safari` biasa, menolak HLS-only pada percobaan normal, serta membatasi unduhan HLS ke format muxed yang memiliki audio dan video.
- Memaksa IPv4 untuk yt-dlp YouTube melalui `source_address=0.0.0.0` (dapat dimatikan dengan `YOUTUBE_FORCE_IPV4=0`).
- Memisahkan counter provider lifetime dari delta permintaan pada upaya terakhir.
- Menambahkan petunjuk akar masalah bot-check berdasarkan bukti provider, tanpa menyatakan blokir IP sebagai kepastian.
- Menambahkan status WPC ke `/health`; WPC tetap tidak diinstal/diaktifkan pada image standar karena memerlukan Chromium.
- Mempertahankan BgUtils 2.0.2, Deno/EJS, resolver sosial, galeri mixed media, preview, Direct Link, dan MP3.

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

---

## v1.13.0 — YouTube Player API diagnostics and optional WPC profile

- Preserves the default Render deployment using BgUtils (`YOUTUBE_POT_PROVIDER_MODE=bgutil`).
- Adds an isolated `Dockerfile.wpc` profile: installs Chromium and `yt-dlp-getpot-wpc==1.1.2`, disables the BgUtils provider in that image, and selects WPC explicitly. This is an experimental profile, not the default Render Free image.
- WPC automatically registers as a yt-dlp PO Token provider when its package is installed. Chromium must be available; `YOUTUBE_WPC_BROWSER_PATH` pins the binary path.
- Adds attempt IDs and safe error events, keeps the highest-priority root error instead of replacing Player API `bot_check` with later format-selection wrapper errors, and captures HTTP 403/429/5xx categories without storing raw response text.
- Avoids retrying the Safari HLS strategy when the same underlying Safari Player API request was already blocked by bot-check/login/HTTP errors.
- Keeps optional Netscape cookies in an owner-only temporary file and removes that file after each YouTube inspect/download operation.
- WPC is not recommended for Render Free: Chromium increases resource use; the profile must be load-tested before production use.

### YouTube provider profiles

**Standard (default):** `Dockerfile`, `YOUTUBE_POT_PROVIDER_MODE=bgutil`, BgUtils server enabled, no Chromium, no WPC package.

**WPC experiment:** `Dockerfile.wpc`, `YOUTUBE_POT_PROVIDER_MODE=wpc`, `YOUTUBE_WPC_ENABLED=1`, `YOUTUBE_WPC_BROWSER_PATH=/usr/bin/chromium`, BgUtils provider disabled in that image. Switch the Render Dockerfile Path back to `./Dockerfile` to restore the standard profile.

The YouTube PO Token guide lists `mweb` as requiring a GVS PO Token currently, while `tv` and `web_embedded` have their own format restrictions. Therefore, WPC is tested here as an alternative provider for the token stage; it is not claimed to cure an Innertube Player API bot-check that happens before format delivery.
