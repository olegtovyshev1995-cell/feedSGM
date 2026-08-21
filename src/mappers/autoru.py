"""Маппер Auto.ru (легковые автомобили).

Формат: `<data><cars><car>…</car></cars></data>` по официальной справке
Авто.ру (конспект — docs/autoru-feed.md, HTML-копии — docs/examples/).

Ключ обновления объявления: mark + model + tech_param_id + year + color +
(VIN или unique_id). Раздел («Новые» / «С пробегом») Авто.ру определяет
сам по составу полей: наличие state/run/registry_year → «С пробегом».

Имена тегов — строго строчными (требование Авто.ру). Порядок простых полей
взят из примеров справки. Пустые поля не эмитим (кроме unique_id, если он
задан) — так фид остаётся компактным и не спорит с необязательностью.

Секции со вложенностью (images, delivery_info, contact_info, badges,
video, panoramas) собираются отдельно из мультизначных колонок:
  images        — ссылки через `|`
  delivery_info — адреса доставки через `|` (до 10)
  badges        — названия стикеров через `|` (до 3)
  contact_info  — из колонок contact_name / contact_phone / contact_time
"""

from __future__ import annotations

from src.mappers.base import BaseMapper, split_list, xml_escape

# Простые (плоские) теги в порядке примеров справки Авто.ру. Двигатель можно
# задать либо modification_id, либо пятью параметрами engine_*/gearbox/drive
# — одновременно нельзя (проверяет валидатор карточки), маппер эмитит то,
# что заполнено.
_SIMPLE_FIELDS = [
    "mark_id", "folder_id", "modification_id",
    # Пять параметров двигателя — альтернатива modification_id.
    "engine_volume", "engine_power", "engine_type", "gearbox", "drive",
    "tech_param_id", "nameplate",
    "complectation_name",
    "body_type", "wheel", "color", "metallic",
    "availability", "custom",
    "state", "owners_number", "run",         # раздел «С пробегом»
    "year", "registry_year", "doors_count",
    "price",
    "credit_discount", "insurance_discount", "tradein_discount", "max_discount",
    "currency", "vin",
    "description", "extras",
]

# Теги после секций images/video (тоже плоские).
_TAIL_FIELDS = [
    "sale_services",
    "poi_id",
    "warranty_expire", "warranty_type", "pts", "sts",   # «С пробегом»
    "armored", "with_nds", "exchange",
    "accepted_autoru_exclusive", "online_view_available",
    "not_registered_in_russia", "pledge_number",
    "action",
]


class AutoRuMapper(BaseMapper):
    platform = "autoru"

    def build_xml(self, records: list[dict[str, str]]) -> str:
        lines: list[str] = [
            '<?xml version="1.0" encoding="utf-8"?>',
            "<data>",
            "  <cars>",
        ]
        for rec in records:
            lines.append("    <car>")
            self._emit_car(lines, rec)
            lines.append("    </car>")
        lines.append("  </cars>")
        lines.append("</data>")
        lines.append("")
        return "\n".join(lines)

    def _emit_car(self, lines: list[str], rec: dict[str, str]) -> None:
        for field in _SIMPLE_FIELDS:
            value = rec.get(field, "").strip()
            if value:
                lines.append(f"      <{field}>{xml_escape(value)}</{field}>")

        # images: до 40 ссылок через `|`.
        images = split_list(rec.get("images", ""), "|")
        if images:
            lines.append("      <images>")
            for url in images:
                lines.append(f"        <image>{xml_escape(url)}</image>")
            lines.append("      </images>")

        video = rec.get("video", "").strip()
        if video:
            lines.append(f"      <video>{xml_escape(video)}</video>")

        # unique_id эмитим, только если он задан (нужен, когда нет VIN).
        unique_id = rec.get("unique_id", "").strip()
        if unique_id:
            lines.append(f"      <unique_id>{xml_escape(unique_id)}</unique_id>")

        # delivery_info: до 10 адресов доставки через `|`.
        deliveries = split_list(rec.get("delivery_info", ""), "|")
        if deliveries:
            lines.append("      <delivery_info>")
            for address in deliveries:
                lines.append("        <delivery>")
                lines.append(f"          <address>{xml_escape(address)}</address>")
                lines.append("        </delivery>")
            lines.append("      </delivery_info>")

        for field in _TAIL_FIELDS:
            value = rec.get(field, "").strip()
            if value:
                lines.append(f"      <{field}>{xml_escape(value)}</{field}>")

        # contact_info: собирается из отдельных колонок.
        self._emit_contact(lines, rec)

        # badges: до 3 стикеров через `|`.
        badges = split_list(rec.get("badges", ""), "|")
        if badges:
            lines.append("      <badges>")
            for name in badges[:3]:
                lines.append("        <badge>")
                lines.append(f"          <name>{xml_escape(name)}</name>")
                lines.append("        </badge>")
            lines.append("      </badges>")

    @staticmethod
    def _emit_contact(lines: list[str], rec: dict[str, str]) -> None:
        name = rec.get("contact_name", "").strip()
        phone = rec.get("contact_phone", "").strip()
        time = rec.get("contact_time", "").strip()
        sip = rec.get("contact_sip_url", "").strip()
        if not (name or phone or time or sip):
            return
        lines.append("      <contact_info>")
        lines.append("        <contact>")
        for tag, value in (("name", name), ("phone", phone),
                           ("sip_url", sip), ("time", time)):
            if value:
                lines.append(f"          <{tag}>{xml_escape(value)}</{tag}>")
        lines.append("        </contact>")
        lines.append("      </contact_info>")
