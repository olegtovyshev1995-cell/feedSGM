"""Маппер Auto.ru (легковые автомобили).

Формат: XML `<data><cars><car>…</car></cars></data>` — как на листе «XML»
шаблона Авто.ру. Имена тегов = заголовки колонок листа «Фид (легковые)».
Несколько фото в колонке images разделяются символом `|`.
Символы < > & в описании экранируются автоматически при сборке.
"""

from __future__ import annotations

from src.mappers.base import BaseMapper, split_list, xml_escape

# Порядок тегов = порядок колонок шаблона Авто.ру.
# images — отдельно (секция со списком <image>).
_SIMPLE_FIELDS = [
    "unique_id", "mark_id", "folder_id", "modification_id", "body_type",
    "year", "color", "price", "currency", "run", "vin", "availability",
    "custom", "state", "owners_number", "pts", "wheel", "engine_type",
    "drive", "haggle", "exchange", "max_discount", "description", "url",
]

_IMAGES_FIELD = "images"
_REQUIRED_ALWAYS = {"unique_id"}  # emit даже если по какой-то причине пусто


class AutoRuMapper(BaseMapper):
    platform = "autoru"

    def build_xml(self, records: list[dict[str, str]]) -> str:
        lines: list[str] = [
            '<?xml version="1.0" encoding="utf-8"?>',
            "<data>",
            "  <cars>",
        ]
        for rec in records:
            lines.append("  <car>")

            for field in _SIMPLE_FIELDS:
                value = rec.get(field, "").strip()
                if value or field in _REQUIRED_ALWAYS:
                    lines.append(f"    <{field}>{xml_escape(value)}</{field}>")

            images = split_list(rec.get(_IMAGES_FIELD, ""), "|")
            if images:
                inner = "".join(f"<image>{xml_escape(u)}</image>" for u in images)
                lines.append(f"    <images>{inner}</images>")

            lines.append("  </car>")

        lines.append("  </cars>")
        lines.append("</data>")
        lines.append("")
        return "\n".join(lines)
