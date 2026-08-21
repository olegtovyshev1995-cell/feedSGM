"""Тесты пайплайна: чтение-в-словари, валидация, маппинг, атомарная запись.

Google API не задействуется — проверяем чистую логику на синтетике.
"""

from __future__ import annotations

from xml.dom.minidom import parseString

import pytest

from src.build import atomic_write
from src.config import (
    AppConfig,
    ConfigError,
    FanoutSettings,
    PlatformConfig,
    load_config,
)
from src.fanout import City, FanoutError, expand_records, load_cities
from src.mappers import MAPPERS
from src.mappers.base import cdata, split_list, xml_escape
from src.sheets_client import rows_to_dicts
from src.validators import ValidationFailure, validate_rows
from scripts.build_from_card import check_photos
from scripts.resolve_ibb_links import extract_direct_url, update_card


# ── Хелперы ────────────────────────────────────────────────────────
def _pc(name: str, id_column: str, required: list[str]) -> PlatformConfig:
    return PlatformConfig(
        name=name, sheet="s", header_row=1, data_start_row=2,
        id_column=id_column, output=f"{name}.xml", required=required,
    )


AVITO_REQ = ["Id", "Category", "Make", "Model", "Year", "Price",
             "Description", "Images", "Address", "ContactPhone"]


def _avito_row(**over) -> dict:
    base = {
        "Id": "NEW-1", "Category": "Автомобили", "VehicleType": "Легковые автомобили",
        "Condition": "Новое", "Make": "Chery", "Model": "Tiggo 7 Pro Max",
        "Year": "2026", "Price": "2890000",
        "Description": "<p>Новый Chery & лучший</p>",
        "Images": "https://s.ru/1.jpg, https://s.ru/2.jpg",
        "Address": "Самара", "ContactPhone": "+79000000000",
        "VIN": "LVVDB21B1PD000000",
    }
    base.update(over)
    return base


# ── rows_to_dicts ──────────────────────────────────────────────────
def test_rows_to_dicts_skips_example_and_empty_and_numbers_rows():
    values = [
        ["Желательно", "Обязательно"],   # row1 — обязательность (Auto.ru)
        ["unique_id", "mark_id"],          # row2 — заголовки
        ["AVTO-EX", "Hyundai"],            # row3 — пример (пропускаем)
        ["SGM-1", "Ford"],                 # row4 — данные
        [],                                 # row5 — пустая (пропускаем)
        ["SGM-2", "Kia"],                  # row6 — данные
    ]
    headers, rows = rows_to_dicts(values, header_row=2, data_start_row=4)
    assert headers == ["unique_id", "mark_id"]
    assert [n for n, _ in rows] == [4, 6]  # реальные номера строк таблицы
    assert rows[0][1] == {"unique_id": "SGM-1", "mark_id": "Ford"}


def test_rows_to_dicts_pads_short_rows():
    values = [["a", "b", "c"], ["1"]]  # короткая строка данных
    headers, rows = rows_to_dicts(values, header_row=1, data_start_row=2)
    assert rows[0][1] == {"a": "1", "b": "", "c": ""}


# ── Валидация ──────────────────────────────────────────────────────
def test_valid_rows_pass():
    headers = list(_avito_row().keys())
    rows = [(5, _avito_row())]
    validate_rows("avito", headers, rows, AVITO_REQ, "Id")  # не бросает


def test_empty_required_field_is_caught():
    headers = list(_avito_row().keys())
    rows = [(5, _avito_row(Make=""))]
    with pytest.raises(ValidationFailure) as exc:
        validate_rows("avito", headers, rows, AVITO_REQ, "Id")
    assert exc.value.row_issues[0].row_number == 5
    assert any("Make" in p for p in exc.value.row_issues[0].problems)


