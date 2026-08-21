"""Расшивка объявления по городам («веер»).

Одна строка листа = один автомобиль. Для регионального размещения та же
карточка должна уйти в несколько городов: у каждой копии свой `idOffer`,
свой `idCity`/`sCity` и, при желании, свой текст с названием города.

Слой чистый: на вход — записи и справочник городов, на выход — записи.
Ни чтения таблицы, ни XML здесь нет; оркестратор просто вставляет вызов
между валидацией и маппером.

Внимание к ключу дедупликации: Drom обновляет объявление при совпадении
**VIN или idOffer**. Уникальный `idOffer` у копии — необходимое, но не
достаточное условие: копии с одинаковым VIN площадка считает одним и тем
же автомобилем. Поэтому расшивка рассчитана на пул разных машин либо на
явное решение владельца кабинета по полю VIN — сам модуль VIN не трогает
и не генерирует.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from src.config import FanoutSettings


class FanoutError(RuntimeError):
    """Проблема расшивки: битый справочник городов или коллизия id."""


@dataclass(frozen=True)
class City:
    """Город из справочника площадки."""

    id: str
    name: str
    # Предложный падеж с предлогом: «в Балашихе», «во Фрязино».
    # Пустая строка — форма неизвестна, тогда падаем обратно на «в Балашиха».
    in_form: str = ""

    @property
    def short_name(self) -> str:
        """«Красногорск, Московская область» → «Красногорск».

        В `sCity` уходит полное название (так оно записано в ref.xml), а в
        текст объявления подставляется короткое — иначе получится
        «Купить в Красногорск, Московская область».
        """
        return self.name.split(",", 1)[0].strip()

    @property
    def prepositional(self) -> str:
        """«в Балашихе» — для фраз «доставка …» в тексте объявления."""
        return self.in_form or f"в {self.short_name}"


def load_cities(path: str | Path) -> list[City]:
    """Читает YAML-справочник городов (см. scripts/gen_drom_mo_cities.py)."""
    p = Path(path)
    if not p.is_file():
        raise FanoutError(f"Справочник городов не найден: {p}")

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise FanoutError(f"Не удалось разобрать {p}: {exc}") from exc

    items = (raw or {}).get("cities")
    if not isinstance(items, list) or not items:
        raise FanoutError(f"{p}: ожидался непустой список 'cities'")

    cities: list[City] = []
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict) or "idCity" not in item or "sCity" not in item:
            raise FanoutError(
                f"{p}: элемент №{i} должен содержать idCity и sCity, получено: {item!r}"
            )
        cities.append(
            City(
                id=str(item["idCity"]).strip(),
                name=str(item["sCity"]).strip(),
                in_form=str(item.get("sCityIn", "")).strip(),
            )
        )

    ids = [c.id for c in cities]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise FanoutError(f"{p}: повторяющиеся idCity: {', '.join(dupes)}")

    return cities


def _is_on(value: str) -> bool:
    """Трактовка ячейки-флага в таблице."""
    return value.strip().lower() in {"1", "да", "yes", "true", "y", "+", "х", "x"}


def expand_records(
    records: list[dict[str, str]],
    cities: list[City],
    settings: FanoutSettings,
    id_column: str,
) -> list[dict[str, str]]:
    """Разворачивает записи по городам.

    Правила:
      * строка расшивается, только если `flag_column` не задана или флаг
        в ней включён; остальные строки проходят насквозь без изменений;
      * `include_original` оставляет исходную строку как есть — это уже
        размещённое объявление, его `idOffer` и город менять нельзя;
      * город, совпадающий с городом исходной строки, из веера убирается,
        иначе в фиде окажутся два объявления в одном городе;
      * в колонках из `substitute_columns` подставляются `{city}`
        (именительный) и `{city_in}` (предложный с предлогом);
      * `clone_overrides` переопределяет поля только у копий — исходное
        объявление остаётся таким, каким оно уже размещено.
    """
    cities_used = cities[: settings.limit] if settings.limit else cities
    # Исходный город тоже ищем в справочнике — ради корректного {city_in}.
    by_id = {c.id: c for c in cities}

    result: list[dict[str, str]] = []
    for record in records:
        flag_on = settings.flag_column is None or _is_on(record.get(settings.flag_column, ""))

        if not flag_on:
            result.append(dict(record))
            continue

        source_city_id = record.get(settings.city_id_column, "").strip()

        if settings.include_original:
            # Плейсхолдер {city} подставляем и в исходной строке — иначе в
            # уже размещённом объявлении останется буквальное «{city}».
            own = by_id.get(source_city_id) or City(
                id=source_city_id,
                name=record.get(settings.city_name_column, ""),
                in_form=record.get(settings.city_in_column, ""),
            )
            result.append(_substitute(dict(record), own, settings))

        for city in cities_used:
            if source_city_id and city.id == source_city_id:
                continue  # исходное объявление в этом городе уже есть
            result.append(_clone(record, city, settings, id_column))

    _assert_unique_ids(result, id_column)
    return result


def _clone(
    record: dict[str, str],
    city: City,
    settings: FanoutSettings,
    id_column: str,
) -> dict[str, str]:
    clone = dict(record)
    clone[settings.city_id_column] = city.id
    clone[settings.city_name_column] = city.name
    clone[id_column] = settings.id_template.format(
        id=record.get(id_column, "").strip(),
        city_id=city.id,
        city_name=city.short_name,
    )

    # Переопределения применяются только к копиям и до подстановки {city},
    # чтобы новый текст тоже получил название города.
    clone.update(settings.clone_overrides)

    return _substitute(clone, city, settings)


def _substitute(record: dict[str, str], city: City, settings: FanoutSettings) -> dict[str, str]:
    """Подставляет название города в текстовые поля.

    `{city}`    — именительный падеж: «Балашиха»
    `{city_in}` — предложный с предлогом: «в Балашихе», «во Фрязино»
    """
    for column in settings.substitute_columns:
        value = record.get(column, "")
        if value:
            record[column] = (
                value.replace("{city_in}", city.prepositional)
                .replace("{city}", city.short_name)
            )
    return record


def _assert_unique_ids(records: list[dict[str, str]], id_column: str) -> None:
    seen: dict[str, int] = {}
    for i, record in enumerate(records, 1):
        value = record.get(id_column, "").strip()
        if not value:
            raise FanoutError(f"после расшивки пустой {id_column} в записи №{i}")
        if value in seen:
            raise FanoutError(
                f"после расшивки дубль {id_column}={value!r} "
                f"(записи №{seen[value]} и №{i}); проверьте id_template"
            )
        seen[value] = i
