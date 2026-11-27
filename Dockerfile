FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DENO_NO_UPDATE_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JavaScript runtime plus its EJS challenge scripts for full
# YouTube extraction. Deno is the runtime recommended by yt-dlp.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && test -x /usr/local/bin/deno \
    && deno --version

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./app.py

EXPOSE 10000
# Saat container bangun (Render Free tidur lalu hidup lagi), yt-dlp diperbarui dulu
# karena Instagram/TikTok/YouTube sering berubah. Gagal/lambat -> lanjut pakai versi terpasang.
# Matikan dengan env YTDLP_AUTO_UPDATE=0.
CMD ["sh", "-c", "if [ \"${YTDLP_AUTO_UPDATE:-1}\" = \"1\" ]; then timeout 60 pip install --no-cache-dir -q -U 'yt-dlp[default]' || echo 'pembaruan yt-dlp dilewati'; fi; exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000}"]
