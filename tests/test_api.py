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
    assert response.json()["version"] == "1.9.0"
    assert "javascriptRuntime" in response.json()
    assert response.json()["youtubePoTokenProvider"] == "bgutil-http"
    assert isinstance(response.json()["youtubePoTokenProviderReady"], bool)
    assert response.json()["mixedMediaAvailable"] is True
    assert response.json()["maxGalleryVideoMB"] >= 1


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
        "https://www.dailymotion.com/video/x123abc": "dailymotion",
        "https://www.linkedin.com/posts/example_123": "linkedin",
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


def sample_photo_items():
    return [
        {"id": 1, "url": "https://cdn.example/photo-1.jpg", "thumbnail": "https://cdn.example/photo-1.jpg", "extension": "jpg", "filename": "photo-1.jpg", "label": "Foto 1", "kind": "image"},
        {"id": 2, "url": "https://cdn.example/photo-2.webp", "thumbnail": "https://cdn.example/photo-2.webp", "extension": "webp", "filename": "photo-2.webp", "label": "Foto 2", "kind": "image"},
    ]




def mixed_photo_manifest():
    return [
        {"id": 1, "sourceIndex": 1, "type": "photo", "kind": "image", "url": "https://scontent.cdninstagram.com/photo-1.jpg", "thumbnail": "https://scontent.cdninstagram.com/photo-1.jpg", "extension": "jpg"},
        {"id": 2, "sourceIndex": 2, "type": "photo", "kind": "image", "url": "https://scontent.cdninstagram.com/photo-2.webp", "thumbnail": "https://scontent.cdninstagram.com/photo-2.webp", "extension": "webp"},
    ]

def test_gallery_url_parser_filters_non_images_and_duplicates():
    output = "\n".join([
        "https://cdn.example/a.jpg?token=one",
        "https://cdn.example/a.jpg?token=one",
        "https://cdn.example/video.mp4",
        "http://cdn.example/not-secure.jpg",
        "https://cdn.example/b.webp",
    ])
    items = api._parse_gallery_urls(output)
    assert [item["extension"] for item in items] == ["jpg", "webp"]
    assert [item["id"] for item in items] == [1, 5]


