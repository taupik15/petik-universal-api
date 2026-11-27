from __future__ import annotations

import os
import re
import shutil
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from yt_dlp import YoutubeDL

APP_VERSION = "1.5.0"
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_MB", "140")) * 1024 * 1024
FILE_TTL_SECONDS = int(os.getenv("FILE_TTL_SECONDS", "1800"))
MAX_DURATION_SECONDS = int(os.getenv("MAX_DURATION_SECONDS", "3600"))
MAX_CONCURRENT = max(1, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2")))
RATE_LIMIT_COUNT = max(1, int(os.getenv("RATE_LIMIT_COUNT", "40")))
RATE_LIMIT_WINDOW = max(60, int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "300")))
PREVIEW_MAX_CONCURRENT = max(2, int(os.getenv("PREVIEW_MAX_CONCURRENT", "6")))
PREVIEW_RATE_COUNT = max(30, int(os.getenv("PREVIEW_RATE_COUNT", "240")))
TEMP_ROOT = Path(os.getenv("TEMP_DIR", "/tmp/petik-media"))
TEMP_ROOT.mkdir(parents=True, exist_ok=True)

SERVICES = [
    "youtube", "instagram", "facebook", "tiktok", "twitter",
    "reddit", "vimeo", "pinterest", "soundcloud", "telegram", "threads", "snapchat",
]

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
    "snapchat.com",
}

app = FastAPI(title="Petik Universal Extractor API", version=APP_VERSION)
allowed_origins = [
    item.strip()
    for item in os.getenv("ALLOWED_ORIGINS", "https://taupik15.github.io").split(",")
    if item.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Length", "Accept-Ranges"],
)

download_slots = threading.BoundedSemaphore(MAX_CONCURRENT)
# Preview punya jatah sendiri: Safari/iOS mengirim banyak request Range paralel
# untuk satu video. Kalau berbagi slot (1) dan limit (20/5 menit) dengan unduhan,
# preview langsung kena 429 dan video tidak bisa diputar.
preview_slots = threading.BoundedSemaphore(PREVIEW_MAX_CONCURRENT)
preview_rate_events: dict[str, deque[float]] = defaultdict(deque)
active_files: dict[str, tuple[Path, str, float]] = {}
rate_events: dict[str, deque[float]] = defaultdict(deque)
state_lock = threading.Lock()
preview_cache: dict[str, tuple[str, dict, float]] = {}


def _base_url(request: Request) -> str:
    configured = os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL")
    if configured:
        return configured.rstrip("/")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or str(request.url.netloc)
    proto = request.headers.get("x-forwarded-proto", "https").split(",", 1)[0].strip()
    return f"{proto}://{host}".rstrip("/")


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    return (request.headers.get("cf-connecting-ip") or (forwarded.split(",")[-1].strip() if forwarded else "") or (request.client.host if request.client else "unknown"))[:80]


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


def _preview_rate_limited(key: str) -> bool:
    now = time.time()
    with state_lock:
        q = preview_rate_events[key]
        while q and now - q[0] > RATE_LIMIT_WINDOW:
            q.popleft()
        if len(q) >= PREVIEW_RATE_COUNT:
            return True
        q.append(now)
        return False


def _cleanup_expired() -> None:
    now = time.time()
    with state_lock:
        expired = [token for token, (_, _, expiry) in active_files.items() if expiry <= now]
        for token in expired:
            path, _, _ = active_files.pop(token)
            shutil.rmtree(path.parent, ignore_errors=True)
    # A download may legitimately run near the one-hour media-duration cap.
    # Keep abandoned in-progress folders longer than that cap before pruning.
    cutoff = now - max(FILE_TTL_SECONDS * 2, MAX_DURATION_SECONDS + 3600)
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
    elif host == "youtube.com" or host.endswith(".youtube.com") or host in {"youtu.be", "youtube-nocookie.com"}:
        platform = "youtube"
    elif host == "instagram.com" or host.endswith(".instagram.com") or host == "instagr.am":
        platform = "instagram"
    elif host == "facebook.com" or host.endswith(".facebook.com") or host in {"fb.watch", "fb.com", "fb.me"}:
        platform = "facebook"
    elif host == "tiktok.com" or host.endswith(".tiktok.com"):
        platform = "tiktok"
    elif host in {"x.com", "twitter.com"} or host.endswith(".twitter.com"):
        platform = "twitter"
    elif host == "snapchat.com" or host.endswith(".snapchat.com"):
        platform = "snapchat"
    return value, platform


