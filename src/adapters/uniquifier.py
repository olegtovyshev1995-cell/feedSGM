"""Адаптер C4: уникализация фото → новая ссылка.

Интерфейс Uniquifier отделяет пайплайн от конкретного сервиса. Пока
контракт pixflow.ru не заведён, по умолчанию работает NoOpUniquifier
(pass-through: возвращает исходную ссылку). Реальная реализация
(PixflowUniquifier) добавляется в этот же файл за тем же интерфейсом.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger("uniquifier")


class UniquifierError(RuntimeError):
    """Понятная ошибка уникализации."""


@dataclass
class UniquifiedImage:
    """Результат уникализации одной картинки."""

    url: str          # ссылка на уникализированное изображение
    raw: dict         # полный ответ сервиса (или {} для no-op)


class Uniquifier(Protocol):
    """Контракт: ссылка/байты картинки → уникализированная ссылка."""

    def uniquify_url(self, image_url: str, name: str | None = None) -> UniquifiedImage: ...


class NoOpUniquifier:
    """Заглушка: не меняет картинку, возвращает исходную ссылку.

    Используется, пока не подключён реальный сервис (provider: none),
    чтобы фото-конвейер работал end-to-end (C1→C3), а C4 включался
    переключением конфига без изменений оркестратора.
    """

    def uniquify_url(self, image_url: str, name: str | None = None) -> UniquifiedImage:
        return UniquifiedImage(url=image_url, raw={})


def build_uniquifier(cfg) -> Uniquifier:
    """Собирает реализацию Uniquifier по конфигу. Ключ — только из env (R1)."""
    provider = getattr(cfg, "provider", "none")
    if provider == "none":
        return NoOpUniquifier()
    if provider == "pixflow":
        # Контракт pixflow.ru ещё не заведён (endpoint/авторизация/формат
        # ответа). До этого — явная ошибка, а не выдуманный запрос.
        raise UniquifierError(
            "provider=pixflow выбран, но адаптер PixflowUniquifier ещё не "
            "реализован: нужен контракт API (endpoint, авторизация, "
            "запрос→ответ). Пока используйте provider: none."
        )
    raise UniquifierError(f"Неизвестный уникализатор {provider!r} (none|pixflow)")