def test_inspect_returns_photo_gallery_when_video_extractor_fails(monkeypatch):
    no_rate_limit(monkeypatch)
    def unavailable(_url):
        raise RuntimeError("Unsupported URL")
    monkeypatch.setattr(api, "_extract_info", unavailable)
    monkeypatch.setattr(api, "_gallery_list_media", lambda _url, _platform: mixed_photo_manifest())
    response = client.post("/api/inspect", json={"url": "https://www.instagram.com/p/ABC123/"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["videoOptions"] == []
    assert body["audioOptions"] == []
    assert len(body["photoItems"]) == 2
    assert body["galleryAvailable"] is True


def test_inspect_combines_video_and_photo_gallery(monkeypatch):
    no_rate_limit(monkeypatch)
    monkeypatch.setattr(api, "_extract_info", lambda _url: {
        "id": "mixed", "title": "Mixed post", "formats": [
            {"format_id": "18", "height": 360, "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4", "url": "https://cdn.example/video.mp4"}
        ]
    })
    monkeypatch.setattr(api, "_gallery_list_media", lambda _url, _platform: mixed_photo_manifest())
    response = client.post("/api/inspect", json={"url": "https://www.instagram.com/p/ABC123/"})
    assert response.status_code == 200
    body = response.json()
    assert body["videoOptions"]
    assert len(body["photoItems"]) == 2


def test_gallery_download_zip_endpoint(monkeypatch, tmp_path):
    no_rate_limit(monkeypatch)
    def fake_gallery(_url, indices):
        assert indices == [1, 2]
        folder = tmp_path / "gallery-job"
        folder.mkdir()
        first, second = folder / "001.jpg", folder / "002.webp"
        first.write_bytes(b"first-photo")
        second.write_bytes(b"second-photo")
        return folder, [first, second]
    manifest = [
        {"id": 1, "sourceIndex": 1, "type": "photo", "url": "https://i.redd.it/one.jpg"},
        {"id": 2, "sourceIndex": 2, "type": "photo", "url": "https://i.redd.it/two.webp"},
    ]
    monkeypatch.setattr(api, "_gallery_list_media", lambda _url, _platform: manifest)
    monkeypatch.setattr(api, "_download_gallery_files", fake_gallery)
    response = client.post("/api/gallery/download", json={
        "url": "https://www.reddit.com/r/videos/comments/abc/title/", "indices": [1, 2], "zip": True
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "tunnel"
    assert body["filename"] == "petik-media.zip"
    file_response = client.get(body["url"])
    assert file_response.status_code == 200
    assert file_response.headers["content-type"] == "application/zip"
    import io, zipfile
    with zipfile.ZipFile(io.BytesIO(file_response.content)) as archive:
        assert archive.namelist() == ["foto-001.jpg", "foto-002.webp"]

def test_mixed_media_zip_uses_source_indices_and_contains_photo_and_video(monkeypatch, tmp_path):
    no_rate_limit(monkeypatch)
    manifest = [
        {"id": 1, "sourceIndex": 1, "type": "photo", "url": "https://i.redd.it/one.jpg"},
        {"id": 2, "sourceIndex": 3, "type": "video", "url": "https://v.redd.it/two.mp4"},
        {"id": 3, "sourceIndex": 4, "type": "photo", "url": "https://i.redd.it/three.webp"},
    ]
    captured = {}
    def fake_download(_url, indices):
        captured["indices"] = indices
        folder = tmp_path / "mixed-gallery-job"
        folder.mkdir()
        video = folder / "003.mp4"
        photo = folder / "004.webp"
        video.write_bytes(b"video-fixture")
        photo.write_bytes(b"photo-fixture")
        return folder, [video, photo]
    monkeypatch.setattr(api, "_gallery_list_media", lambda _url, _platform: manifest)
    monkeypatch.setattr(api, "_download_gallery_files", fake_download)
    response = client.post("/api/gallery/download", json={
        "url": "https://www.reddit.com/r/videos/comments/abc/title/", "indices": [2, 3], "zip": True
    })
    assert response.status_code == 200
    assert captured["indices"] == [3, 4]
    body = response.json()
    assert body["count"] == 2
    file_response = client.get(body["url"])
    import io, zipfile
    with zipfile.ZipFile(io.BytesIO(file_response.content)) as archive:
        assert archive.namelist() == ["video-001.mp4", "foto-002.webp"]


def test_gallery_download_failures_match_missing_source_numbers(tmp_path):
    items = [
        {"id": 1, "sourceIndex": 1, "type": "photo"},
        {"id": 2, "sourceIndex": 3, "type": "video"},
        {"id": 3, "sourceIndex": 4, "type": "photo"},
    ]
    downloaded = tmp_path / "001.jpg"
    downloaded.write_bytes(b"photo")
    failures = api._gallery_download_failures(items, [1, 2, 3], [1, 3, 4], [downloaded])
    assert failures == [
        {"id": 2, "type": "video", "message": "Extractor tidak mengembalikan berkas untuk item ini."},
        {"id": 3, "type": "photo", "message": "Extractor tidak mengembalikan berkas untuk item ini."},
    ]


def test_generic_gallery_endpoint_reports_partial_extractor_result(monkeypatch, tmp_path):
    no_rate_limit(monkeypatch)
    manifest = [
        {"id": 1, "sourceIndex": 1, "type": "photo", "url": "https://i.redd.it/one.jpg"},
        {"id": 2, "sourceIndex": 3, "type": "video", "url": "https://v.redd.it/two.mp4"},
    ]
    monkeypatch.setattr(api, "_gallery_list_media", lambda _url, _platform: manifest)

    def fake_download(_url, indices):
        assert indices == [1, 3]
        folder = tmp_path / "partial-gallery-job"
        folder.mkdir()
        photo = folder / "001.jpg"
        photo.write_bytes(b"photo")
        return folder, [photo]

    monkeypatch.setattr(api, "_download_gallery_files", fake_download)
    response = client.post("/api/gallery/download", json={
        "url": "https://www.reddit.com/r/videos/comments/abc/title/", "indices": [1, 2], "zip": False
    })
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["failedCount"] == 1
    assert body["failures"][0]["id"] == 2
    assert body["failures"][0]["type"] == "video"


def test_gallery_download_individual_files_endpoint(monkeypatch, tmp_path):
    no_rate_limit(monkeypatch)
    def fake_gallery(_url, _indices):
        folder = tmp_path / "gallery-job-individual"
        folder.mkdir()
        first, second = folder / "001.jpg", folder / "002.webp"
        first.write_bytes(b"first-photo")
        second.write_bytes(b"second-photo")
        return folder, [first, second]
    manifest = [
        {"id": 1, "sourceIndex": 1, "type": "photo", "url": "https://i.redd.it/one.jpg"},
        {"id": 2, "sourceIndex": 2, "type": "photo", "url": "https://i.redd.it/two.webp"},
    ]
    monkeypatch.setattr(api, "_gallery_list_media", lambda _url, _platform: manifest)
    monkeypatch.setattr(api, "_download_gallery_files", fake_gallery)
    response = client.post("/api/gallery/download", json={
        "url": "https://www.reddit.com/r/videos/comments/abc/title/", "indices": [1, 2], "zip": False
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["count"] == 2
    for item in body["files"]:
        file_response = client.get(item["url"])
        assert file_response.status_code == 200

def test_gallery_download_rejects_invalid_indices(monkeypatch):
    no_rate_limit(monkeypatch)
    response = client.post("/api/gallery/download", json={
        "url": "https://www.instagram.com/p/ABC123/", "indices": [0, 999], "zip": True
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "error.api.gallery.selection"


def test_gallery_download_command_uses_only_requested_indices(monkeypatch, tmp_path):
    captured = {}
    def fake_run(command, **kwargs):
        captured["command"] = command
        folder = Path(command[command.index("--directory") + 1])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "002.jpg").write_bytes(b"photo-two")
        (folder / "005.webp").write_bytes(b"photo-five")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(api, "_gallery_command", lambda: "/usr/bin/gallery-dl")
    monkeypatch.setattr(api, "TEMP_ROOT", tmp_path)
    monkeypatch.setattr(api.subprocess, "run", fake_run)
    folder, files = api._download_gallery_files("https://www.instagram.com/p/ABC123/", [2, 5])
    assert len(files) == 2
    command = captured["command"]
    assert command[command.index("--range") + 1] == f"1-{api.MAX_GALLERY_SOURCE_ITEMS}"
    assert command[command.index("--filter") + 1] == "num in (2,5)"
    assert "--no-skip" in command
    assert "--filesize-max" in command
    assert command[-1] == "https://www.instagram.com/p/ABC123/"
    import shutil as _shutil
    _shutil.rmtree(folder, ignore_errors=True)


def test_inspect_does_not_mislabel_direct_jpg_as_video(monkeypatch):
    no_rate_limit(monkeypatch)
    monkeypatch.setattr(api, "_extract_info", lambda _url: {
        "id": "photo", "title": "A photo", "ext": "jpg",
        "url": "https://cdn.example/photo.jpg"
    })
    monkeypatch.setattr(api, "_gallery_list_media", lambda _url, _platform: mixed_photo_manifest())
    response = client.post("/api/inspect", json={"url": "https://www.instagram.com/p/ABC123/"})
    assert response.status_code == 200
    body = response.json()
    assert body["videoOptions"] == []
    assert body["audioOptions"] == []
    assert len(body["photoItems"]) == 2


def test_gallery_url_parser_reads_image_format_from_query_and_keeps_source_ids():
    output = "\n".join([
        "https://cdn.example/item-one?token=abc&format=jpeg",
        "https://cdn.example/clip.mp4?token=abc",
        "https://cdn.example/item-two?fm=webp&sig=xyz",
        "https://cdn.example/item-three?mime_type=image%2Fpng&sig=xyz",
    ])
    items = api._parse_gallery_urls(output)
    assert [item["id"] for item in items] == [1, 3, 4]
    assert [item["extension"] for item in items] == ["jpeg", "webp", "png"]


def test_gallery_download_accepts_source_index_above_visible_photo_limit(monkeypatch, tmp_path):
    captured = {}
    def fake_run(command, **kwargs):
        captured["command"] = command
        folder = Path(command[command.index("--directory") + 1])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "025.jpg").write_bytes(b"photo-25")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(api, "_gallery_command", lambda: "/usr/bin/gallery-dl")
    monkeypatch.setattr(api, "TEMP_ROOT", tmp_path)
    monkeypatch.setattr(api.subprocess, "run", fake_run)
    folder, files = api._download_gallery_files("https://www.instagram.com/p/ABC123/", [25])
    assert len(files) == 1
    assert captured["command"][captured["command"].index("--filter") + 1] == "num in (25)"
    import shutil as _shutil
    _shutil.rmtree(folder, ignore_errors=True)


def _threads_fixture_html():
    return '''<html><head><meta property="og:url" content="https://www.threads.com/@demo/post/THREAD123"></head>
    <body><script type="application/json" data-sjs>
    {"data":{"post":{"code":"THREAD123","media_type":8,"carousel_media":[
      {"media_type":1,"image_versions2":{"candidates":[
        {"url":"https://scontent.cdninstagram.com/small.jpg","width":320,"height":320},
        {"url":"https://scontent.cdninstagram.com/large.jpg?format=jpg","width":1080,"height":1080}]}},
      {"media_type":2,"video_versions":[{"url":"https://video.cdninstagram.com/clip.mp4"}],
       "image_versions2":{"candidates":[{"url":"https://scontent.cdninstagram.com/poster.jpg","width":640,"height":640}]}},
      {"media_type":1,"image_versions2":{"candidates":[{"url":"https://scontent.cdninstagram.com/second.webp?fm=webp","width":900,"height":1200}]}}
    ]}}}
    </script></body></html>'''


def test_threads_share_link_resolves_canonical_post_shortcode():
    html = _threads_fixture_html()
    assert api._threads_page_shortcode("https://www.threads.com/share/SHARE123", html) == "THREAD123"


def test_threads_photo_parser_returns_only_real_images_and_best_variant():
    items = api._threads_photo_items("https://www.threads.com/share/SHARE123", _threads_fixture_html())
    assert len(items) == 2
    assert items[0]["url"] == "https://scontent.cdninstagram.com/large.jpg?format=jpg"
    assert items[1]["url"] == "https://scontent.cdninstagram.com/second.webp?fm=webp"
    assert [item["id"] for item in items] == [1, 2]


def test_threads_mixed_parser_preserves_photo_video_order_and_direct_urls():
    items = api._threads_media_items("https://www.threads.com/share/SHARE123", _threads_fixture_html())
    assert [item["type"] for item in items] == ["photo", "video", "photo"]
    assert [item["id"] for item in items] == [1, 2, 3]
    assert items[1]["url"] == "https://video.cdninstagram.com/clip.mp4"
    assert items[1]["thumbnail"] == "https://scontent.cdninstagram.com/poster.jpg"
    assert items[1]["directUrl"] == items[1]["url"]
    assert [item["mediaIndex"] for item in api._media_photo_alias(items)] == [1, 3]


def test_threads_gallery_requires_safe_cdn_hosts():
    assert api._safe_threads_media_url("https://scontent.cdninstagram.com/photo.jpg")
    assert api._safe_threads_media_url("https://video.fna.fbcdn.net/photo.jpg")
    assert not api._safe_threads_media_url("https://example.com/photo.jpg")
    assert not api._safe_threads_media_url("http://scontent.cdninstagram.com/photo.jpg")
    assert not api._safe_threads_media_url("https://127.0.0.1/private.jpg")


def test_threads_inspect_returns_photo_items_without_ytdlp(monkeypatch):
    no_rate_limit(monkeypatch)
    def unavailable(_url):
        raise RuntimeError("Unsupported URL")
    monkeypatch.setattr(api, "_extract_info", unavailable)
    monkeypatch.setattr(api, "_fetch_threads_page", lambda _url: _threads_fixture_html())
    response = client.post("/api/inspect", json={"url": "https://www.threads.com/share/SHARE123"})
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "threads"
    assert len(body["photoItems"]) == 2
    assert [item["type"] for item in body["mediaItems"]] == ["photo", "video", "photo"]
    assert body["mediaCounts"] == {"total": 3, "photos": 2, "videos": 1}
    assert body["galleryAvailable"] is True


def test_threads_gallery_download_routes_to_public_page_resolver(monkeypatch, tmp_path):
    no_rate_limit(monkeypatch)
    mixed = [
        {"id": 1, "sourceIndex": 1, "type": "photo", "url": "https://scontent.cdninstagram.com/a.jpg", "extension": "jpg"},
        {"id": 2, "sourceIndex": 2, "type": "video", "url": "https://video.cdninstagram.com/b.mp4", "extension": "mp4"},
    ]
    def fake_download(_url, platform, items, indices):
        assert platform == "threads"
        assert [item["type"] for item in items] == ["photo", "video"]
        assert indices == [1, 2]
        folder = tmp_path / "threads-gallery-job"
        folder.mkdir()
        photo, video = folder / "foto-001.jpg", folder / "video-002.mp4"
        photo.write_bytes(b"thread-photo")
        video.write_bytes(b"\x00\x00\x00\x18ftypisom-thread-video")
        return folder, [photo, video], []
    monkeypatch.setattr(api, "_threads_list_media", lambda _url: mixed)
    monkeypatch.setattr(api, "_download_meta_media_items", fake_download)
    response = client.post("/api/gallery/download", json={
        "url": "https://www.threads.com/share/SHARE123", "indices": [1, 2], "zip": False
    })
    assert response.status_code == 200
    assert response.json()["service"] == "threads"
    assert response.json()["count"] == 2
    assert [item["filename"].split(".")[-1] for item in response.json()["files"]] == ["jpg", "mp4"]


def test_dailymotion_and_linkedin_are_recognised():
    assert api._normalise_and_validate("https://www.dailymotion.com/video/x123abc")[1] == "dailymotion"
    assert api._normalise_and_validate("https://dai.ly/x123abc")[1] == "dailymotion"
    assert api._normalise_and_validate("https://www.linkedin.com/posts/example_123")[1] == "linkedin"



def test_instagram_public_graphql_normalizes_mixed_photo_video_carousel():
    payload = {
        "edge_sidecar_to_children": {"edges": [
            {"node": {"is_video": False, "display_url": "https://scontent.cdninstagram.com/a.jpg"}},
            {"node": {"is_video": True, "video_url": "https://video.cdninstagram.com/b.mp4", "display_url": "https://scontent.cdninstagram.com/poster.jpg"}},
            {"node": {"is_video": False, "display_url": "https://scontent.cdninstagram.com/c.jpg"}},
        ]}
    }
    items = api._instagram_media_items(payload)
    assert [item["type"] for item in items] == ["photo", "video", "photo"]
    assert [item["url"] for item in items] == [
        "https://scontent.cdninstagram.com/a.jpg",
        "https://video.cdninstagram.com/b.mp4",
        "https://scontent.cdninstagram.com/c.jpg",
    ]
    assert [item["id"] for item in items] == [1, 2, 3]
    photos = api._instagram_photo_items(payload)
    assert [item["id"] for item in photos] == [1, 2]
    assert [item["mediaIndex"] for item in photos] == [1, 3]


def test_instagram_public_fallback_uses_anonymous_graphql_session(monkeypatch):
    payload = {"data": {"xdt_shortcode_media": {
        "edge_sidecar_to_children": {"edges": [
            {"node": {"is_video": False, "display_url": "https://scontent.cdninstagram.com/a.jpg"}},
            {"node": {"is_video": False, "display_url": "https://scontent.cdninstagram.com/b.jpg"}},
        ]}
    }}}
    class FakeResponse:
        status_code = 200
        url = "https://www.instagram.com/"
        text = '<script id="__eqmc">{"l":"anon-lsd"}</script>'
        def __init__(self, data=None): self._data = data
        def json(self): return self._data
    class FakeSession:
        def __init__(self, **kwargs): self.cookies = {"csrftoken": "anonymous"}; self.calls = []; self.kwargs = kwargs
        def get(self, url, **kwargs):
            self.calls.append(("GET", url, kwargs))
            if "get_ruling_for_content" in url: return FakeResponse({"status": "ok"})
            return FakeResponse()
        def post(self, url, **kwargs):
            self.calls.append(("POST", url, kwargs))
            return FakeResponse(payload)
        def close(self): pass
    class FakeCurl:
        last_session = None
        @classmethod
        def Session(cls, **kwargs):
            cls.last_session = FakeSession(**kwargs)
            return cls.last_session
    monkeypatch.setattr(api, "curl_requests", FakeCurl)
    monkeypatch.setattr(api, "HAS_CURL_CFFI", True)
    items = api._instagram_public_gallery("https://www.instagram.com/p/ABC123/")
    assert len(items) == 2
    assert len(FakeCurl.last_session.calls) == 4
    assert FakeCurl.last_session.kwargs == {"impersonate": "chrome"}
    assert any(call[0] == "POST" and "api/graphql" in call[1] for call in FakeCurl.last_session.calls)
    assert "cookies.txt" not in str(FakeCurl.last_session.calls)


def test_health_reports_gallery_and_public_fallback_capabilities():
    body = client.get("/health").json()
    assert body["version"] == "1.9.0"
    assert "threads" in body["galleryPlatforms"]
    assert body["threadsPhotoResolver"] == "public-page-json"
    assert body["instagramPhotoResolver"] == "gallery-dl+logged-out-graphql+page-json"
    assert body["facebookPhotoResolver"] == "gallery-dl+og-image+post-scoped-json"
    assert isinstance(body["socialPublicFallbackAvailable"], bool)
    assert isinstance(body["instagramPublicFallbackAvailable"], bool)


def test_threads_redirects_reject_external_hosts_before_following():
    import httpx
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://example.com/redirect-target"})
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client_http:
        try:
            with api._safe_threads_stream(client_http, "https://www.threads.com/share/ABC"):
                pass
        except RuntimeError as exc:
            assert "Pengalihan" in str(exc)
        else:
            raise AssertionError("External redirect must be rejected")
    assert len(calls) == 1


def test_threads_gallery_download_fetches_and_validates_image_bytes(monkeypatch, tmp_path):
    import httpx
    image_bytes = b"\xff\xd8\xff" + b"petik-test-jpeg"
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"Content-Type": "image/jpeg"}, content=image_bytes, request=request
    ))
    original_client = httpx.Client
    monkeypatch.setattr(api, "TEMP_ROOT", tmp_path)
    monkeypatch.setattr(api, "_threads_list_media", lambda _url: [{
        "id": 1, "sourceIndex": 1, "type": "photo", "kind": "image",
        "url": "https://scontent.cdninstagram.com/test.jpg", "extension": "jpg"
    }])
    monkeypatch.setattr(api.httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs))
    folder, files, failures = api._download_threads_gallery_files("https://www.threads.com/share/ABC", [1])
    assert failures == []
    assert len(files) == 1
    assert files[0].read_bytes() == image_bytes
    assert files[0].suffix == ".jpg"
    import shutil as _shutil
    _shutil.rmtree(folder, ignore_errors=True)


def test_mixed_meta_download_keeps_successes_when_one_item_fails(monkeypatch, tmp_path):
    import httpx
    image_bytes = b"\xff\xd8\xffgood-image"
    video_bytes = b"\x00\x00\x00\x18ftypisom-good-video"
    def handler(request):
        if request.url.path.endswith("three.jpg"):
            return httpx.Response(404, request=request)
        if request.url.path.endswith(".jpg"):
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=image_bytes, request=request)
        if request.url.path.endswith(".mp4"):
            return httpx.Response(200, headers={"Content-Type": "video/mp4"}, content=video_bytes, request=request)
        return httpx.Response(404, request=request)
    transport = httpx.MockTransport(handler)
    original_client = httpx.Client
    monkeypatch.setattr(api, "TEMP_ROOT", tmp_path)
    manifest = [
        {"id": 1, "type": "photo", "url": "https://scontent.cdninstagram.com/one.jpg", "extension": "jpg"},
        {"id": 2, "type": "video", "url": "https://video.cdninstagram.com/two.mp4", "extension": "mp4"},
        {"id": 3, "type": "photo", "url": "https://scontent.cdninstagram.com/three.jpg", "extension": "jpg"},
    ]
    monkeypatch.setattr(api, "_threads_list_media", lambda _url: manifest)
    monkeypatch.setattr(api.httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs))
    folder, files, failures = api._download_threads_gallery_files("https://www.threads.com/share/ABC", [1, 2, 3])
    assert [path.suffix for path in files] == [".jpg", ".mp4"]
    assert len(failures) == 1 and failures[0]["id"] == 3
    import shutil as _shutil
    _shutil.rmtree(folder, ignore_errors=True)


