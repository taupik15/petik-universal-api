"""Fast offline API tests; real yt-dlp extraction is tested at deployment time."""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Allow the API's validation/response tests to run in a minimal test container.
# Production installs the real yt-dlp package from requirements.txt.
try:
    import yt_dlp  # noqa: F401
except ModuleNotFoundError:
    fake = types.ModuleType("yt_dlp")
    fake.YoutubeDL = object  # patched before any extraction test
    sys.modules["yt_dlp"] = fake

api = importlib.import_module("app")
from fastapi.testclient import TestClient

client = TestClient(api.app)


def test_all_platforms_are_advertised():
    response = client.get("/")
    assert response.status_code == 200
    assert set(api.SERVICES).issubset(set(response.json()["cobalt"]["services"]))


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_six_added_platforms_are_recognised():
    samples = {
        "https://www.reddit.com/r/videos/comments/abc/title/": "reddit",
        "https://vimeo.com/123456": "vimeo",
        "https://www.pinterest.com/pin/123456/": "pinterest",
        "https://soundcloud.com/artist/track": "soundcloud",
        "https://t.me/sample_channel/123": "telegram",
        "https://www.threads.net/@sample/post/ABC123": "threads",
    }
    for url, expected in samples.items():
        _, actual = api._normalise_and_validate(url)
        assert actual == expected


def test_short_domains_and_threads_com_are_recognised():
    assert api._normalise_and_validate("https://pin.it/abc123")[1] == "pinterest"
    assert api._normalise_and_validate("https://threads.com/t/abc123")[1] == "threads"
    assert api._normalise_and_validate("https://t.me/s/sample_channel/123")[1] == "telegram"


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


def test_cobalt_compatible_tunnel_and_file_download(monkeypatch, tmp_path):
    media = tmp_path / "demo.mp4"
    media.write_bytes(b"fake-video-data")

    def fake_download(url, mode, quality):
        assert mode == "video"
        return media, "demo.mp4", {"title": "Demo", "id": "demo"}

    monkeypatch.setattr(api, "_download_media", fake_download)
    monkeypatch.setattr(api, "_rate_limited", lambda _key: False)
    response = client.post("/", json={"url": "https://vimeo.com/123456", "downloadMode": "auto", "videoQuality": "720"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "tunnel"
    assert body["service"] == "vimeo"
    token = body["url"].rsplit("/", 1)[-1]
    file_response = client.get(f"/api/file/{token}")
    assert file_response.status_code == 200
    assert file_response.content == b"fake-video-data"


def test_soundcloud_auto_mode_is_returned_as_audio(monkeypatch, tmp_path):
    media = tmp_path / "track.mp3"
    media.write_bytes(b"fake-mp3-data")
    calls = []

    def fake_download(url, mode, quality):
        calls.append(mode)
        return media, "track.mp3", {"title": "Track", "id": "track"}

    monkeypatch.setattr(api, "_download_media", fake_download)
    monkeypatch.setattr(api, "_rate_limited", lambda _key: False)
    response = client.post("/", json={"url": "https://soundcloud.com/artist/track", "downloadMode": "auto"})
    assert response.status_code == 200
    assert calls == ["audio"]
    assert response.json()["filename"].endswith(".mp3")


def test_telegram_and_threads_reach_the_extractor(monkeypatch, tmp_path):
    media = tmp_path / "public.mp4"
    media.write_bytes(b"video")
    called = []

    def fake_download(url, mode, quality):
        called.append(url)
        return media, "public.mp4", {"title": "Public", "id": "public"}

    monkeypatch.setattr(api, "_download_media", fake_download)
    monkeypatch.setattr(api, "_rate_limited", lambda _key: False)
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
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=True):
            assert download is True
            out = Path(captured["outtmpl"].replace("%(id)s.%(ext)s", "track.mp3"))
            out.write_bytes(b"fake-mp3-data")
            return {"id": "track", "title": "Track", "duration": 90}

    monkeypatch.setattr(api, "TEMP_ROOT", tmp_path)
    monkeypatch.setattr(api, "YoutubeDL", FakeYDL)
    path, filename, _info = api._download_media("https://soundcloud.com/a/b", "audio", "720")
    assert path.suffix == ".mp3"
    assert filename == "Track.mp3"
    assert captured["format"] == "bestaudio/best"
    assert captured["postprocessors"][0]["key"] == "FFmpegExtractAudio"
    assert captured["postprocessors"][0]["preferredcodec"] == "mp3"
    assert callable(captured["match_filter"])
    assert captured["match_filter"]({"duration": api.MAX_DURATION_SECONDS + 1}, incomplete=False)
    assert captured["match_filter"]({"duration": 10}, incomplete=False) is None


def test_download_options_keep_video_container_flexible(monkeypatch, tmp_path):
    captured = {}

    class FakeYDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=True):
            out = Path(captured["outtmpl"].replace("%(id)s.%(ext)s", "video.mp4"))
            out.write_bytes(b"fake-video-data")
            return {"id": "video", "title": "Video", "duration": 90}

    monkeypatch.setattr(api, "TEMP_ROOT", tmp_path)
    monkeypatch.setattr(api, "YoutubeDL", FakeYDL)
    path, filename, _info = api._download_media("https://vimeo.com/123456", "video", "720")
    assert path.suffix == ".mp4"
    assert filename == "Video.mp4"
    assert captured["postprocessors"] == []
    assert "height<=720" in captured["format"]
