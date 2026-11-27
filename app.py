from __future__ import annotations

import os
import re
import shutil
import secrets
import tempfile
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from yt_dlp import YoutubeDL

APP_VERSION = "1.0.0"
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_MB", "140")) * 1024 * 1024
FILE_TTL_SECONDS = int(os.getenv("FILE_TTL_SECONDS", "1800"))
MAX_DURATION_SECONDS = int(os.getenv("MAX_DURATION_SECONDS", "3600"))
MAX_CONCURRENT = max(1, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "1")))
RATE_LIMIT_COUNT = max(1, int(os.getenv("RATE_LIMIT_COUNT", "8")))
RATE_LIMIT_WINDOW = max(60, int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "300")))
TEMP_ROOT = Path(os.getenv("TEMP_DIR", "/tmp/petik-media"))
TEMP_ROOT.mkdir(parents=True, exist_ok=True)

SERVICES = [
    "youtube", "instagram", "facebook", "tiktok", "twitter",
    "reddit", "vimeo", "pinterest", "soundcloud", "telegram", "threads",
]

# Only accept URLs from the platforms explicitly supported by this service.
HOSTS = {
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    "instagram.com", "instagr.am",
    "facebook.com", "fb.watch", "fb.com", "fb.me",
    "tiktok.com",
    "x.com", "twitter.com",
    "reddit.com", "redd.it",
    "vimeo.com",
    "pinterest.com", "pin.it",
    "soundcloud.com",
    "t.me", "telegram.me",
    "threads.net", "threads.com",
}

app = FastAPI(title="Petik Universal Extractor API", version=APP_VERSION)
allowed_origins = [item.strip() for item in os.getenv("ALLOWED_ORIGINS", "https://taupik15.github.io").split(",") if item.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Length", "Accept-Ranges"],
)

download_slots = threading.BoundedSemaphore(MAX_CONCURRENT)
active_files: dict[str, tuple[Path, str, float]] = {}
rate_events: dict[str, deque[float]] = defaultdict(deque)
state_lock = threading.Lock()


def _base_url(request: Request) -> str:
    configured = os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL")
    if configured:
        return configured.rstrip("/")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or str(request.url.netloc)
    proto = request.headers.get("x-forwarded-proto", "https").split(",", 1)[0].strip()
    return f"{proto}://{host}".rstrip("/")


def _client_key(request: Request) -> str:
    # Render/Cloudflare supply the connecting IP. This is used only for a soft per-IP limit.
    return (request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
            or (request.client.host if request.client else "unknown"))[:80]


def _rate_limited(key: str) -> bool:
    now = time.time()
    with state_lock:
        q = rate_events[key]
        while q and now - q[0] > RATE_LIMIT_WINDOW:
            q.popleft()
        if len(q) >= RATE_LIMIT_COUNT:
            return True
        q.append(now)
        return False


def _cleanup_expired() -> None:
    now = time.time()
    with state_lock:
        expired = [token for token, (_, _, expiry) in active_files.items() if expiry <= now]
        for token in expired:
            path, _, _ = active_files.pop(token)
            try:
                shutil.rmtree(path.parent, ignore_errors=True)
            except Exception:
                pass
    # Also clear abandoned temporary job folders left after an interrupted extraction.
    cutoff = now - FILE_TTL_SECONDS
    for folder in TEMP_ROOT.iterdir():
        try:
            if folder.is_dir() and folder.stat().st_mtime < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            continue