def test_missing_required_column_is_caught():
    headers = ["Id", "Category", "Make"]  # нет Model/Year/...
    rows = [(5, {"Id": "1", "Category": "Автомобили", "Make": "Ford"})]
    with pytest.raises(ValidationFailure) as exc:
        validate_rows("avito", headers, rows, AVITO_REQ, "Id")
    assert "Model" in exc.value.missing_columns
    assert exc.value.row_issues == []  # без колонок построчно не проверяем


def test_duplicate_id_is_caught():
    headers = list(_avito_row().keys())
    rows = [(5, _avito_row(Id="A1")), (6, _avito_row(Id="A1"))]
    with pytest.raises(ValidationFailure) as exc:
        validate_rows("avito", headers, rows, AVITO_REQ, "Id")
    assert any("дубль" in p for issue in exc.value.row_issues for p in issue.problems)


def test_empty_sheet_is_caught():
    headers = list(_avito_row().keys())
    with pytest.raises(ValidationFailure):
        validate_rows("avito", headers, [], AVITO_REQ, "Id")


# ── Экранирование / утилиты ────────────────────────────────────────
def test_ampersand_and_angle_brackets_escaped():
    assert xml_escape("Stop & Go <b>") == "Stop &amp; Go &lt;b&gt;"


def test_control_chars_removed():
    assert xml_escape("bad" + chr(7) + "x") == "badx"


def test_cdata_wraps_and_guards_terminator():
    out = cdata("a]]>b")
    assert out.startswith("<![CDATA[") and out.endswith("]]>")
    assert "]]>b" not in out.replace("]]]]><![CDATA[>", "")  # терминатор разорван


def test_split_list_variants():
    assert split_list("a.jpg|b.jpg| |c.jpg", "|") == ["a.jpg", "b.jpg", "c.jpg"]
    assert split_list("", "|") == []


# ── Мапперы: валидный XML ──────────────────────────────────────────
def test_avito_mapper_produces_valid_xml_with_cdata_and_images():
    xml = MAPPERS["avito"](_pc("avito", "Id", AVITO_REQ)).build_xml([_avito_row()])
    dom = parseString(xml)  # бросит при невалидном XML
    assert dom.getElementsByTagName("Ads")
    assert "<![CDATA[<p>Новый Chery & лучший</p>]]>" in xml  # HTML сохранён
    assert xml.count("<Image ") == 2


def test_autoru_mapper_produces_valid_xml():
    row = {
        "unique_id": "SGM-1", "mark_id": "Ford", "folder_id": "Ranger Raptor",
        "modification_id": "2.0d AT (210 л.с.) 4WD", "body_type": "Пикап Double Cab",
        "year": "2026", "color": "серый", "price": "8269329", "currency": "RUR",
        "run": "0", "vin": "MPBAMFE60SX697372", "availability": "в наличии",
        "custom": "растаможен", "state": "новый", "description": "Stop & Go — 4×4",
        "images": "https://s.ru/1.jpg|https://s.ru/2.jpg",
    }
    xml = MAPPERS["autoru"](_pc("autoru", "unique_id", [])).build_xml([row])
    dom = parseString(xml)
    assert dom.getElementsByTagName("cars")
    assert xml.count("<image>") == 2
    assert "Stop &amp; Go" in xml  # амперсанд экранирован


def test_drom_mapper_produces_valid_xml_with_photos():
    mapper = MAPPERS["drom"](_pc("drom", "idOffer", []))
    mapper.build_date = "2026-08-10T06:00:00+0000"  # детерминизм
    row = {
        "idOffer": "SGM-1", "sMark": "Ford", "sModel": "Ranger Raptor",
        "sCity": "Самара", "YearOfMade": "2026", "VIN": "MPBAMFE60SX697372",
        "Price": "8269329", "Additional": "A & B",
        "Photos": "https://s.ru/1.jpg|https://s.ru/2.jpg",
    }
    xml = mapper.build_xml([row])
    dom = parseString(xml)
    assert dom.getElementsByTagName("avtoxml")
    assert xml.count("<Photo>") == 2
    assert "<lastBuildDate>2026-08-10T06:00:00+0000</lastBuildDate>" in xml


