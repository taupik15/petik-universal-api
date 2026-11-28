from __future__ import annotations

import os
import copy
import base64
import binascii
import ipaddress
import re
import json
import logging
import math
import html as html_lib
import shutil
import secrets
import subprocess
import tempfile
import threading
import time
import zipfile
import urllib.error
import urllib.request
from contextlib import contextmanager
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs, urljoin

import httpx

try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    curl_requests = None
    HAS_CURL_CFFI = False

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from yt_dlp import YoutubeDL

APP_VERSION = "1.14.0"
MAX_GALLERY_ITEMS = max(1, min(30, int(os.getenv("MAX_GALLERY_ITEMS", "20"))))
MAX_GALLERY_SOURCE_ITEMS = MAX_GALLERY_ITEMS * 2  # Mixed carousels can contain video entries between photos.
MAX_GALLERY_FILE_BYTES = int(os.getenv("MAX_GALLERY_FILE_MB", "20")) * 1024 * 1024
MAX_GALLERY_TOTAL_BYTES = int(os.getenv("MAX_GALLERY_TOTAL_MB", "300")) * 1024 * 1024
MAX_GALLERY_VIDEO_BYTES = int(os.getenv("MAX_GALLERY_VIDEO_MB", "140")) * 1024 * 1024
GALLERY_PLATFORMS = {"instagram", "facebook", "twitter", "pinterest", "reddit"}
THREADS_PAGE_HOSTS = {"threads.net", "threads.com"}
THREADS_MEDIA_SUFFIXES = (".cdninstagram.com", ".fbcdn.net", ".threads.net", ".threads.com", ".fbsbx.com")
SOCIAL_MEDIA_SUFFIXES = (".cdninstagram.com", ".fbcdn.net", ".fbsbx.com")
INSTAGRAM_GRAPHQL_DOC_ID = "27130156389949648"  # Current public post query used by yt-dlp InstagramIE
THREADS_MAX_PAGE_BYTES = 3 * 1024 * 1024
THREADS_CRAWLER_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".heic"}
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
    "dailymotion", "linkedin",
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
    "dailymotion.com", "dai.ly",
    "linkedin.com",
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

# Only safe, non-sensitive YouTube diagnostics are retained in memory. Never
# store source URLs, cookies, visitor data, PO Tokens, or signed media URLs.
# yt-dlp 2026.11.23's maintained anonymous defaults are tv_samsung + visionos.
# "default" delegates that set to yt-dlp so it can evolve with upstream releases.
# mweb remains an explicit PO-token route when BgUtils/WPC is ready.
YOUTUBE_CLIENTS_WITH_PROVIDER = ("mweb",)
YOUTUBE_CLIENT_FALLBACKS = ("tv_samsung", "visionos", "web", "mweb", "web_embedded")
YOUTUBE_EXTRA_CLIENT_ALLOWLIST = {
    "tv_samsung", "visionos", "ios_producer", "web_safari", "tv_downgraded", "tv_simply"
}
def _bounded_env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(maximum, value))


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


YOUTUBE_MIN_ATTEMPT_INTERVAL_SECONDS = _bounded_env_float("YOUTUBE_MIN_ATTEMPT_INTERVAL_SECONDS", 5.0, 0.0, 60.0)
YOUTUBE_CLIENT_FALLBACK_INTERVAL_SECONDS = _bounded_env_float("YOUTUBE_CLIENT_FALLBACK_INTERVAL_SECONDS", 2.0, 0.0, 30.0)
YOUTUBE_RATE_LIMIT_COOLDOWN_SECONDS = _bounded_env_int("YOUTUBE_RATE_LIMIT_COOLDOWN_SECONDS", 300, 30, 3600)
YOUTUBE_SLEEP_INTERVAL_REQUESTS = _bounded_env_float("YOUTUBE_SLEEP_INTERVAL_REQUESTS", 1.0, 0.0, 10.0)
YOUTUBE_INFO_CACHE_TTL_SECONDS = _bounded_env_int("YOUTUBE_INFO_CACHE_TTL_SECONDS", 45, 0, 180)
YOUTUBE_INFO_CACHE_MAX = 64
YOUTUBE_RATE_LOCK = threading.Lock()
YOUTUBE_NETWORK_PROBE_LOCK = threading.Lock()
YOUTUBE_INFO_CACHE: dict[str, tuple[dict, float]] = {}
youtube_last_attempt_monotonic = 0.0
youtube_cooldown_until_monotonic = 0.0
youtube_429_count = 0
youtube_network_probe_last_monotonic = 0.0
YOUTUBE_DIAGNOSTICS_LOCK = threading.Lock()
YOUTUBE_DIAGNOSTICS = {
    "lastOutcome": "not-run",
    "lastStage": "not-started",
    "lastErrorStage": None,
    "lastErrorEvent": None,
    "stageEvents": [],
    "providerAttemptEvents": [],
    "providerEventBaseline": 0,
    "attemptedClients": [],
    "clientOutcomes": [],
    "providerAdvertised": None,
    "providerNames": [],
    "tokenGenerationObserved": False,
    "providerTelemetryAvailable": False,
    "providerRequestCount": 0,
    "providerHttpSuccessCount": 0,
    "providerHttpFailureCount": 0,
    "providerTokenResponseCount": 0,
    "providerLastStatusCode": None,
    "providerLastRequestAt": None,
    "providerLastTokenResponseAt": None,
    "providerRequestBaseline": 0,
    "providerTokenResponseBaseline": 0,
    "providerRequestsThisAttempt": 0,
    "providerTokenResponsesThisAttempt": 0,
    "lastStrategy": None,
    "hlsFormatsObserved": False,
    "rootCauseHint": None,
    "lastErrorCode": None,
    "attemptId": None,
    "attemptStartedAt": None,
    "errorEvents": [],
    "httpStatus": None,
    "http429Observed": False,
    "lastHttp429Client": None,
    "lastHttp429Stage": None,
    "lastHttp429At": None,
    "cooldownRemainingSeconds": 0,
    "infoCacheHit": False,
    "providerMode": "bgutil",
    "updatedAt": None,
}
youtube_client_cache: dict[str, tuple[str, float]] = {}
# Only one YouTube extraction/download may run at a time per API process. This
# makes the local BgUtils counter delta attributable to one attempt.
YOUTUBE_ATTEMPT_LOCK = threading.Lock()
YOUTUBE_COOKIE_LOCK = threading.Lock()
YOUTUBE_COOKIE_PATH = TEMP_ROOT / "youtube-cookies.txt"


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
    elif host == "dailymotion.com" or host.endswith(".dailymotion.com") or host == "dai.ly":
        platform = "dailymotion"
    elif host == "linkedin.com" or host.endswith(".linkedin.com"):
        platform = "linkedin"
    return value, platform


def _safe_filename(info: dict, suffix: str) -> str:
    title = str(info.get("title") or info.get("id") or "petik")
    title = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" .")[:100] or "petik"
    safe_suffix = re.sub(r"[^a-z0-9]+", "", suffix.lower()) or "bin"
    return f"{title}.{safe_suffix}"


BGUTIL_PROVIDER_DEFAULT_URL = "http://127.0.0.1:4416"
BGUTIL_PROVIDER_VERSION = "2.0.2"
YOUTUBE_WPC_PACKAGE_VERSION = "1.1.2"


def _wpc_browser_path() -> str | None:
    """Return an explicitly configured or discoverable Chrome/Chromium binary."""
    configured = os.getenv("YOUTUBE_WPC_BROWSER_PATH", "").strip()
    if configured:
        candidate = Path(configured)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        return None
    for command in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(command)
        if found:
            return found
    return None


def _wpc_status() -> dict:
    """Report whether WPC can actually launch; the package auto-registers in yt-dlp."""
    installed = _package_available("yt-dlp-getpot-wpc")
    browser_path = _wpc_browser_path()
    try:
        from importlib.metadata import version as package_version
        installed_version = package_version("yt-dlp-getpot-wpc") if installed else None
    except Exception:
        installed_version = None
    requested = os.getenv("YOUTUBE_WPC_ENABLED", "0").strip() == "1"
    version_matches = installed_version == YOUTUBE_WPC_PACKAGE_VERSION
    runtime_available = bool(installed and version_matches and browser_path)
    runtime_enabled = bool(requested and runtime_available)
    return {
        "requested": requested,
        "pluginInstalled": installed,
        "pluginVersion": installed_version,
        "versionMatches": version_matches,
        "expectedPluginVersion": YOUTUBE_WPC_PACKAGE_VERSION,
        "browserAvailable": bool(browser_path),
        "browserPath": browser_path,
        "runtimeAvailable": runtime_available,
        "runtimeEnabled": runtime_enabled,
        "configuredAndReady": runtime_enabled,
        "providerAutoDiscovered": installed,
        "providerMode": os.getenv("YOUTUBE_POT_PROVIDER_MODE", "bgutil").strip().lower(),
    }


def _bgutil_provider_status() -> dict:
    """Check BgUtils directly and the loopback telemetry proxy, if available."""
    enabled = os.getenv("BGUTIL_POT_PROVIDER_ENABLED", "1").strip() == "1"
    base_url = (os.getenv("BGUTIL_POT_PROVIDER_URL") or BGUTIL_PROVIDER_DEFAULT_URL).strip().rstrip("/")
    telemetry_url = (os.getenv("BGUTIL_TELEMETRY_URL") or "http://127.0.0.1:4417").strip().rstrip("/")
    plugin_version = None
    try:
        from importlib.metadata import version as package_version
        plugin_version = package_version("bgutil-ytdlp-pot-provider")
    except Exception:
        plugin_version = None

    server_version = None
    server_uptime = None
    server_available = False
    telemetry = None
    telemetry_available = False
    if enabled:
        try:
            request = urllib.request.Request(
                f"{base_url}/ping",
                headers={"User-Agent": "Petik-Health/1.14.0"},
            )
            with urllib.request.urlopen(request, timeout=1.2) as response:
                payload = json.loads(response.read(4096).decode("utf-8", errors="replace"))
            if isinstance(payload, dict) and payload.get("version"):
                server_version = str(payload.get("version"))[:40]
                server_uptime = payload.get("server_uptime")
                server_available = True
        except Exception:
            pass
        try:
            request = urllib.request.Request(
                f"{telemetry_url}/stats",
                headers={"User-Agent": "Petik-Health/1.14.0"},
            )
            with urllib.request.urlopen(request, timeout=0.8) as response:
                candidate = json.loads(response.read(4096).decode("utf-8", errors="replace"))
            required = {"requestCount", "httpSuccessCount", "httpFailureCount", "tokenResponseCount", "lastEventId"}
            if isinstance(candidate, dict) and required.issubset(candidate):
                telemetry = candidate
                telemetry_available = True
        except Exception:
            pass

    plugin_matches = plugin_version == BGUTIL_PROVIDER_VERSION
    server_matches = server_version == BGUTIL_PROVIDER_VERSION
    return {
        "enabled": enabled,
        "ready": bool(enabled and server_available and plugin_matches and server_matches),
        "serverAvailable": server_available,
        "pluginVersion": plugin_version,
        "serverVersion": server_version,
        "serverUptimeSeconds": server_uptime,
        "expectedVersion": BGUTIL_PROVIDER_VERSION,
        "baseUrl": base_url,
        "clientBaseUrl": telemetry_url if telemetry_available else base_url,
        "telemetryAvailable": telemetry_available,
        "telemetry": telemetry or {},
    }


