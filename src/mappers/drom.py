"""Маппер Drom (легковые автомобили).

Формат: XML `<avtoxml><Offers><Offer>…</Offer></Offers></avtoxml>` по
инструкции автозакачивания Drom (bulls.xml) — конспект в
`docs/drom-autoload.md`, справочник в `data/refs/drom_ref.xml`.

Ключ обновления объявления — VIN **или** idOffer: если в новой версии
файла совпало любое из этих полей, Дром обновит объявление, а не создаст
новое. Объявления, которых нет в файле, Дром удаляет.

Числовые id-поля (idMark, idModel, idCity, FrameType, Color …) берутся из
ref.xml и предпочтительнее: значения вне справочника Дром не примет. Если
id неизвестен, инструкция разрешает текстовые дубли (sMark, sModel, sCity,
sTransmission …) — маппер эмитит и те, и другие, что укажете в колонках.

Порядок тегов внутри Offer повторяет порядок таблицы полей в инструкции —
на случай, если XSD автозакачки описывает Offer как xs:sequence.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.mappers.base import (
    BaseMapper,
    split_list,
    xml_attr_escape,
    xml_escape,
)

# Порядок и состав тегов Offer — ровно как в таблице полей инструкции
# (docs/drom-autoload.md). Эмитим только непустые поля; обязательные
# (idOffer, марка, модель, город, YearOfMade, VIN, Price) гарантирует
# валидатор — марка/модель/город принимаются как id-, так и s-вариантом.
_SIMPLE_FIELDS = [
    "idOffer",
    "idMark", "idModel",
    "sMark", "sModel",
    "idModification",
    "idCountry",
    "idCity", "sCity",
    "YearOfMade",
    "VIN",
    "Price",
    "idCurrency",
    "NewType",
    "Volume",
    "FrameType",
    "Color",
    "idTransmission", "sTransmission",
    "idEngineType", "sEngineType",
    "idHybridType", "sHybridType",
    "idGbo", "sGbo",
    "idDriveType", "sDriveType",
    "idWheelType", "sWheelType",
    "Haul",
    "idHaulRussiaType",
    "NumberOfOwners",
    "sComplectation", "idComplectation",
    "Additional",  # описание ТС; здесь же ссылка на видео с YouTube
]

# Поля, идущие в инструкции ПОСЛЕ секции Photos.
_TAIL_FIELDS = [
    "Phone",          # не более 20 символов
    "Whereabouts",    # 0 в наличии / 1 в пути / 2 под заказ
    "Power",
    "idCertProgram",
    "DamagedType",    # 0 небитый / 1 битый или не на ходу
    "SOR",            # номер СТС для авто с пробегом, 10 символов
]

_PHOTOS_FIELD = "Photos"


class DromMapper(BaseMapper):
    platform = "drom"

    def __init__(self, config) -> None:
        super().__init__(config)
        # Дату сборки можно зафиксировать извне (для детерминированных тестов);
        # по умолчанию берётся текущее время UTC в момент сборки фида.
        self.build_date: str | None = None

    def _last_build_date(self) -> str:
        if self.build_date is not None:
            return self.build_date
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")

    def build_xml(self, records: list[dict[str, str]]) -> str:
        lines: list[str] = [
            '<?xml version="1.0" encoding="utf-8"?>',
            "<avtoxml>",
            f"  <lastBuildDate>{xml_escape(self._last_build_date())}</lastBuildDate>",
            "  <Offers>",
        ]
        for rec in records:
            lines.append("    <Offer>")

            for field in _SIMPLE_FIELDS:
                value = rec.get(field, "").strip()
                if value or field == "idOffer":
                    lines.append(f"      <{field}>{xml_escape(value)}</{field}>")

            photos = split_list(rec.get(_PHOTOS_FIELD, ""), "|")
            if photos:
                attrs = ""
                for attr in ("PhotoDir", "PhotoMain"):
                    value = rec.get(attr, "").strip()
                    if value:
                        attrs += f' {attr}="{xml_attr_escape(value)}"'
                lines.append(f"      <Photos{attrs}>")
                for url in photos:
                    lines.append(f"        <Photo>{xml_escape(url)}</Photo>")
                lines.append("      </Photos>")

            for field in _TAIL_FIELDS:
                value = rec.get(field, "").strip()
                if value:
                    lines.append(f"      <{field}>{xml_escape(value)}</{field}>")

            lines.append("    </Offer>")

        lines.append("  </Offers>")
        lines.append("</avtoxml>")
        lines.append("")
        return "\n".join(lines)