def _normalise_and_validate(raw_url: str) -> tuple[str, str]:
    value = (raw_url or "").strip().strip("\"'")
    if not value or len(value) > 2048:
        raise ValueError("Tautan kosong atau terlalu panjang.")
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Gunakan tautan http/https yang valid.")
    host = parsed.hostname.lower().rstrip(".")
    if not any(host == allowed or host.endswith("." + allowed) for allowed in HOSTS):
        raise ValueError("Platform ini belum didukung oleh backend Petik.")

    path = parsed.path or "/"
    platform = "other"
    if host == "t.me" or host.endswith(".t.me") or host == "telegram.me":
        platform = "telegram"
        # Telegram's public web extractor accepts channel/post_id links only.
        match = re.fullmatch(r"/(?:s/)?([A-Za-z0-9_]{3,})/(\d+)/?", path)
        if not match or match.group(1).lower() == "c":
            raise ValueError("Telegram hanya mendukung tautan postingan channel publik, misalnya t.me/channel/123.")
        value = f"https://t.me/{match.group(1)}/{match.group(2)}"
    elif host == "threads.net" or host.endswith(".threads.net") or host == "threads.com" or host.endswith(".threads.com"):
        platform = "threads"
        if not re.search(r"/(?:@[^/]+/post/[^/?#]+|t/[^/?#]+|share/[^/?#]+)", path):
            raise ValueError("Tautan Threads harus mengarah ke postingan publik.")
    elif host == "reddit.com" or host.endswith(".reddit.com") or host == "redd.it":
        platform = "reddit"
    elif host == "vimeo.com" or host.endswith(".vimeo.com"):
        platform = "vimeo"
    elif host == "pinterest.com" or host.endswith(".pinterest.com") or host == "pin.it":
        platform = "pinterest"
    elif host == "soundcloud.com" or host.endswith(".soundcloud.com"):
        platform = "soundcloud"
    elif host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be" or host == "youtube-nocookie.com":
        platform = "youtube"
    elif host == "instagram.com" or host.endswith(".instagram.com") or host == "instagr.am":
        platform = "instagram"
    elif host == "facebook.com" or host.endswith(".facebook.com") or host in {"fb.watch", "fb.com", "fb.me"}:
        platform = "facebook"
    elif host == "tiktok.com" or host.endswith(".tiktok.com"):
        platform = "tiktok"
    elif host in {"x.com", "twitter.com"} or host.endswith(".twitter.com"):
        platform = "twitter"
    return value, platform


def _safe_filename(info: dict, suffix: str) -> str:
    title = str(info.get("title") or info.get("id") or "petik")
    title = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" .")[:100] or "petik"
    return f"{title}.{suffix}"


def _download_media(url: str, mode: str, quality: str) -> tuple[Path, str, dict]:
    job_dir = Path(tempfile.mkdtemp(prefix="job-", dir=TEMP_ROOT))
    outtmpl = str(job_dir / "%(id)s.%(ext)s")
    quality = quality if quality in {"max", "1080", "720", "480", "360"} else "720"
    if mode == "audio":
        fmt = "bestaudio/best"
        postprocessors = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        if quality == "max":
            fmt = "bestvideo+bestaudio/best"
        else:
            fmt = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
        # Let yt-dlp choose a compatible output container; forcing a remux can
        # fail on source codecs/containers that MP4 cannot safely carry.
        postprocessors = []

    def duration_filter(info: dict, *, incomplete: bool) -> str | None:
        duration = info.get("duration")
        if not incomplete and duration and duration > MAX_DURATION_SECONDS:
            return f"Durasi melebihi batas {MAX_DURATION_SECONDS // 60} menit."
        return None

    options = {
        "format": fmt,
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "retries": 1,
        "extractor_retries": 1,
        "socket_timeout": 25,
        "max_filesize": MAX_FILE_BYTES,
        "match_filter": duration_filter,
        "merge_output_format": "mp4",
        "postprocessors": postprocessors,
        "restrictfilenames": True,
        "windowsfilenames": True,
        "cachedir": False,
        "overwrites": True,
        "noprogress": True,
        "concurrent_fragment_downloads": 3,
        "fragment_retries": 1,
    }

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise RuntimeError("Platform tidak mengembalikan media.")
            if info.get("_type") == "playlist":
                entries = [item for item in (info.get("entries") or []) if item]
                if not entries:
                    raise RuntimeError("Postingan tidak memiliki media yang bisa diunduh.")
                info = entries[0]
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    candidates = [p for p in job_dir.rglob("*") if p.is_file() and p.suffix.lower() not in {".jpg", ".jpeg", ".webp", ".json", ".description", ".vtt", ".srt", ".part"}]
    if not candidates:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise RuntimeError("Extractor berhasil membaca tautan, tetapi berkas media tidak terbentuk.")
    file_path = max(candidates, key=lambda p: p.stat().st_size)
    size = file_path.stat().st_size
    if size <= 0 or size > MAX_FILE_BYTES:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise RuntimeError("Ukuran berkas di luar batas Petik (maksimal 140 MB).")
    if mode == "audio" and file_path.suffix.lower() != ".mp3":
        # The postprocessor should have produced MP3; fail instead of returning the wrong media type.
        shutil.rmtree(job_dir, ignore_errors=True)
        raise RuntimeError("Konversi audio MP3 tidak selesai. Coba lagi atau pilih video.")
    if mode == "video" and file_path.suffix.lower() not in {".mp4", ".m4v", ".mov", ".webm", ".mkv"}:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise RuntimeError("Format video hasil ekstraksi tidak didukung.")
    return file_path, _safe_filename(info, "mp3" if mode == "audio" else file_path.suffix.lstrip(".")), info