def _safe_filename(info: dict, suffix: str) -> str:
    title = str(info.get("title") or info.get("id") or "petik")
    title = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" .")[:100] or "petik"
    safe_suffix = re.sub(r"[^a-z0-9]+", "", suffix.lower()) or "bin"
    return f"{title}.{safe_suffix}"


def _yt_common_options() -> dict:
    options = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "retries": 2,
        "extractor_retries": 2,
        "socket_timeout": 25,
        "cachedir": False,
        "playlist_items": "1",
    }
    deno_path = shutil.which("deno")
    if deno_path:
        options["js_runtimes"] = {"deno": {"path": deno_path}}
    return options


def _extract_info(url: str) -> dict:
    options = _yt_common_options()
    options.update({"skip_download": True, "extract_flat": False})
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError("Platform tidak mengembalikan informasi media.")
    if info.get("_type") == "playlist":
        entries = [entry for entry in (info.get("entries") or []) if entry]
        if not entries:
            raise RuntimeError("Postingan tidak memiliki media yang bisa diproses.")
        info = entries[0]
    duration = info.get("duration")
    if duration and duration > MAX_DURATION_SECONDS:
        raise RuntimeError(f"Durasi melebihi batas {MAX_DURATION_SECONDS // 60} menit.")
    return info


def _media_formats(info: dict) -> list[dict]:
    """Return yt-dlp formats, including formats nested in requested downloads."""
    formats = list(info.get("formats") or [])
    for key in ("requested_formats", "requested_downloads"):
        value = info.get(key) or []
        if isinstance(value, dict):
            value = [value]
        if isinstance(value, list):
            formats.extend(item for item in value if isinstance(item, dict))
    if info.get("url") and not formats:
        formats.append(info)
    return formats


def _codec_present(value) -> bool:
    return bool(value and str(value).lower() != "none")


def _available_options(info: dict, platform: str) -> tuple[list[dict], list[dict]]:
    if platform == "soundcloud":
        return [], [{"id": "best", "label": "MP3 · kualitas terbaik"}]

    formats = _media_formats(info)
    heights: set[int] = set()
    has_video = _codec_present(info.get("vcodec"))
    has_audio = _codec_present(info.get("acodec"))
    video_extensions = {"mp4", "webm", "mov", "m4v", "mkv", "flv", "3gp"}
    audio_extensions = {"mp3", "m4a", "aac", "wav", "flac", "ogg", "opus", "weba"}
    for fmt in formats:
        if _codec_present(fmt.get("acodec")):
            has_audio = True
        if _codec_present(fmt.get("vcodec")):
            has_video = True
            height = fmt.get("height")
            if isinstance(height, (int, float)) and 0 < height <= 4320:
                heights.add(int(height))
    # Some social extractors expose a single direct URL instead of a formats array.
    # Infer the media type from its extension only when codec metadata is absent.
    ext = str(info.get("ext") or "").lower()
    if info.get("url") and not has_video and not has_audio:
        if ext in video_extensions or (not ext and platform != "soundcloud"):
            has_video = True
        elif ext in audio_extensions or platform == "soundcloud":
            has_audio = True
    # The user chooses an upper bound; yt-dlp picks the best available format under it.
    video_options = [{"id": "max", "label": "Terbaik yang tersedia"}] if has_video else []
    for height in sorted(heights, reverse=True):
        video_options.append({"id": str(height), "label": f"Maksimal {height}p"})
    audio_options = [{"id": "best", "label": "MP3 · kualitas terbaik"}] if has_audio else []
    return video_options, audio_options


def _safe_preview_url(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value)
        return value if parsed.scheme in {"http", "https"} and parsed.hostname else None
    except Exception:
        return None


def _is_none_codec(value) -> bool:
    """True only when yt-dlp explicitly says "none" (bukan sekadar tidak dilaporkan)."""
    return isinstance(value, str) and value.strip().lower() == "none"


