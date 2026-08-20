"""Канонические доменные модели пайплайна.

CarInput — «сквозная карточка авто» на входе: минимум данных, по которым
агент добывает спецификацию и фото. Ключ идентификации — VIN.
"""

from __future__ import annotations

import hashlib
import json
import re

from pydantic import BaseModel, Field, field_validator


def sanitize_vin(vin: str) -> str:
    """Приводит VIN к безопасному для имени файла виду (латиница/цифры)."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", vin.strip())
    return cleaned.upper()


class CarInput(BaseModel):
    """Нормализованный вход по одному автомобилю."""

    model_config = {"extra": "forbid"}

    vin: str = Field(..., min_length=1)
    make: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    modification: str = ""
    year: str = ""
    price: str = ""
    city: str = ""
    # Номер исходной строки в таблице — для человекочитаемых ошибок.
    row_number: int = 0

    @field_validator("vin")
    @classmethod
    def _vin_sane(cls, v: str) -> str:
        s = sanitize_vin(v)
        if not s:
            raise ValueError("VIN пуст после очистки (нужны латиница/цифры)")
        return s

    @property
    def key(self) -> str:
        """Стабильный ключ идемпотентности = очищенный VIN."""
        return self.vin

    def content_hash(self) -> str:
        """Хэш входных полей: меняется — стадию нужно пересчитать."""
        payload = {
            "vin": self.vin,
            "make": self.make,
            "model": self.model,
            "modification": self.modification,
            "year": self.year,
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def title(self) -> str:
        """Человекочитаемое название модификации для промпта/логов."""
        parts = [self.make, self.model, self.modification, self.year]
        return " ".join(p for p in parts if p).strip()
