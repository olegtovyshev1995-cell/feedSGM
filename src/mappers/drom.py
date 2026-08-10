"""Маппер Drom (легковые автомобили).

Формат: XML `<avtoxml><Offers><Offer>…</Offer></Offers></avtoxml>` по
инструкции автозакачивания Drom (bulls.xml). Ключ обновления объявления —
VIN или idOffer.

Без файла-справочника ref.xml числовые id-поля (idMark, idModel, idCity …)
надёжно подставить нельзя, поэтому используем текстовые поля s* (sMark,
sModel, sCity, sTransmission …) — Drom разрешает указывать значение текстом,
если id недоступен. Фотографии — секция <Photos><Photo>url</Photo></Photos>,
ссылки в колонке Photos разделяются символом `|`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.mappers.base import BaseMapper, split_list, xml_escape

# Порядок и состав тегов Offer. Эмитим только непустые поля; обязательные
# (idOffer, sMark, sModel, sCity, YearOfMade, VIN, Price) гарантирует
# валидатор. id-поля (idMark/idModel/idCity …) поддержаны опционально —
# если пользователь всё же знает id из справочника, он положит их в колонки.
_SIMPLE_FIELDS = [
    "idOffer",
    "idMark", "sMark",
    "idModel", "sModel",
    "idModification",
    "idCity", "sCity",
    "idCountry",
    "YearOfMade",
    "VIN",
    "Price",
    "idCurrency",
    "NewType",
    "Volume",
    "FrameType",
    "Color",
    "sTransmission",
    "sEngineType",
    "sDriveType",
    "sWheelType",
    "Haul",
    "idHaulRussiaType",
    "NumberOfOwners",
    "Power",
    "Whereabouts",
    "DamagedType",
    "Phone",
    "Additional",  # текстовое описание (можно ссылку на YouTube)
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
                lines.append("      <Photos>")
                for url in photos:
                    lines.append(f"        <Photo>{xml_escape(url)}</Photo>")
                lines.append("      </Photos>")

            lines.append("    </Offer>")

        lines.append("  </Offers>")
        lines.append("</avtoxml>")
        lines.append("")
        return "\n".join(lines)
