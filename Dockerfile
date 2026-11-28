FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DENO_NO_UPDATE_CHECK=1 \
    DENO_NO_PROMPT=1

# Native build/runtime libraries are used by BgUtils' Deno npm:canvas dependency.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates curl git unzip \
    build-essential pkg-config python3 \
    libcairo2-dev libpango1.0-dev libjpeg-dev libgif-dev librsvg2-dev \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp needs Deno + yt-dlp-ejs for YouTube's JavaScript challenge solver.
# The current yt-dlp EJS guide requires Deno >= 2.3.0.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && test -x /usr/local/bin/deno \
    && deno --version \
    && deno eval 'const [major, minor] = Deno.version.deno.split(".").map(Number); if (major < 2 || (major === 2 && minor < 3)) { console.error("Deno >= 2.3.0 is required"); Deno.exit(1); }'

# Pin both the PO Token plugin and local HTTP provider to the same upstream release.
ARG BGUTIL_PROVIDER_VERSION=2.0.2
RUN git clone --depth 1 --single-branch --branch "${BGUTIL_PROVIDER_VERSION}" \
      https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
      /opt/bgutil-ytdlp-pot-provider \
    && cd /opt/bgutil-ytdlp-pot-provider/server \
    && deno install --allow-scripts=npm:canvas --frozen

# Optional free egress for YouTube: Cloudflare WARP exposed as a local SOCKS5
# proxy (wgcf registers a WARP identity, wireproxy runs it in userspace, so no
# NET_ADMIN/privileged mode is needed on Render). Only used when
# YOUTUBE_WARP_ENABLED=1.
ARG WGCF_VERSION=2.3.1
ARG WIREPROXY_VERSION=1.1.3
RUN ARCH="$(dpkg --print-architecture)" \
    && curl -fsSL -o /usr/local/bin/wgcf \
       "https://github.com/ViRb3/wgcf/releases/download/v${WGCF_VERSION}/wgcf_${WGCF_VERSION}_linux_${ARCH}" \
    && chmod 755 /usr/local/bin/wgcf \
    && curl -fsSL "https://github.com/windtf/wireproxy/releases/download/v${WIREPROXY_VERSION}/wireproxy_linux_${ARCH}.tar.gz" \
       | tar -xz -C /usr/local/bin wireproxy \
    && chmod 755 /usr/local/bin/wireproxy \
    && wgcf --help >/dev/null && wireproxy -v

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./app.py
COPY pot_telemetry.py ./pot_telemetry.py
COPY start.sh ./start.sh
RUN chmod 755 ./start.sh

EXPOSE 10000
# Start the BgUtils service on container loopback only, then start the API.
CMD ["sh", "/app/start.sh"]
