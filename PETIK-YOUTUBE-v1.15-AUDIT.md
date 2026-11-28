# Petik YouTube v1.15.0 — Audit & Implementation Notes

## Ringkasan

v1.14 dari deployment pengguna telah mencatat HTTP 429 dari endpoint YouTube, disertai bot-check pada tahap ekstraksi. BgUtils dinyatakan siap tetapi telemetry tidak melihat permintaan token. Hasil tersebut mendukung kesimpulan bahwa ekstraksi gagal sebelum alur token/format selesai. Ini **belum membuktikan** satu alamat IP tertentu, seluruh rentang IP Render, atau satu fingerprint client sebagai akar penyebab tunggal.

Rilis v1.15 memperbaiki bagian yang bisa dikendalikan oleh aplikasi: retry yt-dlp yang masih berjalan di dalam satu percobaan, konkurensi fragmen, prioritas klasifikasi 429, dan pemulihan status/header HTTP ke website.

## Pelaksanaan fase roadmap

| Fase | Hasil audit / implementasi | Status |
|---|---|---|
| 1. Audit akar masalah | Mengikuti event `http_429` per client dan memastikan bot-check berikutnya tidak menimpa error 429. | Selesai |
| 2. Reputasi IP Render | Log pengguna menunjukkan YouTube mengirim 429 ke service Render. Perbandingan lintas jaringan/region tidak dapat dijalankan dari source package; perlu service uji terpisah dan tes live. | Parsial — perlu tes deployment |
| 3. Browser impersonation | `curl_cffi` tersedia di requirements, tetapi impersonasi tetap opt-in. Tidak diaktifkan otomatis karena fingerprint tidak mengubah reputasi IP egress dan dapat menambah variabel baru. | Dievaluasi, tidak diaktifkan |
| 4. YouTube client | Urutan client eksplisit v1.14 dipertahankan: `tv_samsung`, `visionos`, `web`, `mweb` saat provider siap, lalu `web_embedded`. Satu client per proses yt-dlp; berhenti ketika ada 429. | Dipertahankan |
| 5. Innertube | Override host, key, player-skip, dan visitor-data tetap kosong kecuali sengaja dikonfigurasi pengguna. Nilai tebakan tidak ditambahkan. | Dievaluasi, opt-in |
| 6. PO Token/BgUtils | BgUtils 2.0.2 dipertahankan. Telemetry 0 request token pada kegagalan sebelumnya bukan bukti provider rusak; permintaan belum mencapai tahap GVS token. | Dipertahankan |
| 7. Session/cookies | Dukungan cookies opsional v1.14 tetap tersedia dengan file sementara yang dibatasi permission dan dibersihkan. Tidak ada cookies atau kredensial di paket. | Dipertahankan |
| 8. Render Free/WPC | WPC/Chromium tetap tidak aktif pada profil standar. Dedicated outbound IP Render bukan bagian dari paket gratis ini. | Dievaluasi, tidak diaktifkan |
| 9. Website | Patch v1.15 mengenali error HTTP 429 khusus YouTube, menghentikan fallback aplikasi setelah inspeksi 429, dan menampilkan waktu tunggu dari `Retry-After` jika tersedia. | Selesai secara statis |
| 10. Regression & packaging | 146 tes offline lulus; validasi sintaks dan ZIP dilakukan. Tes live masih harus dijalankan setelah deployment. | Selesai lokal |

## Perubahan backend

- `retries=0`
- `extractor_retries=0`
- `fragment_retries=0`
- `file_access_retries=0`
- `concurrent_fragment_downloads=1`

Kebijakan di atas hanya diterapkan bila `platform == "youtube"`. Extractor Instagram, Facebook, Threads, dan platform lain tetap memakai retry/konkurensi sebelumnya.

Ketika logger yt-dlp melihat HTTP 429:

1. Petik mengaktifkan cooldown yang sudah ada.
2. Petik menghentikan client fallback.
3. Error akhir diberi label HTTP 429, sekalipun ada teks bot-check sesudahnya.
4. `/api/inspect` dan `/api/download` mengembalikan HTTP 429 dengan `Retry-After`.
5. CORS mengekspos header `Retry-After` agar browser GitHub Pages dapat membaca waktunya.
6. `/health` menampilkan `youtubeRequestPolicy` untuk audit konfigurasi retry.

## Catatan teknis eksternal

- Render menjelaskan outbound IP layanan menggunakan rentang berdasarkan region dan rentang tersebut dapat dipakai bersama oleh layanan lain pada region yang sama: https://render.com/docs/outbound-ip-addresses
- Panduan PO Token yt-dlp saat ini mengidentifikasi `mweb` membutuhkan PO Token untuk GVS; panduan tersebut tidak menyatakan semua kegagalan Player API dapat diselesaikan dengan token GVS: https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide

Dua fakta tersebut tidak membuktikan YouTube memblokir seluruh IP Render. Untuk menguji pengaruh region, buat deployment uji terpisah dari dashboard Render, gunakan service dan URL uji yang sama, lalu bandingkan respons satu kali pada tiap deployment. Jangan mengganti service utama tanpa hasil pengujian. Hindari pengulangan request selama cooldown.

## Batas yang belum dapat dipenuhi

- Tidak ada akses untuk memindahkan service Render pengguna atau mengubah IP egress-nya.
- Tidak ada uji perbandingan lintas region pada deployment pengguna.
- Tidak ada bukti live bahwa YouTube menerima ekstraksi MP4 atau MP3 setelah v1.15.
- Tidak ada Docker/Podman pada lingkungan audit ini sehingga image build tidak dilakukan.
- Tidak ada layanan proxy gratis/pihak ketiga atau rotasi IP yang ditambahkan.

**Kesimpulan:** v1.15 mengurangi retry yang tidak perlu dan memperjelas penanganan rate limit. Ini memperbaiki perilaku Petik saat menerima 429, tetapi tidak dapat menjamin YouTube akan membuka akses dari egress Render. Bila tes setelah deployment masih mendapat 429, akar masalah kemungkinan berada di luar pengaturan retry aplikasi dan perlu pengujian jaringan/region.
