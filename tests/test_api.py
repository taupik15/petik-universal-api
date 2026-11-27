"""Offline API contract tests. They do not claim live extraction success."""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import yt_dlp  # noqa: F401
except ModuleNotFoundError:
    fake = types.ModuleType("yt_dlp")
    fake.YoutubeDL = object
    sys.modules["yt_dlp"] = fake

api = importlib.import_module("app")
from fastapi.testclient import TestClient

client = TestClient(api.app)


def no_rate_limit(monkeypatch):
    monkeypatch.setattr(api, "_rate_limited", lambda _key: False)


def test_health_endpoint_and_version():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["version"] == "1.5.0"
    assert "javascriptRuntime" in response.json()


def test_root_advertises_all_expected_platforms():
    response = client.get("/")
    assert response.status_code == 200
    assert set(api.SERVICES).issubset(set(response.json()["cobalt"]["services"]))
    assert "snapchat" in api.SERVICES


def test_twelve_platforms_are_recognised():
    samples = {
        "https://www.youtube.com/watch?v=abc12345678": "youtube",
        "https://www.instagram.com/reel/ABC123/": "instagram",
        "https://www.facebook.com/watch/?v=123": "facebook",
        "https://www.tiktok.com/@user/video/123": "tiktok",
        "https://x.com/user/status/123": "twitter",
        "https://www.reddit.com/r/videos/comments/abc/title/": "reddit",
        "https://vimeo.com/123456": "vimeo",
        "https://www.pinterest.com/pin/123456/": "pinterest",
        "https://soundcloud.com/artist/track": "soundcloud",
        "https://t.me/sample_channel/123": "telegram",
        "https://www.threads.net/@sample/post/ABC123": "threads",
        "https://www.snapchat.com/spotlight/abc123": "snapchat",
    }
    for url, expected in samples.items():
        _, actual = api._normalise_and_validate(url)
        assert actual == expected


def test_short_domains_are_recognised():
    assert api._normalise_and_validate("https://youtu.be/abc12345678")[1] == "youtube"
    assert api._normalise_and_validate("https://pin.it/abc123")[1] == "pinterest"
    assert api._normalise_and_validate("https://threads.com/t/abc123")[1] == "threads"
    assert api._normalise_and_validate("https://t.me/s/sample_channel/123")[1] == "telegram"
    assert api._normalise_and_validate("https://redd.it/abc123")[1] == "reddit"


def test_private_telegram_links_are_rejected():
    try:
        api._normalise_and_validate("https://t.me/c/123456/987")
    except ValueError:
        pass
    else:
        raise AssertionError("Private Telegram /c/ links must be rejected")


def test_untrusted_hosts_are_rejected():
    for url in ("https://example.com/video", "https://reddit.com.example.org/video", "javascript:alert(1)"):
        try:
            api._normalise_and_validate(url)
        except ValueError:
            continue
        raise AssertionError(f"Unexpectedly accepted URL: {url}")