def _youtube_cookies_file() -> str | None:
    """Materialize optional Netscape cookies supplied through a secret env var.

    The cookie value is never returned by health endpoints or written to logs.
    It is written only to ephemeral /tmp with owner-only permissions.
    """
    encoded = "".join(os.getenv("YOUTUBE_COOKIES_B64", "").split())
    if not encoded:
        return None
    if len(encoded) > 2_000_000:
        raise RuntimeError("YOUTUBE_COOKIES_B64 terlalu besar (maksimal 1,5 MB setelah decode).")
    with YOUTUBE_COOKIE_LOCK:
        try:
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) > 1_500_000:
                raise ValueError("cookie file too large")
            content = raw.decode("utf-8-sig")
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError("YOUTUBE_COOKIES_B64 tidak valid. Gunakan file cookies format Netscape yang di-Base64.") from exc
        lines = [line for line in content.splitlines() if line and (not line.startswith("#") or line.startswith("#HttpOnly_"))]
        if not any(len(line.split("\t")) >= 7 for line in lines):
            raise RuntimeError("File cookies YouTube bukan format Netscape yang valid.")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(str(YOUTUBE_COOKIE_PATH), flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(raw)
                handle.flush()
        finally:
            os.close(fd)
        return str(YOUTUBE_COOKIE_PATH)


def _youtube_cookie_configured() -> bool:
    return bool(os.getenv("YOUTUBE_COOKIES_B64", "").strip())


def _cleanup_youtube_cookie_file() -> None:
    """Remove the ephemeral materialized cookie jar after a YouTube operation."""
    with YOUTUBE_COOKIE_LOCK:
        try:
            YOUTUBE_COOKIE_PATH.unlink(missing_ok=True)
        except OSError:
            pass


def _update_youtube_diagnostics(**updates) -> None:
    with YOUTUBE_DIAGNOSTICS_LOCK:
        YOUTUBE_DIAGNOSTICS.update(updates)
        YOUTUBE_DIAGNOSTICS["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _youtube_http_status(message: str) -> int | None:
    """Extract only a numeric HTTP status from an yt-dlp error string."""
    match = re.search(r"(?:http\s+error|status\s+code|http\s+status)\s*[:=]?\s*(\d{3})", str(message or ""), re.I)
    if match:
        status = int(match.group(1))
        return status if 100 <= status <= 599 else None
    match = re.search(r"\b(403|429|500|502|503|504)\s*[: ]", str(message or ""), re.I)
    return int(match.group(1)) if match else None


def _youtube_error_code(message: str) -> str:
    lowered = str(message or "").lower()
    if "sign in to confirm" in lowered or "confirm you're not a bot" in lowered or "confirm you’re not a bot" in lowered:
        return "bot_check"
    if "po token" in lowered or "bgutil" in lowered or "botguard" in lowered:
        return "po_token"
    http_status = _youtube_http_status(message)
    if http_status == 429:
        return "http_429"
    if http_status in {500, 502, 503, 504}:
        return "http_5xx"
    if http_status == 403 or "http error 403" in lowered or "403: forbidden" in lowered:
        return "http_403"
    if "no hls formats" in lowered or "no hls format" in lowered:
        return "no_hls_formats"
    if any(term in lowered for term in ("no formats", "no video formats", "requested format is not available", "no downloadable formats", "format yang dapat diunduh", "format non-hls", "sabr", "m3u8")):
        return "no_downloadable_formats"
    if "login_required" in lowered or "sign in" in lowered or "login" in lowered:
        return "login_required"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    return "extractor_error"


def _youtube_rate_control_snapshot() -> dict:
    now = time.monotonic()
    with YOUTUBE_RATE_LOCK:
        cooldown = max(0, int(round(youtube_cooldown_until_monotonic - now)))
    return {
        "minimumAttemptIntervalSeconds": YOUTUBE_MIN_ATTEMPT_INTERVAL_SECONDS,
        "clientFallbackIntervalSeconds": YOUTUBE_CLIENT_FALLBACK_INTERVAL_SECONDS,
        "cooldownSeconds": YOUTUBE_RATE_LIMIT_COOLDOWN_SECONDS,
        "cooldownRemainingSeconds": cooldown,
        "sleepBetweenExtractorRequestsSeconds": YOUTUBE_SLEEP_INTERVAL_REQUESTS,
        "inspectCacheTtlSeconds": YOUTUBE_INFO_CACHE_TTL_SECONDS,
    }


def _activate_youtube_cooldown(client: str | None, stage: str | None) -> None:
    global youtube_cooldown_until_monotonic, youtube_429_count
    now = time.monotonic()
    with YOUTUBE_RATE_LOCK:
        youtube_cooldown_until_monotonic = max(
            youtube_cooldown_until_monotonic, now + YOUTUBE_RATE_LIMIT_COOLDOWN_SECONDS
        )
        youtube_429_count += 1
    _update_youtube_diagnostics(
        http429Observed=True,
        lastHttp429Client=client,
        lastHttp429Stage=stage,
        lastHttp429At=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        lastErrorCode="http_429",
        lastErrorEvent="http_429",
        lastErrorStage=stage or "http_429",
        httpStatus=429,
    )


def _youtube_fallback_pause() -> None:
    """Space distinct client attempts; the 429 path never reaches this pause."""
    if YOUTUBE_CLIENT_FALLBACK_INTERVAL_SECONDS > 0:
        time.sleep(YOUTUBE_CLIENT_FALLBACK_INTERVAL_SECONDS)


def _youtube_request_gate(*, allow_recent_inspect_followup: bool = False) -> None:
    """Honor cooldown and spacing; permit one immediate save after a fresh inspect."""
    global youtube_last_attempt_monotonic
    now = time.monotonic()
    with YOUTUBE_RATE_LOCK:
        remaining = youtube_cooldown_until_monotonic - now
        if remaining > 0:
            wait_for = 0.0
        else:
            wait_for = 0.0 if allow_recent_inspect_followup else max(
                0.0, YOUTUBE_MIN_ATTEMPT_INTERVAL_SECONDS - (now - youtube_last_attempt_monotonic)
            )
    if remaining > 0:
        seconds = max(1, int(round(remaining)))
        _update_youtube_diagnostics(
            lastOutcome="cooldown-blocked", lastStage="rate_limit_cooldown",
            lastErrorCode="http_429", lastErrorEvent="http_429", lastErrorStage="rate_limit_cooldown",
            httpStatus=429, cooldownRemainingSeconds=seconds,
        )
        raise RuntimeError(f"YouTube HTTP Error 429: permintaan ditahan sementara selama cooldown ({seconds} detik tersisa).")
    if wait_for > 0:
        time.sleep(wait_for)
    with YOUTUBE_RATE_LOCK:
        now = time.monotonic()
        remaining = youtube_cooldown_until_monotonic - now
        if remaining > 0:
            seconds = max(1, int(round(remaining)))
            _update_youtube_diagnostics(
                lastOutcome="cooldown-blocked", lastStage="rate_limit_cooldown",
                lastErrorCode="http_429", lastErrorEvent="http_429", lastErrorStage="rate_limit_cooldown",
                httpStatus=429, cooldownRemainingSeconds=seconds,
            )
            raise RuntimeError(f"YouTube HTTP Error 429: permintaan ditahan sementara selama cooldown ({seconds} detik tersisa).")
        youtube_last_attempt_monotonic = now
    _update_youtube_diagnostics(cooldownRemainingSeconds=0)


def _get_cached_youtube_info(url: str) -> dict | None:
    if YOUTUBE_INFO_CACHE_TTL_SECONDS <= 0:
        return None
    now = time.monotonic()
    with state_lock:
        cached = YOUTUBE_INFO_CACHE.get(url)
        if not cached:
            return None
        if cached[1] <= now:
            YOUTUBE_INFO_CACHE.pop(url, None)
            return None
        return copy.deepcopy(cached[0])


def _cache_youtube_info(url: str, info: dict) -> None:
    if YOUTUBE_INFO_CACHE_TTL_SECONDS <= 0:
        return
    now = time.monotonic()
    with state_lock:
        for key, value in list(YOUTUBE_INFO_CACHE.items()):
            if value[1] <= now:
                YOUTUBE_INFO_CACHE.pop(key, None)
        YOUTUBE_INFO_CACHE[url] = (copy.deepcopy(info), now + YOUTUBE_INFO_CACHE_TTL_SECONDS)
        while len(YOUTUBE_INFO_CACHE) > YOUTUBE_INFO_CACHE_MAX:
            YOUTUBE_INFO_CACHE.pop(next(iter(YOUTUBE_INFO_CACHE)))


def _consume_youtube_inspect_cache(url: str) -> bool:
    """Allow one immediate download following a successful inspection of this URL."""
    now = time.monotonic()
    with state_lock:
        cached = YOUTUBE_INFO_CACHE.pop(url, None)
    return bool(cached and cached[1] > now)


def _youtube_safe_extractor_overrides() -> dict:
    """Read optional, allowlisted test knobs without exposing secret values."""
    result: dict = {}
    host = os.getenv("YOUTUBE_INNERTUBE_HOST", "").strip().lower()
    allowed_hosts = {"youtubei.googleapis.com", "www.youtube.com", "music.youtube.com", "studio.youtube.com"}
    if host in allowed_hosts:
        result["innertube_host"] = [host]
    key = os.getenv("YOUTUBE_INNERTUBE_KEY", "").strip()
    if key and len(key) <= 256 and re.fullmatch(r"[A-Za-z0-9._-]+", key):
        result["innertube_key"] = [key]
    raw_skip = os.getenv("YOUTUBE_PLAYER_SKIP", "")
    skip_values = [item.strip().lower() for item in raw_skip.split(",") if item.strip()]
    skip_values = [item for item in skip_values if item in {"configs", "webpage", "js", "initial_data"}]
    if skip_values:
        result["player_skip"] = sorted(set(skip_values))
    visitor_data = os.getenv("YOUTUBE_VISITOR_DATA", "").strip()
    if (
        visitor_data and len(visitor_data) <= 512
        and re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", visitor_data)
        and {"webpage", "configs"}.issubset(set(skip_values))
        and not _youtube_cookie_configured()
    ):
        result["visitor_data"] = [visitor_data]
    return result


def _youtube_extra_clients() -> list[str]:
    values = [item.strip().lower() for item in os.getenv("YOUTUBE_EXTRA_CLIENTS", "").split(",") if item.strip()]
    return list(dict.fromkeys(item for item in values if item in YOUTUBE_EXTRA_CLIENT_ALLOWLIST))


def _youtube_impersonate_target() -> str | None:
    if not HAS_CURL_CFFI:
        return None
    value = os.getenv("YOUTUBE_HTTP_IMPERSONATE", "").strip().lower()
    if re.fullmatch(r"(?:chrome|safari|firefox|edge)(?:[-_]?[0-9]{2,3})?", value):
        return value
    return None


def _youtube_diagnostics_snapshot() -> dict:
    with YOUTUBE_DIAGNOSTICS_LOCK:
        return {
            **YOUTUBE_DIAGNOSTICS,
            "attemptedClients": list(YOUTUBE_DIAGNOSTICS.get("attemptedClients") or []),
            "clientOutcomes": [dict(item) for item in (YOUTUBE_DIAGNOSTICS.get("clientOutcomes") or [])],
            "providerNames": list(YOUTUBE_DIAGNOSTICS.get("providerNames") or []),
            "stageEvents": [dict(item) for item in (YOUTUBE_DIAGNOSTICS.get("stageEvents") or [])],
            "providerAttemptEvents": [dict(item) for item in (YOUTUBE_DIAGNOSTICS.get("providerAttemptEvents") or [])],
            "errorEvents": [dict(item) for item in (YOUTUBE_DIAGNOSTICS.get("errorEvents") or [])],
        }


def _youtube_error_priority(code: str | None) -> int:
    return {
        None: 0, "extractor_error": 1, "timeout": 2, "http_5xx": 3,
        "no_downloadable_formats": 4, "no_hls_formats": 4, "http_403": 5,
        "po_token": 7, "login_required": 8, "bot_check": 9, "http_429": 10,
    }.get(code, 2)


def _youtube_error_event(code: str | None) -> str | None:
    return "youtube_bot_check" if code == "bot_check" else code


def _record_youtube_attempt(client: str, outcome: str, error_code: str | None = None, stage: str | None = None) -> None:
    with YOUTUBE_DIAGNOSTICS_LOCK:
        attempted = list(YOUTUBE_DIAGNOSTICS.get("attemptedClients") or [])
        if client not in attempted:
            attempted.append(client)
        outcomes = list(YOUTUBE_DIAGNOSTICS.get("clientOutcomes") or [])
        outcomes.append({
            "attemptId": YOUTUBE_DIAGNOSTICS.get("attemptId"),
            "client": client, "outcome": outcome, "errorCode": error_code, "stage": stage,
        })
        updates = {
            "attemptedClients": attempted,
            "clientOutcomes": outcomes[-12:],
            "lastOutcome": outcome,
            "lastStage": stage or YOUTUBE_DIAGNOSTICS.get("lastStage"),
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        # Keep the first meaningful failure (for example bot_check at Player API)
        # instead of replacing it with yt-dlp's later generic extractor_error wrapper.
        previous_error = YOUTUBE_DIAGNOSTICS.get("lastErrorCode")
        if error_code and _youtube_error_priority(error_code) > _youtube_error_priority(previous_error):
            updates["lastErrorCode"] = error_code
            updates["lastErrorStage"] = stage or YOUTUBE_DIAGNOSTICS.get("lastErrorStage")
            updates["lastErrorEvent"] = _youtube_error_event(error_code)
        elif outcome in {"success", "download-success"}:
            updates.update({"lastErrorCode": None, "lastErrorStage": None, "lastErrorEvent": None, "httpStatus": None})
        YOUTUBE_DIAGNOSTICS.update(updates)


def _classify_youtube_stage(message: str) -> str | None:
    """Map yt-dlp debug text to safe stage labels; never retain the raw line."""
    value = str(message or "").lower()
    if "po token providers:" in value:
        return "provider_discovery"
    if "generating a" in value and "po token" in value:
        return "po_token_generation"
    if "downloading" in value and "player api json" in value:
        return "innertube_player_api"
    if "downloading" in value and "client config" in value:
        return "innertube_client_config"
    if "downloading" in value and ("initial data" in value or "initial data json" in value):
        return "youtube_initial_data"
    if "downloading" in value and ("webpage" in value or "watch page" in value):
        return "youtube_watch_page"
    if "downloading" in value and "player" in value and ("js" in value or "javascript" in value):
        return "youtube_player_script"
    if "downloading" in value and "api json" in value:
        return "innertube_api"
    if "extracting url" in value:
        return "extractor_start"
    if "formats" in value and any(term in value for term in ("available", "requested", "format")):
        return "format_selection"
    http_status = _youtube_http_status(value)
    if http_status == 429:
        return "http_429"
    if http_status in {500, 502, 503, 504}:
        return "http_5xx"
    if http_status == 403:
        return "http_403"
    return None


class _YtDlpDiagnosticLogger:
    """Capture safe provider-flow evidence without retaining raw URLs or tokens."""
    def __init__(self, client: str):
        self.client = client
        self.logger = logging.getLogger("uvicorn.error")
        self.last_stage = "extractor_start"
        self.first_error_stage: str | None = None
        self.first_error_code: str | None = None
        self.http_status: int | None = None
        self.http_429_seen = False
        self.stage_events: list[dict] = []

    def _stage(self, stage: str, *, update_current: bool = True) -> None:
        event = {
            "attemptId": YOUTUBE_DIAGNOSTICS.get("attemptId"),
            "client": self.client, "stage": stage,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.stage_events.append(event)
        self.stage_events = self.stage_events[-16:]
        with YOUTUBE_DIAGNOSTICS_LOCK:
            history = list(YOUTUBE_DIAGNOSTICS.get("stageEvents") or [])
            history.append(event)
            YOUTUBE_DIAGNOSTICS["stageEvents"] = history[-40:]
            if update_current:
                self.last_stage = stage
                YOUTUBE_DIAGNOSTICS["lastStage"] = stage
            YOUTUBE_DIAGNOSTICS["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _error(self, message: str, *, level: str) -> None:
        value = str(message or "")
        stage = _classify_youtube_stage(value)
        if stage:
            self._stage(stage)
        code = _youtube_error_code(value)
        status = _youtube_http_status(value)
        if code == "bot_check":
            self._stage("youtube_bot_check", update_current=False)
        if status is not None:
            self.http_status = status
        if status == 429 or code == "http_429":
            self.http_429_seen = True
            _update_youtube_diagnostics(
                http429Observed=True, lastHttp429Client=self.client,
                lastHttp429Stage=self.last_stage,
                lastHttp429At=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
        if _youtube_error_priority(code) > _youtube_error_priority(self.first_error_code):
            self.first_error_code = code
            self.first_error_stage = self.last_stage
        event = {
            "attemptId": YOUTUBE_DIAGNOSTICS.get("attemptId"),
            "client": self.client,
            "stage": self.first_error_stage or self.last_stage,
            "errorCode": code,
            "httpStatus": status,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with YOUTUBE_DIAGNOSTICS_LOCK:
            events = list(YOUTUBE_DIAGNOSTICS.get("errorEvents") or [])
            events.append(event)
            YOUTUBE_DIAGNOSTICS["errorEvents"] = events[-20:]
            previous_error = YOUTUBE_DIAGNOSTICS.get("lastErrorCode")
            if _youtube_error_priority(code) > _youtube_error_priority(previous_error):
                YOUTUBE_DIAGNOSTICS["lastErrorCode"] = code
                YOUTUBE_DIAGNOSTICS["lastErrorStage"] = self.first_error_stage or self.last_stage
                YOUTUBE_DIAGNOSTICS["lastErrorEvent"] = _youtube_error_event(code)
                YOUTUBE_DIAGNOSTICS["httpStatus"] = status
            YOUTUBE_DIAGNOSTICS["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if level == "warning":
            if any(term in value.lower() for term in ("po token", "bot", "403", "429", "login_required", "no formats")):
                self.logger.warning("[Petik YouTube] yt_dlp_warning=%s client=%s stage=%s http=%s", code, self.client, self.first_error_stage or self.last_stage, status)
        else:
            self.logger.error("[Petik YouTube] yt_dlp_error=%s client=%s stage=%s http=%s", code, self.client, self.first_error_stage or self.last_stage, status)

    def debug(self, message):
        value = str(message or "")
        lowered = value.lower()
        stage = _classify_youtube_stage(value)
        if stage:
            self._stage(stage, update_current=(stage != "provider_discovery"))
        if "po token providers:" in lowered:
            provider_names = []
            for name in ("bgutil:http", "bgutil:script-node", "bgutil:script-deno", "wpc"):
                if name in lowered:
                    provider_names.append(name)
            _update_youtube_diagnostics(providerAdvertised=bool(provider_names), providerNames=provider_names)
            self.logger.info("[Petik YouTube] provider_registry=%s client=%s", ",".join(provider_names) or "none", self.client)
        if "generating a" in lowered and "po token" in lowered:
            _update_youtube_diagnostics(tokenGenerationObserved=True, lastStrategy=self.client, lastStage="po_token_generation")
            context = "gvs" if "gvs" in lowered else "player" if "player" in lowered else "other"
            self.logger.info("[Petik YouTube] token_generation_requested=true context=%s client=%s", context, self.client)
        elif "po token" in lowered and any(term in lowered for term in ("generated", "generation failed", "failed to generate")):
            _update_youtube_diagnostics(tokenGenerationObserved=True, lastStage="po_token_generation")
            self.logger.info("[Petik YouTube] token_provider_event=true client=%s", self.client)

    def warning(self, message):
        self._error(str(message or ""), level="warning")

    def error(self, message):
        self._error(str(message or ""), level="error")


def _youtube_provider_status() -> dict:
    return _bgutil_provider_status()


def _begin_youtube_diagnostics_attempt(provider: dict, outcome: str) -> None:
    telemetry = provider.get("telemetry") or {}
    request_baseline = int(telemetry.get("requestCount") or 0)
    token_baseline = int(telemetry.get("tokenResponseCount") or 0)
    event_baseline = int(telemetry.get("lastEventId") or 0)
    attempt_id = secrets.token_hex(4)
    _update_youtube_diagnostics(
        attemptId=attempt_id,
        attemptStartedAt=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        errorEvents=[],
        httpStatus=None,
        http429Observed=False,
        lastHttp429Client=None,
        lastHttp429Stage=None,
        lastHttp429At=None,
        infoCacheHit=False,
        cooldownRemainingSeconds=_youtube_rate_control_snapshot()["cooldownRemainingSeconds"],
        providerMode=os.getenv("YOUTUBE_POT_PROVIDER_MODE", "bgutil").strip().lower(),
        lastOutcome=outcome,
        lastStage="attempt_started",
        lastErrorStage=None,
        lastErrorEvent=None,
        stageEvents=[],
        providerAttemptEvents=[],
        providerEventBaseline=event_baseline,
        attemptedClients=[],
        clientOutcomes=[],
        providerAdvertised=None,
        providerNames=[],
        tokenGenerationObserved=False,
        providerTelemetryAvailable=bool(provider.get("telemetryAvailable")),
        providerRequestCount=request_baseline,
        providerHttpSuccessCount=int(telemetry.get("httpSuccessCount") or 0),
        providerHttpFailureCount=int(telemetry.get("httpFailureCount") or 0),
        providerTokenResponseCount=token_baseline,
        providerLastStatusCode=telemetry.get("lastStatusCode"),
        providerLastRequestAt=telemetry.get("lastRequestAt"),
        providerLastTokenResponseAt=telemetry.get("lastTokenResponseAt"),
        providerRequestBaseline=request_baseline,
        providerTokenResponseBaseline=token_baseline,
        providerRequestsThisAttempt=0,
        providerTokenResponsesThisAttempt=0,
        lastErrorCode=None,
        lastStrategy=None,
        hlsFormatsObserved=False,
        rootCauseHint=None,
    )


def _youtube_root_cause_hint(diagnostics: dict) -> str | None:
    code = diagnostics.get("lastErrorCode")
    stage = diagnostics.get("lastErrorStage") or diagnostics.get("lastStage")
    if diagnostics.get("http429Observed") or code == "http_429":
        return "youtube_http_429_seen_stop_fallback_and_wait_for_process_cooldown"
    if code == "bot_check":
        if stage == "innertube_player_api":
            return "innertube_player_api_bot_check_before_format_delivery_possible_session_or_egress_reputation"
        if diagnostics.get("providerTelemetryAvailable") is False:
            return "provider_telemetry_unavailable_token_request_result_unknown"
        token_responses = int(diagnostics.get("providerTokenResponsesThisAttempt") or 0)
        requests = int(diagnostics.get("providerRequestsThisAttempt") or 0)
        if token_responses:
            return "bot_check_after_provider_returned_po_token_possible_ip_or_session_restriction"
        if requests:
            return "provider_called_but_no_po_token_response_confirmed"
        if diagnostics.get("providerAdvertised"):
            if stage == "po_token_generation":
                return "yt_dlp_started_po_token_generation_but_telemetry_saw_no_provider_request_check_provider_route"
            if stage in {"extractor_start", "youtube_watch_page", "youtube_initial_data", "innertube_client_config"}:
                return f"bot_check_before_player_or_format_stage_{stage}"
            return "provider_loaded_but_no_token_request_observed_before_extractor_failure"
        return "bot_check_without_provider_evidence_possible_client_session_or_egress_restriction"
    if code == "http_429":
        return "youtube_rate_limited_retry_after_cooldown_and_reduce_request_rate"
    if code == "http_403":
        return "youtube_forbidden_response_check_token_session_and_egress_policy"
    if code == "http_5xx":
        return "youtube_upstream_server_error_retry_after_short_cooldown"
    if code in {"no_downloadable_formats", "no_hls_formats"}:
        return "client_did_not_expose_a_supported_non_drm_download_format"
    if code == "po_token":
        return "po_token_provider_or_token_flow_error_check_provider_telemetry"
    return diagnostics.get("rootCauseHint")


def _finish_youtube_diagnostics(outcome: str, fallback_error: str | None = None) -> None:
    """Finalize outcome without replacing an earlier, more informative root cause."""
    with YOUTUBE_DIAGNOSTICS_LOCK:
        current = YOUTUBE_DIAGNOSTICS.get("lastErrorCode")
        YOUTUBE_DIAGNOSTICS["lastOutcome"] = outcome
        if fallback_error and _youtube_error_priority(fallback_error) > _youtube_error_priority(current):
            YOUTUBE_DIAGNOSTICS["lastErrorCode"] = fallback_error
            YOUTUBE_DIAGNOSTICS["lastErrorEvent"] = _youtube_error_event(fallback_error)
        YOUTUBE_DIAGNOSTICS["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _refresh_youtube_provider_diagnostics(provider: dict) -> None:
    telemetry = provider.get("telemetry") or {}
    if not provider.get("telemetryAvailable"):
        _update_youtube_diagnostics(
            providerTelemetryAvailable=False,
            providerRequestCount=None,
            providerHttpSuccessCount=None,
            providerHttpFailureCount=None,
            providerTokenResponseCount=None,
            providerLastStatusCode=None,
            providerLastRequestAt=None,
            providerLastTokenResponseAt=None,
            providerRequestsThisAttempt=None,
            providerTokenResponsesThisAttempt=None,
            providerAttemptEvents=[],
        )
        return
    request_count = int(telemetry.get("requestCount") or 0)
    token_count = int(telemetry.get("tokenResponseCount") or 0)
    baseline = int(YOUTUBE_DIAGNOSTICS.get("providerEventBaseline") or 0)
    events = telemetry.get("events") if isinstance(telemetry.get("events"), list) else None
    if events is not None:
        attempt_events = [
            {
                "attemptId": YOUTUBE_DIAGNOSTICS.get("attemptId"),
                "eventId": int(item.get("eventId") or 0),
                "at": item.get("at"),
                "statusCode": int(item.get("statusCode") or 0),
                "outcome": str(item.get("outcome") or "unknown"),
                "tokenPresent": bool(item.get("tokenPresent")),
                "elapsedMs": int(item.get("elapsedMs") or 0),
            }
            for item in events
            if isinstance(item, dict) and int(item.get("eventId") or 0) > baseline
        ]
        request_delta = len(attempt_events)
        token_delta = sum(1 for item in attempt_events if item["tokenPresent"])
    else:
        # Compatibility with older telemetry proxies: use lifetime counter deltas.
        attempt_events = []
        request_delta = max(0, request_count - int(YOUTUBE_DIAGNOSTICS.get("providerRequestBaseline") or 0))
        token_delta = max(0, token_count - int(YOUTUBE_DIAGNOSTICS.get("providerTokenResponseBaseline") or 0))
    _update_youtube_diagnostics(
        providerTelemetryAvailable=True,
        providerRequestCount=request_count,
        providerHttpSuccessCount=int(telemetry.get("httpSuccessCount") or 0),
        providerHttpFailureCount=int(telemetry.get("httpFailureCount") or 0),
        providerTokenResponseCount=token_count,
        providerLastStatusCode=telemetry.get("lastStatusCode"),
        providerLastRequestAt=telemetry.get("lastRequestAt"),
        providerLastTokenResponseAt=telemetry.get("lastTokenResponseAt"),
        providerRequestsThisAttempt=request_delta,
        providerTokenResponsesThisAttempt=token_delta,
        providerAttemptEvents=attempt_events[-25:],
        tokenGenerationObserved=(request_delta > 0) or bool(YOUTUBE_DIAGNOSTICS.get("tokenGenerationObserved")),
    )


def _youtube_client_candidates(
    provider_ready: bool, preferred: str | None = None, wpc_ready: bool = False
) -> list[str]:
    candidates = list(YOUTUBE_CLIENT_FALLBACKS)
    if not (provider_ready or wpc_ready) and "mweb" in candidates:
        candidates.remove("mweb")
    for extra in _youtube_extra_clients():
        if extra not in candidates:
            candidates.append(extra)
    if preferred in candidates:
        candidates.remove(preferred)
        candidates.insert(0, preferred)
    return candidates


def _get_cached_youtube_client(url: str) -> str | None:
    with state_lock:
        cached = youtube_client_cache.get(url)
        if not cached:
            return None
        if cached[1] <= time.time():
            youtube_client_cache.pop(url, None)
            return None
        return cached[0]


def _cache_youtube_client(url: str, client: str) -> None:
    now = time.time()
    with state_lock:
        for key, value in list(youtube_client_cache.items()):
            if value[1] <= now:
                youtube_client_cache.pop(key, None)
        youtube_client_cache[url] = (client, now + 900)
        while len(youtube_client_cache) > 128:
            youtube_client_cache.pop(next(iter(youtube_client_cache)))


def _youtube_should_skip_hls_retry(previous_client: str | None, previous_error: str | None) -> bool:
    """Do not repeat Safari's same Player API request as an HLS-only strategy."""
    return previous_client == "web_safari" and previous_error in {
        "bot_check", "login_required", "http_403", "http_429", "http_5xx", "timeout"
    }


def _youtube_should_fallback(exc: Exception | str) -> bool:
    lowered = str(exc or "").lower()
    return any(term in lowered for term in (
        "sign in to confirm", "confirm you're not a bot", "confirm you’re not a bot",
        "login_required", "po token", "bgutil", "botguard", "http error 403",
        "403: forbidden", "no formats", "no video formats",
        "requested format is not available", "no downloadable formats", "no playable formats", "sabr", "hls", "m3u8",
        "tidak mengembalikan format", "format yang dapat diunduh", "format non-hls", "berkas media tidak terbentuk",
        "not embeddable", "embedding disabled", "video unavailable", "this video is not available",
    ))


def _youtube_hls_format_selector(quality: str) -> str:
    """Prefer a muxed HLS format and never fall back to a silent video-only stream."""
    height_filter = f"[height<={quality}]" if quality not in {"max", "best"} else ""
    return (
        f"best[protocol^=m3u8]{height_filter}[vcodec!=none][acodec!=none]"
        f"/best[protocol^=hls]{height_filter}[vcodec!=none][acodec!=none]"
    )


def _youtube_info_has_downloadable_formats(
    info: dict, *, hls_only: bool = False, exclude_hls: bool = False
) -> bool:
    formats = _media_formats(info)
    for item in formats:
        if not isinstance(item, dict) or item.get("has_drm"):
            continue
        protocol = str(item.get("protocol") or "").lower()
        ext = str(item.get("ext") or "").lower()
        is_hls = "m3u8" in protocol or "hls" in protocol or ext == "m3u8"
        if hls_only and not is_hls:
            continue
        if exclude_hls and is_hls:
            continue
        if any(term in protocol for term in ("mhtml", "sabr")):
            continue
        if item.get("url") or item.get("manifest_url"):
            return True
    return False


def _yt_common_options(platform: str | None = None, youtube_client: str | None = None) -> dict:
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

    if platform == "youtube":
        provider = _youtube_provider_status()
        wpc = _wpc_status()
        provider_mode = os.getenv("YOUTUBE_POT_PROVIDER_MODE", "bgutil").strip().lower()
        wpc_selected = bool(wpc.get("runtimeEnabled") and provider_mode == "wpc")
        strategy = youtube_client or ("mweb" if (provider.get("ready") or wpc_selected) else "tv_samsung")
        client = "web_safari" if strategy == "web_safari_hls" else strategy
        youtube_args = {}
        # Use one explicit client per Petik attempt. The yt-dlp "default" alias
        # expands to multiple internal clients, which makes a precise stop on 429
        # harder to enforce. Petik owns the sequential fallback order instead.
        if client != "default":
            youtube_args["player_client"] = [client]
        youtube_args.update(_youtube_safe_extractor_overrides())
        extractor_args = {"youtube": youtube_args} if youtube_args else {}
        if "visitor_data" in youtube_args:
            extractor_args["youtubetab"] = {"skip": ["webpage"]}
        if wpc_selected and wpc.get("browserPath"):
            # WPC is an external yt-dlp PO Token provider; its browser binary is
            # explicit to avoid relying on environment-specific auto-discovery.
            extractor_args["youtubepot-wpc"] = {"browser_path": [wpc["browserPath"]]}
        elif provider.get("ready") and client in YOUTUBE_CLIENTS_WITH_PROVIDER:
            extractor_args.setdefault("youtube", {})
            extractor_args["youtubepot-bgutilhttp"] = {"base_url": [provider.get("clientBaseUrl") or provider["baseUrl"]]}
        if os.getenv("YOUTUBE_FORCE_IPV4", "1").strip() == "1":
            options["source_address"] = "0.0.0.0"
        cookie_file = _youtube_cookies_file()
        if cookie_file:
            options["cookiefile"] = cookie_file
        if YOUTUBE_SLEEP_INTERVAL_REQUESTS > 0:
            options["sleep_interval_requests"] = YOUTUBE_SLEEP_INTERVAL_REQUESTS
        impersonate = _youtube_impersonate_target()
        if impersonate:
            options["impersonate"] = impersonate
        if extractor_args:
            options["extractor_args"] = extractor_args
        options.update({
            "logger": _YtDlpDiagnosticLogger(client),
            "verbose": True,
            # Warnings are routed through the filtered logger above; raw yt-dlp
            # messages are never copied to Render logs.
            "no_warnings": False,
        })
    return options


def _extract_info(url: str) -> dict:
    platform = _normalise_and_validate(url)[1]
    if platform != "youtube":
        return _extract_info_impl(url)
    try:
        with YOUTUBE_ATTEMPT_LOCK:
            cached = _get_cached_youtube_info(url)
            if cached is not None:
                _update_youtube_diagnostics(infoCacheHit=True, lastOutcome="cache-hit", lastStage="inspect_cache")
                return cached
            _youtube_request_gate()
            info = _extract_info_impl(url)
            if isinstance(info, dict):
                _cache_youtube_info(url, info)
            return info
    finally:
        _cleanup_youtube_cookie_file()


def _extract_info_impl(url: str) -> dict:
    platform = _normalise_and_validate(url)[1]
    if platform != "youtube":
        options = _yt_common_options(platform)
        options.update({"skip_download": True, "extract_flat": False})
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            raise RuntimeError("Platform tidak mengembalikan informasi media.")
    else:
        provider = _youtube_provider_status()
        _refresh_youtube_provider_diagnostics(provider)
        preferred = _get_cached_youtube_client(url)
        wpc_ready = bool(
            _wpc_status().get("runtimeEnabled")
            and os.getenv("YOUTUBE_POT_PROVIDER_MODE", "bgutil").strip().lower() == "wpc"
        )
        clients = _youtube_client_candidates(bool(provider.get("ready")), preferred, wpc_ready)
        _begin_youtube_diagnostics_attempt(provider, "attempting")
        failures = []
        info = None
        selected_client = None
        last_exception = None
        previous_client = None
        previous_error_code = None
        for index, client in enumerate(clients):
            if client == "web_safari_hls" and _youtube_should_skip_hls_retry(previous_client, previous_error_code):
                continue
            _record_youtube_attempt(client, "attempting")
            _update_youtube_diagnostics(lastStrategy=client)
            options = _yt_common_options("youtube", client)
            diagnostic_logger = options.get("logger")
            options.update({"skip_download": True, "extract_flat": False})
            try:
                with YoutubeDL(options) as ydl:
                    candidate = ydl.extract_info(url, download=False)
                if not candidate:
                    raise RuntimeError("YouTube tidak mengembalikan informasi media.")
                hls_only = client == "web_safari_hls"
                if hls_only:
                    has_hls = _youtube_info_has_downloadable_formats(candidate, hls_only=True)
                    _update_youtube_diagnostics(hlsFormatsObserved=bool(has_hls), lastStrategy=client)
                    if not has_hls:
                        raise RuntimeError("web_safari tidak mengembalikan format HLS m3u8.")
                elif not _youtube_info_has_downloadable_formats(
                    candidate, exclude_hls=(client == "web_safari")
                ):
                    raise RuntimeError(f"YouTube client {client} tidak mengembalikan format non-HLS yang dapat diunduh.")
                info = candidate
                selected_client = client
                _record_youtube_attempt(client, "success")
                _cache_youtube_client(url, client)
                break
            except Exception as exc:
                last_exception = exc
                observed_429 = bool(getattr(diagnostic_logger, "http_429_seen", False))
                code = "http_429" if observed_429 else _youtube_error_code(exc)
                stage = "http_429" if observed_429 else getattr(diagnostic_logger, "last_stage", None)
                is_rate_limited = observed_429 or code == "http_429"
                if is_rate_limited:
                    _activate_youtube_cooldown(client, stage or "http_429")
                previous_client, previous_error_code = client, code
                failures.append(f"{client}:{code}")
                _record_youtube_attempt(client, "failed", code, stage=stage)
                if is_rate_limited or index >= len(clients) - 1 or not _youtube_should_fallback(exc):
                    break
                _youtube_fallback_pause()
        if info is None:
            detail = str(last_exception)[:240] if last_exception else "YouTube tidak mengembalikan media yang dapat diunduh."
            _finish_youtube_diagnostics(
                "failed", failures[-1].split(":", 1)[-1] if failures else "unknown"
            )
            raise RuntimeError(f"YouTube gagal pada client yang diuji ({', '.join(failures)}). {detail}")
        info = dict(info)
        info["_petik_youtube_client"] = selected_client

    if info.get("_type") in {"playlist", "multi_video"}:
        entries = [entry for entry in (info.get("entries") or []) if isinstance(entry, dict)]
        if not entries:
            raise RuntimeError("Postingan tidak memiliki media yang bisa diproses.")
        info = dict(entries[0])
        info["_petik_entries"] = entries[:MAX_GALLERY_SOURCE_ITEMS]
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
    image_extensions = {"jpg", "jpeg", "png", "webp", "gif", "avif", "heic"}
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
        if ext in image_extensions:
            pass
        elif ext in video_extensions or (not ext and platform != "soundcloud"):
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
    """Accept only public HTTP(S) media URLs suitable for browser direct links."""
    if not isinstance(value, str) or len(value) > 8192:
        return None
    try:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".test")):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return None
        return value.strip()
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


def _direct_media_urls(info: dict | None, platform: str) -> dict[str, str | None]:
    """Return independently usable progressive media URLs, even if Petik's save pipeline later fails."""
    if not isinstance(info, dict):
        return {"video": None, "audio": None}

    video_url = _preview_url(info, platform)
    audio_candidates: list[tuple[int, str]] = []
    audio_extensions = {"mp3", "m4a", "aac", "wav", "flac", "ogg", "opus", "weba", "webm"}
    for fmt in _media_formats(info):
        direct = _safe_preview_url(fmt.get("url"))
        if not direct:
            continue
        ext = str(fmt.get("ext") or "").lower()
        protocol = str(fmt.get("protocol") or "").lower()
        if ext in {"m3u8", "mpd"} or "m3u8" in protocol or "dash" in protocol:
            continue
        acodec, vcodec = fmt.get("acodec"), fmt.get("vcodec")
        audio_only = _is_none_codec(vcodec) and _codec_present(acodec)
        if not audio_only and vcodec is None and ext in audio_extensions:
            audio_only = True
        if audio_only and (ext in audio_extensions or not ext):
            rank = 0 if ext in {"mp3", "m4a"} else 1
            audio_candidates.append((rank, direct))

    if not audio_candidates:
        root_url = _safe_preview_url(info.get("url"))
        ext = str(info.get("ext") or "").lower()
        if root_url and ext in audio_extensions and (info.get("vcodec") is None or _is_none_codec(info.get("vcodec"))):
            audio_candidates.append((0 if ext in {"mp3", "m4a"} else 1, root_url))

    audio_url = sorted(audio_candidates, key=lambda item: item[0])[0][1] if audio_candidates else None
    return {"video": video_url, "audio": audio_url}


def _safe_thumbnail(info: dict) -> str | None:
    thumb = info.get("thumbnail")
    if not isinstance(thumb, str):
        return None
    try:
        parsed = urlparse(thumb)
        return thumb if parsed.scheme in {"http", "https"} and parsed.hostname else None
    except Exception:
        return None


def _gallery_command() -> str | None:
    return shutil.which("gallery-dl")


def _image_extension_from_url(value: str) -> str | None:
    """Resolve common image extensions from a CDN path or signed query string."""
    parsed = urlparse(value)
    suffix = Path(unquote(parsed.path)).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return suffix

    query = parse_qs(parsed.query, keep_blank_values=False)
    for key in ("format", "fm", "ext", "extension", "image_format", "mime", "mime_type", "content_type", "filename", "file", "name"):
        for raw in query.get(key, []):
            candidate = unquote(str(raw)).lower().strip()
            if "image/" in candidate:
                candidate = candidate.split("image/", 1)[1].split(";", 1)[0]
            candidate = candidate.split("?", 1)[0].split("#", 1)[0]
            ext = Path(candidate).suffix.lower()
            if ext in IMAGE_EXTENSIONS:
                return ext
            candidate = candidate.lstrip(".")
            if f".{candidate}" in IMAGE_EXTENSIONS:
                return f".{candidate}"
            if candidate in {item.lstrip(".") for item in IMAGE_EXTENSIONS}:
                return f".{candidate}"
    return None


VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".3gp", ".ts", ".m3u8", ".mpd"}
DIRECT_VIDEO_EXTENSIONS = VIDEO_EXTENSIONS - {".ts", ".m3u8", ".mpd"}


def _is_direct_video_url(value: str) -> bool:
    extension = _video_extension_from_url(value)
    return extension in DIRECT_VIDEO_EXTENSIONS if extension else False


def _video_extension_from_url(value: str) -> str | None:
    try:
        parsed = urlparse(value)
        suffix = Path(unquote(parsed.path)).suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            return suffix
        query = parse_qs(parsed.query, keep_blank_values=False)
        for key in ("format", "fm", "ext", "extension", "mime", "mime_type", "content_type", "filename", "file", "name"):
            for raw in query.get(key, []):
                token = unquote(str(raw)).lower().strip().split(";", 1)[0]
                if token.startswith("video/"):
                    token = token.split("/", 1)[1]
                if token in {item.lstrip(".") for item in VIDEO_EXTENSIONS}:
                    return f".{token}"
                suffix = Path(token.split("?", 1)[0]).suffix.lower()
                if suffix in VIDEO_EXTENSIONS:
                    return suffix
    except Exception:
        return None
    return None


def _media_url_key(value: str) -> str:
    parsed = urlparse(value)
    return f"{(parsed.hostname or '').lower()}{unquote(parsed.path).lower()}"


def _normalise_media_items(items: list[dict]) -> list[dict]:
    """Merge extractor manifests, deduplicate CDN URLs and assign stable ordered IDs."""
    unique: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        item_type = item.get("type") or ("video" if item.get("kind") == "video" else "photo")
        if item_type not in {"photo", "video"}:
            continue
        value = item["url"]
        if urlparse(value).scheme != "https" or not _safe_preview_url(value):
            continue
        key = _media_url_key(value)
        if key not in unique:
            unique[key] = {**item, "type": item_type, "kind": "video" if item_type == "video" else "image"}
        else:
            old = unique[key]
            if not old.get("thumbnail") and item.get("thumbnail"):
                old["thumbnail"] = item["thumbnail"]
            if not old.get("duration") and item.get("duration"):
                old["duration"] = item["duration"]
    ordered = sorted(unique.values(), key=lambda item: int(item.get("sourceIndex") or item.get("id") or 999999))
    result: list[dict] = []
    counters = {"photo": 0, "video": 0}
    for index, item in enumerate(ordered[:MAX_GALLERY_ITEMS], 1):
        item_type = item["type"]
        counters[item_type] += 1
        prefix = "video" if item_type == "video" else "foto"
        extension = str(item.get("extension") or ("mp4" if item_type == "video" else "jpg")).lstrip(".")
        item["id"] = index
        item["index"] = index
        item["extension"] = extension
        item["filename"] = f"{prefix}-{counters[item_type]:03d}.{extension}"
        item["label"] = f"{prefix.title()} {counters[item_type]}"
        item["directUrl"] = _safe_preview_url(item.get("directUrl")) or item["url"]
        result.append(item)
    return result


def _media_photo_alias(items: list[dict]) -> list[dict]:
    """Legacy photoItems contract: photo-only IDs remain compact and sequential."""
    photos = [dict(item) for item in items if item.get("type") == "photo"]
    for index, item in enumerate(photos, 1):
        item["mediaIndex"] = item.get("id")
        item["id"] = index
        item["label"] = f"Foto {index}"
        item["filename"] = f"foto-{index:03d}.{item.get('extension') or 'jpg'}"
    return photos


def _parse_gallery_media_urls(output: str) -> list[dict]:
    """Parse ordered gallery-dl URLs for supported image and video attachments."""
    found: list[dict] = []
    seen: set[str] = set()
    source_index = 0
    for raw in (output or "").splitlines():
        value = raw.strip().strip('"').strip("'")
        if not value.startswith(("https://", "http://")):
            continue
        source_index += 1
        if source_index > MAX_GALLERY_SOURCE_ITEMS:
            break
        try:
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                continue
            image_ext = _image_extension_from_url(value)
            video_ext = _video_extension_from_url(value)
            if image_ext:
                item_type, suffix = "photo", image_ext
            elif video_ext and video_ext not in {".m3u8", ".mpd", ".ts"}:
                item_type, suffix = "video", video_ext
            else:
                continue
            key = _media_url_key(value)
            if key in seen:
                continue
            seen.add(key)
            prefix = "video" if item_type == "video" else "foto"
            count = sum(1 for item in found if item["type"] == item_type) + 1
            filename = Path(unquote(parsed.path)).name
            if not Path(filename).suffix:
                filename = f"{prefix}-{count:03d}{suffix}"
            found.append({
                "id": len(found) + 1, "sourceIndex": source_index, "type": item_type,
                "kind": "video" if item_type == "video" else "image", "url": value, "directUrl": value,
                "thumbnail": value if item_type == "photo" else None, "extension": suffix.lstrip("."),
                "filename": filename[:160], "label": f"{prefix.title()} {count}",
            })
        except Exception:
            continue
        if len(found) >= MAX_GALLERY_ITEMS:
            break
    return _normalise_media_items(found)


def _parse_gallery_urls(output: str) -> list[dict]:
    """Legacy photo-only parser retaining gallery-dl's original source numbers."""
    media = _parse_gallery_media_urls(output)
    photos = [dict(item) for item in media if item.get("type") == "photo"]
    for item in photos:
        item["id"] = int(item.get("sourceIndex") or item.get("id") or 0)
        item["kind"] = "image"
        item.pop("type", None)
    return photos


def _gallery_list_media(url: str, platform: str) -> list[dict]:
    """Enumerate one ordered, mixed gallery-dl manifest for a public post."""
    if platform not in GALLERY_PLATFORMS:
        return []
    executable = _gallery_command()
    if not executable:
        return []
    command = [executable, "--config-ignore", "--no-input", "--no-colors", "--quiet",
               "--range", f"1-{MAX_GALLERY_SOURCE_ITEMS}", "--get-urls", "--", url]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=40, check=False)
        if result.returncode not in (0, 1):
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            if detail:
                print(f"[gallery-dl:{platform}] {detail[-1][:300]}")
            return []
        return _parse_gallery_media_urls(result.stdout)
    except subprocess.TimeoutExpired:
        print(f"[gallery-dl:{platform}] timeout after 40 seconds")
        return []
    except OSError as exc:
        print(f"[gallery-dl:{platform}] unavailable: {exc}")
        return []


def _gallery_list_images(url: str, platform: str) -> list[dict]:
    """Backward-compatible photo-only view over the ordered mixed manifest."""
    return [dict(item) for item in _gallery_list_media(url, platform) if item.get("type") == "photo"]


def _gallery_list_videos(url: str, platform: str) -> list[dict]:
    """Backward-compatible video-only view over the ordered mixed manifest."""
    return [dict(item) for item in _gallery_list_media(url, platform) if item.get("type") == "video"]


def _gallery_source_indices(media_items: list[dict], selected_ids: list[int]) -> list[int]:
    """Translate UI's sequential mixed-media IDs to gallery-dl's source item numbers."""
    lookup = {int(item.get("id")): int(item.get("sourceIndex") or item.get("id"))
              for item in media_items if isinstance(item, dict) and item.get("id") is not None}
    if any(item_id not in lookup for item_id in selected_ids):
        raise RuntimeError("Pilihan media tidak cocok dengan daftar sumber. Ambil ulang tautannya.")
    return [lookup[item_id] for item_id in selected_ids]


def _gallery_dl_indices_for_items(url: str, platform: str, selected_items: list[dict]) -> list[int]:
    """Map selected CDN objects back to gallery-dl item numbers for safe fallback."""
    manifest = _gallery_list_media(url, platform)
    by_url = {_media_url_key(str(item.get("url") or "")): int(item.get("sourceIndex") or item.get("id") or 0)
              for item in manifest}
    mapped = []
    for item in selected_items:
        key = _media_url_key(str(item.get("url") or ""))
        source_index = by_url.get(key)
        if not source_index:
            raise RuntimeError("Extractor cadangan tidak menemukan media terpilih yang sama. Ambil ulang tautannya.")
        mapped.append(source_index)
    return mapped


def _gallery_download_failures(media_items: list[dict], selected_ids: list[int], source_indices: list[int], files: list[Path]) -> list[dict]:
    """Report partial gallery-dl results by matching the source number in each filename."""
    downloaded_sources: set[int] = set()
    for file_path in files:
        match = re.match(r"^0*(\d+)(?:[._-]|$)", file_path.name)
        if match:
            downloaded_sources.add(int(match.group(1)))
    by_id = {int(item.get("id")): item for item in media_items
             if isinstance(item, dict) and item.get("id") is not None}
    failures: list[dict] = []
    for media_id, source_index in zip(selected_ids, source_indices):
        if source_index in downloaded_sources:
            continue
        item = by_id.get(media_id, {})
        media_type = item.get("type") or ("video" if item.get("kind") == "video" else "photo")
        failures.append({
            "id": media_id,
            "type": media_type,
            "message": "Extractor tidak mengembalikan berkas untuk item ini.",
        })
    return failures

def _safe_social_media_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        return bool(
            parsed.scheme == "https" and host and not parsed.username and not parsed.password
            and any(host.endswith(suffix) for suffix in SOCIAL_MEDIA_SUFFIXES)
            and (parsed.port is None or parsed.port == 443)
        )
    except Exception:
        return False


def _instagram_shortcode_to_media_id(shortcode: str) -> str:
    """Convert an Instagram shortcode to its numeric media ID (base64 alphabet)."""
    if len(shortcode) > 28:
        shortcode = shortcode[:-28]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    value = 0
    for char in shortcode:
        try:
            digit = alphabet.index(char)
        except ValueError as exc:
            raise ValueError("Kode postingan Instagram tidak valid.") from exc
        value = value * 64 + digit
    if value <= 0:
        raise ValueError("Kode postingan Instagram tidak valid.")
    return str(value)


def _instagram_best_image(node: dict) -> str | None:
    image_url = node.get("display_url") or node.get("display_src") or node.get("thumbnail_src")
    if isinstance(image_url, str) and _safe_social_media_url(image_url):
        return image_url
    versions = node.get("image_versions2")
    candidates = versions.get("candidates") if isinstance(versions, dict) else []
    if not isinstance(candidates, list):
        candidates = []
    candidates = [item for item in candidates if isinstance(item, dict)
                  and isinstance(item.get("url"), str) and _safe_social_media_url(item["url"])]
    resources = node.get("display_resources")
    if not candidates and isinstance(resources, list):
        candidates = [{"url": item.get("src"), "width": item.get("config_width") or item.get("width") or 0,
                       "height": item.get("config_height") or item.get("height") or 0}
                      for item in resources if isinstance(item, dict) and isinstance(item.get("src"), str)
                      and _safe_social_media_url(item["src"])]
    best = max(candidates, key=lambda item: int(item.get("width") or 0) * int(item.get("height") or 0)) if candidates else None
    return best.get("url") if best else None


def _instagram_best_video(node: dict) -> str | None:
    candidates = node.get("video_versions")
    if isinstance(candidates, list):
        candidates = [item for item in candidates if isinstance(item, dict)
                      and isinstance(item.get("url"), str) and _safe_social_media_url(item["url"])
                      and _is_direct_video_url(item["url"])]
        best = max(candidates, key=lambda item: (
            int(item.get("width") or 0) * int(item.get("height") or 0),
            int(item.get("type") or 0), int(item.get("bitrate") or 0)
        )) if candidates else None
        if best:
            return best["url"]
    for key in ("video_url", "url"):
        value = node.get(key)
        if isinstance(value, str) and _safe_social_media_url(value) and _is_direct_video_url(value):
            return value
    return None


def _instagram_media_items(media: dict | list) -> list[dict]:
    """Normalize logged-out Instagram post/carousel data without dropping videos."""
    if isinstance(media, list):
        nodes = media
    elif isinstance(media, dict) and isinstance(media.get("carousel_media"), list):
        nodes = media["carousel_media"]
    else:
        sidecar = media.get("edge_sidecar_to_children") if isinstance(media, dict) else None
        edges = sidecar.get("edges") if isinstance(sidecar, dict) else None
        nodes = [edge.get("node") for edge in edges if isinstance(edge, dict) and isinstance(edge.get("node"), dict)] if isinstance(edges, list) else [media]

    items: list[dict] = []
    seen: set[str] = set()
    for source_index, node in enumerate(nodes, 1):
        if not isinstance(node, dict):
            continue
        is_video = (node.get("is_video") is True or str(node.get("media_type") or "") == "2"
                    or bool(node.get("video_versions") or node.get("video_url")))
        if is_video:
            media_url = _instagram_best_video(node)
            if not media_url:
                # A video poster is decorative; do not expose it as a photo.
                continue
            extension = _video_extension_from_url(media_url) or ".mp4"
            thumbnail = _instagram_best_image(node) or node.get("thumbnail_url")
            item_type = "video"
        else:
            media_url = _instagram_best_image(node)
            if not media_url:
                continue
            extension = _image_extension_from_url(media_url) or ".jpg"
            thumbnail = media_url
            item_type = "photo"
        canonical = _media_url_key(media_url)
        if canonical in seen:
            continue
        seen.add(canonical)
        count = sum(1 for item in items if item["type"] == item_type) + 1
        prefix = "video" if item_type == "video" else "foto"
        filename = f"{prefix}-{count:03d}{extension}"
        items.append({
            "id": len(items) + 1, "sourceIndex": source_index, "type": item_type,
            "kind": "video" if item_type == "video" else "image",
            "url": media_url, "directUrl": media_url,
            "thumbnail": thumbnail if isinstance(thumbnail, str) and _safe_social_media_url(thumbnail) else None,
            "extension": extension.lstrip("."), "filename": filename,
            "label": f"{prefix.title()} {count}",
            "duration": node.get("video_duration") or node.get("duration") if item_type == "video" else None,
        })
        if len(items) >= MAX_GALLERY_ITEMS:
            break
    return items


def _instagram_photo_items(media: dict | list) -> list[dict]:
    """Backward-compatible photo-only view of the Instagram mixed-media parser."""
    photos = [dict(item) for item in _instagram_media_items(media) if item.get("type") == "photo"]
    for index, item in enumerate(photos, 1):
        item["mediaIndex"] = item.get("id")
        item["id"] = index
        item["label"] = f"Foto {index}"
        item["filename"] = f"foto-{index:03d}.{item.get('extension') or 'jpg'}"
    return photos


def _instagram_find_product_info(root: object) -> dict | list | None:
    """Walk Instagram page JSON and locate the logged-out product payload."""
    stack = [root]
    visited = 0
    while stack and visited < 200_000:
        value = stack.pop()
        visited += 1
        if isinstance(value, dict):
            media = value.get("xig_polaris_media")
            if isinstance(media, dict):
                product = media.get("if_not_gated_logged_out")
                if isinstance(product, (dict, list)):
                    return product
            # Legacy/older page JSON variants.
            for key in ("xdt_shortcode_media", "shortcode_media"):
                candidate = value.get(key)
                if isinstance(candidate, dict):
                    return candidate
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return None



def _is_meta_page_url(value: str, platform: str) -> bool:
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.port not in (None, 443):
            return False
        if platform == "instagram":
            return host == "instagram.com" or host.endswith(".instagram.com")
        return host in {"facebook.com", "fb.com", "fb.watch", "fb.me"} or host.endswith(".facebook.com")
    except Exception:
        return False


def _curl_meta_page_get(session, url: str, *, headers: dict, timeout: int, platform: str, params: dict | None = None):
    """Fetch an Instagram/Facebook page while restricting redirects to that platform."""
    current = url
    current_params = params
    for _ in range(5):
        if not _is_meta_page_url(current, platform):
            raise RuntimeError("Pengalihan halaman Meta ke host di luar platform ditolak.")
        response = session.get(current, headers=headers, timeout=timeout, params=current_params, allow_redirects=False)
        current_params = None
        if response.status_code in (301, 302, 303, 307, 308):
            location = getattr(response, "headers", {}).get("location")
            if not location:
                raise RuntimeError("Halaman Meta mengirim pengalihan tanpa alamat tujuan.")
            target = urljoin(current, location)
            if not _is_meta_page_url(target, platform):
                raise RuntimeError("Pengalihan halaman Meta ke host di luar platform ditolak.")
            current = target
            continue
        final_url = str(getattr(response, "url", current))
        if not _is_meta_page_url(final_url, platform):
            raise RuntimeError("Host akhir halaman Meta tidak termasuk daftar yang diizinkan.")
        return response
    raise RuntimeError("Terlalu banyak pengalihan halaman Meta.")

def _instagram_public_media(url: str) -> list[dict]:
    """Best-effort logged-out Instagram resolver returning ordered photos and videos."""
    if not HAS_CURL_CFFI or curl_requests is None:
        return []
    match = re.search(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", url)
    if not match:
        return []
    shortcode = match.group(1)
    try:
        media_id = _instagram_shortcode_to_media_id(shortcode)
    except ValueError:
        return []

    session = None
    try:
        # curl_cffi accepts impersonation in Session(...); assigning a property later
        # does not configure the underlying TLS/browser fingerprint.
        session = curl_requests.Session(impersonate="chrome")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        home = _curl_meta_page_get(session, "https://www.instagram.com/", headers=headers, timeout=14, platform="instagram")
        home_html = str(getattr(home, "text", "") or "")
        page = _curl_meta_page_get(session, url, headers=headers, timeout=14, platform="instagram")
        if page.status_code != 200:
            print(f"[instagram-public-gallery] post page HTTP {page.status_code}")
            return []
        page_html = str(getattr(page, "text", "") or "")
        combined_html = home_html + "\n" + page_html

        # The current logged-out query initializes its LSD token on the home page.
        lsd = ""
        eqmc = re.search(r'<script\b[^>]*\bid=["\']__eqmc["\'][^>]*>(.*?)</script>', combined_html, re.I | re.S)
        if eqmc:
            try:
                eqmc_data = json.loads(eqmc.group(1))
                lsd = str(eqmc_data.get("l") or "") if isinstance(eqmc_data, dict) else ""
            except Exception:
                pass
        if not lsd:
            lsd_match = re.search(r'\["LSD",\[\],\{"token":"([^"\\]+)', combined_html)
            lsd = lsd_match.group(1) if lsd_match else ""

        try:
            csrf = session.cookies.get("csrftoken")
        except Exception:
            csrf = None
        api_check = None
        try:
            api_check = _curl_meta_page_get(
                session,
                "https://www.instagram.com/api/v1/web/get_ruling_for_content/",
                params={"content_type": "MEDIA", "target_id": media_id},
                headers={**headers, "X-IG-App-ID": "936619743392459", "X-ASBD-ID": "359341",
                         "X-IG-WWW-Claim": "0", "Origin": "https://www.instagram.com", "Referer": url}, timeout=12, platform="instagram",
            )
        except Exception as exc:
            print(f"[instagram-public-gallery] access check: {str(exc)[:180]}")
        if api_check is not None and api_check.status_code == 200:
            try:
                if (api_check.json() or {}).get("status") != "ok":
                    csrf = None
            except Exception:
                csrf = None

        if lsd:
            try:
                response = session.post(
                    "https://www.instagram.com/api/graphql",
                    data={
                        "lsd": lsd,
                        "fb_api_caller_class": "RelayModern",
                        "fb_api_req_friendly_name": "PolarisLoggedOutDesktopWWWPostRootContentQuery",
                        "server_timestamps": "true",
                        "variables": json.dumps({"media_id": media_id}, separators=(",", ":")),
                        "doc_id": INSTAGRAM_GRAPHQL_DOC_ID,
                    },
                    headers={
                        **headers,
                        "X-IG-App-ID": "936619743392459",
                        "X-ASBD-ID": "359341",
                        "X-IG-WWW-Claim": "0",
                        "Origin": "https://www.instagram.com",
                        "X-FB-Friendly-Name": "PolarisLoggedOutDesktopWWWPostRootContentQuery",
                        "X-CSRFToken": csrf or "",
                        "X-FB-LSD": lsd,
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": url,
                        "Accept": "*/*",
                    }, timeout=14, allow_redirects=False,
                )
                if response.status_code == 200 and _is_meta_page_url(str(getattr(response, "url", "https://www.instagram.com/api/graphql")), "instagram"):
                    payload = response.json()
                    product = _instagram_find_product_info(payload)
                    if product:
                        media_items = _instagram_media_items(product)
                        if media_items:
                            return media_items
            except Exception as exc:
                print(f"[instagram-public-gallery] GraphQL: {str(exc)[:240]}")

        # The page itself can contain the same prefetched JSON when GraphQL is disabled.
        for raw_json in re.findall(r'<script\b[^>]*\bdata-sjs[^>]*>(\{.+?\})</script>', page_html, re.I | re.S):
            try:
                product = _instagram_find_product_info(json.loads(html_lib.unescape(raw_json)))
                if product:
                    media_items = _instagram_media_items(product)
                    if media_items:
                        return media_items
            except Exception:
                continue
        print("[instagram-public-gallery] no public mixed-media payload was exposed")
        return []
    except Exception as exc:
        print(f"[instagram-public-gallery] {str(exc)[:240]}")
        return []
    finally:
        try:
            if session is not None:
                session.close()
        except Exception:
            pass


def _instagram_public_gallery(url: str) -> list[dict]:
    """Backward-compatible Instagram photo-only gallery resolver."""
    return [item for item in _instagram_public_media(url) if item.get("type") == "photo"]


def _facebook_target_ids(value: str) -> set[str]:
    """Return post/photo identifiers explicitly present in a Facebook URL."""
    try:
        parsed = urlparse(value)
    except Exception:
        return set()
    query = parse_qs(parsed.query, keep_blank_values=False)
    identifiers: set[str] = set()
    for key in ("fbid", "story_fbid", "post_id", "video_id", "media_id", "photo_id", "v"):
        for item in query.get(key, []):
            item = str(item).strip()
            if item and len(item) <= 160:
                identifiers.add(item)
    parts = [part for part in parsed.path.split("/") if part]
    for index, part in enumerate(parts[:-1]):
        if part.lower() in {"posts", "videos", "photos", "permalink", "reel", "reels"}:
            candidate = parts[index + 1].split("?", 1)[0]
            if candidate and len(candidate) <= 160:
                identifiers.add(candidate)
    return identifiers


def _facebook_photo_url_from_node(node: dict) -> str | None:
    """Read only the image URL belonging to a structured Facebook Photo node."""
    image = node.get("image")
    candidates = []
    if isinstance(image, dict):
        candidates.extend([image.get("uri"), image.get("url"), image.get("src")])
    elif isinstance(image, str):
        candidates.append(image)
    for key in ("photo_image", "thumbnail", "preferred_thumbnail"):
        value = node.get(key)
        if isinstance(value, dict):
            candidates.extend([value.get("uri"), value.get("url"), value.get("src")])
        elif isinstance(value, str):
            candidates.append(value)
    for value in candidates:
        if isinstance(value, str) and _safe_social_media_url(value) and _image_extension_from_url(value):
            return value
    return None


def _facebook_collect_target_attachment_photos(target: dict) -> list[str]:
    """Walk only the attachment subtree of a URL-matched post/photo object."""
    relation_keys = {
        "attachments", "all_subattachments", "nodes", "edges", "media", "attachment",
        "style_type_renderer", "styles", "target", "target_group", "comet_sections",
        "content", "story", "attached_story", "image", "photo",
    }
    photos: list[str] = []
    seen: set[str] = set()
    stack: list[object] = [target]
    visited = 0
    while stack and visited < 100_000 and len(photos) < MAX_GALLERY_ITEMS:
        value = stack.pop()
        visited += 1
        if isinstance(value, dict):
            typename = str(value.get("__typename") or "")
            is_photo = typename == "Photo" or value.get("__isMedia") == "Photo"
            if is_photo:
                image_url = _facebook_photo_url_from_node(value)
                if image_url:
                    canonical = image_url.split("?", 1)[0]
                    if canonical not in seen:
                        seen.add(canonical)
                        photos.append(image_url)
                continue
            for key in relation_keys:
                child = value.get(key)
                if isinstance(child, (dict, list)):
                    stack.append(child)
        elif isinstance(value, list):
            stack.extend(value)
    return list(reversed(photos))


def _facebook_photo_items_from_html(page_html: str, source_url: str | None = None) -> list[dict]:
    """Extract only post-scoped Facebook photos; never scrape arbitrary page CDN URLs.

    Open Graph images are authoritative for the requested page. Additional album
    images are accepted only from a structured Photo attachment subtree whose
    owning post/photo identifier matches the supplied URL or its canonical og:url.
    Broad URL harvesting is intentionally forbidden because Facebook pages also
    embed recommendations, profile pictures, reaction assets, and unrelated cards.
    """
    if not isinstance(page_html, str) or not page_html:
        return []
    html_text = html_lib.unescape(page_html)
    html_text = (html_text.replace(r"\/", "/").replace(r"\u0026", "&")
                 .replace(r"\u003d", "=").replace(r"\u003A", ":").replace(r"\u003a", ":"))

    target_ids = _facebook_target_ids(source_url or "")
    candidates: list[str] = []
    canonical_urls: list[str] = []
    meta_entries: list[tuple[str, str]] = []

    for tag in re.findall(r"<meta\b[^>]*>", html_text, re.I):
        prop = re.search(r"\b(?:property|name)\s*=\s*([\"'])(.*?)\1", tag, re.I)
        content = re.search(r"\bcontent\s*=\s*([\"'])(.*?)\1", tag, re.I)
        if not (prop and content):
            continue
        meta_name = prop.group(2).lower()
        meta_value = html_lib.unescape(content.group(2)).replace("&amp;", "&")
        meta_entries.append((meta_name, meta_value))
        if meta_name in {"og:url", "twitter:url"}:
            canonical_urls.append(meta_value)

    source_parsed = urlparse(source_url or "")
    source_query = parse_qs(source_parsed.query)
    is_video_page = (
        source_parsed.path.lower().startswith(("/watch", "/video.php"))
        or "/videos/" in source_parsed.path.lower()
        or bool(source_query.get("v") or source_query.get("video_id"))
        or any(name in {"og:video", "og:video:url", "og:video:secure_url", "twitter:player"}
               for name, _value in meta_entries)
        or any(name == "og:type" and value.lower().startswith("video") for name, value in meta_entries)
    )
    for meta_name, meta_value in meta_entries:
        if meta_name in {"og:image", "og:image:url", "twitter:image"} and not is_video_page:
            candidates.append(meta_value)

    for canonical_url in canonical_urls:
        target_ids.update(_facebook_target_ids(canonical_url))

    # Search only JSON script payloads, then bind photo nodes to a post/photo object
    # that carries an identifier found in the requested URL. Do not scrape all URLs.
    script_pattern = re.compile(
        r"<script\b(?=[^>]*(?:\btype\s*=\s*[\"']application/json[\"']|\bdata-sjs\b))[^>]*>(.*?)</script>",
        re.I | re.S,
    )
    id_keys = ("id", "post_id", "postID", "story_id", "story_fbid", "legacy_story_id", "top_level_post_id", "fbid")
    for script in script_pattern.finditer(html_text):
        raw = script.group(1).strip()
        if raw.startswith("for (;;);"):
            raw = raw[len("for (;;);"):]
        try:
            root = json.loads(raw)
        except Exception:
            continue
        stack = [root]
        visited = 0
        while stack and visited < 200_000:
            value = stack.pop()
            visited += 1
            if isinstance(value, dict):
                values = {str(value.get(key)).strip() for key in id_keys if value.get(key) is not None}
                if target_ids and values.intersection(target_ids):
                    candidates.extend(_facebook_collect_target_attachment_photos(value))
                # Broad traversal is used only to locate the target's ID-bearing
                # node; actual media collection remains restricted to that node's
                # attachment subtree above.
                stack.extend(child for child in value.values() if isinstance(child, (dict, list)))
            elif isinstance(value, list):
                stack.extend(value)

    photos: list[dict] = []
    seen: set[str] = set()
    for value in candidates:
        value = str(value).replace("&amp;", "&").strip()
        if not _safe_social_media_url(value):
            continue
        suffix = _image_extension_from_url(value)
        if not suffix:
            continue
        parsed = urlparse(value)
        dedupe = f"{(parsed.hostname or '').lower()}{unquote(parsed.path).lower()}"
        if dedupe in seen:
            continue
        seen.add(dedupe)
        filename = Path(unquote(parsed.path)).name
        if not Path(filename).suffix:
            filename = f"foto-{len(photos) + 1:03d}{suffix}"
        photos.append({
            "id": len(photos) + 1, "url": value, "thumbnail": value,
            "extension": suffix.lstrip("."), "filename": filename[:160],
            "label": f"Foto {len(photos) + 1}", "kind": "image",
        })
        if len(photos) >= MAX_GALLERY_ITEMS:
            break
    return photos

def _facebook_public_gallery(url: str) -> list[dict]:
    """Best-effort logged-out Facebook post/photo page parser; no account cookies."""
    if not HAS_CURL_CFFI or curl_requests is None:
        return []
    session = None
    try:
        session = curl_requests.Session(impersonate="chrome")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        response = _curl_meta_page_get(session, url, headers=headers, timeout=16, platform="facebook")
        final_url = str(getattr(response, "url", url))
        if response.status_code != 200:
            print(f"[facebook-public-gallery] page HTTP {response.status_code}")
            return []
        page_html = str(getattr(response, "text", "") or "")
        if "login/?" in final_url.lower() or "login" in page_html[:5000].lower() and "og:image" not in page_html[:5000].lower():
            print("[facebook-public-gallery] Facebook returned a login wall")
            return []
        photos = _facebook_photo_items_from_html(page_html, url)
        if not photos:
            print("[facebook-public-gallery] no public CDN image URLs found")
        return photos
    except Exception as exc:
        print(f"[facebook-public-gallery] {str(exc)[:240]}")
        return []
    finally:
        try:
            if session is not None:
                session.close()
        except Exception:
            pass

def _safe_threads_media_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        return bool(
            parsed.scheme == "https" and host and not parsed.username and not parsed.password
            and any(host.endswith(suffix) for suffix in THREADS_MEDIA_SUFFIXES)
            and (parsed.port is None or parsed.port == 443)
        )
    except Exception:
        return False


def _threads_page_shortcode(url: str, page_html: str) -> str | None:
    match = re.search(r"/@[^/]+/post/([A-Za-z0-9_-]+)", urlparse(url).path)
    if match:
        return match.group(1)
    # /share/ links usually reveal the canonical /@user/post/CODE URL in og:url.
    for match in re.finditer(r'<meta\b[^>]*(?:property|name)=["\']og:url["\'][^>]*>', page_html, re.I):
        content = re.search(r'content=["\']([^"\']+)', match.group(0), re.I)
        if content:
            canonical = html_lib.unescape(content.group(1))
            code_match = re.search(r"/@[^/]+/post/([A-Za-z0-9_-]+)", urlparse(canonical).path)
            if code_match:
                return code_match.group(1)
    return None


def _threads_collect_post_dicts(page_html: str) -> list[dict]:
    """Read public post JSON blocks; fail closed when the page layout changes."""
    scripts = re.finditer(
        r'<script\b(?=[^>]*\btype=["\']application/json["\'])[^>]*>(.*?)</script>',
        page_html, re.I | re.S,
    )
    posts: list[dict] = []
    media_keys = {"video_versions", "video_dash_manifest", "image_versions2", "carousel_media"}
    node_count = 0
    for script in scripts:
        try:
            root = json.loads(html_lib.unescape(script.group(1)))
        except Exception:
            continue
        stack = [root]
        while stack and node_count < 200_000:
            item = stack.pop()
            node_count += 1
            if isinstance(item, dict):
                if isinstance(item.get("code"), str) and media_keys.intersection(item):
                    posts.append(item)
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    return posts


def _threads_best_image(media: dict) -> str | None:
    versions = media.get("image_versions2")
    candidates = versions.get("candidates") if isinstance(versions, dict) else None
    if not isinstance(candidates, list):
        return None
    valid: list[tuple[int, str]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_url = candidate.get("url")
        if not isinstance(candidate_url, str) or not _safe_threads_media_url(candidate_url):
            continue
        width = candidate.get("width") if isinstance(candidate.get("width"), (int, float)) else 0
        height = candidate.get("height") if isinstance(candidate.get("height"), (int, float)) else 0
        valid.append((int(width * height), candidate_url))
    return max(valid, default=(0, None), key=lambda pair: pair[0])[1]


def _threads_media_items(url: str, page_html: str) -> list[dict]:
    """Return the target Threads post's ordered photo/video attachments."""
    posts = _threads_collect_post_dicts(page_html)
    target = _threads_page_shortcode(url, page_html)
    selected = next((post for post in posts if post.get("code") == target), None) if target else None
    if selected is None and not target and len(posts) == 1:
        selected = posts[0]
    if selected is None:
        return []

    media_entries = selected.get("carousel_media")
    if not isinstance(media_entries, list) or not media_entries:
        media_entries = [selected]

    items: list[dict] = []
    seen: set[str] = set()
    for source_index, media in enumerate(media_entries, 1):
        if not isinstance(media, dict):
            continue
        versions = media.get("video_versions")
        is_video = (isinstance(versions, list) and bool(versions)) or str(media.get("media_type") or "") == "2" or bool(media.get("video_url"))
        if is_video:
            candidates = [item for item in (versions or []) if isinstance(item, dict)
                          and isinstance(item.get("url"), str) and _safe_threads_media_url(item["url"])
                          and _is_direct_video_url(item["url"])]
            best = max(candidates, key=lambda item: (
                int(item.get("width") or 0) * int(item.get("height") or 0), int(item.get("type") or 0)
            )) if candidates else None
            media_url = best.get("url") if best else media.get("video_url")
            if not isinstance(media_url, str) or not _safe_threads_media_url(media_url) or not _is_direct_video_url(media_url):
                continue  # Never mislabel a video's poster as a photo.
            item_type = "video"
            extension = _video_extension_from_url(media_url)
            thumbnail = _threads_best_image(media)
        else:
            media_url = _threads_best_image(media)
            item_type = "photo"
            extension = _image_extension_from_url(media_url) if media_url else None
            thumbnail = media_url
        if not media_url or not _safe_threads_media_url(media_url):
            continue
        canonical = _media_url_key(media_url)
        if canonical in seen:
            continue
        seen.add(canonical)
        count = sum(1 for item in items if item["type"] == item_type) + 1
        prefix = "video" if item_type == "video" else "foto"
        extension = extension or (".mp4" if item_type == "video" else ".jpg")
        items.append({
            "id": len(items) + 1, "sourceIndex": source_index, "type": item_type,
            "kind": "video" if item_type == "video" else "image",
            "url": media_url, "directUrl": media_url,
            "thumbnail": thumbnail if isinstance(thumbnail, str) and _safe_threads_media_url(thumbnail) else None,
            "extension": extension.lstrip("."), "filename": f"{prefix}-{count:03d}{extension}",
            "label": f"{prefix.title()} {count}",
            "duration": media.get("video_duration") or media.get("duration") if item_type == "video" else None,
        })
        if len(items) >= MAX_GALLERY_ITEMS:
            break
    return items


def _threads_photo_items(url: str, page_html: str) -> list[dict]:
    """Backward-compatible photo-only view of the Threads media parser."""
    photos = [dict(item) for item in _threads_media_items(url, page_html) if item.get("type") == "photo"]
    for index, item in enumerate(photos, 1):
        item["id"] = index
        item["label"] = f"Foto {index}"
        item["filename"] = f"foto-{index:03d}.{item.get('extension') or 'jpg'}"
    return photos


@contextmanager
def _safe_threads_stream(client: httpx.Client, url: str, *, media: bool = False):
    """Follow only same-platform or approved CDN redirects; avoid open-redirect SSRF."""
    current = url
    for _ in range(5):
        parsed_current = urlparse(current)
        current_host = (parsed_current.hostname or "").lower()
        current_allowed = _safe_threads_media_url(current) if media else (
            parsed_current.scheme == "https" and not parsed_current.username and not parsed_current.password
            and (parsed_current.port is None or parsed_current.port == 443)
            and (current_host in THREADS_PAGE_HOSTS or any(current_host.endswith("." + base) for base in THREADS_PAGE_HOSTS))
        )
        if not current_allowed:
            raise RuntimeError("Alamat sumber berada di luar daftar host HTTPS yang diizinkan.")
        with client.stream("GET", current) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise RuntimeError("Platform mengirim pengalihan tanpa alamat tujuan.")
                target = urljoin(current, location)
                parsed = urlparse(target)
                host = (parsed.hostname or "").lower()
                allowed = _safe_threads_media_url(target) if media else (
                    parsed.scheme == "https" and any(host == base or host.endswith("." + base) for base in THREADS_PAGE_HOSTS)
                )
                if not allowed:
                    raise RuntimeError("Pengalihan ke host di luar daftar aman ditolak.")
                current = target
                continue
            final_url = str(response.url)
            allowed = _safe_threads_media_url(final_url) if media else (
                (urlparse(final_url).hostname or "").lower() in THREADS_PAGE_HOSTS
                or any((urlparse(final_url).hostname or "").lower().endswith("." + base) for base in THREADS_PAGE_HOSTS)
            )
            if not allowed:
                raise RuntimeError("Host tujuan tidak termasuk sumber Threads yang diizinkan.")
            yield response
            return
    raise RuntimeError("Terlalu banyak pengalihan dari sumber Threads.")


def _fetch_threads_page(url: str) -> str:
    headers = {
        "User-Agent": THREADS_CRAWLER_UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    timeout = httpx.Timeout(12.0, connect=8.0)
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
        with _safe_threads_stream(client, url) as response:
            if response.status_code in (401, 403):
                raise RuntimeError("Postingan Threads membatasi akses publik atau meminta login.")
            if response.status_code == 404:
                raise RuntimeError("Postingan Threads tidak ditemukan atau sudah dihapus.")
            if response.status_code == 429:
                raise RuntimeError("Threads membatasi permintaan sementara. Coba lagi nanti.")
            if not response.is_success:
                raise RuntimeError(f"Halaman Threads gagal diakses (HTTP {response.status_code}).")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > THREADS_MAX_PAGE_BYTES:
                    raise RuntimeError("Halaman Threads terlalu besar untuk diproses.")
                chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _threads_list_media(url: str) -> list[dict]:
    try:
        page_html = _fetch_threads_page(url)
        items = _threads_media_items(url, page_html)
        if not items:
            print("[threads-gallery] no public photo/video data matched the requested post")
        return items
    except Exception as exc:
        print(f"[threads-gallery] {str(exc)[:300]}")
        return []


def _threads_list_images(url: str) -> list[dict]:
    """Backward-compatible photo-only listing."""
    return [item for item in _threads_list_media(url) if item.get("type") == "photo"]


def _extension_from_mime(content_type: str) -> str | None:
    return {
        "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
        "image/webp": "webp", "image/gif": "gif", "image/avif": "avif",
        "image/heic": "heic", "image/heif": "heic",
    }.get(content_type.lower())


def _looks_like_image(data: bytes, content_type: str) -> bool:
    if data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith((b"GIF87a", b"GIF89a")) or (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        return True
    if len(data) >= 12 and data[4:8] == b"ftyp" and (b"avif" in data[8:16] or b"heic" in data[8:16] or b"heix" in data[8:16] or b"mif1" in data[8:16]):
        return True
    return False



@contextmanager
def _safe_social_stream(client: httpx.Client, url: str):
    """Download a media URL while allowing redirects only to known Meta CDNs."""
    current = url
    for _ in range(5):
        if not _safe_social_media_url(current):
            raise RuntimeError("Alamat CDN media tidak lolos pemeriksaan keamanan.")
        with client.stream("GET", current) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise RuntimeError("CDN mengirim pengalihan tanpa alamat tujuan.")
                target = urljoin(current, location)
                if not _safe_social_media_url(target):
                    raise RuntimeError("Pengalihan CDN ke host di luar daftar aman ditolak.")
                current = target
                continue
            if not _safe_social_media_url(str(response.url)):
                raise RuntimeError("Host CDN tujuan tidak termasuk daftar aman.")
            yield response
            return
    raise RuntimeError("Terlalu banyak pengalihan dari CDN media.")


def _looks_like_video(data: bytes, content_type: str) -> bool:
    if content_type.startswith("video/"):
        return True
    # MP4/MOV family files carry an ISO-BMFF ftyp box near the start.
    return len(data) >= 12 and data[4:8] == b"ftyp"


def _video_extension_from_mime(content_type: str) -> str | None:
    return {
        "video/mp4": "mp4", "video/x-m4v": "m4v", "video/quicktime": "mov",
        "video/webm": "webm", "video/3gpp": "3gp", "video/x-msvideo": "avi",
    }.get(content_type.lower())


def _download_meta_media_items(url: str, platform: str, items: list[dict], indices: list[int]) -> tuple[Path, list[Path], list[dict]]:
    """Stream selected photo/video items to disk independently; keep partial successes."""
    if not indices or len(indices) > MAX_GALLERY_ITEMS:
        raise ValueError(f"Pilih 1 sampai {MAX_GALLERY_ITEMS} media.")
    chosen_indices = set(indices)
    chosen = [item for item in items if item.get("id") in chosen_indices]
    if len(chosen) != len(indices):
        raise RuntimeError("Media tidak lagi tersedia atau urutan galeri berubah. Ambil ulang tautannya.")

    job_dir = Path(tempfile.mkdtemp(prefix=f"{platform}-media-", dir=TEMP_ROOT))
    downloaded: list[Path] = []
    failures: list[dict] = []
    total_bytes = 0
    referer = "https://www.threads.net/" if platform == "threads" else "https://www.instagram.com/" if platform == "instagram" else "https://www.facebook.com/"
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1",
        "Referer": referer, "Accept": "*/*",
    }
    stream_context = _safe_threads_stream if platform == "threads" else _safe_social_stream
    try:
        with httpx.Client(timeout=httpx.Timeout(45.0, connect=12.0), follow_redirects=False, headers=headers) as client:
            for position, item in enumerate(chosen, 1):
                media_type = item.get("type") or ("video" if item.get("kind") == "video" else "photo")
                media_url = str(item.get("url") or "")
                prefix = "video" if media_type == "video" else "foto"
                extension = str(item.get("extension") or ("mp4" if media_type == "video" else "jpg")).lower().lstrip(".")
                path = job_dir / f"{prefix}-{position:03d}.{extension}"
                try:
                    is_safe = _safe_threads_media_url(media_url) if platform == "threads" else _safe_social_media_url(media_url)
                    if not is_safe:
                        raise RuntimeError("Alamat CDN media tidak lolos pemeriksaan keamanan.")
                    first_bytes = bytearray()
                    item_bytes = 0
                    with (stream_context(client, media_url, media=True) if platform == "threads" else stream_context(client, media_url)) as response:
                        if not response.is_success:
                            raise RuntimeError(f"CDN media gagal diakses (HTTP {response.status_code}).")
                        content_type = (response.headers.get("content-type") or "").split(";", 1)[0].lower()
                        max_bytes = MAX_GALLERY_VIDEO_BYTES if media_type == "video" else MAX_GALLERY_FILE_BYTES
                        with path.open("wb") as destination:
                            for chunk in response.iter_bytes():
                                item_bytes += len(chunk)
                                if item_bytes > max_bytes:
                                    label = "Video" if media_type == "video" else "Foto"
                                    raise RuntimeError(f"{label} melebihi batas ukuran per berkas.")
                                if len(first_bytes) < 32:
                                    first_bytes.extend(chunk[:32 - len(first_bytes)])
                                destination.write(chunk)
                    if media_type == "video":
                        if not content_type.startswith("video/") and not _looks_like_video(bytes(first_bytes), content_type):
                            raise RuntimeError("Isi berkas CDN bukan video yang valid.")
                        resolved_extension = _video_extension_from_mime(content_type) or extension
                    else:
                        if not content_type.startswith("image/") or not _looks_like_image(bytes(first_bytes), content_type):
                            raise RuntimeError("Isi berkas CDN bukan gambar yang valid.")
                        resolved_extension = _extension_from_mime(content_type) or extension
                    if item_bytes <= 0:
                        raise RuntimeError("CDN mengirim berkas kosong.")
                    if total_bytes + item_bytes > MAX_GALLERY_TOTAL_BYTES:
                        raise RuntimeError("Total media melebihi batas ukuran unduhan galeri.")
                    total_bytes += item_bytes
                    if resolved_extension != extension:
                        corrected = job_dir / f"{prefix}-{position:03d}.{resolved_extension}"
                        path.replace(corrected)
                        path = corrected
                    downloaded.append(path)
                except Exception as exc:
                    path.unlink(missing_ok=True)
                    failures.append({
                        "id": item.get("id"), "type": media_type,
                        "message": str(exc)[:180] or "Media gagal diunduh.",
                    })
        if not downloaded:
            detail = failures[0].get("message") if failures else "Tidak ada berkas yang berhasil diunduh."
            raise RuntimeError(f"Tidak ada media yang berhasil diunduh. {detail}")
        return job_dir, downloaded, failures
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

def _download_social_gallery_items(url: str, platform: str, items: list[dict], indices: list[int]) -> tuple[Path, list[Path], list[dict]]:
    """Compatibility wrapper; now supports both photos and videos."""
    return _download_meta_media_items(url, platform, items, indices)

def _download_threads_gallery_files(url: str, indices: list[int]) -> tuple[Path, list[Path], list[dict]]:
    items = _threads_list_media(url)
    return _download_meta_media_items(url, "threads", items, indices)


def _ytdlp_gallery_video_items(info: dict | None, source_index_offset: int = 0) -> list[dict]:
    """Expose ordered yt-dlp carousel/playlist entries as gallery video items."""
    if not isinstance(info, dict):
        return []
    entries = info.get("_petik_entries")
    if not isinstance(entries, list) or len(entries) < 2:
        return []
    items: list[dict] = []
    for entry_number, entry in enumerate(entries, 1):
        source_index = source_index_offset + entry_number
        if not isinstance(entry, dict):
            continue
        media_url = entry.get("url")
        if not isinstance(media_url, str) or urlparse(media_url).scheme != "https":
            continue
        ext = str(entry.get("ext") or _video_extension_from_url(media_url) or "mp4").lower().lstrip(".")
        if f".{ext}" not in DIRECT_VIDEO_EXTENSIONS:
            continue
        thumbnail = entry.get("thumbnail")
        if not isinstance(thumbnail, str) or urlparse(thumbnail).scheme != "https":
            thumbnail = None
        items.append({
            "id": len(items) + 1, "sourceIndex": source_index, "type": "video", "kind": "video",
            "url": media_url, "directUrl": media_url, "thumbnail": thumbnail,
            "extension": ext, "filename": f"video-{len(items) + 1:03d}.{ext}",
            "label": f"Video {len(items) + 1}", "duration": entry.get("duration"),
        })
    return items


def _ytdlp_primary_video_item(info: dict | None, source_index: int = 999) -> dict | None:
    """Return one progressive video attachment when a post also has photos."""
    if not isinstance(info, dict):
        return None
    direct = _direct_media_urls(info, str(info.get("extractor_key") or "").lower()).get("video")
    if not direct:
        direct = _preview_url(info, str(info.get("extractor_key") or "").lower())
    if not isinstance(direct, str) or urlparse(direct).scheme != "https":
        return None
    ext = _video_extension_from_url(direct) or ".mp4"
    thumbnail = info.get("thumbnail")
    if not isinstance(thumbnail, str) or urlparse(thumbnail).scheme != "https":
        thumbnail = None
    return {
        "id": 1, "sourceIndex": source_index, "type": "video", "kind": "video",
        "url": direct, "directUrl": direct, "thumbnail": thumbnail,
        "extension": ext.lstrip("."), "filename": f"video-001{ext}", "label": "Video 1",
        "duration": info.get("duration"),
    }


def _gallery_media_for_post(url: str, platform: str, info: dict | None) -> list[dict]:
    """Collect mixed media from platform-specific public resolvers and gallery-dl."""
    items: list[dict] = []
    if platform == "threads":
        items.extend(_threads_list_media(url))
    elif platform == "instagram":
        items.extend(_instagram_public_media(url))
        if not items:
            items.extend(_gallery_list_media(url, platform))
    elif platform == "facebook":
        # Prefer gallery-dl's single ordered post manifest when available. If it
        # cannot enumerate the post, use the stricter ID-scoped Meta photo parser.
        items.extend(_gallery_list_media(url, platform))
        if not items:
            for item in _facebook_public_gallery(url):
                item = dict(item)
                item.setdefault("type", "photo")
                item.setdefault("sourceIndex", item.get("id"))
                items.append(item)
    elif platform in GALLERY_PLATFORMS:
        items.extend(_gallery_list_media(url, platform))
    # Only expose a selectable gallery on platforms whose download endpoint can
    # map and fetch those attachments. Other platforms retain single video/audio flow.
    if platform in GALLERY_PLATFORMS and not any(item.get("type") == "video" for item in items):
        source_offset = max((int(item.get("sourceIndex") or item.get("id") or 0) for item in items), default=0)
        items.extend(_ytdlp_gallery_video_items(info, source_offset))
    if platform in GALLERY_PLATFORMS and any(item.get("type") == "photo" for item in items) and not any(item.get("type") == "video" for item in items):
        primary_video = _ytdlp_primary_video_item(info, max((int(item.get("sourceIndex") or item.get("id") or 0) for item in items), default=0) + 1)
        if primary_video:
            items.append(primary_video)
    return _normalise_media_items(items)


def _inspect_payload(url: str, platform: str) -> dict:
    info: dict | None = None
    extraction_error: Exception | None = None
    try:
        info = _extract_info(url)
    except Exception as exc:
        extraction_error = exc

    media_items = _gallery_media_for_post(url, platform, info)
    photo_items = _media_photo_alias(media_items)
    if info is None and not media_items:
        if extraction_error:
            raise extraction_error
        raise RuntimeError("Tidak ada media yang bisa diambil dari tautan ini.")

    videos, audios = _available_options(info, platform) if info else ([], [])
    title = str((info or {}).get("title") or (f"Media · {platform.title()}" if media_items else "Media"))[:300]
    video_count = sum(1 for item in media_items if item.get("type") == "video")
    photo_count = sum(1 for item in media_items if item.get("type") == "photo")
    return {
        "success": True,
        "status": "ready",
        "service": platform,
        "title": title,
        "author": str((info or {}).get("uploader") or (info or {}).get("channel") or (info or {}).get("artist") or "")[:180] or None,
        "duration": (info or {}).get("duration"),
        "thumbnail": _safe_thumbnail(info or {}) or next((item.get("thumbnail") for item in media_items if item.get("thumbnail")), None),
        "previewUrl": _preview_url(info, platform) if info else None,
        "directUrls": _direct_media_urls(info, platform),
        "videoOptions": videos,
        "audioOptions": audios,
        "mediaItems": media_items,
        "photoItems": photo_items,
        "mediaCounts": {"total": len(media_items), "photos": photo_count, "videos": video_count},
        "galleryAvailable": bool(media_items),
        "galleryLimit": MAX_GALLERY_ITEMS,
        "mediaLimit": MAX_GALLERY_ITEMS,
        "defaultVideoQuality": "max",
        "defaultAudioQuality": "best",
    }


def _download_gallery_files(url: str, indices: list[int]) -> tuple[Path, list[Path]]:
    executable = _gallery_command()
    if not executable:
        raise RuntimeError("Mesin galeri belum terpasang pada server. Pastikan gallery-dl selesai dipasang saat deployment.")
    if not indices or len(indices) > MAX_GALLERY_ITEMS:
        raise ValueError(f"Pilih 1 sampai {MAX_GALLERY_ITEMS} media.")
    if any(type(index) is not int or index < 1 or index > MAX_GALLERY_SOURCE_ITEMS for index in indices):
        raise ValueError("Nomor media yang dipilih tidak valid.")
    job_dir = Path(tempfile.mkdtemp(prefix="gallery-", dir=TEMP_ROOT))
    command = [executable, "--config-ignore", "--no-input", "--no-colors", "--quiet",
               "--no-mtime", "--no-skip", "--filesize-max", f"{max(1, max(MAX_GALLERY_FILE_BYTES, MAX_GALLERY_VIDEO_BYTES) // (1024 * 1024))}M",
               "--range", f"1-{MAX_GALLERY_SOURCE_ITEMS}", "--filter",
               "num in (" + ",".join(str(i) for i in indices) + ")",
               "--directory", str(job_dir), "--filename", "{num:>03}.{extension}", "--", url]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=150, check=False)
        files = sorted(path for path in job_dir.rglob("*") if path.is_file() and path.suffix.lower() in (IMAGE_EXTENSIONS | VIDEO_EXTENSIONS))
        if result.returncode not in (0, 1) or not files:
            detail = (result.stderr or result.stdout or "Extractor galeri tidak menemukan media yang dipilih.").strip().splitlines()[-1][:240]
            raise RuntimeError(detail or "Gagal mengunduh media dari sumber.")
        if len(files) > len(indices):
            files = files[:len(indices)]
        total = 0
        for file_path in files:
            size = file_path.stat().st_size
            total += size
            limit = MAX_GALLERY_VIDEO_BYTES if file_path.suffix.lower() in VIDEO_EXTENSIONS else MAX_GALLERY_FILE_BYTES
            if size <= 0 or size > limit:
                raise RuntimeError("Ada media yang melebihi batas ukuran per berkas.")
            if total > MAX_GALLERY_TOTAL_BYTES:
                raise RuntimeError("Total media melebihi batas ukuran unduhan galeri.")
        return job_dir, files
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise


def _download_media_impl(url: str, mode: str, quality: str) -> tuple[Path, str, dict]:
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
        postprocessors = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}]
    else:
        fmt = "bestvideo+bestaudio/best" if quality in {"max", "best"} else f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]"
        postprocessors = []

    def duration_filter(info: dict, *, incomplete: bool) -> str | None:
        duration = info.get("duration")
        if not incomplete and duration and duration > MAX_DURATION_SECONDS:
            return f"Durasi melebihi batas {MAX_DURATION_SECONDS // 60} menit."
        return None

    platform = _normalise_and_validate(url)[1]
    clients = [None]
    if platform == "youtube":
        provider = _youtube_provider_status()
        _refresh_youtube_provider_diagnostics(provider)
        preferred = _get_cached_youtube_client(url)
        wpc_ready = bool(
            _wpc_status().get("runtimeEnabled")
            and os.getenv("YOUTUBE_POT_PROVIDER_MODE", "bgutil").strip().lower() == "wpc"
        )
        clients = _youtube_client_candidates(bool(provider.get("ready")), preferred, wpc_ready)
        if mode == "audio":
            clients = [item for item in clients if item != "web_safari_hls"]
        _begin_youtube_diagnostics_attempt(provider, "attempting-download")

    last_error = None
    previous_client = None
    previous_error_code = None
    for index, client in enumerate(clients):
        if client == "web_safari_hls" and _youtube_should_skip_hls_retry(previous_client, previous_error_code):
            continue
        job_dir = Path(tempfile.mkdtemp(prefix="job-", dir=TEMP_ROOT))
        outtmpl = str(job_dir / "%(id)s.%(ext)s")
        options = _yt_common_options(platform, client) if platform == "youtube" else _yt_common_options(platform)
        diagnostic_logger = options.get("logger") if platform == "youtube" else None
        if platform == "youtube" and client == "web_safari_hls" and mode == "video":
            # Keep the user's requested height and avoid silent video-only streams.
            fmt_for_attempt = _youtube_hls_format_selector(quality)
        else:
            fmt_for_attempt = fmt
        options.update({
            "format": fmt_for_attempt,
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
            if platform == "youtube":
                _record_youtube_attempt(str(client), "attempting-download")
                _update_youtube_diagnostics(lastStrategy=str(client))
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    raise RuntimeError("Platform tidak mengembalikan media.")
                if info.get("_type") == "playlist":
                    entries = [item for item in (info.get("entries") or []) if item]
                    if not entries:
                        raise RuntimeError("Postingan tidak memiliki media yang bisa diunduh.")
                    info = entries[0]

            candidates = [
                path for path in job_dir.rglob("*")
                if path.is_file() and path.suffix.lower() not in {
                    ".jpg", ".jpeg", ".webp", ".json", ".description", ".vtt", ".srt", ".part"
                }
            ]
            if not candidates:
                raise RuntimeError("Extractor membaca tautan, tetapi berkas media tidak terbentuk.")
            file_path = max(candidates, key=lambda path: path.stat().st_size)
            size = file_path.stat().st_size
            if size <= 0 or size > MAX_FILE_BYTES:
                raise RuntimeError(f"Ukuran berkas di luar batas Petik (maksimal {MAX_FILE_BYTES // (1024 * 1024)} MB).")
            if mode == "audio" and file_path.suffix.lower() != ".mp3":
                raise RuntimeError("Konversi audio MP3 tidak selesai. Coba lagi atau pilih video.")
            if mode == "video" and file_path.suffix.lower() not in {".mp4", ".m4v", ".mov", ".webm", ".mkv"}:
                raise RuntimeError("Format video hasil ekstraksi tidak didukung.")
            if platform == "youtube":
                _record_youtube_attempt(str(client), "download-success")
                _cache_youtube_client(url, str(client))
            return file_path, _safe_filename(info, "mp3" if mode == "audio" else file_path.suffix.lstrip(".")), info
        except Exception as exc:
            last_error = exc
            shutil.rmtree(job_dir, ignore_errors=True)
            if platform != "youtube":
                raise
            observed_429 = bool(getattr(diagnostic_logger, "http_429_seen", False))
            code = "http_429" if observed_429 else _youtube_error_code(exc)
            stage = "http_429" if observed_429 else getattr(diagnostic_logger, "last_stage", None)
            is_rate_limited = observed_429 or code == "http_429"
            if is_rate_limited:
                _activate_youtube_cooldown(str(client), stage or "http_429")
            previous_client, previous_error_code = str(client), code
            _record_youtube_attempt(str(client), "download-failed", code, stage=stage)
            if is_rate_limited or index >= len(clients) - 1 or not _youtube_should_fallback(exc):
                break
            _youtube_fallback_pause()

    if platform == "youtube":
        _finish_youtube_diagnostics("download-failed", _youtube_error_code(last_error))
    if last_error:
        raise last_error
    raise RuntimeError("Unduhan tidak berhasil.")


def _download_media(url: str, mode: str, quality: str) -> tuple[Path, str, dict]:
    platform = _normalise_and_validate(url)[1]
    if platform != "youtube":
        return _download_media_impl(url, mode, quality)
    try:
        with YOUTUBE_ATTEMPT_LOCK:
            followup = _consume_youtube_inspect_cache(url)
            _youtube_request_gate(allow_recent_inspect_followup=followup)
            return _download_media_impl(url, mode, quality)
    finally:
        _cleanup_youtube_cookie_file()


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
    original = str(exc)[:500] or "Media tidak tersedia."
    lowered = original.lower()
    code = "error.api.content.video.unavailable"
    if "there is no video in this post" in lowered or "no video in this post" in lowered:
        code = "error.api.gallery.unavailable"
        message = "Postingan Instagram ini tidak menyediakan stream video. Petik sudah mencoba extractor foto, tetapi server belum menerima data foto publik. Pastikan postingan bisa dibuka tanpa login; Instagram juga dapat membatasi akses dari server."
    elif platform == "facebook" and any(term in lowered for term in ("unsupported url", "no formats", "no media", "not supported")):
        code = "error.api.gallery.unavailable"
        message = "Facebook tidak mengembalikan data media publik yang bisa dibaca Petik. Periksa apakah tautan dapat dibuka tanpa login; beberapa postingan atau album membatasi akses anonim dari server."
    elif platform == "youtube" and any(term in lowered for term in (
        "sign in to confirm you're not a bot", "confirm you're not a bot", "confirm you’re not a bot",
        "po token", "bgutil", "botguard", "http error 403", "403: forbidden",
        "youtube gagal pada client yang diuji", "no downloadable formats", "http error 429",
        "age_restricted", "age restricted", "members only", "video unavailable", "this video is not available",
        "too many requests", "http error 500", "http error 502", "http error 503", "http error 504",
        "login_required",
    )):
        code = "error.api.youtube.restricted"
        if "sign in to confirm" in lowered or "confirm you're not a bot" in lowered or "confirm you’re not a bot" in lowered:
            message = "YouTube menolak permintaan publik Petik dengan pemeriksaan anti-bot. Petik sudah mencoba beberapa jalur client. Hal ini dapat berkaitan dengan tahap ekstraksi, sesi, atau reputasi IP server; penyebab pastinya belum dapat dipastikan dari pesan ini saja."
        elif "429" in lowered or "too many requests" in lowered:
            code = "error.api.rate.limit"
            message = "YouTube membatasi permintaan sementara. Tunggu beberapa menit sebelum mencoba lagi."
        elif any(term in lowered for term in ("http error 500", "http error 502", "http error 503", "http error 504")):
            message = "Server YouTube sedang mengalami gangguan atau gagal merespons. Coba lagi beberapa saat."
        elif "po token" in lowered or "bgutil" in lowered or "botguard" in lowered:
            message = "Proses PO Token YouTube belum berhasil. Periksa diagnostik provider pada endpoint /health dan log Render untuk melihat apakah permintaan token benar-benar dijalankan."
        elif any(term in lowered for term in ("login_required", "age_restricted", "private", "members only", "video unavailable", "this video is not available")):
            message = "Video YouTube ini kemungkinan privat, dibatasi usia/wilayah, khusus anggota, atau memerlukan login. Petik tidak menggunakan cookie atau login akun secara otomatis."
        else:
            message = "Client YouTube yang tersedia tidak memberikan format media yang dapat diunduh. Video mungkin dibatasi atau YouTube hanya memberikan format streaming yang tidak kompatibel."
    elif "unsupported url" in lowered or "not supported" in lowered or "belum didukung" in lowered:
        code = "error.api.link.unsupported"
        message = "Jenis tautan ini belum didukung oleh extractor yang tersedia."
    elif "too many requests" in lowered or "429" in lowered or "rate limit" in lowered:
        code = "error.api.rate.limit"
        message = "Platform membatasi permintaan sementara. Coba lagi beberapa saat."
    elif any(term in lowered for term in ("private", "login", "sign in", "not available", "deleted", "403: forbidden", "401: unauthorized")):
        message = "Konten tidak dapat diakses secara publik tanpa login, atau platform sedang membatasi server Petik."
    elif "timed out" in lowered or "timeout" in lowered:
        message = "Platform terlalu lama merespons. Coba lagi beberapa saat."
    elif "gallery" in lowered and ("not installed" in lowered or "missing" in lowered):
        message = "Mesin galeri belum tersedia pada server. Periksa status deployment API."
    else:
        message = original
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
            "features": ["inspect", "quality-picker", "audio-mp3", "youtube-po-token-provider", "mixed-media-gallery", "multi-media-selection", "mixed-media-zip-download", "threads-public-photo-video-gallery", "temporary-tunnel"],
        },
        "git": {"remote": "Petik Universal Extractor"},
    }


def _package_available(package_name: str) -> bool:
    try:
        from importlib.metadata import version as package_version
        return bool(package_version(package_name))
    except Exception:
        return False


def _ytdlp_version() -> str:
    try:
        from yt_dlp.version import __version__
        return str(__version__)
    except Exception:
        return "unknown"


def _gallery_version() -> str:
    if not _gallery_command():
        return "missing"
    try:
        from importlib.metadata import version as package_version, PackageNotFoundError
        try:
            return package_version("gallery-dl")[:40]
        except PackageNotFoundError:
            pass
    except Exception:
        pass
    try:
        result = subprocess.run([_gallery_command(), "--version"], capture_output=True, text=True, timeout=5, check=False)
        return (result.stdout or result.stderr or "unknown").strip()[:40]
    except Exception:
        return "unknown"


@app.get("/health")
def health():
    pot_provider = _bgutil_provider_status()
    wpc_status = _wpc_status()
    _refresh_youtube_provider_diagnostics(pot_provider)
    youtube_diagnostics = _youtube_diagnostics_snapshot()
    provider_mode = os.getenv("YOUTUBE_POT_PROVIDER_MODE", "bgutil").strip().lower()
    youtube_diagnostics["providerMode"] = provider_mode
    youtube_diagnostics["rootCauseHint"] = _youtube_root_cause_hint(youtube_diagnostics)
    wpc_ready = bool(wpc_status["runtimeEnabled"] and provider_mode == "wpc")
    effective_provider = "wpc" if wpc_ready else "bgutil-http" if pot_provider["ready"] or pot_provider["enabled"] else "unavailable"
    return {
        "success": True,
        "status": "healthy",
        "version": APP_VERSION,
        "engine": "yt-dlp+gallery-dl+ffmpeg+pot-providers",
        "youtubePoTokenProvider": effective_provider,
        "youtubePoTokenProviderMode": provider_mode,
        "youtubePoTokenProviderEnabled": bool(wpc_ready or pot_provider["enabled"]),
        "youtubePoTokenProviderReady": bool(wpc_ready or pot_provider["ready"]),
        "youtubeBgutilProviderEnabled": pot_provider["enabled"],
        "youtubeBgutilProviderReady": pot_provider["ready"],
        "youtubePoTokenServerAvailable": pot_provider["serverAvailable"],
        "youtubePoTokenPluginVersion": pot_provider["pluginVersion"],
        "youtubePoTokenServerVersion": pot_provider["serverVersion"],
        "youtubePoTokenServerUptimeSeconds": pot_provider["serverUptimeSeconds"],
        "youtubePoTokenExpectedVersion": pot_provider["expectedVersion"],
        "youtubePoTokenClient": "mweb" if (pot_provider["ready"] or wpc_ready) else None,
        "youtubePoTokenTelemetryAvailable": pot_provider["telemetryAvailable"],
        "youtubeForceIPv4": os.getenv("YOUTUBE_FORCE_IPV4", "1").strip() == "1",
        "youtubeWpc": {
            **wpc_status,
            "note": (
                "WPC hanya aktif bila YOUTUBE_WPC_ENABLED=1, mode provider=wpc, plugin 1.1.2, dan Chrome/Chromium tersedia. "
                "Paket Docker standar tidak memasang browser; profil Dockerfile.wpc adalah opsi eksperimen "
                "dan tidak direkomendasikan untuk Render Free karena batas RAM/CPU."
            ),
            "recommendedForFreePlan": False,
        },
        "youtubeCookiesConfigured": _youtube_cookie_configured(),
        "youtubeRateControl": _youtube_rate_control_snapshot(),
        "youtubeHttpImpersonation": {
            "available": HAS_CURL_CFFI,
            "enabled": bool(_youtube_impersonate_target()),
            "target": _youtube_impersonate_target(),
        },
        "youtubeExperimentalOptions": {
            "playerSkip": _youtube_safe_extractor_overrides().get("player_skip", []),
            "innertubeHostConfigured": "innertube_host" in _youtube_safe_extractor_overrides(),
            "innertubeKeyConfigured": "innertube_key" in _youtube_safe_extractor_overrides(),
            "visitorDataConfigured": "visitor_data" in _youtube_safe_extractor_overrides(),
            "extraClients": _youtube_extra_clients(),
        },
        "renderRuntime": {"detected": os.getenv("RENDER", "").lower() == "true"},
        "youtubeClientFallbacks": _youtube_client_candidates(bool(pot_provider["ready"]), wpc_ready=wpc_ready),
        "youtubeDownloadFallbacks": _youtube_client_candidates(bool(pot_provider["ready"]), wpc_ready=wpc_ready),
        "youtubeDiagnostics": youtube_diagnostics,
        "ytdlpVersion": _ytdlp_version(),
        "galleryDlAvailable": bool(_gallery_command()),
        "galleryDlVersion": _gallery_version(),
        "galleryPlatforms": sorted(GALLERY_PLATFORMS | {"threads"}),
        "threadsMediaResolver": "public-page-json-photo-video",
        "threadsPhotoResolver": "public-page-json",
        "instagramMediaResolver": "gallery-dl+logged-out-graphql+page-json-photo-video",
        "instagramPhotoResolver": "gallery-dl+logged-out-graphql+page-json",
        "facebookPhotoResolver": "gallery-dl+og-image+post-scoped-json",
        "mixedMediaAvailable": True,
        "socialPublicFallbackAvailable": HAS_CURL_CFFI,
        "instagramPublicFallbackAvailable": HAS_CURL_CFFI,
        "maxGalleryItems": MAX_GALLERY_ITEMS,
        "maxGalleryFileMB": MAX_GALLERY_FILE_BYTES // (1024 * 1024),
        "maxGalleryVideoMB": MAX_GALLERY_VIDEO_BYTES // (1024 * 1024),
        "maxGalleryTotalMB": MAX_GALLERY_TOTAL_BYTES // (1024 * 1024),
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

@app.get("/api/diagnostics/youtube-network")
def youtube_network_diagnostics(request: Request):
    """On-demand, fixed-destination reachability probe; no IP or response body is exposed."""
    global youtube_network_probe_last_monotonic
    if _rate_limited(_client_key(request)):
        return _error("error.api.rate.limit", "Terlalu banyak permintaan diagnostik.", 429)
    now = time.monotonic()
    with YOUTUBE_NETWORK_PROBE_LOCK:
        remaining = 120 - (now - youtube_network_probe_last_monotonic)
        if remaining > 0:
            return JSONResponse({
                "success": False, "status": "cooldown",
                "retryAfterSeconds": max(1, int(round(remaining))),
                "message": "Tes jaringan YouTube dibatasi maksimal sekali setiap 120 detik.",
            }, status_code=429)
        youtube_network_probe_last_monotonic = now

    checks = []
    for label, target in (
        ("youtube_watch", "https://www.youtube.com/generate_204"),
        ("innertube_host", "https://youtubei.googleapis.com/"),
    ):
        started = time.monotonic()
        status = None
        outcome = "network_error"
        try:
            req = urllib.request.Request(
                target,
                headers={"User-Agent": "Mozilla/5.0 Petik-Network-Diagnostic/1.14.0", "Accept": "*/*"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=8) as response:
                status = int(response.status)
                outcome = "rate_limited" if status == 429 else "reachable"
                response.read(1)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            outcome = "rate_limited" if status == 429 else "reachable"
            try:
                exc.close()
            except Exception:
                pass
        except Exception:
            outcome = "network_error"
        checks.append({
            "endpoint": label, "statusCode": status, "outcome": outcome,
            "elapsedMs": max(0, int((time.monotonic() - started) * 1000)),
        })
        # A probe that receives 429 stops immediately; do not hit another
        # YouTube endpoint after the upstream has already asked us to slow down.
        if outcome == "rate_limited":
            break
    rate_limited = any(item["outcome"] == "rate_limited" for item in checks)
    if rate_limited:
        limited_endpoint = next((item["endpoint"] for item in checks if item["outcome"] == "rate_limited"), "youtube_network")
        _activate_youtube_cooldown("network_probe", limited_endpoint)
    reachable = all(item["outcome"] != "network_error" for item in checks)
    return JSONResponse({
        "success": reachable and not rate_limited,
        "status": "rate_limited" if rate_limited else "reachable" if reachable else "network_error",
        "checks": checks,
        "note": "Status 4xx selain 429 hanya membuktikan host merespons; bukan bukti Player API dapat mengekstrak video.",
    }, status_code=200 if reachable and not rate_limited else 503)


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


@app.post("/api/gallery/download")
def download_gallery(payload: dict, request: Request):
    limited = _acquire(request)
    if limited:
        return limited
    job_dir: Path | None = None
    failures: list[dict] = []
    try:
        normalised, error = _normalise_request(payload)
        if error:
            return error
        url, platform = normalised
        if platform not in GALLERY_PLATFORMS and platform != "threads":
            return _error("error.api.gallery.unsupported", "Platform ini belum memiliki extractor galeri campuran Petik.", 400, platform)
        raw_indices = payload.get("indices")
        if not isinstance(raw_indices, list) or not raw_indices or len(raw_indices) > MAX_GALLERY_ITEMS:
            return _error("error.api.gallery.selection", f"Pilih 1 sampai {MAX_GALLERY_ITEMS} media.", 400, platform)
        if any(type(value) is not int or value < 1 or value > MAX_GALLERY_SOURCE_ITEMS for value in raw_indices) or len(set(raw_indices)) != len(raw_indices):
            return _error("error.api.gallery.selection", "Daftar pilihan media tidak valid.", 400, platform)

        if platform == "threads":
            items = _threads_list_media(url)
            if not items:
                raise RuntimeError("Media Threads tidak lagi tersedia secara publik. Ambil ulang tautannya.")
            job_dir, files, failures = _download_meta_media_items(url, platform, items, raw_indices)
        elif platform in {"instagram", "facebook"}:
            if platform == "instagram":
                items = _instagram_public_media(url)
                if not items:
                    items = _gallery_list_media(url, platform)
            else:
                items = _gallery_list_media(url, platform)
                if not items:
                    items = _normalise_media_items(_facebook_public_gallery(url))
            if not items:
                raise RuntimeError("Extractor tidak menemukan item foto/video publik pada postingan ini.")
            try:
                job_dir, files, failures = _download_social_gallery_items(url, platform, items, raw_indices)
            except Exception as direct_error:
                # Never use a UI index as a gallery-dl index without matching URLs.
                source_indices = _gallery_dl_indices_for_items(
                    url, platform, [item for item in items if int(item.get("id") or 0) in set(raw_indices)]
                )
                try:
                    job_dir, files = _download_gallery_files(url, source_indices)
                    failures = _gallery_download_failures(items, raw_indices, source_indices, files)
                except Exception as fallback_error:
                    raise RuntimeError(
                        f"Unduhan media langsung gagal ({str(direct_error)[:140]}). Fallback gallery-dl juga gagal ({str(fallback_error)[:140]})."
                    ) from fallback_error
        else:
            items = _gallery_list_media(url, platform)
            if not items:
                raise RuntimeError("Extractor tidak menemukan daftar media publik untuk tautan ini.")
            source_indices = _gallery_source_indices(items, raw_indices)
            job_dir, files = _download_gallery_files(url, source_indices)
            failures = _gallery_download_failures(items, raw_indices, source_indices, files)

        zip_requested = bool(payload.get("zip"))
        if zip_requested:
            archive = job_dir / "petik-media.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
                for index, file_path in enumerate(files, 1):
                    suffix = file_path.suffix.lower()
                    prefix = "video" if suffix in VIDEO_EXTENSIONS else "foto"
                    bundle.write(file_path, arcname=f"{prefix}-{index:03d}{suffix}")
            token = _store_file(archive, "petik-media.zip")
            job_dir = None
            return {"status": "tunnel", "url": f"{_base_url(request)}/api/file/{token}",
                    "filename": "petik-media.zip", "count": len(files), "service": platform,
                    "failedCount": len(failures), "failures": failures}

        file_results = []
        for index, file_path in enumerate(files, 1):
            single_dir = Path(tempfile.mkdtemp(prefix="gallery-file-", dir=TEMP_ROOT))
            suffix = file_path.suffix.lower()
            prefix = "video" if suffix in VIDEO_EXTENSIONS else "foto"
            target = single_dir / (file_path.name[:140] or f"{prefix}-{index:03d}{suffix}")
            shutil.copy2(file_path, target)
            token = _store_file(target, target.name)
            file_results.append({"url": f"{_base_url(request)}/api/file/{token}", "filename": target.name})
        shutil.rmtree(job_dir, ignore_errors=True)
        job_dir = None
        return {"status": "ready", "files": file_results, "count": len(file_results), "service": platform,
                "failedCount": len(failures), "failures": failures}
    except ValueError as exc:
        return _error("error.api.gallery.selection", str(exc), 400)
    except Exception as exc:
        return _extract_error(exc, platform if "platform" in locals() else "gallery")
    finally:
        if job_dir is not None:
            shutil.rmtree(job_dir, ignore_errors=True)
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
    media_type = "audio/mpeg" if suffix == ".mp3" else "video/mp4" if suffix in {".mp4", ".m4v"} else "application/zip" if suffix == ".zip" else "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png" if suffix == ".png" else "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
