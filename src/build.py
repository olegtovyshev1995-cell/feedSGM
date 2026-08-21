"""Оркестратор сборки фидов.

Тонкий слой: конфиг → чтение листа → валидация → маппинг → атомарная
запись. Бизнес-логики здесь нет — только связывание слоёв, логирование и
корректный код выхода (ненулевой при любой ошибке сборки, чтобы CI покраснел).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from src.config import AppConfig, ConfigError, PlatformConfig, load_config, require_env
from src.fanout import FanoutError, expand_records, load_cities
from src.mappers import MAPPERS
from src.sheets_client import SheetsAccessError, SheetsClient, rows_to_dicts
from src.validators import ValidationFailure, validate_rows

# Корень проекта = родитель каталога src/. feeds/ лежит рядом с src/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FEEDS_DIR = PROJECT_ROOT / "feeds"

logger = logging.getLogger("build")


def _setup_logging() -> None:
    """Структурные логи в stderr; stdout остаётся чистым."""
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def atomic_write(path: Path, content: str) -> None:
    """Атомарно записывает текст в файл (R5).

    Пишем во временный файл в той же папке и делаем os.replace (атомарный
    rename в пределах ФС). Площадка никогда не увидит наполовину записанный
    фид. При любой ошибке временный файл подчищаем.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())  # данные на диске до rename
        os.replace(tmp, path)
    except Exception:
        # Не оставляем висящий tmp, чтобы .gitignore/следующий прогон были чисты.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def _build_one(
    platform: PlatformConfig,
    client: SheetsClient,
    spreadsheet_id: str,
) -> Path:
    """Собирает фид одной площадки. Бросает исключение при любой проблеме."""
    plog = logging.getLogger(f"build[{platform.name}]")

    mapper_cls = MAPPERS.get(platform.name)
    if mapper_cls is None:
        raise ConfigError(
            f"[{platform.name}] нет маппера в реестре MAPPERS. "
            f"Доступны: {', '.join(sorted(MAPPERS))}."
        )

    plog.info("чтение листа %r", platform.sheet)
    values = client.read_values(spreadsheet_id, platform.sheet)
    headers, rows = rows_to_dicts(values, platform.header_row, platform.data_start_row)
    plog.info("строк с данными: %d", len(rows))

    # Валидация ДО записи: битые данные роняют билд, фид не трогаем (R2, R10).
    validate_rows(platform.name, headers, rows, platform.required, platform.id_column)

    records = [record for _, record in rows]

    # Расшивка по городам — после валидации (исходные строки уже проверены)
    # и до маппера (маппер не должен знать про регионы).
    fanout = platform.fanout
    if fanout is not None and fanout.enabled:
        cities = load_cities(PROJECT_ROOT / fanout.cities_file)
        before = len(records)
        records = expand_records(records, cities, fanout, platform.id_column)
        plog.info(
            "расшивка по городам: %d строк → %d объявлений (городов в справочнике: %d)",
            before, len(records), len(cities),
        )

    xml = mapper_cls(platform).build_xml(records)

    out_path = FEEDS_DIR / platform.output
    atomic_write(out_path, xml)
    plog.info("фид записан: %s (%d объявл., %d байт)",
              out_path.relative_to(PROJECT_ROOT), len(records), len(xml.encode("utf-8")))
    return out_path


def run(config_path: str) -> int:
    """Полный прогон. Возвращает код выхода (0 — успех, 1 — были ошибки)."""
    cfg: AppConfig = load_config(config_path)

    # Секрет только из окружения (R1). Парсим JSON-ключ сервисного аккаунта.
    sa_raw = require_env("GOOGLE_SA_JSON")
    try:
        sa_info = json.loads(sa_raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"GOOGLE_SA_JSON не является корректным JSON: {exc}"
        ) from exc

    client = SheetsClient(
        sa_info=sa_info,
        timeout_seconds=cfg.request_timeout_seconds,
        max_retries=cfg.max_retries,
    )

    platforms = cfg.enabled_platforms()
    logger.info("к сборке площадок: %d (%s)",
                len(platforms), ", ".join(p.name for p in platforms))

    failures: list[str] = []
    for platform in platforms:
        try:
            _build_one(platform, client, cfg.spreadsheet_id)
        except (ValidationFailure, SheetsAccessError, ConfigError, FanoutError) as exc:
            # Ожидаемые, человекочитаемые ошибки — печатаем как есть.
            logging.getLogger(f"build[{platform.name}]").error("%s", exc)
            failures.append(platform.name)
        except Exception as exc:  # noqa: BLE001 — верхняя граница, логируем и продолжаем
            logging.getLogger(f"build[{platform.name}]").exception(
                "непредвиденная ошибка: %s", exc
            )
            failures.append(platform.name)

    if failures:
        logger.error("сборка завершена с ошибками у площадок: %s", ", ".join(failures))
        return 1

    logger.info("все фиды собраны успешно")
    return 0


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(description="Генератор товарных фидов")
    parser.add_argument(
        "--config",
        default=os.environ.get("CONFIG_PATH", str(PROJECT_ROOT / "config" / "config.yaml")),
        help="Путь к config.yaml (по умолчанию config/config.yaml)",
    )
    args = parser.parse_args(argv)

    try:
        return run(args.config)
    except ConfigError as exc:
        # Ошибка конфига/окружения — падаем сразу с понятным сообщением.
        logger.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
