"""Тесты стадии C: host_and_uniquify (заглушки PhotoHost/Uniquifier, без сети)."""

from __future__ import annotations

import json

from src.adapters.photo_host import HostedImage
from src.adapters.uniquifier import NoOpUniquifier, UniquifiedImage
from src.pipeline.photos import host_and_uniquify
from src.pipeline.state import StateStore


# ── Заглушки адаптеров ─────────────────────────────────────────────
class FakeHost:
    def __init__(self, fail_urls: set[str] | None = None):
        self.calls = []
        self.fail = fail_urls or set()

    def upload_url(self, image_url, name=None):
        self.calls.append(image_url)
        if image_url in self.fail:
            raise RuntimeError("host boom")
        return HostedImage(url=f"https://i.ibb.co/h/{name}.jpg",
                           delete_url="https://ibb.co/del", id="x", raw={})


class CountingUniquifier:
    def __init__(self):
        self.calls = []

    def uniquify_url(self, image_url, name=None):
        self.calls.append(image_url)
        return UniquifiedImage(url=image_url.replace("/h/", "/u/"), raw={})


def _write_found(photos_dir, vin, urls):
    p = photos_dir / vin / "found.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"vin": vin, "photos": [
        {"image_url": u, "status": "фото-факт"} for u in urls
    ]}), encoding="utf-8")


# ── Тесты ──────────────────────────────────────────────────────────
def test_host_and_uniquify_writes_hosted_and_caches(tmp_path):
    photos = tmp_path / "photos"
    store = StateStore(tmp_path / "state")
    _write_found(photos, "V1", ["https://s/1.jpg", "https://s/2.jpg"])
    host, uniq = FakeHost(), CountingUniquifier()

    res = host_and_uniquify(["V1"], host, uniq, store, photos)
    assert res[0].ok and res[0].count_ok == 2 and not res[0].cached
    data = json.loads((photos / "V1" / "hosted.json").read_text(encoding="utf-8"))
    assert data["images"][0]["hosted_url"].startswith("https://i.ibb.co")
    assert data["images"][0]["unique_url"].startswith("https://i.ibb.co/u/")
    assert host.calls == ["https://s/1.jpg", "https://s/2.jpg"]

    # Повторный прогон: found.json не менялся → без новых заливок.
    res2 = host_and_uniquify(["V1"], host, uniq, store, photos)
    assert res2[0].cached and res2[0].count_ok == 2
    assert host.calls == ["https://s/1.jpg", "https://s/2.jpg"]  # без повторов


def test_one_photo_failure_does_not_stop_others(tmp_path):
    photos = tmp_path / "photos"
    store = StateStore(tmp_path / "state")
    _write_found(photos, "V1", ["https://ok/1.jpg", "https://bad/2.jpg"])
    host = FakeHost(fail_urls={"https://bad/2.jpg"})

    res = host_and_uniquify(["V1"], host, NoOpUniquifier(), store, photos)
    assert res[0].ok and res[0].count_ok == 1 and res[0].count_total == 2
    imgs = json.loads((photos / "V1" / "hosted.json").read_text(encoding="utf-8"))["images"]
    assert imgs[0]["ok"] is True and imgs[1]["ok"] is False and imgs[1]["error"]


def test_noop_uniquifier_passes_through(tmp_path):
    photos = tmp_path / "photos"
    store = StateStore(tmp_path / "state")
    _write_found(photos, "V1", ["https://s/1.jpg"])
    res = host_and_uniquify(["V1"], FakeHost(), NoOpUniquifier(), store, photos)
    img = json.loads((photos / "V1" / "hosted.json").read_text(encoding="utf-8"))["images"][0]
    # no-op: unique_url == hosted_url
    assert img["unique_url"] == img["hosted_url"]


def test_missing_found_json_is_skipped(tmp_path):
    photos = tmp_path / "photos"
    store = StateStore(tmp_path / "state")
    res = host_and_uniquify(["NOVIN"], FakeHost(), NoOpUniquifier(), store, photos)
    assert res[0].ok and res[0].count_total == 0


def test_all_failures_not_cached(tmp_path):
    photos = tmp_path / "photos"
    store = StateStore(tmp_path / "state")
    _write_found(photos, "V1", ["https://bad/1.jpg"])
    host = FakeHost(fail_urls={"https://bad/1.jpg"})
    res = host_and_uniquify(["V1"], host, NoOpUniquifier(), store, photos)
    assert res[0].ok is False
    # не помечено свежим → повторится
    from src.pipeline.photos import _hash_file
    h = _hash_file(photos / "V1" / "found.json")
    assert store.is_fresh("V1", "photos", h) is False
