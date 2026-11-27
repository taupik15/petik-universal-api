# Petik Universal Extractor API

Backend tambahan yang kompatibel dengan request Cobalt milik Petik. Mendukung YouTube, Instagram, Facebook, TikTok, X, Reddit, Vimeo, Pinterest, SoundCloud, Telegram publik, dan Threads publik. Video diunduh sebagai MP4/container video; mode audio diproses menjadi MP3 dengan FFmpeg.

## Deploy paling mudah lewat Render (tanpa terminal)

1. Buat repository GitHub baru bernama `petik-universal-api`.
2. Upload isi folder API ini ke root repository (pastikan `Dockerfile`, `app.py`, `requirements.txt`, dan `render.yaml` berada langsung di root).
3. Di Render pilih **New → Blueprint** atau **New → Web Service**, hubungkan repository tadi, pilih Docker jika membuat Web Service, lalu deploy.
4. Setelah status deploy Live, buka URL `https://NAMA-LAYANAN.onrender.com/health`. Hasil yang benar memuat `success: true` dan `status: healthy`.
5. Buka URL root `/` dan pastikan array `cobalt.services` memuat `reddit`, `vimeo`, `pinterest`, `soundcloud`, `telegram`, dan `threads`.
6. Di Petik → Pengaturan → Alamat API unduhan, tempel URL dasar Render (tanpa `/health` atau path lain), lalu simpan.

## Batasan yang memang disengaja

- Telegram hanya untuk postingan channel/grup publik dengan link bentuk `t.me/nama_channel/123`. Link privat, invite, chat pribadi, konten protected, dan link `t.me/c/...` tidak didukung.
- Threads hanya untuk postingan publik. Extractor Threads memakai payload halaman publik untuk crawler; Meta dapat mengubah formatnya sehingga dukungan perlu diperbarui.
- Tidak ada login/cookies; media privat atau yang memerlukan akun akan ditolak.
- Batas file default 140 MiB dan durasi 60 menit. Ubah env var jika instance yang dipakai punya kapasitas memadai.
- Render Free tidur setelah 15 menit tanpa permintaan dan biasanya perlu sekitar satu menit untuk bangun. Sistem file bersifat sementara. Unduhan yang sedang berjalan bisa terpengaruh restart; jangan anggap paket gratis sebagai layanan produksi dengan uptime terjamin.
- Backend membatasi satu proses unduhan bersamaan dan delapan permintaan per alamat IP dalam lima menit untuk mengurangi penyalahgunaan.
- Jangan gunakan untuk mengambil atau menyebarkan konten tanpa izin pemiliknya.

## Konfigurasi opsional

`PUBLIC_BASE_URL` (jika URL publik tidak terdeteksi otomatis), `ALLOWED_ORIGINS` (default `https://taupik15.github.io`, pisahkan beberapa origin dengan koma), `MAX_FILE_MB`, `MAX_DURATION_SECONDS`, `FILE_TTL_SECONDS`, `MAX_CONCURRENT_DOWNLOADS`, `RATE_LIMIT_COUNT`, `RATE_LIMIT_WINDOW_SECONDS`.

## Pemeriksaan lokal

Folder `tests/` berisi uji offline untuk deteksi platform, validasi link, respons API kompatibel Cobalt, unduhan berkas, SoundCloud sebagai audio, dan opsi FFmpeg. Uji ini memakai media tiruan, jadi tidak menguji apakah sebuah postingan publik tertentu masih dapat diekstrak oleh situs sumber.