def test_gallery_endpoint_reports_partial_media_failures(monkeypatch, tmp_path):
    no_rate_limit(monkeypatch)
    manifest = [
        {"id": 1, "sourceIndex": 1, "type": "photo", "url": "https://scontent.cdninstagram.com/one.jpg"},
        {"id": 2, "sourceIndex": 2, "type": "video", "url": "https://video.cdninstagram.com/two.mp4"},
    ]
    def fake_download(_url, _platform, _items, _indices):
        folder = tmp_path / "partial-endpoint"
        folder.mkdir()
        file_path = folder / "foto-001.jpg"
        file_path.write_bytes(b"\xff\xd8\xffpartial-ok")
        return folder, [file_path], [{"id": 2, "type": "video", "message": "CDN timeout"}]
    monkeypatch.setattr(api, "_instagram_public_media", lambda _url: manifest)
    monkeypatch.setattr(api, "_download_social_gallery_items", fake_download)
    monkeypatch.setattr(api, "_store_file", lambda *_args: "partial-token")
    response = client.post("/api/gallery/download", json={
        "url": "https://www.instagram.com/p/ABC123/", "indices": [1, 2], "zip": False
    })
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1 and body["failedCount"] == 1
    assert body["failures"][0]["id"] == 2


def test_threads_stream_rejects_unsafe_initial_host_before_network_request():
    import httpx
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content="unexpected", request=request)
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client_http:
        try:
            with api._safe_threads_stream(client_http, "https://example.com/private"):
                pass
        except RuntimeError as exc:
            assert "daftar host HTTPS" in str(exc)
        else:
            raise AssertionError("Unsafe initial host must be rejected")
    assert calls == []



