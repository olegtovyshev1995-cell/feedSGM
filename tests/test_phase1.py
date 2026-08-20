"""Тесты Фазы 1: ingest → state → enrich_spec. Без сети (SpecResearcher — заглушка)."""

from __future__ import annotations

from src.config import ColumnMapping, load_config
from src.pipeline.enrich_spec import enrich_specs
from src.pipeline.ingest import IngestError, normalize_rows
from src.pipeline.models import CarInput, PhotoRef, ResearchResult, sanitize_vin
from src.pipeline.state import StateStore

import pytest


# ── CarInput ───────────────────────────────────────────────────────
def test_vin_sanitized_and_hash_stable():
    c1 = CarInput(vin=" mpb amfe60sx697372 ", make="Ford", model="Ranger")
    assert c1.vin == "MPBAMFE60SX697372"
    c2 = CarInput(vin="MPBAMFE60SX697372", make="Ford", model="Ranger")
    assert c1.content_hash() == c2.content_hash()  # стабилен


def test_hash_changes_on_modification():
    a = CarInput(vin="V1", make="Ford", model="Ranger", modification="2.0 AT")
    b = CarInput(vin="V1", make="Ford", model="Ranger", modification="2.0 MT")
    assert a.content_hash() != b.content_hash()


def test_empty_vin_rejected():
    with pytest.raises(Exception):
        CarInput(vin="   ", make="Ford", model="Ranger")


# ── normalize_rows ─────────────────────────────────────────────────
_CM = ColumnMapping(vin="VIN", make="Make", model="Model", modification="Mod")


def test_normalize_ok_and_dedup():
    h = ["VIN", "Make", "Model", "Mod"]
    rows = [(2, {"VIN": "v1", "Make": "Ford", "Model": "Ranger", "Mod": "2.0"})]
    cars = normalize_rows(h, rows, _CM)
    assert cars[0].vin == "V1" and cars[0].row_number == 2

    dup = rows + [(3, {"VIN": "v1", "Make": "Ford", "Model": "Ranger", "Mod": "2.0"})]
    with pytest.raises(IngestError) as e:
        normalize_rows(h, dup, _CM)
    assert "дубль" in e.value.problems[0]


def test_normalize_missing_column():
    with pytest.raises(IngestError):
        normalize_rows(["VIN", "Make"], [(2, {"VIN": "v", "Make": "F"})], _CM)


# ── StateStore ─────────────────────────────────────────────────────
def test_state_freshness(tmp_path):
    store = StateStore(tmp_path / "state")
    artifact = tmp_path / "specs" / "V1.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("x", encoding="utf-8")

    assert store.is_fresh("V1", "spec", "h1") is False          # ещё нет
    store.mark("V1", "spec", "h1", path=str(artifact))
    assert store.is_fresh("V1", "spec", "h1") is True           # отмечено
    assert store.is_fresh("V1", "spec", "h2") is False          # хэш изменился

    artifact.unlink()
    assert store.is_fresh("V1", "spec", "h1") is False          # артефакт исчез


# ── enrich_specs (кэш/идемпотентность/устойчивость) ────────────────
class StubResearcher:
    """Заглушка CardResearcher: считает вызовы, может падать на заданных VIN."""

    def __init__(self, fail_on: set[str] | None = None):
        self.calls = []
        self.fail_on = fail_on or set()

    def research(self, car: CarInput) -> ResearchResult:
        self.calls.append(car.vin)
        if car.vin in self.fail_on:
            raise RuntimeError("boom")
        return ResearchResult(
            spec_markdown=f"# {car.title()}\n- Двигатель: тест [Факт]\n",
            photos=[PhotoRef(image_url="https://s/1.jpg", status="фото-факт")],
        )


def _cars():
    return [
        CarInput(vin="V1", make="Ford", model="Ranger", modification="2.0"),
        CarInput(vin="V2", make="Kia", model="Rio", modification="1.6"),
    ]


def test_enrich_writes_and_caches(tmp_path):
    store = StateStore(tmp_path / "state")
    specs, photos = tmp_path / "specs", tmp_path / "photos"
    r = StubResearcher()

    res1 = enrich_specs(_cars(), r, store, specs, photos)
    assert all(x.ok and not x.cached for x in res1)
    assert (specs / "V1.md").read_text(encoding="utf-8").startswith("# Ford Ranger")
    assert (photos / "V1" / "found.json").is_file()          # фото сохранены
    assert res1[0].photos_count == 1
    assert r.calls == ["V1", "V2"]

    # Повторный прогон: вход не менялся → LLM не вызывается.
    res2 = enrich_specs(_cars(), r, store, specs, photos)
    assert all(x.cached for x in res2)
    assert res2[0].photos_count == 1                         # счётчик из кэша
    assert r.calls == ["V1", "V2"]  # без новых вызовов


def test_enrich_force_recomputes(tmp_path):
    store = StateStore(tmp_path / "state")
    specs, photos = tmp_path / "specs", tmp_path / "photos"
    r = StubResearcher()
    enrich_specs(_cars(), r, store, specs, photos)
    enrich_specs(_cars(), r, store, specs, photos, force=True)
    assert r.calls == ["V1", "V2", "V1", "V2"]  # пересчитано


def test_enrich_one_failure_does_not_stop_others(tmp_path):
    store = StateStore(tmp_path / "state")
    specs, photos = tmp_path / "specs", tmp_path / "photos"
    r = StubResearcher(fail_on={"V1"})
    res = enrich_specs(_cars(), r, store, specs, photos)
    by_vin = {x.vin: x for x in res}
    assert by_vin["V1"].ok is False and by_vin["V1"].error
    assert by_vin["V2"].ok is True
    assert not (specs / "V1.md").exists()      # упавший файл не создан
    assert (specs / "V2.md").exists()
    # Провалившийся VIN не помечен свежим — пересоберётся на следующем прогоне.
    assert store.is_fresh("V1", "spec", _cars()[0].content_hash()) is False


# ── Конфиг с ingest+agent ──────────────────────────────────────────
def test_example_config_has_ingest_and_agent():
    cfg = load_config("config/config.example.yaml")
    assert cfg.ingest is not None and cfg.ingest.columns.vin == "VIN"
    assert cfg.agent is not None and cfg.agent.model == "claude-opus-5"


def test_sanitize_vin():
    assert sanitize_vin(" ab-12_x! ") == "AB-12_X"
