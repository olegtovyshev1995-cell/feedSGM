"""Маппер Avito (автозагрузка, легковые автомобили).

Формат: XML `<Ads formatVersion="3" target="Avito.ru">` со списком `<Ad>`.
Имена тегов совпадают с заголовками колонок листа (шаблон автозагрузки).
Актуальный список полей и допустимых значений — autoload.avito.ru/format.
"""

from __future__ import annotations

from src.mappers.base import BaseMapper, cdata, split_list, xml_attr_escape, xml_escape

# Порядок тегов = порядок колонок в шаблоне автозагрузки Avito.
# Images и Description обрабатываются отдельно (списком и CDATA).
_SIMPLE_FIELDS = [
    "Id", "Category", "VehicleType", "Condition", "Make", "Model",
    "Generation", "Modification", "Year", "Mileage", "BodyType", "Doors",
    "Color", "EngineType", "EngineVolume", "Power", "Transmission", "Drive",
    "Wheel", "SteeringWheel", "VIN", "Availability", "OwnersCount", "Price",
    "Address", "ContactPhone", "ManagerName", "AdStatus", "DateBegin",
]

_DESCRIPTION_FIELD = "Description"  # HTML-описание → CDATA
_IMAGES_FIELD = "Images"           # ссылки на фото через запятую


class AvitoMapper(BaseMapper):
    platform = "avito"

    def build_xml(self, records: list[dict[str, str]]) -> str:
        lines: list[str] = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<Ads formatVersion="3" target="Avito.ru">',
        ]
        for rec in records:
            lines.append("  <Ad>")

            for field in _SIMPLE_FIELDS:
                value = rec.get(field, "").strip()
                # Id обязателен (гарантирован валидатором); прочие пустые опускаем.
                if value or field == "Id":
                    lines.append(f"    <{field}>{xml_escape(value)}</{field}>")

            description = rec.get(_DESCRIPTION_FIELD, "").strip()
            if description:
                # HTML-теги описания должны дойти как разметка → CDATA (R2:
                # спецсимволы при этом не ломают XML).
                lines.append(f"    <{_DESCRIPTION_FIELD}>{cdata(description)}</{_DESCRIPTION_FIELD}>")

            images = split_list(rec.get(_IMAGES_FIELD, ""), ",")
            if images:
                lines.append("    <Images>")
                for url in images:
                    lines.append(f'      <Image url="{xml_attr_escape(url)}"/>')
                lines.append("    </Images>")

            lines.append("  </Ad>")

        lines.append("</Ads>")
        lines.append("")  # финальный перевод строки
        return "\n".join(lines)