def test_instagram_shortcode_to_numeric_media_id_matches_base64_alphabet():
    assert api._instagram_shortcode_to_media_id("ABC") == str(0 * 64 * 64 + 1 * 64 + 2)


def test_instagram_product_payload_parser_handles_carousel_and_video_mixture():
    media = {"carousel_media": [
        {"image_versions2": {"candidates": [
            {"url": "https://scontent.cdninstagram.com/small.jpg", "width": 320, "height": 320},
            {"url": "https://scontent.cdninstagram.com/large.jpg?format=jpg", "width": 1080, "height": 1080},
        ]}},
        {"video_versions": [{"url": "https://video.cdninstagram.com/clip.mp4"}],
         "image_versions2": {"candidates": [{"url": "https://scontent.cdninstagram.com/poster.jpg", "width": 640, "height": 640}]}},
        {"image_versions2": {"candidates": [{"url": "https://scontent.cdninstagram.com/second.webp?fm=webp", "width": 900, "height": 1200}]}},
    ]}
    items = api._instagram_media_items(media)
    assert [item["type"] for item in items] == ["photo", "video", "photo"]
    assert [item["url"] for item in items] == [
        "https://scontent.cdninstagram.com/large.jpg?format=jpg",
        "https://video.cdninstagram.com/clip.mp4",
        "https://scontent.cdninstagram.com/second.webp?fm=webp",
    ]
    photos = api._instagram_photo_items(media)
    assert len(photos) == 2
    assert [item["mediaIndex"] for item in photos] == [1, 3]


