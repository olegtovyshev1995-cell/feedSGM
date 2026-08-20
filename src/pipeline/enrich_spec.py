"""Стадия B+C1 (оркестрация): CarInput[] → спека + список найденных фото.

Кэш по VIN: если вход не менялся и спека есть — дорогой вызов LLM
пропускается. Падение одного авто не рушит остальные.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from src.pipeline.models import CarInput, sanitize_vin
from src.pipeline.state import StateStore

logger = logging.getLogger("enrich")

STAGE = "spec"


@dataclass
class SpecResult:
    vin: str
    spec_path: Path
    photos_path: Path
    photos_count: int
    cached: bool           # True — взято из кэша, LLM не вызывался
    ok: bool               # False — стадия упала для этого авто
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


def enrich_specs(
    cars: list[CarInput],
    researcher,               # CardResearcher (DI): research(car) -> ResearchResult
    store: StateStore,
    specs_dir: str | Path,
    photos_dir: str | Path,
    force: bool = False,
) -> list[SpecResult]:
    """Обогащает каждый авто спекой + списком фото. Кэш по VIN."""
    specs_dir = Path(specs_dir)
    photos_dir = Path(photos_dir)
    results: list[SpecResult] = []

    for car in cars:
        vin = car.vin
        vin_safe = sanitize_vin(vin)
        spec_path = specs_dir / f"{vin_safe}.md"
        photos_path = photos_dir / vin_safe / "found.json"
        content_hash = car.content_hash()

        if not force and store.is_fresh(vin, STAGE, content_hash):
            count = _count_photos(photos_path)
            logger.info("[%s] спека актуальна — пропуск (кэш), фото: %d", vin, count)
            results.append(SpecResult(vin, spec_path, photos_path, count,
                                      cached=True, ok=True))
            continue

        try:
            result = researcher.research(car)
            _atomic_write(spec_path, result.spec_markdown)
            _atomic_write(
                photos_path,
                json.dumps(
                    {"vin": vin_safe, "photos": [p.to_dict() for p in result.photos]},
                    ensure_ascii=False, indent=2,
                ),
            )
            # В состоянии ключевой артефакт — спека; фото лежат рядом.
            store.mark(vin, STAGE, content_hash, path=str(spec_path),
                       extra={"photos_path": str(photos_path),
                              "photos_count": len(result.photos)})
            logger.info("[%s] спека сохранена (%d симв.), фото: %d",
                        vin, len(result.spec_markdown), len(result.photos))
            results.append(SpecResult(vin, spec_path, photos_path,
                                      len(result.photos), cached=False, ok=True))
        except Exception as exc:  # noqa: BLE001 — граница стадии: логируем, идём дальше
            logger.error("[%s] не удалось собрать спеку/фото: %s", vin, exc)
            results.append(SpecResult(vin, spec_path, photos_path, 0,
                                      cached=False, ok=False, error=str(exc)))

    ok = sum(1 for r in results if r.ok)
    cached = sum(1 for r in results if r.cached)
    total_photos = sum(r.photos_count for r in results if r.ok)
    logger.info("стадия spec: успешно %d/%d (кэш %d), фото найдено суммарно %d",
                ok, len(results), cached, total_photos)
    return results


def _count_photos(photos_path: Path) -> int:
    if not photos_path.is_file():
        return 0
    try:
        data = json.loads(photos_path.read_text(encoding="utf-8"))
        return len(data.get("photos", []))
    except (json.JSONDecodeError, OSError):
        return 0
