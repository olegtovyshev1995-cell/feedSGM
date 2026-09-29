"""Фид Drom из активных объявлений кабинета Auto.ru.

Вход — выгрузка API Auto.ru (``scripts/autoru_export.py``):
``exports/autoru_offers_all_latest.json``. Берём только легковые (CARS) в
статусе ACTIVE, переводим поля Auto.ru в теги Drom и собираем фид тем же
DromMapper, что и основной пайплайн (``<avtoxml><Offers><Offer>``).

Коды Auto.ru (AUTOMATIC, GASOLINE, ALL_WHEEL_DRIVE, цвет в hex …) переводятся
в текстовые значения Drom (поля s*), потому что числовых id из справочника
ref.xml Drom у нас нет. Объявления без обязательных полей Drom (марка,
модель, город, год, VIN, цена) пропускаются с предупреждением в логе.

Запуск:
  python -m src.autoru_to_drom                       # → feeds/drom_autoru.xml
  python -m src.autoru_to_drom --city "Москва"       # город, если в объявлении пусто
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from src.build import FEEDS_DIR, PROJECT_ROOT, _setup_logging, atomic_write
from src.config import PlatformConfig
from src.mappers.drom import DromMapper

logger = logging.getLogger("autoru_to_drom")

DEFAULT_INPUT = PROJECT_ROOT / "exports" / "autoru_offers_all_latest.json"
DEFAULT_OUTPUT = "drom_autoru.xml"

# Обязательные поля Offer по инструкции Drom (как в config.example.yaml).
DROM_REQUIRED = ["idOffer", "sMark", "sModel", "sCity", "YearOfMade", "VIN", "Price"]

TRANSMISSION = {
    "AUTOMATIC": "автомат",
    "MECHANICAL": "механика",
    "ROBOT": "робот",
    "VARIATOR": "вариатор",
}
ENGINE = {
    "GASOLINE": "бензин",
    "DIESEL": "дизель",
    "HYBRID": "гибрид",
    "ELECTRO": "электро",
    "LPG": "ГБО",
}
DRIVE = {
    "FORWARD_CONTROL": "передний",
    "REAR_DRIVE": "задний",
    "ALL_WHEEL_DRIVE": "4WD",
}
WHEEL = {"LEFT": "левый", "RIGHT": "правый"}
# Палитра цветов Auto.ru (color_hex) → название.
COLORS = {
    "040001": "черный",
    "FAFBFB": "белый",
    "CACECB": "серебристый",
    "97948F": "серый",
    "0000CC": "синий",
    "22A0F8": "голубой",
    "EE1D19": "красный",
    "007F00": "зеленый",
    "200204": "коричневый",
    "C49648": "бежевый",
    "DEA522": "золотистый",
    "FFD600": "желтый",
    "FF8649": "оранжевый",
    "660099": "пурпурный",
    "4A2197": "фиолетовый",
    "FFC0CB": "розовый",
}
# Тип кузова Auto.ru → название (по префиксу, т.к. есть варианты *_3_DOORS/_5_DOORS).
BODY_PREFIXES = [
    ("ALLROAD", "джип/SUV"),
    ("SEDAN", "седан"),
    ("HATCHBACK", "хэтчбек"),
    ("LIFTBACK", "лифтбек"),
    ("WAGON", "универсал"),
    ("COUPE", "купе"),
    ("CABRIO", "открытый"),
    ("ROADSTER", "открытый"),
    ("MINIVAN", "минивэн"),
    ("COMPACTVAN", "минивэн"),
    ("PICKUP", "пикап"),
    ("VAN", "фургон"),
    ("LIMOUSINE", "лимузин"),
]


def dig(obj: Any, dotted: str) -> Any:
    for key in dotted.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def first(offer: dict, *paths: str) -> Any:
    """Первое непустое значение из нескольких возможных путей."""
    for path in paths:
        value = dig(offer, path)
        if value not in (None, "", [], {}):
            return value
    return None


def _str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def body_type(code: Any) -> str:
    code = _str(code).upper()
    for prefix, name in BODY_PREFIXES:
        if code.startswith(prefix):
            return name
    return ""


def photo_urls(offer: dict) -> list[str]:
    """Ссылки на фото максимального размера из state.image_urls[].sizes."""
    urls: list[str] = []
    for image in dig(offer, "state.image_urls") or []:
        sizes = image.get("sizes") if isinstance(image, dict) else None
        if not isinstance(sizes, dict) or not sizes:
            continue

        def area(key: str) -> int:
            w, _, h = key.rstrip("n").partition("x")
            return int(w) * int(h) if w.isdigit() and h.isdigit() else 0

        url = sizes.get("full") or sizes[max(sizes, key=area)]
        if url.startswith("//"):
            url = "https:" + url
        urls.append(url)
    return urls


def offer_to_drom(offer: dict, default_city: str = "") -> dict[str, str]:
    """Одно объявление Auto.ru → запись с тегами Drom (строки)."""
    power = first(offer, "car_info.horse_power", "car_info.tech_param.power")
    phones = dig(offer, "seller.phones") or []
    phone = phones[0].get("phone") if phones and isinstance(phones[0], dict) else ""
    section = _str(offer.get("section")).upper()
    mileage = first(offer, "state.mileage")
    if mileage is None and section == "NEW":
        mileage = 0
    return {
        "idOffer": _str(offer.get("id")),
        "sMark": _str(first(offer, "car_info.mark_info.name", "car_info.mark")),
        "sModel": _str(first(offer, "car_info.model_info.name", "car_info.model")),
        "sCity": _str(first(offer, "seller.location.region_info.name",
                            "salon.place.region_info.name")) or default_city,
        "YearOfMade": _str(dig(offer, "documents.year")),
        "VIN": _str(dig(offer, "documents.vin")).upper(),
        "Price": _str(first(offer, "price_info.price", "price_info.RUR")),
        "Volume": _str(dig(offer, "car_info.tech_param.displacement")),
        "FrameType": body_type(dig(offer, "car_info.body_type")),
        "Color": COLORS.get(_str(offer.get("color_hex")).upper(), ""),
        "sTransmission": TRANSMISSION.get(_str(dig(offer, "car_info.transmission")), ""),
        "sEngineType": ENGINE.get(_str(dig(offer, "car_info.engine_type")), ""),
        "sDriveType": DRIVE.get(_str(dig(offer, "car_info.drive")), ""),
        "sWheelType": WHEEL.get(_str(dig(offer, "car_info.steering_wheel")), ""),
        "Haul": _str(mileage),
        "NumberOfOwners": _str(dig(offer, "documents.owners_number")),
        "Power": _str(power),
        "Phone": _str(phone),
        "Additional": _str(offer.get("description")),
        "Photos": "|".join(photo_urls(offer)),
    }


def is_active_car(offer: dict) -> bool:
    status = _str(offer.get("status")).upper()
    category = _str(offer.get("category")).upper()
    return status == "ACTIVE" and category in ("CARS", "")


def convert(offers: list[dict], default_city: str = "") -> tuple[list[dict], list[str]]:
    """→ (записи Drom, список пропущенных с причиной)."""
    records, skipped = [], []
    for offer in offers:
        if not is_active_car(offer):
            continue
        rec = offer_to_drom(offer, default_city)
        missing = [f for f in DROM_REQUIRED if not rec.get(f)]
        if missing:
            skipped.append(f"{rec['idOffer'] or '?'} ({rec['sMark']} {rec['sModel']}): "
                           f"нет {', '.join(missing)}")
            continue
        records.append(rec)
    return records, skipped


def build_feed(records: list[dict]) -> str:
    platform = PlatformConfig(name="drom", sheet="-", header_row=1, data_start_row=2,
                              id_column="idOffer", output=DEFAULT_OUTPUT)
    return DromMapper(platform).build_xml(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Фид Drom из активных объявлений Auto.ru")
    parser.add_argument("--input", default=str(DEFAULT_INPUT),
                        help="JSON-выгрузка scripts/autoru_export.py")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="имя файла в feeds/")
    parser.add_argument("--city", default=os.environ.get("DROM_CITY", ""),
                        help="город по умолчанию, если в объявлении не указан")
    args = parser.parse_args(argv)
    _setup_logging()

    src = Path(args.input)
    if not src.is_file():
        logger.error("нет файла выгрузки %s — сначала запустите scripts/autoru_export.py", src)
        return 1
    offers = json.loads(src.read_text(encoding="utf-8"))
    records, skipped = convert(offers, args.city)
    for line in skipped:
        logger.warning("пропущено: %s", line)
    if not records:
        logger.error("в выгрузке нет активных легковых объявлений с полными данными "
                     "(всего объявлений: %s, пропущено: %s)", len(offers), len(skipped))
        return 1

    out = FEEDS_DIR / args.output
    atomic_write(out, build_feed(records))
    logger.info("фид Drom: %s объявлений → %s (пропущено: %s)",
                len(records), out, len(skipped))
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
