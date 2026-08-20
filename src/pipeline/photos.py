"""Стадия C (оркестрация): found.json → хостинг (C3) → уникализация (C4).

Для каждого авто берёт найденные фото (из стадии B+C1), заливает на
хостинг (imgbb), затем уникализирует (pixflow / no-op) и сохраняет
data/photos/<VIN>/hosted.json. Идемпотентность по содержимому found.json:
не перезаливаем, если фото не менялись.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from src.pipeline.models import PhotoRef, sanitize_vin
from src.pipeline.state import StateStore

logger = logging.getLogger("photos")

STAGE = "photos"


@dataclass
class HostedPhoto:
    source_image_url: str
    hosted_url: str = ""
    unique_url: str = ""
    delete_url: str | None = None
    status: str = ""
    ok: bool = True
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "source_image_url": self.source_image_url,
            "hosted_url": self.hosted_url,
            "unique_url": self.unique_url,
            "delete_url": self.delete_url,
            "status": self.status,
            "ok": self.ok,
            "error": self.error,
        }


@dataclass
class PhotosResult:
    vin: str
    hosted_path: Path
    count_ok: int
    count_total: int
    cached: bool
    ok: bool
    error: str | None = None


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)  # атомарный rename (R5)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def _load_found(found_path: Path) -> list[PhotoRef]:
    data = json.loads(found_path.read_text(encoding="utf-8"))
    return [PhotoRef.from_dict(p) for p in data.get("photos", [])]


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def host_and_uniquify(
    vins: list[str],
    photo_host,               # PhotoHost (DI): upload_url(...)
    uniquifier,               # Uniquifier (DI): uniquify_url(...)
    store: StateStore,
    photos_dir: str | Path,
    force: bool = False,
) -> list[PhotosResult]:
    """Хостит и уникализирует фото каждого VIN. Кэш по содержимому found.json."""
    photos_dir = Path(photos_dir)
    results: list[PhotosResult] = []

    for vin in vins:
        vin_safe = sanitize_vin(vin)
        found_path = photos_dir / vin_safe / "found.json"
        hosted_path = photos_dir / vin_safe / "hosted.json"

        if not found_path.is_file():
            logger.info("[%s] нет found.json — фото пропущены", vin)
            results.append(PhotosResult(vin, hosted_path, 0, 0, cached=False, ok=True))
            continue

        content_hash = _hash_file(found_path)
        if not force and store.is_fresh(vin, STAGE, content_hash) and hosted_path.is_file():
            count = _count_hosted(hosted_path)
            logger.info("[%s] фото уже размещены — пропуск (кэш): %d", vin, count)
            results.append(PhotosResult(vin, hosted_path, count, count, cached=True, ok=True))
            continue

        try:
            found = _load_found(found_path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("[%s] битый found.json: %s", vin, exc)
            results.append(PhotosResult(vin, hosted_path, 0, 0, cached=False,
                                        ok=False, error=str(exc)))
            continue

        hosted: list[HostedPhoto] = []
        for i, ph in enumerate(found, start=1):
            name = f"{vin_safe}_{i}"
            item = HostedPhoto(source_image_url=ph.image_url, status=ph.status)
            try:
                up = photo_host.upload_url(ph.image_url, name=name)   # C3
                item.hosted_url = up.url
                item.delete_url = up.delete_url
                uni = uniquifier.uniquify_url(up.url, name=name)      # C4
                item.unique_url = uni.url
            except Exception as exc:  # noqa: BLE001 — одно фото не рушит остальные
                item.ok = False
                item.error = str(exc)
                logger.warning("[%s] фото %d не обработано: %s", vin, i, exc)
            hosted.append(item)

        count_ok = sum(1 for h in hosted if h.ok)
        _atomic_write(
            hosted_path,
            json.dumps({"vin": vin_safe, "images": [h.to_dict() for h in hosted]},
                       ensure_ascii=False, indent=2),
        )

        # Помечаем свежим только если хоть одно фото размещено — иначе
        # на следующем прогоне попробуем снова (не кэшируем полный провал).
        stage_ok = count_ok > 0 or len(hosted) == 0
        if stage_ok:
            store.mark(vin, STAGE, content_hash, path=str(hosted_path),
                       extra={"count_ok": count_ok, "count_total": len(hosted)})
        logger.info("[%s] размещено фото: %d/%d", vin, count_ok, len(hosted))
        results.append(PhotosResult(vin, hosted_path, count_ok, len(hosted),
                                    cached=False, ok=stage_ok,
                                    error=None if stage_ok else "все фото не размещены"))

    total_ok = sum(r.count_ok for r in results)
    logger.info("стадия photos: авто %d, размещено фото суммарно %d",
                len(results), total_ok)
    return results


def _count_hosted(hosted_path: Path) -> int:
    try:
        data = json.loads(hosted_path.read_text(encoding="utf-8"))
        return sum(1 for im in data.get("images", []) if im.get("ok"))
    except (json.JSONDecodeError, OSError):
        return 0