def test_instagram_current_graphql_payload_unwraps_logged_out_media():
    product = {"image_versions2": {"candidates": [{"url": "https://scontent.cdninstagram.com/one.jpg", "width": 900, "height": 900}]}}
    root = {"data": {"xig_polaris_media": {"if_not_gated_logged_out": product}}}
    assert api._instagram_find_product_info(root) == product


def test_non_gallery_platform_does_not_advertise_undownloadable_multi_media():
    info = {
        "_petik_entries": [
            {"url": "https://cdn.example/video-1.mp4", "ext": "mp4"},
            {"url": "https://cdn.example/video-2.mp4", "ext": "mp4"},
        ]
    }
    assert api._gallery_media_for_post("https://www.tiktok.com/@user/video/123", "tiktok", info) == []


def test_media_normalizer_rejects_non_public_direct_media_urls():
    items = api._normalise_media_items([
        {"id": 1, "sourceIndex": 1, "type": "photo", "url": "https://127.0.0.1/private.jpg"},
        {"id": 2, "sourceIndex": 2, "type": "video", "url": "https://cdn.example/public.mp4"},
    ])
    assert [item["url"] for item in items] == ["https://cdn.example/public.mp4"]


def test_gallery_dl_parser_detects_mixed_media_and_preserves_source_positions():
    output = "\n".join([
        "https://i.redd.it/first.jpg",
        "https://v.redd.it/playlist.m3u8",
        "https://v.redd.it/second.mp4",
        "https://i.redd.it/third.webp",
    ])
    items = api._parse_gallery_media_urls(output)
    assert [item["type"] for item in items] == ["photo", "video", "photo"]
    assert [item["id"] for item in items] == [1, 2, 3]
    assert [item["sourceIndex"] for item in items] == [1, 3, 4]


