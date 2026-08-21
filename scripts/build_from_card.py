#!/usr/bin/env python3
"""Сборка фида из локальной карточки авто, без Google Sheets.

Нужен, когда карточка одна и приходит «руками» (из скриншотов, из
кабинета), а таблица ещё не заполнена. Логика та же, что в основном
пайплайне: карточка → (расшивка по городам) → маппер → атомарная запись.

Площадка берётся из ключа `platform` карточки (drom | autoru); по
умолчанию drom. Обязательные поля и файл фида берутся из
config/config.example.yaml для этой площадки.

    python -m scripts.build_from_card data/cards/example.yaml
    python -m scripts.build_from_card data/cards/ford-...-autoru.yaml --stdout
    python -m scripts.build_from_card data/cards/example.yaml --limit 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from src.build import PROJECT_ROOT, atomic_write
from src.config import FanoutSettings, PlatformConfig, load_config
from src.fanout import FanoutError, expand_records, load_cities
from src.mappers import MAPPERS

_EXAMPLE_CONFIG = "config/config.example.yaml"

# Группы обязательных полей, где годится любой из вариантов (id- или
# текстовая форма). Проверяются поверх плоского списка required из конфига.
_ANY_OF = {
    "drom": [("idMark", "sMark"), ("idModel", "sModel"), ("idCity", "sCity")],
    "autoru": [
        ("modification_id", "engine_type"),  # двигатель: код ИЛИ 5 параметров
        ("vin", "unique_id"),                # идентификатор: VIN ИЛИ unique_id
    ],
}


def load_card(path: Path) -> tuple[str, dict[str, str], FanoutSettings | None]:
    if not path.is_file():
        raise SystemExit(f"карточка не найдена: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    platform = str(raw.get("platform", "drom")).strip() or "drom"
    offer = raw.get("offer")
    if not isinstance(offer, dict):
        raise SystemExit(f"{path}: ожидался раздел 'offer' с полями объявления")

    # Всё приводим к строкам: маппер работает со строковыми ячейками.
    record = {k: ("" if v is None else str(v)).strip() for k, v in offer.items()}

    fanout_raw = raw.get("fanout")
    fanout = FanoutSettings(**fanout_raw) if isinstance(fanout_raw, dict) else None
    return platform, record, fanout


def platform_config(platform: str) -> PlatformConfig:
    """Берёт required / id_column / output площадки из example-конфига."""
    cfg = load_config(_EXAMPLE_CONFIG)
    for p in cfg.platforms:
        if p.name == platform:
            return p
    known = ", ".join(pc.name for pc in cfg.platforms)
    raise SystemExit(f"неизвестная площадка {platform!r}; в конфиге есть: {known}")


def check_required(platform: str, record: dict[str, str], required: list[str]) -> None:
    any_of = _ANY_OF.get(platform, [])
    # Плоские required, кроме тех, что покрыты группами «любой из».
    grouped = {f for group in any_of for f in group}
    missing = [f for f in required if f not in grouped and not record.get(f, "").strip()]
    missing += [
        " / ".join(group)
        for group in any_of
        if not any(record.get(f, "").strip() for f in group)
    ]
    if missing:
        raise SystemExit("не заполнены обязательные поля: " + ", ".join(missing))


def check_photos(record: dict[str, str]) -> list[str]:  # noqa: D401
    """Возвращает список ссылок, которые Дром не примет как фотографию.

    Инструкция требует файлы с расширением .jpg/.jpeg. Ссылка-страница
    (например, https://ibb.co/XXXXXXX) отдаёт HTML, и фото не загрузится —
    ловим это до отправки фида, а не по отчёту через сутки.
    """
    prefix = record.get("PhotoDir", "").strip()
    urls = [u.strip() for u in record.get("Photos", "").split("|") if u.strip()]
    main_photo = record.get("PhotoMain", "").strip()
    if main_photo:
        urls.append(main_photo)
    return [u for u in urls if not (prefix + u).lower().endswith((".jpg", ".jpeg"))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("card", help="YAML-карточка авто")
    parser.add_argument("--out", default=None,
                        help="куда писать фид (по умолчанию feeds/<output площадки>)")
    parser.add_argument("--limit", type=int, default=None,
                        help="ограничить число городов (перебивает карточку)")
    parser.add_argument("--stdout", action="store_true",
                        help="напечатать фид, не записывая файл")
    args = parser.parse_args(argv)

    platform, record, fanout = load_card(Path(args.card))
    pc = platform_config(platform)
    check_required(platform, record, pc.required)

    bad_photos = check_photos(record)
    if bad_photos:
        print(
            f"ВНИМАНИЕ: {len(bad_photos)} ссылок на фото не заканчиваются на "
            f".jpg/.jpeg — Дром такие не загрузит. Нужны прямые ссылки на файл "
            f"(в imgbb это «Direct link», вида https://i.ibb.co/<хеш>/<имя>.jpg). "
            f"Например: {bad_photos[0]}",
            file=sys.stderr,
        )

    records = [record]
    if fanout is not None and fanout.enabled:
        if args.limit:
            fanout = fanout.model_copy(update={"limit": args.limit})
        cities = load_cities(PROJECT_ROOT / fanout.cities_file)
        records = expand_records(records, cities, fanout, pc.id_column)

    xml = MAPPERS[platform](pc).build_xml(records)

    if args.stdout:
        sys.stdout.write(xml)
    else:
        rel = args.out or f"feeds/{pc.output}"
        out = PROJECT_ROOT / rel
        atomic_write(out, xml)
        print(f"{rel}: {len(records)} объявлений, "
              f"{len(xml.encode('utf-8'))} байт")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FanoutError as exc:
        raise SystemExit(f"расшивка: {exc}") from exc
