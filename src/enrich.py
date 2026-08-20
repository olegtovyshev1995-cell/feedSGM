"""Entrypoint Фазы 1: вход из Sheets → спецификации (Claude API).

Тонкий оркестратор: конфиг → ingest → enrich_spec. Структурные логи в
stderr, ненулевой код выхода при ошибке (чтобы CI покраснел).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from src.config import AppConfig, ConfigError, load_config, require_env

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SPECS_DIR = DATA_DIR / "specs"
STATE_DIR = DATA_DIR / "state"

logger = logging.getLogger("enrich")


def _setup_logging() -> None:
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def run(config_path: str, force: bool = False) -> int:
    cfg: AppConfig = load_config(config_path)
    if cfg.ingest is None or cfg.agent is None:
        raise ConfigError(
            "Для обогащения нужны секции ingest и agent в конфиге "
            "(см. config.example.yaml)."
        )

    # Секреты только из окружения (R1).
    sa_raw = require_env("GOOGLE_SA_JSON")
    try:
        sa_info = json.loads(sa_raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"GOOGLE_SA_JSON не является корректным JSON: {exc}") from exc

    # Ленивые импорты: держим entrypoint импортируемым без тяжёлых зависимостей.
    from src.agent.claude_client import ClaudeClient
    from src.agent.spec_researcher import ClaudeSpecResearcher
    from src.pipeline.enrich_spec import enrich_specs
    from src.pipeline.ingest import read_car_inputs
    from src.pipeline.state import StateStore

    cars = read_car_inputs(cfg, sa_info)
    if not cars:
        logger.warning("на входе нет авто — нечего обогащать")
        return 0

    client = ClaudeClient(cfg.agent)  # ANTHROPIC_API_KEY проверится здесь
    researcher = ClaudeSpecResearcher(client)
    store = StateStore(STATE_DIR)

    results = enrich_specs(cars, researcher, store, SPECS_DIR, force=force)

    failed = [r.vin for r in results if not r.ok]
    if failed:
        logger.error("спецификация не собрана у: %s", ", ".join(failed))
        return 1
    logger.info("готово: спецификации собраны для %d авто", len(results))
    return 0


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(description="Фаза 1: обогащение спецификациями")
    parser.add_argument(
        "--config",
        default=os.environ.get("CONFIG_PATH", str(PROJECT_ROOT / "config" / "config.yaml")),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Пересобрать спеки даже при актуальном кэше",
    )
    args = parser.parse_args(argv)
    try:
        return run(args.config, force=args.force)
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