def test_gallery_source_indices_translate_ui_selection_to_extractor_positions():
    items = [
        {"id": 1, "sourceIndex": 1},
        {"id": 2, "sourceIndex": 3},
        {"id": 3, "sourceIndex": 4},
    ]
    assert api._gallery_source_indices(items, [2, 3]) == [3, 4]


def test_facebook_public_html_parser_ignores_unrelated_page_cdn_urls():
    html = ('''<html><head>
      <meta property="og:image" content="https://scontent.xx.fbcdn.net/v/t39.30808-6/a.jpg?stp=abc&amp;oe=123">
      <meta property="og:image" content="https://scontent.xx.fbcdn.net/v/t39.30808-6/a.jpg?stp=abc&amp;oe=123">
    </head><body><script>"image_uri":"https://scontent.xx.fbcdn.net/v/t39.30808-6/unrelated.webp?format=webp&amp;oe=999"</script></body></html>''')
    items = api._facebook_photo_items_from_html(html, "https://www.facebook.com/photo/?fbid=123")
    assert len(items) == 1
    assert items[0]["extension"] == "jpg"


def test_facebook_parser_extracts_only_target_post_attachment_photos():
    import json
    target_story = {
        "id": "123",
        "comet_sections": {"content": {"story": {"attachments": [{
            "style_type_renderer": {"attachment": {"all_subattachments": {"nodes": [
                {"media": {"__typename": "Photo", "id": "photo-1", "image": {"uri": "https://scontent.xx.fbcdn.net/phone.jpg"}}},
                {"media": {"__typename": "Photo", "id": "photo-2", "image": {"uri": "https://scontent.xx.fbcdn.net/detail.webp?format=webp"}}},
            ]}}}
        }]}}}
    }
    unrelated_story = {
        "id": "999",
        "attachments": [{"media": {"__typename": "Photo", "id": "other", "image": {"uri": "https://scontent.xx.fbcdn.net/recommendation.jpg"}}}],
    }
    html = '<script type="application/json">' + json.dumps({"data": {"nodes": [target_story, unrelated_story]}}) + '</script>'
    items = api._facebook_photo_items_from_html(html, "https://www.facebook.com/example/posts/123")
    assert [item["url"] for item in items] == [
        "https://scontent.xx.fbcdn.net/phone.jpg",
        "https://scontent.xx.fbcdn.net/detail.webp?format=webp",
    ]


def test_facebook_parser_does_not_guess_album_from_unbound_json():
    import json
    page = '<script type="application/json">' + json.dumps({"Photo": {
        "__typename": "Photo", "id": "unrelated", "image": {"uri": "https://scontent.xx.fbcdn.net/random.jpg"}
    }}) + '</script>'
    assert api._facebook_photo_items_from_html(page, "https://www.facebook.com/posts/123") == []


def test_facebook_public_resolver_passes_source_url_to_scoped_parser(monkeypatch):
    import json
    target_story = {
        "id": "123",
        "attachments": [{"media": {"__typename": "Photo", "id": "p1", "image": {
            "uri": "https://scontent.xx.fbcdn.net/phone-only.jpg"
        }}}],
    }
    page = '<meta property="og:image" content="https://scontent.xx.fbcdn.net/phone-only.jpg">' + (
        '<script type="application/json">' + json.dumps({"data": {"story": target_story}}) + '</script>'
    )

    class FakeResponse:
        status_code = 200
        url = "https://www.facebook.com/example/posts/123"
        text = page
        headers = {}

    class FakeSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
        def get(self, *_args, **_kwargs):
            return FakeResponse()
        def close(self):
            pass

    class FakeCurl:
        @staticmethod
        def Session(**kwargs):
            return FakeSession(**kwargs)

    monkeypatch.setattr(api, "curl_requests", FakeCurl)
    monkeypatch.setattr(api, "HAS_CURL_CFFI", True)
    items = api._facebook_public_gallery("https://www.facebook.com/example/posts/123")
    assert len(items) == 1
    assert items[0]["url"].endswith("phone-only.jpg")


