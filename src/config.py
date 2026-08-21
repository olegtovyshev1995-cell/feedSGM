"""Загрузка и валидация конфига на старте.

Битый config.yaml должен ронять процесс сразу, с понятной ошибкой,
а не через несколько минут на записи фида. Поэтому вся структура
описана Pydantic-моделями и проверяется в момент загрузки.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator


class ConfigError(RuntimeError):
    """Понятная ошибка конфигурации/окружения для вывода пользователю."""


class FanoutSettings(BaseModel):
    """Расшивка одной строки листа по списку городов.

    Нужна для регионального размещения: та же карточка авто уходит в
    несколько городов, у каждой копии свой idOffer и свой idCity/sCity.
    Логика — в src/fanout.py, здесь только параметры.
    """

    model_config = {"extra": "forbid"}

    enabled: bool = False
    # YAML со списком городов площадки: cities: [{idCity, sCity}, …].
    # Путь относительно корня проекта.
    cities_file: str = Field(..., min_length=1)
    # Шаблон id копии. Доступны {id}, {city_id}, {city_name}.
    id_template: str = "{id}-{city_id}"
    city_id_column: str = "idCity"
    city_name_column: str = "sCity"
    # Предложный падеж исходного города («в Самаре») — для {city_in} в
    # строке, город которой отсутствует в справочнике расшивки.
    city_in_column: str = "sCityIn"
    # Колонка-флаг «расшивать эту строку» (да/1/+). None — расшивать все.
    flag_column: str | None = None
    # Оставлять исходную строку как есть (уже размещённое объявление).
    include_original: bool = True
    # Колонки, где плейсхолдер {city} заменяется названием города.
    substitute_columns: list[str] = Field(default_factory=list)
    # Значения, которыми переопределяются поля ТОЛЬКО у копий (исходное
    # объявление не трогаем). Пустая строка = убрать тег из копии.
    # Типовой случай: у копий свой Whereabouts или пустой VIN.
    clone_overrides: dict[str, str] = Field(default_factory=dict)
    # Ограничить число городов (для прогонов и тестов). None — все.
    limit: int | None = Field(None, ge=1)

    @field_validator("id_template")
    @classmethod
    def _template_varies_by_city(cls, v: str) -> str:
        # Без {city_id}/{city_name} все копии получат одинаковый idOffer,
        # и площадка сочтёт их одним объявлением.
        if "{city_id}" not in v and "{city_name}" not in v:
            raise ValueError(
                "id_template обязан содержать {city_id} или {city_name}, "
                f"иначе id копий совпадут; получено: {v!r}"
            )
        return v


class PlatformConfig(BaseModel):
    """Описание одной площадки (одного листа таблицы → одного фида)."""

    model_config = {"extra": "forbid"}

    name: str = Field(..., description="Ключ маппера в реестре MAPPERS")
    enabled: bool = True
    sheet: str = Field(..., description="Имя листа в Google-таблице")

    # header_row/data_start_row — 1-based, как строки в самой таблице.
    header_row: int = Field(..., ge=1, description="Строка с именами колонок")
    data_start_row: int = Field(..., ge=1, description="Первая строка с данными")

    id_column: str = Field(..., description="Колонка со стабильным Id (R6)")
    output: str = Field(..., description="Имя файла фида в feeds/")
    required: list[str] = Field(default_factory=list)

    # Категорийные/произвольные доп. поля, которые маппер может пробросить
    # гибко, не завися от жёсткой схемы (R12).
    extra: dict[str, str] = Field(default_factory=dict)

    # Расшивка по городам (региональное размещение). None — выключена.
    fanout: FanoutSettings | None = None

    @field_validator("data_start_row")
    @classmethod
    def _data_after_header(cls, v: int, info) -> int:
        header = info.data.get("header_row")
        if header is not None and v <= header:
            raise ValueError(
                f"data_start_row ({v}) должен быть больше header_row ({header})"
            )
        return v

    @field_validator("output")
    @classmethod
    def _safe_output(cls, v: str) -> str:
        # Имя файла, а не путь: фид всегда пишется в feeds/, без выхода вверх.
        if "/" in v or "\\" in v or v in (".", ".."):
            raise ValueError(f"output должен быть именем файла, получено: {v!r}")
        return v


class ColumnMapping(BaseModel):
    """Сопоставление канонических полей авто с заголовками входного листа.

    Значение — имя колонки в таблице. vin/make/model обязательны (по ним
    агент ищет спецификацию); остальные — опциональны.
    """

    model_config = {"extra": "forbid"}

    vin: str
    make: str
    model: str
    modification: str | None = None
    year: str | None = None
    price: str | None = None
    city: str | None = None


class IngestConfig(BaseModel):
    """Входной лист (минимальные данные по авто: VIN, марка, модель…)."""

    model_config = {"extra": "forbid"}

    sheet: str
    header_row: int = Field(1, ge=1)
    data_start_row: int = Field(2, ge=1)
    columns: ColumnMapping

    @field_validator("data_start_row")
    @classmethod
    def _after_header(cls, v: int, info) -> int:
        header = info.data.get("header_row")
        if header is not None and v <= header:
            raise ValueError(
                f"data_start_row ({v}) должен быть больше header_row ({header})"
            )
        return v


class AgentConfig(BaseModel):
    """Настройки AI-агента (Claude API) для стадии обогащения."""

    model_config = {"extra": "forbid"}

    # Модель по умолчанию — самая способная (см. рекомендации Anthropic).
    # Для экономии можно поставить claude-sonnet-5 / claude-haiku-4-5.
    model: str = "claude-opus-5"
    # Таймаут одного запроса к Claude API, сек. Веб-поиск + рассуждение
    # занимают заметно больше обычного чата, поэтому 120с, а не 30.
    request_timeout_seconds: int = Field(120, ge=10, le=600)
    max_retries: int = Field(4, ge=0, le=10)
    # Глубина рассуждения: low|medium|high|xhigh|max.
    effort: str = "high"
    # Веб-поиск: включён по умолчанию (агент ищет ТТХ в интернете).
    web_search: bool = True
    # Тип server-tool веб-поиска. Для Opus 5/4.8 — _20260209;
    # для моделей старше 4.6 нужен базовый web_search_20250305.
    web_search_tool_type: str = "web_search_20260209"
    # Потолок числа веб-поисков за один запрос (защита от разрастания).
    max_web_searches: int = Field(8, ge=1, le=30)
    # Веб-фетч полных страниц (скилл берёт цифры/подписи из fetch, не сниппетов).
    web_fetch: bool = True
    web_fetch_tool_type: str = "web_fetch_20260209"
    max_web_fetches: int = Field(6, ge=1, le=30)
    # Кэшировать большой системный промпт скилла (экономия на пачке авто).
    cache_system: bool = True
    # Потолок выходных токенов ответа (спека — это документ).
    max_tokens: int = Field(16000, ge=1024, le=64000)


class UniquifierConfig(BaseModel):
    """Настройки уникализатора фото (C4)."""

    model_config = {"extra": "forbid"}

    # none — не уникализировать (pass-through); pixflow — сервис pixflow.ru.
    provider: str = "none"
    base_url: str = "https://pixflow.ru/api/v1"
    request_timeout_seconds: int = Field(120, ge=5, le=600)
    max_retries: int = Field(4, ge=0, le=10)


class PhotosConfig(BaseModel):
    """Настройки фото-стадии: хостинг (C3) и уникализация (C4)."""

    model_config = {"extra": "forbid"}

    host: str = "imgbb"                       # ключ реализации PhotoHost
    endpoint: str = "https://api.imgbb.com/1/upload"
    request_timeout_seconds: int = Field(60, ge=5, le=300)
    max_retries: int = Field(4, ge=0, le=10)
    # Автоудаление залитых картинок через N секунд (60–15552000).
    # None — не удалять (imgbb хранит бессрочно).
    expiration_seconds: int | None = Field(None, ge=60, le=15552000)
    # Уникализация (C4). По умолчанию выключена (pass-through).
    uniquifier: UniquifierConfig = Field(default_factory=UniquifierConfig)


class AppConfig(BaseModel):
    """Корневой конфиг приложения."""

    model_config = {"extra": "forbid"}

    spreadsheet_id: str = Field(..., min_length=1)
    request_timeout_seconds: int = Field(30, ge=1, le=300)
    max_retries: int = Field(4, ge=1, le=10)
    platforms: list[PlatformConfig] = Field(..., min_length=1)
    # Стадии обогащения (Фаза 1+). Опциональны: базовая сборка фидов
    # работает и без них.
    ingest: IngestConfig | None = None
    agent: AgentConfig | None = None
    photos: PhotosConfig | None = None

    @field_validator("platforms")
    @classmethod
    def _unique_names_and_outputs(cls, v: list[PlatformConfig]):
        names = [p.name for p in v]
        if len(names) != len(set(names)):
            raise ValueError("Имена площадок (name) должны быть уникальны")
        outputs = [p.output for p in v]
        if len(outputs) != len(set(outputs)):
            raise ValueError("Имена файлов фидов (output) должны быть уникальны")
        return v

    def enabled_platforms(self) -> list[PlatformConfig]:
        return [p for p in self.platforms if p.enabled]


def load_config(path: str | os.PathLike[str]) -> AppConfig:
    """Читает YAML и валидирует его в AppConfig.

    Любая проблема (нет файла, битый YAML, неверная схема) превращается
    в ConfigError с человекочитаемым сообщением.
    """
    p = Path(path)
    if not p.is_file():
        raise ConfigError(
            f"Конфиг не найден: {p}. Скопируйте config/config.example.yaml "
            f"в config/config.yaml и заполните значения."
        )

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Не удалось разобрать YAML {p}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Ожидался объект в корне конфига {p}, получено: {type(raw).__name__}")

    try:
        return AppConfig(**raw)
    except ValidationError as exc:
        raise ConfigError(f"Конфиг {p} не прошёл валидацию:\n{exc}") from exc


def require_env(name: str) -> str:
    """Возвращает значение обязательной переменной окружения или падает.

    Используется для секретов (например, GOOGLE_SA_JSON): секрет никогда
    не хранится в коде или конфиге — только в окружении / GitHub Secrets (R1).
    """
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        raise ConfigError(
            f"Не задана переменная окружения {name}. "
            f"Для локального запуска: export {name}=\"$(cat sa-key.json)\". "
            f"В CI — GitHub Secrets."
        )
    return value
