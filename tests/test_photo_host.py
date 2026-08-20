"""Тесты адаптера imgbb на подставном poster (без сети и без requests)."""

from __future__ import annotations

import pytest

from src.adapters.photo_host import HostedImage, ImgbbPhotoHost, PhotoHostError


def _ok_body(url="https://i.ibb.co/abc/f.jpg"):
    return {
        "success": True,
        "status": 200,
        "data": {
            "id": "2ndCYJK",
            "url": url,
            "display_url": "https://i.ibb.co/disp/f.jpg",
            "delete_url": "https://ibb.co/2ndCYJK/deltoken",
            "image": {"url": url},
        },
    }


def test_upload_url_success_parses_direct_link():
    calls = []

    def poster(endpoint, data):
        calls.append((endpoint, data))
        return 200, _ok_body()

    host = ImgbbPhotoHost("KEY", poster=poster)
    res = host.upload_url("https://site.ru/car/1.jpg", name="car1")
    assert isinstance(res, HostedImage)
    assert res.url == "https://i.ibb.co/abc/f.jpg"
    assert res.delete_url.endswith("deltoken")
    # ключ и image ушли формой
    assert calls[0][1]["key"] == "KEY"
    assert calls[0][1]["image"] == "https://site.ru/car/1.jpg"
    assert calls[0][1]["name"] == "car1"


def test_upload_bytes_sends_base64():
    seen = {}

    def poster(endpoint, data):
        seen.update(data)
        return 200, _ok_body()

    host = ImgbbPhotoHost("KEY", poster=poster)
    host.upload_bytes(b"\x89PNG\r\n", name="x.png")
    import base64
    assert seen["image"] == base64.b64encode(b"\x89PNG\r\n").decode()


def test_expiration_included_when_set():
    seen = {}

    def poster(endpoint, data):
        seen.update(data)
        return 200, _ok_body()

    host = ImgbbPhotoHost("KEY", expiration_seconds=600, poster=poster)
    host.upload_url("https://s/1.jpg")
    assert seen["expiration"] == "600"


def test_retry_on_503_then_success(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)  # без задержек в тесте
    seq = [(503, {"status_txt": "Service Unavailable"}), (200, _ok_body())]

    def poster(endpoint, data):
        return seq.pop(0)

    host = ImgbbPhotoHost("KEY", max_retries=4, poster=poster)
    assert host.upload_url("https://s/1.jpg").url.startswith("https://i.ibb.co")


def test_non_retryable_400_raises():
    def poster(endpoint, data):
        return 400, {"error": {"message": "Invalid API v1 key"}, "status_txt": "Bad Request"}

    host = ImgbbPhotoHost("KEY", poster=poster)
    with pytest.raises(PhotoHostError) as e:
        host.upload_url("https://s/1.jpg")
    assert "Invalid API v1 key" in str(e.value)


def test_success_but_no_url_raises():
    def poster(endpoint, data):
        return 200, {"success": True, "status": 200, "data": {"id": "x"}}

    host = ImgbbPhotoHost("KEY", poster=poster)
    with pytest.raises(PhotoHostError):
        host.upload_url("https://s/1.jpg")


def test_empty_key_rejected():
    with pytest.raises(PhotoHostError):
        ImgbbPhotoHost("")


def test_network_error_retries_then_raises(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)

    def poster(endpoint, data):
        raise ConnectionError("boom")

    host = ImgbbPhotoHost("KEY", max_retries=3, poster=poster)
    with pytest.raises(PhotoHostError) as e:
        host.upload_url("https://s/1.jpg")
    assert "недоступен" in str(e.value)
