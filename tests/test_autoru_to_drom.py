"""Тесты конвертера выгрузки Auto.ru → фид Drom (без сети)."""

from __future__ import annotations

import json
from xml.dom.minidom import parseString

from src import autoru_to_drom
from src.autoru_to_drom import build_feed, convert, offer_to_drom


def _offer(i=1, status="ACTIVE", **over):
    offer = {
        "id": f"110{i}-abc",
        "category": "CARS",
        "section": "USED",
        "status": status,
        "color_hex": "040001",
        "description": "Один владелец & без ДТП <торг>",
        "car_info": {
            "mark_info": {"name": "BMW"}, "model_info": {"name": "X5"},
            "body_type": "ALLROAD_5_DOORS", "engine_type": "DIESEL",
            "transmission": "AUTOMATIC", "drive": "ALL_WHEEL_DRIVE",
            "steering_wheel": "LEFT", "horse_power": 249,
            "tech_param": {"displacement": 2993},
        },
        "documents": {"year": 2020, "vin": f"wbacv610x0lm0000{i}", "owners_number": 1},
        "price_info": {"price": 5_500_000},
        "state": {"mileage": 45000, "image_urls": [
            {"sizes": {"320x240": "//avatars.mds.yandex.net/a/320",
                       "1200x900": "//avatars.mds.yandex.net/a/1200"}},
        ]},
        "seller": {"location": {"region_info": {"name": "Москва"}},
                   "phones": [{"phone": "79990000000"}]},
    }
    offer.update(over)
    return offer


def test_offer_mapping():
    rec = offer_to_drom(_offer())
    assert rec["sMark"] == "BMW" and rec["sModel"] == "X5" and rec["sCity"] == "Москва"
    assert rec["VIN"] == "WBACV610X0LM00001"
    assert rec["sTransmission"] == "автомат" and rec["sEngineType"] == "дизель"
    assert rec["sDriveType"] == "4WD" and rec["FrameType"] == "джип/SUV"
    assert rec["Color"] == "черный" and rec["Haul"] == "45000" and rec["Power"] == "249"
    assert rec["Photos"] == "https://avatars.mds.yandex.net/a/1200"


def test_only_active_cars_and_required_fields():
    offers = [
        _offer(1),
        _offer(2, status="INACTIVE"),
        _offer(3, category="MOTO"),
        _offer(4, documents={"year": 2019}),   # нет VIN → пропуск
    ]
    records, skipped = convert(offers)
    assert [r["idOffer"] for r in records] == ["1101-abc"]
    assert len(skipped) == 1 and "VIN" in skipped[0]


def test_default_city_when_missing():
    records, _ = convert([_offer(seller={})], default_city="Самара")
    assert records[0]["sCity"] == "Самара"


def test_feed_is_valid_xml():
    records, _ = convert([_offer(1), _offer(2)])
    dom = parseString(build_feed(records).encode("utf-8"))
    offers = dom.getElementsByTagName("Offer")
    assert len(offers) == 2
    assert offers[0].getElementsByTagName("Photo")[0].firstChild.data.startswith("https://")


def test_main_writes_feed(tmp_path, monkeypatch):
    src = tmp_path / "export.json"
    src.write_text(json.dumps([_offer(1), _offer(2, status="INACTIVE")]), encoding="utf-8")
    monkeypatch.setattr(autoru_to_drom, "FEEDS_DIR", tmp_path)
    assert autoru_to_drom.main(["--input", str(src), "--output", "d.xml"]) == 0
    xml = (tmp_path / "d.xml").read_text(encoding="utf-8")
    assert xml.count("<Offer>") == 1


def test_main_fails_without_input(tmp_path):
    assert autoru_to_drom.main(["--input", str(tmp_path / "nope.json")]) == 1
