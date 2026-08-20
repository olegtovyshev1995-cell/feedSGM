"""Стадия B: получение заводской спецификации по CarInput.

Интерфейс SpecResearcher отделён от реализации на Claude — оркестратор и
тесты зависят от интерфейса (DI), а не от SDK. Промпт живёт отдельным
файлом, чтобы его правил не-программист.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from src.agent.claude_client import ClaudeClient
from src.pipeline.models import CarInput

logger = logging.getLogger("spec")

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_SYSTEM = (
    "Ты собираешь только проверенные заводские характеристики автомобилей "
    "по официальным источникам. Не выдумывай числа. Отвечай строго в "
    "запрошенном формате Markdown, без преамбулы."
)


class SpecResearcher(Protocol):
    """Контракт поставщика спецификации: CarInput → Markdown-спека."""

    def research(self, car: CarInput) -> str: ...


def load_prompt(name: str) -> str:
    """Читает шаблон промпта из agent/prompts/<name>."""
    path = _PROMPTS_DIR / name
    return path.read_text(encoding="utf-8")


class ClaudeSpecResearcher:
    """Реализация SpecResearcher на Claude API + веб-поиск."""

    def __init__(self, client: ClaudeClient, prompt_name: str = "spec_research.md") -> None:
        self._client = client
        self._template = load_prompt(prompt_name)

    def research(self, car: CarInput) -> str:
        prompt = self._template.format(
            make=car.make,
            model=car.model,
            modification=car.modification or "(не указана)",
            year=car.year or "(не указан)",
            vin=car.vin,
        )
        logger.info("[%s] запрос спецификации: %s", car.vin, car.title())
        markdown = self._client.complete(system=_SYSTEM, user=prompt)
        return markdown.strip() + "\n"
