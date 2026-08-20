"""Entrypoint стадии C: разместить и уникализировать найденные фото.

Читает VIN'ы из data/photos/*/found.json (результат стадии B+C1), заливает
на хостинг (imgbb) и уникализирует. Не требует Google — только ключи
фото-сервисов из окружения.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from src.config import AppConfig, ConfigError, load_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PHOTOS_DIR = DATA_DIR / "photos"
STATE_DIR = DATA_DIR / "state"

logger = logging.getLogger("host_photos")


def _setup_logging() -> None:
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _discover_vins(photos_dir: Path) -> list[str]:
    if not photos_dir.is_dir():
        return []
    return sorted(
        p.parent.name for p in photos_dir.glob("*/found.json")
    )


def run(config_path: str, force: bool = False) -> int:
    cfg: AppConfig = load_config(config_path)
    if cfg.photos is None:
        raise ConfigError("В конфиге нет секции photos (см. config.example.yaml).")

    from src.adapters.photo_host import build_photo_host
    from src.adapters.uniquifier import build_uniquifier
    from src.pipeline.photos import host_and_uniquify
    from src.pipeline.state import StateStore

    vins = _discover_vins(PHOTOS_DIR)
    if not vins:
        logger.warning("нет data/photos/*/found.json — сначала запустите src.enrich")
        return 0

    photo_host = build_photo_host(cfg.photos)              # env IMGBB_API_KEY
    uniquifier = build_uniquifier(cfg.photos.uniquifier)   # none|pixflow
    store = StateStore(STATE_DIR)

    results = host_and_uniquify(vins, photo_host, uniquifier, store, PHOTOS_DIR, force=force)

    failed = [r.vin for r in results if not r.ok]
    if failed:
        logger.error("фото не размещены у: %s", ", ".join(failed))
        return 1
    total = sum(r.count_ok for r in results)
    logger.info("готово: размещено фото суммарно %d для %d авто", total, len(results))
    return 0


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(description="Стадия C: хостинг+уникализация фото")
    parser.add_argument(
        "--config",
        default=os.environ.get("CONFIG_PATH", str(PROJECT_ROOT / "config" / "config.yaml")),
    )
    parser.add_argument("--force", action="store_true", help="Перезалить даже при кэше")
    args = parser.parse_args(argv)
    try:
        return run(args.config, force=args.force)
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
