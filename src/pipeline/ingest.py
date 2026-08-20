"""Стадия A: чтение минимального входа из Google Sheets → CarInput.

Тонкий слой: сетевое чтение делает sheets_client, здесь — только
нормализация строк по ColumnMapping и валидация обязательных полей.
Чистая функция normalize_rows тестируется без сети.
"""

from __future__ import annotations

import logging

from src.config import AppConfig, ColumnMapping, ConfigError, IngestConfig
from src.pipeline.models import CarInput

logger = logging.getLogger("ingest")


class IngestError(Exception):
    """Проблемы входных данных с точными номерами строк таблицы."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(
            "Входные данные не прошли проверку:\n  " + "\n  ".join(problems)
        )


def normalize_rows(
    headers: list[str],
    rows: list[tuple[int, dict[str, str]]],
    columns: ColumnMapping,
) -> list[CarInput]:
    """Преобразует строки листа в список CarInput.

    Проверяет, что колонки vin/make/model присутствуют в шапке (R10) и что
    в этих полях нет пустот (R2). Копит все проблемы и бросает IngestError
    разом — чтобы пользователь увидел весь список за один прогон.
    """
    problems: list[str] = []

    header_set = set(headers)
    required_cols = {"vin": columns.vin, "make": columns.make, "model": columns.model}
    missing = [name for name in required_cols.values() if name not in header_set]
    if missing:
        raise IngestError([f"нет обязательных колонок: {', '.join(missing)}"])

    def cell(rec: dict[str, str], header: str | None) -> str:
        if not header:
            return ""
        return rec.get(header, "").strip()

    cars: list[CarInput] = []
    for row_number, rec in rows:
        vin = cell(rec, columns.vin)
        make = cell(rec, columns.make)
        model = cell(rec, columns.model)

        row_problems = []
        if not vin:
            row_problems.append("пустой VIN")
        if not make:
            row_problems.append("пустая марка")
        if not model:
            row_problems.append("пустая модель")
        if row_problems:
            problems.append(f"строка {row_number}: " + "; ".join(row_problems))
            continue

        cars.append(
            CarInput(
                vin=vin,
                make=make,
                model=model,
                modification=cell(rec, columns.modification),
                year=cell(rec, columns.year),
                price=cell(rec, columns.price),
                city=cell(rec, columns.city),
                row_number=row_number,
            )
        )

    if problems:
        raise IngestError(problems)

    # Дубли VIN во входе — это ошибка идемпотентности (R6).
    seen: dict[str, int] = {}
    dup_problems: list[str] = []
    for car in cars:
        if car.vin in seen:
            dup_problems.append(
                f"строка {car.row_number}: дубль VIN {car.vin} "
                f"(впервые в строке {seen[car.vin]})"
            )
        else:
            seen[car.vin] = car.row_number
    if dup_problems:
        raise IngestError(dup_problems)

    return cars


def read_car_inputs(cfg: AppConfig, sa_info: dict) -> list[CarInput]:
    """Читает входной лист из Google Sheets и нормализует его в CarInput."""
    if cfg.ingest is None:
        raise ConfigError(
            "В конфиге нет секции ingest — нечего читать для обогащения. "
            "Добавьте ingest.sheet и ingest.columns (см. config.example.yaml)."
        )
    ingest: IngestConfig = cfg.ingest

    # Ленивая зависимость: sheets_client тянет google-стек только тут.
    from src.sheets_client import SheetsClient, rows_to_dicts

    client = SheetsClient(
        sa_info=sa_info,
        timeout_seconds=cfg.request_timeout_seconds,
        max_retries=cfg.max_retries,
    )
    values = client.read_values(cfg.spreadsheet_id, ingest.sheet)
    headers, rows = rows_to_dicts(values, ingest.header_row, ingest.data_start_row)
    cars = normalize_rows(headers, rows, ingest.columns)
    logger.info("прочитано авто на входе: %d", len(cars))
    return cars