def test_facebook_video_thumbnail_is_not_misreported_as_photo_gallery():
    html = '''<meta property="og:type" content="video.other">
      <meta property="og:video" content="https://video.xx.fbcdn.net/clip.mp4">
      <meta property="og:image" content="https://scontent.xx.fbcdn.net/video-poster.jpg">'''
    assert api._facebook_photo_items_from_html(html, "https://www.facebook.com/watch/?v=123") == []


def test_facebook_parser_uses_canonical_post_id_when_share_url_has_no_id():
    import json
    story = {
        "id": "123",
        "attachments": [{"media": {"__typename": "Photo", "id": "p1", "image": {
            "uri": "https://scontent.xx.fbcdn.net/share-post-photo.jpg"
        }}}],
    }
    html = '<meta property="og:url" content="https://www.facebook.com/demo/posts/123">' + (
        '<script type="application/json">' + json.dumps({"data": {"story": story}}) + '</script>'
    )
    items = api._facebook_photo_items_from_html(html, "https://www.facebook.com/share/p/SHARETOKEN/")
    assert len(items) == 1
    assert items[0]["url"].endswith("share-post-photo.jpg")


def test_social_media_hosts_allow_only_meta_image_cdns():
    assert api._safe_social_media_url("https://scontent.xx.fbcdn.net/photo.jpg")
    assert api._safe_social_media_url("https://scontent.cdninstagram.com/photo.jpg")
    assert not api._safe_social_media_url("https://example.com/photo.jpg")
    assert not api._safe_social_media_url("http://scontent.xx.fbcdn.net/photo.jpg")
    assert not api._safe_social_media_url("https://127.0.0.1/photo.jpg")



def test_inspect_falls_back_to_instagram_photo_resolver_after_no_video_error(monkeypatch):
    no_rate_limit(monkeypatch)
    monkeypatch.setattr(api, "_extract_info", lambda _url: (_ for _ in ()).throw(RuntimeError("There is no video in this post")))
    monkeypatch.setattr(api, "_gallery_list_media", lambda _url, _platform: [])
    monkeypatch.setattr(api, "_instagram_public_media", lambda _url: [
        {**item, "type": "photo", "sourceIndex": index, "kind": "image"}
        for index, item in enumerate(sample_photo_items(), 1)
    ])
    response = client.post("/api/inspect", json={"url": "https://www.instagram.com/p/ABC123/"})
    assert response.status_code == 200
    assert response.json()["galleryAvailable"] is True
    assert len(response.json()["photoItems"]) == 2


def test_inspect_falls_back_to_facebook_public_page_resolver(monkeypatch):
    no_rate_limit(monkeypatch)
    monkeypatch.setattr(api, "_extract_info", lambda _url: (_ for _ in ()).throw(RuntimeError("Unsupported URL")))
    monkeypatch.setattr(api, "_gallery_list_media", lambda _url, _platform: [])
    monkeypatch.setattr(api, "_facebook_public_gallery", lambda _url: sample_photo_items())
    response = client.post("/api/inspect", json={"url": "https://www.facebook.com/photo/?fbid=123"})
    assert response.status_code == 200
    assert response.json()["galleryAvailable"] is True
    assert response.json()["service"] == "facebook"


def test_instagram_photo_error_is_reported_as_gallery_access_issue():
    result = api._extract_error(RuntimeError("There is no video in this post"), "instagram")
    assert result.status_code == 502
    assert result.body
    assert b"Petik sudah mencoba extractor foto" in result.body


def test_social_gallery_download_fetches_meta_cdn_and_checks_image_bytes(monkeypatch, tmp_path):
    import httpx
    image_bytes = b"\xff\xd8\xff" + b"petik-social-test-jpeg"
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"Content-Type": "image/jpeg"}, content=image_bytes, request=request
    ))
    original_client = httpx.Client
    monkeypatch.setattr(api, "TEMP_ROOT", tmp_path)
    monkeypatch.setattr(api.httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs))
    items = [{"id": 1, "url": "https://scontent.cdninstagram.com/test.jpg", "extension": "jpg"}]
    folder, files, failures = api._download_social_gallery_items(
        "https://www.instagram.com/p/ABC123/", "instagram", items, [1]
    )
    assert failures == []
    assert len(files) == 1
    assert files[0].read_bytes() == image_bytes
    assert files[0].suffix == ".jpg"
    import shutil as _shutil
    _shutil.rmtree(folder, ignore_errors=True)


def test_social_media_stream_rejects_redirect_to_unapproved_host():
    import httpx
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://example.com/private.jpg"}, request=request)
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client_http:
        try:
            with api._safe_social_stream(client_http, "https://scontent.xx.fbcdn.net/photo.jpg"):
                pass
        except RuntimeError as exc:
            assert "Pengalihan CDN" in str(exc)
        else:
            raise AssertionError("External CDN redirect must be rejected")
    assert len(calls) == 1



