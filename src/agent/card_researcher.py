"""Стадия B+C1: спецификация + подбор фото через скилл car-spec-researcher.

CardResearcher — интерфейс (DI). ClaudeCardResearcher запускает скилл как
промпт через Claude API (web_search + web_fetch) и разбирает ответ на
спеку (Markdown) и список подтверждённых фото (из JSON-контракта).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from src.agent.claude_client import ClaudeClient
from src.agent.skill_prompt import (
    PHOTOS_JSON_TAG,
    build_system_prompt,
    build_user_prompt,
)
from src.pipeline.models import CarInput, PhotoRef, ResearchResult

logger = logging.getLogger("card")

# Прямая ссылка на файл изображения (для imgbb): http(s) + расширение картинки,
# допускаем query-строку после расширения.
_IMAGE_URL_RE = re.compile(
    r"^https?://\S+\.(?:jpg|jpeg|png|webp|gif)(?:\?\S*)?$", re.IGNORECASE
)

# Любой fenced-блок ```json ... ``` (метка после json допускается).
_JSON_FENCE_RE = re.compile(r"```json[^\n]*\n(.*?)```", re.DOTALL | re.IGNORECASE)


class CardResearcher(Protocol):
    """Контракт: CarInput → ResearchResult (спека + фото)."""

    def research(self, car: CarInput) -> ResearchResult: ...


def _is_direct_image(url: str) -> bool:
    return bool(_IMAGE_URL_RE.match(url.strip()))


def parse_photos(text: str) -> list[PhotoRef]:
    """Извлекает подтверждённые фото из последнего JSON-блока ответа.

    Устойчиво: берёт последний ```json``` блок, парсит {"photos":[...]},
    оставляет только записи с ПРЯМОЙ ссылкой на изображение.
    """
    matches = _JSON_FENCE_RE.findall(text)
    if not matches:
        logger.warning("в ответе нет JSON-блока с фото — считаю, что фото нет")
        return []

    raw = matches[-1].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("JSON-блок фото невалиден (%s) — пропускаю", exc)
        return []

    items = data.get("photos", []) if isinstance(data, dict) else []
    photos: list[PhotoRef] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        url = str(it.get("image_url", "")).strip()
        if not url or not _is_direct_image(url):
            # Непрямые ссылки (страницы-источники) imgbb не перезальёт — пропуск.
            logger.info("пропускаю фото без прямой ссылки на файл: %r", url[:80])
            continue
        photos.append(
            PhotoRef(
                image_url=url,
                source_url=str(it.get("source_url", "")).strip(),
                angle=str(it.get("angle", "")).strip(),
                proof=str(it.get("proof", "")).strip(),
                color=str(it.get("color", "")).strip(),
                status=str(it.get("status", "")).strip(),
            )
        )
    return photos


def strip_photos_block(text: str) -> str:
    """Убирает финальный JSON-блок фото из текста спецификации."""
    # Удаляем помеченный блок (или последний json-блок), чтобы .md был чистым.
    tagged = re.compile(
        r"```json\s*" + re.escape(PHOTOS_JSON_TAG) + r".*?```",
        re.DOTALL | re.IGNORECASE,
    )
    if tagged.search(text):
        return tagged.sub("", text).rstrip() + "\n"
    # Фолбэк: срезаем последний json-блок, если он в самом хвосте.
    matches = list(_JSON_FENCE_RE.finditer(text))
    if matches and matches[-1].end() >= len(text.rstrip()) - 3:
        m = matches[-1]
        return (text[: m.start()]).rstrip() + "\n"
    return text


class ClaudeCardResearcher:
    """Реализация CardResearcher на Claude API + скилл car-spec-researcher."""

    def __init__(self, client: ClaudeClient) -> None:
        self._client = client
        self._system = build_system_prompt()  # стабилен → кэшируется в клиенте

    def research(self, car: CarInput) -> ResearchResult:
        user = build_user_prompt(
            make=car.make,
            model=car.model,
            modification=car.modification,
            year=car.year,
            vin=car.vin,
        )
        logger.info("[%s] спека+фото: %s", car.vin, car.title())
        answer = self._client.complete(system=self._system, user=user)

        photos = parse_photos(answer)
        spec_md = strip_photos_block(answer).strip() + "\n"
        logger.info("[%s] получено фото с прямой ссылкой: %d", car.vin, len(photos))
        return ResearchResult(spec_markdown=spec_md, photos=photos)
