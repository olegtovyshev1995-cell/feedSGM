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


class AppConfig(BaseModel):
    """Корневой конфиг приложения."""

    model_config = {"extra": "forbid"}

    spreadsheet_id: str = Field(..., min_length=1)
    request_timeout_seconds: int = Field(30, ge=1, le=300)
    max_retries: int = Field(4, ge=1, le=10)
    platforms: list[PlatformConfig] = Field(..., min_length=1)

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