def test_drom_offer_matches_official_example_layout():
    """Состав и порядок тегов — как в docs/examples/drom_bulls_example.xml.

    Официальный пример Дрома расходится с таблицей полей инструкции:
    Photos идёт перед Additional, VIN — последним тегом Offer, а поля
    справочников называются idNewType/idFrameType/idColor/sWhereabouts,
    а не NewType/FrameType/Color/Whereabouts. Ловим регресс по обоим.
    """
    mapper = MAPPERS["drom"](_pc("drom", "idOffer", []))
    mapper.build_date = "2026-08-10T06:00:00+0000"
    row = {
        "idOffer": "SGM-1", "sMark": "Toyota", "sModel": "Camry",
        "idCity": "663", "sCity": "Красногорск, Московская область",
        "YearOfMade": "2018", "Price": "2500016", "idNewType": "0",
        "Volume": "2494", "Power": "181", "idFrameType": "10",
        "idColor": "12", "idTransmission": "2", "idEngineType": "1",
        "idDriveType": "1", "idWheelType": "2", "Haul": "78000",
        "idHaulRussiaType": "1", "PhotoDir": "https://i.ibb.co/abc/",
        "PhotoMain": "main.jpg", "Photos": "a.jpg|b.jpg",
        "Additional": "описание", "Phone": "+7(800)000-00-01",
        "Phone2": "+7(800)000-00-02", "sWhereabouts": "в наличии",
        "idDamagedType": "0", "VIN": "XW7BF4FK00S123456",
    }
    xml = mapper.build_xml([row])
    offer = parseString(xml).getElementsByTagName("Offer")[0]
    tags = [n.tagName for n in offer.childNodes if n.nodeType == n.ELEMENT_NODE]

    assert tags[0] == "idOffer"
    assert tags[-1] == "VIN"
    assert tags.index("Photos") < tags.index("Additional")
    assert tags.index("Phone") < tags.index("Phone2")
    # короткие синонимы из таблицы инструкции не подмешиваются
    for legacy in ("NewType", "FrameType", "Color", "DamagedType"):
        assert legacy not in tags

    photos = offer.getElementsByTagName("Photos")[0]
    assert photos.getAttribute("PhotoDir") == "https://i.ibb.co/abc/"
    assert photos.getAttribute("PhotoMain") == "main.jpg"


def test_drom_supports_legacy_short_field_names():
    """Таблица инструкции разрешает NewType/FrameType/Color/Whereabouts."""
    mapper = MAPPERS["drom"](_pc("drom", "idOffer", []))
    mapper.build_date = "2026-08-10T06:00:00+0000"
    row = {
        "idOffer": "SGM-2", "sMark": "Ford", "sModel": "Ranger",
        "sCity": "Химки", "YearOfMade": "2026", "Price": "8269329",
        "VIN": "MPBAMFE60SX697372", "NewType": "1", "FrameType": "12",
        "Color": "12", "Whereabouts": "0", "DamagedType": "0",
    }
    xml = mapper.build_xml([row])
    for tag in ("NewType", "FrameType", "Color", "Whereabouts", "DamagedType"):
        assert f"<{tag}>" in xml


def test_avito_skips_empty_optional_fields():
    # Generation/Modification пустые — тегов быть не должно.
    xml = MAPPERS["avito"](_pc("avito", "Id", AVITO_REQ)).build_xml(
        [_avito_row(Generation="", Modification="")]
    )
    assert "<Generation>" not in xml
    assert "<Modification>" not in xml


# ── Атомарная запись ───────────────────────────────────────────────
def test_atomic_write_creates_file_and_cleans_tmp(tmp_path):
    target = tmp_path / "feeds" / "avito.xml"
    atomic_write(target, "<xml/>\n")
    assert target.read_text(encoding="utf-8") == "<xml/>\n"
    # временный файл не остался
    assert not (target.parent / "avito.xml.tmp").exists()


