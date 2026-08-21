#!/usr/bin/env python3
"""Сборка фида Drom из локальной карточки авто, без Google Sheets.

Нужен, когда карточка одна и она приходит «руками» (из скриншотов,
из кабинета), а таблица ещё не заполнена. Логика та же, что в основном
пайплайне: карточка → расшивка по городам → маппер → атомарная запись.

    python -m scripts.build_from_card data/cards/example.yaml
    python -m scripts.build_from_card data/cards/example.yaml --out feeds/drom.xml
    python -m scripts.build_from_card data/cards/example.yaml --limit 3 --stdout
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from src.build import PROJECT_ROOT, atomic_write
from src.config import FanoutSettings, PlatformConfig
from src.fanout import FanoutError, expand_records, load_cities
from src.mappers import MAPPERS

# Обязательные поля Offer: марка/модель/город принимаются в id- или s-виде.
_REQUIRED_ANY = [
    ("idOffer",),
    ("idMark", "sMark"),
    ("idModel", "sModel"),
    ("idCity", "sCity"),
    ("YearOfMade",),
    ("VIN",),
    ("Price",),
]


def load_card(path: Path) -> tuple[dict[str, str], FanoutSettings | None]:
    if not path.is_file():
        raise SystemExit(f"карточка не найдена: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    offer = raw.get("offer")
    if not isinstance(offer, dict):
        raise SystemExit(f"{path}: ожидался раздел 'offer' с полями объявления")

    # Всё приводим к строкам: маппер работает со строковыми ячейками.
    record = {k: ("" if v is None else str(v)).strip() for k, v in offer.items()}

    fanout_raw = raw.get("fanout")
    fanout = FanoutSettings(**fanout_raw) if isinstance(fanout_raw, dict) else None
    return record, fanout


def check_required(record: dict[str, str]) -> None:
    missing = [
        " / ".join(group)
        for group in _REQUIRED_ANY
        if not any(record.get(f, "").strip() for f in group)
    ]
    if missing:
        raise SystemExit(
            "не заполнены обязательные поля Offer: " + ", ".join(missing)
        )


def check_photos(record: dict[str, str]) -> list[str]:
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
    parser.add_argument("--out", default="feeds/drom.xml", help="куда писать фид")
    parser.add_argument("--limit", type=int, default=None,
                        help="ограничить число городов (перебивает карточку)")
    parser.add_argument("--stdout", action="store_true",
                        help="напечатать фид, не записывая файл")
    args = parser.parse_args(argv)

    record, fanout = load_card(Path(args.card))
    check_required(record)

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
        records = expand_records(records, cities, fanout, "idOffer")

    platform = PlatformConfig(
        name="drom", sheet="-", header_row=1, data_start_row=2,
        id_column="idOffer", output="drom.xml",
    )
    xml = MAPPERS["drom"](platform).build_xml(records)

    if args.stdout:
        sys.stdout.write(xml)
    else:
        out = PROJECT_ROOT / args.out
        atomic_write(out, xml)
        print(f"{args.out}: {len(records)} объявлений, "
              f"{len(xml.encode('utf-8'))} байт")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FanoutError as exc:
        raise SystemExit(f"расшивка: {exc}") from exc