@app.get("/")
def server_info(request: Request):
    return {
        "cobalt": {
            "version": f"petik-universal-{APP_VERSION}",
            "url": _base_url(request),
            "services": SERVICES,
            "durationLimit": MAX_DURATION_SECONDS,
        },
        "git": {"remote": "Petik Universal Extractor"},
    }


@app.get("/health")
def health():
    return {"success": True, "status": "healthy", "version": APP_VERSION, "engine": "yt-dlp+ffmpeg"}


@app.post("/")
def cobalt_compatible_extract(payload: dict, request: Request):
    _cleanup_expired()
    client_key = _client_key(request)
    if _rate_limited(client_key):
        return JSONResponse({"status": "error", "error": {"code": "error.api.rate.limit"}}, status_code=429)
    if not download_slots.acquire(blocking=False):
        return JSONResponse({"status": "error", "error": {"code": "error.api.rate.limit"}}, status_code=429)
    try:
        try:
            url, platform = _normalise_and_validate(str(payload.get("url") or ""))
        except ValueError as exc:
            return JSONResponse({"status": "error", "error": {"code": "error.api.link.unsupported", "context": {"message": str(exc)}}}, status_code=400)
        # SoundCloud is an audio platform: return an MP3 even when the UI is
        # left in its default video mode. The frontend identifies it by filename.
        mode = "audio" if payload.get("downloadMode") == "audio" or platform == "soundcloud" else "video"
        quality = str(payload.get("videoQuality") or "720")
        try:
            path, filename, info = _download_media(url, mode, quality)
        except Exception as exc:
            message = str(exc)[:350] or "Media tidak tersedia."
            code = "error.api.content.video.unavailable"
            lowered = message.lower()
            if "unsupported url" in lowered or "not supported" in lowered or "belum didukung" in lowered:
                code = "error.api.link.unsupported"
            elif "private" in lowered or "login" in lowered or "not available" in lowered or "deleted" in lowered:
                code = "error.api.content.video.unavailable"
            elif "too many requests" in lowered or "429" in lowered:
                code = "error.api.rate.limit"
            return JSONResponse({"status": "error", "error": {"code": code, "context": {"message": message, "service": platform}}}, status_code=502)

        token = secrets.token_urlsafe(24)
        expiry = time.time() + FILE_TTL_SECONDS
        with state_lock:
            active_files[token] = (path, filename, expiry)
        return {
            "status": "tunnel",
            "url": f"{_base_url(request)}/api/file/{token}",
            "filename": filename,
            "service": platform,
            "title": info.get("title"),
        }
    finally:
        download_slots.release()


@app.get("/api/file/{token}")
def download_file(token: str):
    with state_lock:
        item = active_files.get(token)
    if not item:
        return JSONResponse({"success": False, "error": "Berkas sudah kedaluwarsa atau tautan tidak valid."}, status_code=404)
    path, filename, expiry = item
    if expiry <= time.time() or not path.is_file():
        with state_lock:
            active_files.pop(token, None)
        shutil.rmtree(path.parent, ignore_errors=True)
        return JSONResponse({"success": False, "error": "Berkas sudah kedaluwarsa."}, status_code=410)
    suffix = path.suffix.lower()
    media_type = "audio/mpeg" if suffix == ".mp3" else "video/mp4" if suffix in {".mp4", ".m4v"} else "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
