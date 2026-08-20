"""Тесты парсинга фото и сборки промпта скилла (без сети)."""

from __future__ import annotations

from src.agent.card_researcher import (
    ClaudeCardResearcher,
    parse_photos,
    strip_photos_block,
)
from src.agent.skill_prompt import (
    PHOTOS_JSON_TAG,
    build_system_prompt,
    build_user_prompt,
)
from src.config import AgentConfig
from src.pipeline.models import CarInput


_ANSWER = """# Ford Ranger Raptor — 2026

## 1. Общее
| Параметр | Значение | Статус |
| Мощность | 397 л.с. | [Факт] S1 |

## 14. Фотографии (реестр)
| P-n | ... |

```json PHOTOS_JSON
{"photos": [
  {"image_url": "https://media.ford.com/raptor/front.jpg", "source_url": "https://media.ford.com/x", "angle": "3/4 перед", "proof": "подпись 2026 GCC", "color": "серый", "status": "фото-факт"},
  {"image_url": "https://media.ford.com/raptor/page", "source_url": "", "angle": "борт", "proof": "", "color": "", "status": "фото-факт"}
]}
```
"""


def test_parse_photos_keeps_only_direct_image_links():
    photos = parse_photos(_ANSWER)
    assert len(photos) == 1                        # вторая — не прямая ссылка
    assert photos[0].image_url.endswith("front.jpg")
    assert photos[0].status == "фото-факт"


def test_parse_photos_empty_when_no_block():
    assert parse_photos("нет json тут") == []


def test_parse_photos_tolerates_broken_json():
    bad = "```json PHOTOS_JSON\n{not valid}\n```"
    assert parse_photos(bad) == []


def test_parse_photos_empty_list():
    assert parse_photos('```json PHOTOS_JSON\n{"photos": []}\n```') == []


def test_strip_photos_block_removes_trailing_json():
    md = strip_photos_block(_ANSWER)
    assert "```json" not in md
    assert md.startswith("# Ford Ranger Raptor")
    assert "## 14" in md


def test_build_system_prompt_includes_skill_and_contract():
    sysp = build_system_prompt()
    assert "Car Spec Researcher" in sysp                 # SKILL.md
    assert "photo-matching.md" in sysp                   # подключён reference
    assert PHOTOS_JSON_TAG in sysp                       # контракт вывода
    assert "Режим запуска: API" in sysp                  # API-режим


def test_build_user_prompt_has_car_fields():
    up = build_user_prompt("Ford", "Ranger Raptor", "3.0 V6", "2026", "VIN123")
    assert "Ford" in up and "Ranger Raptor" in up and "VIN123" in up


# ── Интеграция ресёрчера с подставным клиентом ─────────────────────
class FakeClient:
    def __init__(self, answer):
        self.answer = answer
        self.system = None

    def complete(self, system, user):
        self.system = system
        return self.answer


def test_researcher_splits_spec_and_photos():
    fc = FakeClient(_ANSWER)
    r = ClaudeCardResearcher(fc)
    car = CarInput(vin="V1", make="Ford", model="Ranger Raptor", modification="3.0", year="2026")
    res = r.research(car)
    assert res.spec_markdown.startswith("# Ford Ranger Raptor")
    assert "```json" not in res.spec_markdown
    assert len(res.photos) == 1
    # системный промпт содержит скилл (значит собран из бандла)
    assert "Car Spec Researcher" in fc.system
