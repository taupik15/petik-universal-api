# Petik v1.15.0 — Test Report

Tanggal paket: 28 November 2026

## Hasil verifikasi lokal

| Pemeriksaan | Hasil |
|---|---:|
| Pytest suite | **146 passed** |
| `python -m py_compile app.py pot_telemetry.py` | Lulus |
| `bash -n start.sh` | Lulus |
| `node --check assets/petik-youtube-v1_15.js` | Lulus |
| Website JS smoke test (429 intercept + countdown) | Lulus |
| Pemeriksaan YAML Render | Lulus parse |
| Pemeriksaan isi ZIP / CRC | Lulus |

Tes baru/yang diperluas memeriksa:

- retry yt-dlp YouTube bernilai nol;
- konkurensi fragmen YouTube satu;
- retry default platform non-YouTube tidak berubah;
- HTTP 429 mengambil prioritas di atas teks bot-check yang muncul kemudian;
- endpoint `/api/inspect` dan `/api/download` meneruskan HTTP 429 dan header CORS `Retry-After`;
- health endpoint memublikasikan kebijakan request YouTube.

## Batas pengujian

- Docker dan Podman tidak tersedia; Docker image tidak dibangun.
- Tidak ada koneksi live ke YouTube dari lingkungan ini.
- Tidak ada tes download MP4/MP3 nyata dari Render pengguna.
- Tidak ada perbandingan egress IP lintas region Render.

**146 tes lulus adalah hasil tes offline, bukan bukti bahwa YouTube telah berhasil diunduh.** Tes live tetap diperlukan setelah pengguna mengunggah paket dan Render selesai melakukan deployment.
