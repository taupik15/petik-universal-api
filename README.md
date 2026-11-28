# Petik Universal API v1.17.0 — rotasi pool WARP untuk YouTube (lihat PETIK-v1.17-LANGKAH.md)

Petik adalah backend publik berbasis FastAPI, yt-dlp, gallery-dl, Deno/EJS, BgUtils PO Token Provider, HTTPX, curl-cffi, dan FFmpeg. Rilis ini berfokus pada penghentian retry YouTube yang tersembunyi setelah HTTP 429. Resolver sosial, galeri campuran foto + video, kompatibilitas API, dan konfigurasi BgUtils v1.14 dipertahankan.

## Perubahan v1.15.0

- **Retry YouTube dimatikan pada semua lapisan yt-dlp:** `retries=0`, `extractor_retries=0`, `fragment_retries=0`, dan `file_access_retries=0`.
- **Request fragmen diserialkan:** `concurrent_fragment_downloads=1` hanya untuk YouTube. Extractor sosial tetap menggunakan retry dan konkurensi sebelumnya.
- **429 diprioritaskan:** bila logger yt-dlp sudah mencatat HTTP 429, pesan bot-check yang muncul sesudahnya tidak boleh menutupi kejadian 429.
- **Status HTTP dipertahankan:** error upstream YouTube 429 dari `/api/inspect` dan `/api/download` dikembalikan sebagai HTTP 429 dengan header `Retry-After` berisi sisa cooldown atau nilai default cooldown. Header ini juga diekspos lewat CORS agar website GitHub Pages dapat membacanya.
- **Health dapat diaudit:** `/health` menambahkan `youtubeRequestPolicy` untuk menunjukkan jumlah retry dan konkurensi YouTube.
- **Website patch v1.15:** menampilkan pesan rate limit yang lebih jelas dan menghitung waktu tunggu dari header `Retry-After` jika tersedia. Tombol Simpan tidak diaktifkan ketika tidak ada format media.
- **Tidak ada pengelakan pembatasan:** tidak mengaktifkan proxy pihak ketiga, rotasi IP, atau impersonasi otomatis. `curl_cffi`, WPC, cookies, dan opsi Innertube eksperimental tetap mengikuti konfigurasi opt-in yang sudah ada.

## Temuan dan batas diagnosis

Deployment v1.14 yang diuji pengguna mengembalikan HTTP 429 dari YouTube. Ini membuktikan permintaan dari lingkungan server dibatasi pada saat pengujian, tetapi belum membuktikan satu alamat IP tertentu atau seluruh rentang IP Render yang menjadi penyebab. Dokumentasi Render menyatakan layanan pada region yang sama dapat memakai alamat dari rentang outbound bersama. Pengujian region perlu dilakukan melalui deployment uji yang terpisah; v1.15 tidak memindahkan service atau mengubah lokasi deployment.

Dokumentasi PO Token yt-dlp saat ini membedakan tahap Player API dan GVS. Untuk `mweb`, kebutuhan PO Token yang tercantum saat ini adalah GVS; oleh karena itu provider BgUtils tidak dianggap sebagai penyebab utama ketika ekstraksi berhenti lebih awal di Player API atau pada HTTP 429. BgUtils tetap dipertahankan untuk tahap stream yang memang membutuhkannya.

Sumber teknis yang dirujuk:
- Render outbound IP ranges: https://render.com/docs/outbound-ip-addresses
- yt-dlp YouTube PO Token Guide: https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide
- yt-dlp YouTube extractor documentation: https://github.com/yt-dlp/yt-dlp/wiki/Extractors

## Hal yang sengaja tidak diaktifkan

- `YOUTUBE_HTTP_IMPERSONATE`: tetap kosong secara default. Mengubah fingerprint HTTP tidak menghilangkan rate limit pada egress IP dan dapat mengurangi stabilitas.
- `YOUTUBE_INNERTUBE_HOST`, `YOUTUBE_INNERTUBE_KEY`, `YOUTUBE_PLAYER_SKIP`, `YOUTUBE_VISITOR_DATA`: tetap opt-in; jangan mengisi nilai tebakan atau data sesi orang lain.
- WPC/Chromium: tetap eksperimental dan tidak aktif pada profil Render Free standar.
- Cookies: mekanisme opsional dari v1.14 tetap tersedia, tetapi kredensial/cookies tidak dimasukkan ke ZIP, GitHub, atau log. Gunakan hanya sesi milik sendiri dan pahami risiko keamanan akun.
- Perpindahan region, dedicated IP, proxy, dan tes lintas jaringan tidak dilakukan otomatis oleh paket ini. Tidak ada layanan berbayar yang ditambahkan.

## Verifikasi v1.15.0

Tes offline memeriksa konfigurasi retry khusus YouTube, memastikan retry platform sosial tetap utuh, memastikan HTTP 429 mengalahkan bot-check yang muncul sesudahnya, memeriksa status endpoint dan header `Retry-After`, serta menjalankan seluruh kontrak regresi yang tersedia. Tes offline tidak membuktikan YouTube akan memberikan format video dari IP Render. Docker build, tes live YouTube, unduhan MP4/MP3 nyata, dan perbandingan region tetap memerlukan deployment dan pengujian jaringan nyata.

---

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
2. Ekstrak ZIP backend v1.15.0, lalu unggah semua isinya langsung ke root repository (jangan unggah file ZIP sebagai satu file).
3. Pastikan `Dockerfile`, `Dockerfile.wpc`, `app.py`, `pot_telemetry.py`, `start.sh`, `requirements.txt`, `requirements-wpc.txt`, `render.yaml`, `README.md`, dan folder `tests/` berada di root.
4. Commit perubahan dari GitHub.
5. Di Render, buka Blueprint Petik lalu jalankan **Manual Sync / Apply**.
6. Setelah layanan berstatus Live, buka endpoint `/health`. Versi yang diharapkan `1.15.0`, `mixedMediaAvailable: true`, dan `galleryDlAvailable: true` bila gallery-dl berhasil terpasang. Untuk YouTube, pastikan `youtubePoTokenProviderReady: true`, `youtubePoTokenPluginVersion: "2.0.2"`, dan `youtubePoTokenServerVersion: "2.0.2"`.
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

Folder `tests/` berisi 146 tes offline pada v1.15.0 untuk parser Instagram/Threads campuran, deteksi URL gallery-dl, urutan `sourceIndex`, pemetaan ID unduhan, ZIP yang berisi foto dan video, unduhan CDN streaming, partial failure, validasi host/redirect, endpoint inspect/download, health, kompatibilitas Cobalt, preview, MP3, durasi, dan kualitas. Tes ini memvalidasi kontrak dan perilaku lokal; tes offline bukan bukti bahwa setiap tautan live dari tiap platform akan berhasil diekstrak.

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
