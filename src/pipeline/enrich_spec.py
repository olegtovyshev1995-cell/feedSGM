"""Стадия B (оркестрация): CarInput[] → spec-файлы, с кэшем по VIN.

Не тащит бизнес-логику: вызывает SpecResearcher (DI), пишет Markdown
атомарно, ведёт состояние. Дорогой вызов LLM пропускается, если для VIN
спека уже есть и вход не менялся (идемпотентность).
"""

from __future__ import annotations

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
    path: Path
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
    researcher,               # SpecResearcher (DI)
    store: StateStore,
    specs_dir: str | Path,
    force: bool = False,
) -> list[SpecResult]:
    """Обогащает каждый авто спецификацией. Падение одного не рушит остальные."""
    specs_dir = Path(specs_dir)
    results: list[SpecResult] = []

    for car in cars:
        vin = car.vin
        out_path = specs_dir / f"{sanitize_vin(vin)}.md"
        content_hash = car.content_hash()

        # Идемпотентность: неизменившийся вход с готовой спекой не пересчитываем.
        if not force and store.is_fresh(vin, STAGE, content_hash):
            logger.info("[%s] спека актуальна — пропуск (кэш)", vin)
            results.append(SpecResult(vin, out_path, cached=True, ok=True))
            continue

        try:
            markdown = researcher.research(car)
            _atomic_write(out_path, markdown)
            store.mark(vin, STAGE, content_hash, path=str(out_path))
            logger.info("[%s] спека сохранена: %s (%d символов)",
                        vin, out_path, len(markdown))
            results.append(SpecResult(vin, out_path, cached=False, ok=True))
        except Exception as exc:  # noqa: BLE001 — граница стадии: логируем, идём дальше
            logger.error("[%s] не удалось получить спецификацию: %s", vin, exc)
            results.append(
                SpecResult(vin, out_path, cached=False, ok=False, error=str(exc))
            )

    ok = sum(1 for r in results if r.ok)
    cached = sum(1 for r in results if r.cached)
    logger.info("стадия spec: успешно %d/%d (из них из кэша %d)",
                ok, len(results), cached)
    return results