def _preview_url(info: dict, platform: str) -> str | None:
    """Choose a browser-friendly progressive stream for inline video preview."""
    if platform == "soundcloud":
        return None
    candidates = []
    for fmt in _media_formats(info):
        url = _safe_preview_url(fmt.get("url"))
        if not url:
            continue
        vcodec, acodec = fmt.get("vcodec"), fmt.get("acodec")
        # "none" berarti pasti tidak ada; None/kosong berarti tidak dilaporkan.
        if _is_none_codec(vcodec) or _is_none_codec(acodec):
            continue
        ext = str(fmt.get("ext") or "").lower()
        protocol = str(fmt.get("protocol") or "").lower()
        if ext in {"m3u8", "mpd"} or "m3u8" in protocol or "dash" in protocol:
            continue
        known = _codec_present(vcodec) and _codec_present(acodec)
        if not known and ext not in {"mp4", "mov", "m4v", "webm"}:
            continue
        height = fmt.get("height")
        height = int(height) if isinstance(height, (int, float)) and height > 0 else 0
        candidates.append((0 if known else 1, 0 if ext == "mp4" else 1, 0 if 0 < height <= 720 else 1, -min(height or 720, 720), url))
    if candidates:
        candidates.sort()
        return candidates[0][-1]

    root_url = _safe_preview_url(info.get("url"))
    ext = str(info.get("ext") or "").lower()
    if root_url and not _is_none_codec(info.get("vcodec")) and not _is_none_codec(info.get("acodec")) \
            and ext not in {"mp3", "m4a", "aac", "wav", "flac", "ogg", "opus", "m3u8", "mpd"}:
        return root_url
    return None


def _safe_thumbnail(info: dict) -> str | None:
    thumb = info.get("thumbnail")
    if not isinstance(thumb, str):
        return None
    try:
        parsed = urlparse(thumb)
        return thumb if parsed.scheme in {"http", "https"} and parsed.hostname else None
    except Exception:
        return None


def _inspect_payload(url: str, platform: str) -> dict:
    info = _extract_info(url)
    videos, audios = _available_options(info, platform)
    title = str(info.get("title") or info.get("id") or "Media")[:300]
    return {
        "success": True,
        "status": "ready",
        "service": platform,
        "title": title,
        "author": str(info.get("uploader") or info.get("channel") or info.get("artist") or "")[:180] or None,
        "duration": info.get("duration"),
        "thumbnail": _safe_thumbnail(info),
        "previewUrl": _preview_url(info, platform),
        "videoOptions": videos,
        "audioOptions": audios,
        "defaultVideoQuality": "max",
        "defaultAudioQuality": "best",
    }


def _download_media(url: str, mode: str, quality: str) -> tuple[Path, str, dict]:
    job_dir = Path(tempfile.mkdtemp(prefix="job-", dir=TEMP_ROOT))
    outtmpl = str(job_dir / "%(id)s.%(ext)s")
    quality = str(quality or "max").strip().lower()
    if quality not in {"max", "best"}:
        try:
            height_limit = int(quality)
            if not 144 <= height_limit <= 4320:
                quality = "max"
        except ValueError:
            quality = "max"
    if mode == "audio":
        fmt = "bestaudio/best"
        postprocessors = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "0",
        }]
    else:
        if quality in {"max", "best"}:
            fmt = "bestvideo+bestaudio/best"
        else:
            fmt = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]"
        postprocessors = []

    def duration_filter(info: dict, *, incomplete: bool) -> str | None:
        duration = info.get("duration")
        if not incomplete and duration and duration > MAX_DURATION_SECONDS:
            return f"Durasi melebihi batas {MAX_DURATION_SECONDS // 60} menit."
        return None

    options = _yt_common_options()
    options.update({
        "format": fmt,
        "outtmpl": outtmpl,
        "match_filter": duration_filter,
        "max_filesize": MAX_FILE_BYTES,
        "merge_output_format": "mp4",
        "postprocessors": postprocessors,
        "restrictfilenames": True,
        "windowsfilenames": True,
        "overwrites": True,
        "noprogress": True,
        "concurrent_fragment_downloads": 3,
        "fragment_retries": 2,
    })

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

    candidates = [
        path for path in job_dir.rglob("*")
        if path.is_file() and path.suffix.lower() not in {
            ".jpg", ".jpeg", ".webp", ".json", ".description", ".vtt", ".srt", ".part"
        }
    ]
    if not candidates:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise RuntimeError("Extractor membaca tautan, tetapi berkas media tidak terbentuk.")
    file_path = max(candidates, key=lambda path: path.stat().st_size)
    size = file_path.stat().st_size
    if size <= 0 or size > MAX_FILE_BYTES:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise RuntimeError(f"Ukuran berkas di luar batas Petik (maksimal {MAX_FILE_BYTES // (1024 * 1024)} MB).")
    if mode == "audio" and file_path.suffix.lower() != ".mp3":
        shutil.rmtree(job_dir, ignore_errors=True)
        raise RuntimeError("Konversi audio MP3 tidak selesai. Coba lagi atau pilih video.")
    if mode == "video" and file_path.suffix.lower() not in {".mp4", ".m4v", ".mov", ".webm", ".mkv"}:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise RuntimeError("Format video hasil ekstraksi tidak didukung.")
    return file_path, _safe_filename(info, "mp3" if mode == "audio" else file_path.suffix.lstrip(".")), info


