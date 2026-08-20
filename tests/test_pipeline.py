"""Тесты пайплайна: чтение-в-словари, валидация, маппинг, атомарная запись.

Google API не задействуется — проверяем чистую логику на синтетике.
"""

from __future__ import annotations

from xml.dom.minidom import parseString

import pytest

from src.build import atomic_write
from src.config import AppConfig, ConfigError, PlatformConfig, load_config
from src.mappers import MAPPERS
from src.mappers.base import cdata, split_list, xml_escape
from src.sheets_client import rows_to_dicts
from src.validators import ValidationFailure, validate_rows


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