def test_instagram_gallery_download_routes_through_public_cdn_items(monkeypatch, tmp_path):
    no_rate_limit(monkeypatch)
    monkeypatch.setattr(api, "TEMP_ROOT", tmp_path)
    calls = []
    def fake_download(url, platform, items, indices):
        calls.append((url, platform, [item["id"] for item in items], indices))
        folder = tmp_path / "instagram-download"
        folder.mkdir(exist_ok=True)
        file_path = folder / "foto-001.jpg"
        file_path.write_bytes(b"\xff\xd8\xfftest")
        return folder, [file_path], []
    monkeypatch.setattr(api, "_instagram_public_media", lambda _url: [{
        "id": 1, "sourceIndex": 1, "type": "photo", "kind": "image",
        "url": "https://scontent.cdninstagram.com/photo.jpg", "extension": "jpg"
    }])
    monkeypatch.setattr(api, "_download_social_gallery_items", fake_download)
    monkeypatch.setattr(api, "_download_gallery_files", lambda *_args: (_ for _ in ()).throw(AssertionError("fallback not expected")))
    monkeypatch.setattr(api, "_store_file", lambda *_args: "test-token")
    response = client.post("/api/gallery/download", json={
        "url": "https://www.instagram.com/p/ABC123/", "indices": [1], "zip": True
    })
    assert response.status_code == 200
    assert response.json()["status"] == "tunnel"
    assert response.json()["service"] == "instagram"
    assert calls and calls[0][1:] == ("instagram", [1], [1])



def test_facebook_unsupported_url_gets_public_access_message():
    result = api._extract_error(RuntimeError("Unsupported URL"), "facebook")
    assert result.status_code == 502
    assert b"Facebook tidak mengembalikan data media publik" in result.body


def test_inspect_returns_direct_video_and_audio_urls_independent_of_save(monkeypatch):
    no_rate_limit(monkeypatch)
    monkeypatch.setattr(api, "_extract_info", lambda _url: {
        "id": "direct-demo", "title": "Direct demo", "formats": [
            {"format_id": "18", "ext": "mp4", "height": 360, "vcodec": "avc1", "acodec": "mp4a", "url": "https://cdn.example/video-360.mp4"},
            {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "url": "https://cdn.example/audio.m4a"},
        ],
    })
    response = client.post("/api/inspect", json={"url": "https://vimeo.com/123456"})
    assert response.status_code == 200
    direct = response.json()["directUrls"]
    assert direct["video"] == "https://cdn.example/video-360.mp4"
    assert direct["audio"] == "https://cdn.example/audio.m4a"


def test_direct_media_url_validation_rejects_local_and_credentials():
    assert api._safe_preview_url("https://cdn.example/video.mp4") == "https://cdn.example/video.mp4"
    for value in (
        "javascript:alert(1)", "ftp://cdn.example/video.mp4", "http://127.0.0.1/private.mp4",
        "http://192.168.1.2/file.mp4", "http://localhost/video.mp4",
        "https://user:pass@cdn.example/video.mp4", "http://169.254.169.254/latest/meta-data",
    ):
        assert api._safe_preview_url(value) is None


def test_direct_video_url_not_returned_for_video_only_dash_or_hls_formats():
    info = {"formats": [
        {"url": "https://cdn.example/video-only.mp4", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 1080},
        {"url": "https://cdn.example/manifest.m3u8", "ext": "m3u8", "vcodec": "avc1", "acodec": "none", "protocol": "m3u8_native"},
    ]}
    assert api._direct_media_urls(info, "vimeo")["video"] is None


def test_direct_audio_url_inferred_from_audio_extension_without_codec_fields():
    info = {"url": "https://cdn.example/direct-track.mp3", "ext": "mp3"}
    assert api._direct_media_urls(info, "soundcloud")["audio"] == "https://cdn.example/direct-track.mp3"


def test_youtube_uses_mweb_only_when_bgutil_provider_is_ready(monkeypatch):
    monkeypatch.setattr(api, "_bgutil_provider_status", lambda: {
        "ready": True,
        "baseUrl": "http://127.0.0.1:4416",
    })
    options = api._yt_common_options("youtube")
    assert options["extractor_args"]["youtube"]["player_client"] == ["mweb"]
    assert options["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == ["http://127.0.0.1:4416"]


def test_youtube_does_not_force_mweb_when_provider_is_unavailable(monkeypatch):
    monkeypatch.setattr(api, "_bgutil_provider_status", lambda: {
        "ready": False,
        "baseUrl": "http://127.0.0.1:4416",
    })
    options = api._yt_common_options("youtube")
    assert "extractor_args" not in options


def test_bgutil_status_is_disabled_without_network_when_flag_is_off(monkeypatch):
    monkeypatch.setenv("BGUTIL_POT_PROVIDER_ENABLED", "0")
    monkeypatch.setattr(api.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network should not be used")))
    status = api._bgutil_provider_status()
    assert status["enabled"] is False
    assert status["ready"] is False
    assert status["serverAvailable"] is False


def test_youtube_error_explains_po_token_and_ip_restrictions():
    import json
    response = api._extract_error(RuntimeError("Sign in to confirm you're not a bot"), "youtube")
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["error"]["code"] == "error.api.youtube.restricted"
    assert "IP server Petik" in payload["error"]["context"]["message"]


def test_bgutil_provider_status_requires_matching_plugin_and_server(monkeypatch):
    import importlib.metadata
    import io

    monkeypatch.setenv("BGUTIL_POT_PROVIDER_ENABLED", "1")
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "2.0.2")
    monkeypatch.setattr(
        api.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(b'{"version":"2.0.2","server_uptime":12}'),
    )
    status = api._bgutil_provider_status()
    assert status["ready"] is True
    assert status["pluginVersion"] == "2.0.2"
    assert status["serverVersion"] == "2.0.2"


def test_bgutil_provider_status_rejects_version_mismatch(monkeypatch):
    import importlib.metadata
    import io

    monkeypatch.setenv("BGUTIL_POT_PROVIDER_ENABLED", "1")
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "2.0.2")
    monkeypatch.setattr(
        api.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(b'{"version":"2.0.1","server_uptime":12}'),
    )
    status = api._bgutil_provider_status()
    assert status["serverAvailable"] is True
    assert status["ready"] is False