def _store_file(path: Path, filename: str) -> str:
    token = secrets.token_urlsafe(24)
    expiry = time.time() + FILE_TTL_SECONDS
    with state_lock:
        active_files[token] = (path, filename, expiry)
    return token


def _error(code: str, message: str, status_code: int = 502, service: str | None = None):
    context = {"message": message[:350]}
    if service:
        context["service"] = service
    return JSONResponse({"status": "error", "error": {"code": code, "context": context}}, status_code=status_code)


def _extract_error(exc: Exception, platform: str):
    message = str(exc)[:350] or "Media tidak tersedia."
    lowered = message.lower()
    code = "error.api.content.video.unavailable"
    if "unsupported url" in lowered or "not supported" in lowered or "belum didukung" in lowered:
        code = "error.api.link.unsupported"
    elif "too many requests" in lowered or "429" in lowered or "rate limit" in lowered:
        code = "error.api.rate.limit"
    elif "private" in lowered or "login" in lowered or "not available" in lowered or "deleted" in lowered:
        code = "error.api.content.video.unavailable"
    return _error(code, message, 502, platform)


def _acquire(request: Request):
    _cleanup_expired()
    if _rate_limited(_client_key(request)):
        return _error("error.api.rate.limit", "Terlalu banyak permintaan. Tunggu sebentar, lalu coba lagi.", 429)
    if not download_slots.acquire(blocking=False):
        return _error("error.api.rate.limit", "Server sedang memproses unduhan lain. Coba lagi sebentar.", 429)
    return None


def _normalise_request(payload: dict):
    try:
        return _normalise_and_validate(str(payload.get("url") or "")), None
    except ValueError as exc:
        return None, _error("error.api.link.unsupported", str(exc), 400)


def _run_download(url: str, platform: str, mode: str, quality: str, request: Request):
    try:
        path, filename, info = _download_media(url, mode, quality)
        token = _store_file(path, filename)
        return {
            "status": "tunnel",
            "url": f"{_base_url(request)}/api/file/{token}",
            "filename": filename,
            "service": platform,
            "title": info.get("title"),
            "quality": quality,
        }
    except Exception as exc:
        return _extract_error(exc, platform)


@app.get("/")
def server_info(request: Request):
    return {
        "cobalt": {
            "version": f"petik-universal-{APP_VERSION}",
            "url": _base_url(request),
            "services": SERVICES,
            "durationLimit": MAX_DURATION_SECONDS,
            "features": ["inspect", "quality-picker", "audio-mp3", "temporary-tunnel"],
        },
        "git": {"remote": "Petik Universal Extractor"},
    }


def _ytdlp_version() -> str:
    try:
        from yt_dlp.version import __version__
        return str(__version__)
    except Exception:
        return "unknown"


@app.get("/health")
def health():
    return {
        "success": True,
        "status": "healthy",
        "version": APP_VERSION,
        "engine": "yt-dlp+ffmpeg",
        "ytdlpVersion": _ytdlp_version(),
        "javascriptRuntime": "deno" if shutil.which("deno") else "missing",
    }



