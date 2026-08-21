#!/usr/bin/env python3
"""Генерирует справочник городов Московской области для фида Drom.

Вход  — `data/refs/drom_ref.xml` (официальный ref.xml Дрома).
Выход — `config/drom_mo_cities.yaml`: список `idCity` + `sCity`.

Зачем отдельный скрипт: в ref.xml у города нет поля региона, есть только
`idCity`/`sCity`. Регион восстанавливаем двумя способами:

1. Названия, неоднозначные по России, Дром пишет с регионом —
   «Красногорск, Московская область». Такие берём автоматически.
2. Названия, уникальные по России (Балашиха, Химки, Подольск …), в
   справочнике идут без региона — их перечисляем явно в `_UNIQUE_MO`.

Список сверен с ref.xml от 21.08.2026; при обновлении справочника скрипт
сообщит, какие имена из `_UNIQUE_MO` пропали (значит, Дром их переименовал
или добавил регион в название).

Запуск:  python -m scripts.gen_drom_mo_cities
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REF_PATH = Path("data/refs/drom_ref.xml")
OUT_PATH = Path("config/drom_mo_cities.yaml")

# Суффикс, которым Дром размечает неоднозначные названия.
_MO_SUFFIX = "Московская область"

# Городской округ / посёлок МО, название которого уникально по стране,
# поэтому в справочнике идёт без региона.
_UNIQUE_MO = [
    "Апрелевка", "Балашиха", "Белоозёрский", "Бронницы", "Верея", "Видное",
    "Волоколамск", "Воскресенск", "Высоковск", "Голицыно", "Дедовск",
    "Дзержинский", "Дмитров", "Долгопрудный", "Домодедово", "Дрезна",
    "Егорьевск", "Жуковский", "Запрудня", "Зарайск", "Звенигород", "Икша",
    "Истра", "Кашира", "Климовск", "Клин", "Коломна", "Королёв",
    "Котельники", "Красково", "Краснозаводск", "Кратово", "Кубинка",
    "Куровское", "Ликино-Дулёво", "Лобня", "Лосино-Петровский", "Луховицы",
    "Лыткарино", "Люберцы", "Малаховка", "Мишеронский", "Можайск", "Монино",
    "Мытищи", "Наро-Фоминск", "Нахабино", "Некрасовский", "Ногинск",
    "Обухово", "Одинцово", "Ожерелье", "Озёры", "Орехово-Зуево",
    "Павловский Посад", "Пересвет", "Подольск", "Протвино", "Пущино",
    "Раменское", "Реутов", "Рошаль", "Руза", "Свердловский",
    "Селятино", "Серебряные Пруды", "Сергиев Посад", "Серпухов", "Снегири",
    "Солнечногорск", "Софрино", "Старая Купавна", "Столбовая", "Ступино",
    "Талдом", "Томилино", "Тучково", "Уваровка", "Удельная", "Фрязино",
    "Фряново", "Химки", "Хотьково", "Черноголовка", "Черусти", "Шатура",
    "Шатурторф", "Шаховская", "Щербинка", "Щёлково", "Электрогорск",
    "Электросталь", "Электроугли", "Яхрома", "Развилка",
]

# Записи, которые НЕ являются городами МО (или являются регионом целиком)
# и в список расшивки по умолчанию не идут.
_EXCLUDE = {"Московская область"}


def load_cities(ref_path: Path) -> list[tuple[int, str]]:
    root = ET.parse(ref_path).getroot()
    section = root.find("Cities")
    if section is None:
        raise SystemExit(f"{ref_path}: нет секции <Cities> — это не ref.xml Дрома")
    return [
        (int(c.findtext("idCity")), c.findtext("sCity"))
        for c in section
        if c.findtext("idCity") and c.findtext("sCity")
    ]


def select_mo(cities: list[tuple[int, str]]) -> tuple[list[tuple[int, str]], list[str]]:
    by_name = {name: cid for cid, name in cities}

    picked: dict[int, str] = {}
    for cid, name in cities:
        if _MO_SUFFIX in name and name not in _EXCLUDE:
            picked[cid] = name

    missing: list[str] = []
    for name in _UNIQUE_MO:
        cid = by_name.get(name)
        if cid is None:
            missing.append(name)
        else:
            picked[cid] = name

    return sorted(picked.items(), key=lambda x: x[1]), missing


def render_yaml(rows: list[tuple[int, str]]) -> str:
    lines = [
        "# Города Московской области для расшивки объявлений на Drom.",
        "# СГЕНЕРИРОВАНО: python -m scripts.gen_drom_mo_cities — не править руками.",
        "# Источник: data/refs/drom_ref.xml (секция <Cities>).",
        f"# Всего городов: {len(rows)}.",
        "",
        "cities:",
    ]
    for cid, name in rows:
        lines.append(f'  - idCity: {cid}')
        lines.append(f'    sCity: "{name}"')
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    if not REF_PATH.exists():
        print(f"не найден {REF_PATH}", file=sys.stderr)
        return 1

    cities = load_cities(REF_PATH)
    rows, missing = select_mo(cities)

    if missing:
        print(
            "ВНИМАНИЕ: в ref.xml больше нет городов: " + ", ".join(missing)
            + " — проверьте справочник и поправьте _UNIQUE_MO",
            file=sys.stderr,
        )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(render_yaml(rows), encoding="utf-8")
    print(f"{OUT_PATH}: {len(rows)} городов (всего в справочнике {len(cities)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