def test_inspect_returns_actual_format_choices(monkeypatch):
    no_rate_limit(monkeypatch)
    monkeypatch.setattr(api, "_extract_info", lambda _url: {
        "id": "demo", "title": "Demo video", "uploader": "Demo channel", "duration": 84,
        "thumbnail": "https://img.example/thumb.jpg",
        "formats": [
            {"format_id": "18", "height": 360, "vcodec": "avc1", "acodec": "mp4a"},
            {"format_id": "22", "height": 720, "vcodec": "avc1", "acodec": "mp4a"},
            {"format_id": "137", "height": 1080, "vcodec": "avc1", "acodec": "none"},
            {"format_id": "140", "vcodec": "none", "acodec": "mp4a"},
        ],
    })
    response = client.post("/api/inspect", json={"url": "https://vimeo.com/123456"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["title"] == "Demo video"
    assert body["previewUrl"] is None
    assert body["videoOptions"][0]["id"] == "max"
    assert {item["id"] for item in body["videoOptions"]} >= {"720", "1080", "360"}
    assert body["audioOptions"] == [{"id": "best", "label": "MP3 · kualitas terbaik"}]



def test_inspect_exposes_progressive_video_preview_url(monkeypatch):
    no_rate_limit(monkeypatch)
    monkeypatch.setattr(api, "_extract_info", lambda _url: {
        "id": "demo", "title": "Demo video", "duration": 84,
        "thumbnail": "https://img.example/thumb.jpg",
        "formats": [
            {"format_id": "137", "height": 1080, "vcodec": "avc1", "acodec": "none", "ext": "mp4", "url": "https://cdn.example/video-only.mp4"},
            {"format_id": "18", "height": 360, "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4", "url": "https://cdn.example/progressive-360.mp4"},
            {"format_id": "22", "height": 720, "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4", "url": "https://cdn.example/progressive-720.mp4"},
        ],
    })
    response = client.post("/api/inspect", json={"url": "https://vimeo.com/123456"})
    assert response.status_code == 200
    assert response.json()["previewUrl"] == "https://cdn.example/progressive-720.mp4"


def test_inspect_supports_direct_social_video_url_without_formats(monkeypatch):
    no_rate_limit(monkeypatch)
    monkeypatch.setattr(api, "_extract_info", lambda _url: {
        "id": "thread", "title": "Threads clip", "ext": "mp4",
        "url": "https://cdn.example/thread-video.mp4", "thumbnail": "https://img.example/thread.jpg",
    })
    response = client.post("/api/inspect", json={"url": "https://threads.net/@sample/post/ABC123"})
    assert response.status_code == 200
    body = response.json()
    assert body["videoOptions"]
    assert body["previewUrl"] == "https://cdn.example/thread-video.mp4"


def test_inspect_audio_only_media_disables_video(monkeypatch):
    no_rate_limit(monkeypatch)
    monkeypatch.setattr(api, "_extract_info", lambda _url: {
        "id": "audio", "title": "Audio only", "formats": [
            {"acodec": "opus", "vcodec": "none"},
        ],
    })
    response = client.post("/api/inspect", json={"url": "https://vimeo.com/123456"})
    body = response.json()
    assert response.status_code == 200
    assert body["videoOptions"] == []
    assert body["audioOptions"]


def test_soundcloud_inspect_is_audio_only(monkeypatch):
    no_rate_limit(monkeypatch)
    monkeypatch.setattr(api, "_extract_info", lambda _url: {
        "id": "track", "title": "Demo track", "formats": [{"acodec": "mp3", "vcodec": "none"}],
    })
    response = client.post("/api/inspect", json={"url": "https://soundcloud.com/artist/track"})
    body = response.json()
    assert response.status_code == 200
    assert body["videoOptions"] == []
    assert body["audioOptions"][0]["id"] == "best"


def test_inspect_rejects_bad_url(monkeypatch):
    no_rate_limit(monkeypatch)
    response = client.post("/api/inspect", json={"url": "https://example.org/private"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "error.api.link.unsupported"


def test_cobalt_compatible_tunnel_and_file_download(monkeypatch, tmp_path):
    no_rate_limit(monkeypatch)
    media = tmp_path / "demo.mp4"
    media.write_bytes(b"fake-video-data")

    def fake_download(url, mode, quality):
        assert mode == "video"
        assert quality == "720"
        return media, "demo.mp4", {"title": "Demo", "id": "demo"}

    monkeypatch.setattr(api, "_download_media", fake_download)
    response = client.post("/", json={"url": "https://vimeo.com/123456", "downloadMode": "auto", "videoQuality": "720"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "tunnel"
    assert body["service"] == "vimeo"
    token = body["url"].rsplit("/", 1)[-1]
    file_response = client.get(f"/api/file/{token}")
    assert file_response.status_code == 200
    assert file_response.content == b"fake-video-data"


def test_native_download_uses_selected_quality(monkeypatch, tmp_path):
    no_rate_limit(monkeypatch)
    media = tmp_path / "video.mp4"
    media.write_bytes(b"native-video")
    calls = []

    def fake_download(url, mode, quality):
        calls.append((mode, quality))
        return media, "video.mp4", {"title": "Video"}

    monkeypatch.setattr(api, "_download_media", fake_download)
    response = client.post("/api/download", json={
        "url": "https://vimeo.com/123456", "mode": "video", "quality": "1080"
    })
    assert response.status_code == 200
    assert response.json()["status"] == "tunnel"
    assert calls == [("video", "1080")]


def test_soundcloud_auto_mode_is_mp3(monkeypatch, tmp_path):
    no_rate_limit(monkeypatch)
    media = tmp_path / "track.mp3"
    media.write_bytes(b"fake-mp3-data")
    calls = []

    def fake_download(url, mode, quality):
        calls.append((mode, quality))
        return media, "track.mp3", {"title": "Track", "id": "track"}

    monkeypatch.setattr(api, "_download_media", fake_download)
    response = client.post("/", json={"url": "https://soundcloud.com/artist/track", "downloadMode": "auto"})
    assert response.status_code == 200
    assert calls == [("audio", "720")]
    assert response.json()["filename"].endswith(".mp3")


def test_telegram_and_threads_reach_extractor(monkeypatch, tmp_path):
    no_rate_limit(monkeypatch)
    media = tmp_path / "public.mp4"
    media.write_bytes(b"video")
    called = []

    def fake_download(url, mode, quality):
        called.append(url)
        return media, "public.mp4", {"title": "Public", "id": "public"}

    monkeypatch.setattr(api, "_download_media", fake_download)
    for url, service in (
        ("https://t.me/sample_channel/123", "telegram"),
        ("https://threads.net/@sample/post/ABC123", "threads"),
    ):
        result = client.post("/", json={"url": url, "downloadMode": "auto"})
        assert result.status_code == 200
        assert result.json()["service"] == service
    assert len(called) == 2


def test_download_options_create_mp3_with_ffmpeg(monkeypatch, tmp_path):
    captured = {}

    class FakeYDL:
        def __init__(self, options): captured.update(options)
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def extract_info(self, _url, download=True):
            assert download is True
            out = Path(captured["outtmpl"].replace("%(id)s.%(ext)s", "track.mp3"))
            out.write_bytes(b"fake-mp3-data")
            return {"id": "track", "title": "Track", "duration": 90}

    monkeypatch.setattr(api, "TEMP_ROOT", tmp_path)
    monkeypatch.setattr(api, "YoutubeDL", FakeYDL)
    path, filename, _info = api._download_media("https://soundcloud.com/a/b", "audio", "best")
    assert path.suffix == ".mp3"
    assert filename == "Track.mp3"
    assert captured["format"] == "bestaudio/best"
    assert captured["postprocessors"][0]["key"] == "FFmpegExtractAudio"
    assert captured["postprocessors"][0]["preferredcodec"] == "mp3"
    assert captured["postprocessors"][0]["preferredquality"] == "0"
    assert callable(captured["match_filter"])
    assert captured["match_filter"]({"duration": api.MAX_DURATION_SECONDS + 1}, incomplete=False)
    assert captured["match_filter"]({"duration": 10}, incomplete=False) is None


def test_download_options_respect_quality_limit(monkeypatch, tmp_path):
    captured = {}

    class FakeYDL:
        def __init__(self, options): captured.update(options)
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def extract_info(self, _url, download=True):
            out = Path(captured["outtmpl"].replace("%(id)s.%(ext)s", "video.mp4"))
            out.write_bytes(b"fake-video-data")
            return {"id": "video", "title": "Video", "duration": 90}

    monkeypatch.setattr(api, "TEMP_ROOT", tmp_path)
    monkeypatch.setattr(api, "YoutubeDL", FakeYDL)
    path, filename, _info = api._download_media("https://vimeo.com/123456", "video", "720")
    assert path.suffix == ".mp4"
    assert filename == "Video.mp4"
    assert "height<=720" in captured["format"]
    assert captured["postprocessors"] == []


def test_preview_endpoint_forwards_range_and_streams_bytes(monkeypatch):
    from io import BytesIO

    class Headers(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    class FakeUpstream(BytesIO):
        status = 206
        headers = Headers({
            "Content-Type": "video/mp4", "Content-Length": "4",
            "Content-Range": "bytes 0-3/10", "Accept-Ranges": "bytes",
        })

    captured = {}
    no_rate_limit(monkeypatch)
    monkeypatch.setattr(api, "_preview_source", lambda _url: ("https://cdn.example/video.mp4", {}))
    def fake_urlopen(request, timeout=30):
        captured["range"] = request.headers.get("Range")
        return FakeUpstream(b"test")
    monkeypatch.setattr(api.urllib.request, "urlopen", fake_urlopen)

    response = client.get(
        "/api/preview",
        params={"url": "https://www.instagram.com/reel/ABC123/"},
        headers={"Range": "bytes=0-3"},
    )
    assert response.status_code == 206
    assert response.content == b"test"
    assert response.headers["content-range"] == "bytes 0-3/10"
    assert captured["range"] == "bytes=0-3"


def test_preview_url_accepts_instagram_style_formats_without_codec_info():
    info = {"formats": [
        {"url": "https://cdn.example/ig.mp4", "ext": "mp4", "height": 1280, "vcodec": None, "acodec": None, "protocol": "https"},
        {"url": "https://cdn.example/dash-video.mp4", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "protocol": "https"},
    ]}
    assert api._preview_url(info, "instagram") == "https://cdn.example/ig.mp4"


def test_preview_not_blocked_by_busy_download_slot_or_download_rate_limit(monkeypatch):
    from io import BytesIO

    class FakeUpstream(BytesIO):
        status = 200
        headers = {"Content-Type": "video/mp4"}

    monkeypatch.setattr(api, "_preview_source", lambda _url: ("https://cdn.example/v.mp4", {}))
    monkeypatch.setattr(api.urllib.request, "urlopen", lambda *a, **k: FakeUpstream(b"ok"))
    assert api.download_slots.acquire(blocking=False)  # unduhan sedang jalan
    try:
        for _ in range(25):  # lebih banyak dari RATE_LIMIT_COUNT unduhan
            r = client.get("/api/preview", params={"url": "https://www.instagram.com/reel/ABC123/"})
            assert r.status_code == 200
    finally:
        api.download_slots.release()


def test_health_reports_ytdlp_version():
    body = client.get("/health").json()
    assert body["ytdlpVersion"] and body["ytdlpVersion"] != ""