# ── Конфиг ─────────────────────────────────────────────────────────
def test_config_example_loads():
    cfg = load_config("config/config.example.yaml")
    assert isinstance(cfg, AppConfig)
    assert {p.name for p in cfg.platforms} == {"avito", "autoru", "drom"}


def test_config_missing_file_raises():
    with pytest.raises(ConfigError):
        load_config("config/does-not-exist.yaml")


def test_config_rejects_data_start_not_after_header(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "spreadsheet_id: X\n"
        "platforms:\n"
        "  - name: avito\n"
        "    sheet: s\n"
        "    header_row: 5\n"
        "    data_start_row: 3\n"
        "    id_column: Id\n"
        "    output: avito.xml\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(str(bad))


# ── Расшивка по городам ────────────────────────────────────────────
_CITIES = [
    City(id="325", name="Балашиха", in_form="в Балашихе"),
    City(id="663", name="Красногорск, Московская область", in_form="в Красногорске"),
    City(id="1123", name="Химки", in_form="в Химках"),
]


def _fanout(**kw) -> FanoutSettings:
    base = {"enabled": True, "cities_file": "config/drom_mo_cities.yaml"}
    base.update(kw)
    return FanoutSettings(**base)


def _car(**kw) -> dict[str, str]:
    row = {
        "idOffer": "SGM-1", "sMark": "Toyota", "sModel": "Camry",
        "idCity": "663", "sCity": "Красногорск, Московская область",
        "YearOfMade": "2018", "Price": "2500000", "VIN": "XW7BF4FK00S123456",
        "Additional": "Продаётся в городе {city}.",
    }
    row.update(kw)
    return row


def test_fanout_expands_row_over_cities_and_keeps_original():
    out = expand_records([_car()], _CITIES, _fanout(), "idOffer")

    # исходная строка + города, кроме её собственного (663)
    assert len(out) == 3
    assert out[0]["idOffer"] == "SGM-1"
    assert out[0]["idCity"] == "663"
    assert [r["idCity"] for r in out[1:]] == ["325", "1123"]
    assert [r["idOffer"] for r in out[1:]] == ["SGM-1-325", "SGM-1-1123"]
    assert out[1]["sCity"] == "Балашиха"


def test_fanout_skips_city_equal_to_source_city():
    ids = [r["idCity"] for r in expand_records([_car()], _CITIES, _fanout(), "idOffer")]
    assert ids.count("663") == 1  # не задвоили исходный город


def test_fanout_substitutes_city_placeholder_only_where_asked():
    out = expand_records(
        [_car()], _CITIES, _fanout(substitute_columns=["Additional"]), "idOffer"
    )
    assert out[1]["Additional"] == "Продаётся в городе Балашиха."
    # длинное имя из справочника в текст не протекает
    assert "Московская область" not in out[2]["Additional"]
    # в исходной строке подставляется её собственный город
    assert out[0]["Additional"] == "Продаётся в городе Красногорск."


def test_fanout_without_original_and_with_limit():
    out = expand_records(
        [_car()], _CITIES, _fanout(include_original=False, limit=2), "idOffer"
    )
    assert [r["idCity"] for r in out] == ["325"]  # 663 отброшен как исходный


def test_fanout_flag_column_filters_rows():
    rows = [_car(), _car(idOffer="SGM-2", VIN="X2", **{"Расшивка": "нет"})]
    rows[0]["Расшивка"] = "да"
    out = expand_records(rows, _CITIES, _fanout(flag_column="Расшивка"), "idOffer")
    ids = [r["idOffer"] for r in out]
    assert ids == ["SGM-1", "SGM-1-325", "SGM-1-1123", "SGM-2"]


def test_fanout_detects_id_collision():
    # Две машины с одинаковым idOffer — расшивка обязана это поймать.
    rows = [_car(), _car(VIN="X2")]
    with pytest.raises(FanoutError, match="дубль"):
        expand_records(rows, _CITIES, _fanout(), "idOffer")


def test_fanout_city_name_uses_full_ref_value_in_scity():
    out = expand_records([_car(idCity="", sCity="")], _CITIES, _fanout(), "idOffer")
    kras = [r for r in out if r["idCity"] == "663"][0]
    assert kras["sCity"] == "Красногорск, Московская область"


def test_fanout_substitutes_prepositional_case():
    """{city_in} даёт «в Химках», а не «в Химки» — 129 объявлений читают люди."""
    row = _car(Additional="Доставка {city_in}. Склад: {city}.")
    out = expand_records(
        [row], _CITIES, _fanout(substitute_columns=["Additional"]), "idOffer"
    )
    assert out[0]["Additional"] == "Доставка в Красногорске. Склад: Красногорск."
    assert out[1]["Additional"] == "Доставка в Балашихе. Склад: Балашиха."
    assert out[2]["Additional"] == "Доставка в Химках. Склад: Химки."


def test_fanout_falls_back_when_case_form_missing():
    cities = [City(id="325", name="Балашиха")]  # sCityIn в справочнике нет
    out = expand_records(
        [_car(idCity="", sCity="", Additional="Доставка {city_in}.")],
        cities, _fanout(substitute_columns=["Additional"]), "idOffer",
    )
    assert out[-1]["Additional"] == "Доставка в Балашиха."


def test_load_cities_reads_generated_dictionary():
    cities = load_cities("config/drom_mo_cities.yaml")
    assert len(cities) == 129
    by_id = {c.id: c.name for c in cities}
    assert by_id["325"] == "Балашиха"
    assert by_id["1123"] == "Химки"
    assert by_id["663"] == "Красногорск, Московская область"

    by_id_full = {c.id: c for c in cities}
    assert by_id_full["1123"].prepositional == "в Химках"
    assert by_id_full["663"].prepositional == "в Красногорске"
    assert by_id_full["855"].prepositional == "в Одинцово"


def test_load_cities_rejects_missing_file():
    with pytest.raises(FanoutError, match="не найден"):
        load_cities("config/нет-такого.yaml")


# ── Фото: прямые ссылки ────────────────────────────────────────────
def test_check_photos_flags_page_links():
    """ibb.co/XXXX — страница, а не файл: Дром такое фото не загрузит."""
    bad = check_photos({
        "Photos": "https://ibb.co/C5988sKL|https://i.ibb.co/x/a.jpg",
        "PhotoDir": "", "PhotoMain": "",
    })
    assert bad == ["https://ibb.co/C5988sKL"]


def test_check_photos_accounts_for_photodir_prefix():
    # PhotoDir + имя файла в сумме дают .jpg — претензий нет.
    assert check_photos({
        "Photos": "a.jpg|b.jpeg", "PhotoDir": "https://i.ibb.co/x/", "PhotoMain": "m.jpg",
    }) == []


def test_extract_direct_url_prefers_og_image():
    html = '<meta property="og:image" content="https://i.ibb.co/abc/ford-1.jpg">'
    assert extract_direct_url(html) == "https://i.ibb.co/abc/ford-1.jpg"


def test_extract_direct_url_falls_back_to_markup_and_gives_up():
    assert extract_direct_url('<img src="https://i.ibb.co/z/p.jpeg">') == \
        "https://i.ibb.co/z/p.jpeg"
    assert extract_direct_url("<html>пусто</html>") is None


def test_update_card_replaces_photos_line_only(tmp_path):
    card = tmp_path / "card.yaml"
    card.write_text(
        'offer:\n  Photos: "https://ibb.co/A|https://ibb.co/B"\n  Price: "100"\n',
        encoding="utf-8",
    )
    update_card(card, ["https://i.ibb.co/x/1.jpg", "https://i.ibb.co/y/2.jpg"])
    text = card.read_text(encoding="utf-8")
    assert 'Photos: "https://i.ibb.co/x/1.jpg|https://i.ibb.co/y/2.jpg"' in text
    assert 'Price: "100"' in text  # остальное не тронуто