def _preview_source(url: str) -> tuple[str, dict]:
    """Resolve and briefly cache a browser-playable progressive media URL."""
    now = time.time()
    with state_lock:
        cached = preview_cache.get(url)
        if cached and cached[2] > now:
            return cached[0], cached[1]
    info = _extract_info(url)
    media_url = _preview_url(info, _normalise_and_validate(url)[1])
    if not media_url:
        raise RuntimeError("Sumber tidak menyediakan stream video langsung untuk pratinjau.")
    headers = {str(k): str(v) for k, v in (info.get("http_headers") or {}).items()}
    headers.setdefault("User-Agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1")
    with state_lock:
        preview_cache[url] = (media_url, headers, now + 240)
        for key, value in list(preview_cache.items()):
            if value[2] <= now:
                preview_cache.pop(key, None)
    return media_url, headers


@app.get("/api/preview")
def preview_media(request: Request, url: str):
    """Range-aware media proxy so Safari can play, pause and seek inline previews."""
    if _preview_rate_limited(_client_key(request)):
        return _error("error.api.rate.limit", "Terlalu banyak permintaan pratinjau.", 429)
    if not preview_slots.acquire(blocking=False):
        return _error("error.api.rate.limit", "Pratinjau sedang sibuk. Coba lagi sebentar.", 429)
    upstream = None
    try:
        normalised, error = _normalise_request({"url": url})
        if error:
            preview_slots.release()
            return error
        source_url, _platform = normalised
        media_url, upstream_headers = _preview_source(source_url)
        headers = dict(upstream_headers)
        range_header = request.headers.get("range")
        if range_header:
            headers["Range"] = range_header
        req = urllib.request.Request(media_url, headers=headers, method="GET")
        try:
            upstream = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as exc:
            upstream = exc
        status = getattr(upstream, "status", 200)
        content_type = upstream.headers.get("Content-Type", "video/mp4")
        response_headers = {
            "Accept-Ranges": upstream.headers.get("Accept-Ranges", "bytes"),
            "Content-Type": content_type,
            "Cache-Control": "private, max-age=60",
            "X-Content-Type-Options": "nosniff",
        }
        for key in ("Content-Length", "Content-Range"):
            value = upstream.headers.get(key)
            if value:
                response_headers[key] = value
        def stream():
            try:
                while True:
                    chunk = upstream.read(256 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                upstream.close()
                preview_slots.release()
        return StreamingResponse(stream(), status_code=status, headers=response_headers, media_type=None)
    except Exception as exc:
        if upstream is not None:
            try: upstream.close()
            except Exception: pass
        preview_slots.release()
        return JSONResponse({"success": False, "error": str(exc)[:300]}, status_code=502)

@app.post("/api/inspect")
def inspect_media(payload: dict, request: Request):
    limited = _acquire(request)
    if limited:
        return limited
    try:
        normalised, error = _normalise_request(payload)
        if error:
            return error
        url, platform = normalised
        try:
            return _inspect_payload(url, platform)
        except Exception as exc:
            return _extract_error(exc, platform)
    finally:
        download_slots.release()


@app.post("/api/download")
def download_media(payload: dict, request: Request):
    limited = _acquire(request)
    if limited:
        return limited
    try:
        normalised, error = _normalise_request(payload)
        if error:
            return error
        url, platform = normalised
        mode = "audio" if payload.get("mode") == "audio" else "video"
        if mode == "audio" and platform == "soundcloud":
            mode = "audio"
        quality = str(payload.get("quality") or ("best" if mode == "audio" else "max"))
        return _run_download(url, platform, mode, quality, request)
    finally:
        download_slots.release()


@app.post("/")
def cobalt_compatible_extract(payload: dict, request: Request):
    limited = _acquire(request)
    if limited:
        return limited
    try:
        normalised, error = _normalise_request(payload)
        if error:
            return error
        url, platform = normalised
        mode = "audio" if payload.get("downloadMode") == "audio" or platform == "soundcloud" else "video"
        quality = str(payload.get("videoQuality") or "720")
        return _run_download(url, platform, mode, quality, request)
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
