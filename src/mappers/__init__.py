"""Реестр мапперов площадок.

Добавить площадку = создать файл-маппер (наследник BaseMapper) и
дописать одну строку в MAPPERS. Оркестратор (build.py) при этом не меняется.
"""

from __future__ import annotations

from src.mappers.autoru import AutoRuMapper
from src.mappers.avito import AvitoMapper
from src.mappers.base import BaseMapper
from src.mappers.drom import DromMapper

# Ключ = name площадки в конфиге.
MAPPERS: dict[str, type[BaseMapper]] = {
    AvitoMapper.platform: AvitoMapper,
    AutoRuMapper.platform: AutoRuMapper,
    DromMapper.platform: DromMapper,
    # Новая площадка (например, ЦИАН) добавляется здесь одной строкой.
}

__all__ = ["MAPPERS", "BaseMapper"]
