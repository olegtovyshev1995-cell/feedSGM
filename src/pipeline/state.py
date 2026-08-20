"""Состояние и идемпотентность пайплайна (по VIN).

Каждый прогон агент вызывает Claude API и внешние фото-API — это деньги.
Стор хранит, какие стадии для VIN уже пройдены и с каким хэшем входа, чтобы
не пересчитывать неизменившееся. Хранилище — по JSON-файлу на VIN.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.pipeline.models import sanitize_vin

logger = logging.getLogger("state")


class StateStore:
    """Файловый стор статусов стадий, ключ — VIN."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, vin: str) -> Path:
        return self.base_dir / f"{sanitize_vin(vin)}.json"

    def get(self, vin: str) -> dict:
        """Возвращает запись состояния VIN (пустой dict, если нет)."""
        p = self._path(vin)
        if not p.is_file():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # Битый файл состояния не должен ронять прогон — считаем «нет данных»
            # и пересчитаем стадию заново.
            logger.warning("битый файл состояния %s: %s — игнорирую", p, exc)
            return {}

    def is_fresh(self, vin: str, stage: str, content_hash: str) -> bool:
        """True, если стадия для VIN пройдена и хэш входа совпадает.

        Дополнительно проверяет, что артефакт (файл), на который ссылается
        стадия, ещё существует — иначе считаем стадию непройденной.
        """
        record = self.get(vin)
        entry = record.get("stages", {}).get(stage)
        if not entry or entry.get("hash") != content_hash:
            return False
        artifact = entry.get("path")
        if artifact and not Path(artifact).is_file():
            return False
        return True

    def mark(
        self,
        vin: str,
        stage: str,
        content_hash: str,
        path: str | None = None,
        extra: dict | None = None,
    ) -> None:
        """Отмечает стадию пройденной для VIN (атомарная запись)."""
        record = self.get(vin)
        record.setdefault("vin", sanitize_vin(vin))
        stages = record.setdefault("stages", {})
        entry: dict = {
            "hash": content_hash,
            "done_at": datetime.now(timezone.utc).isoformat(),
        }
        if path is not None:
            entry["path"] = str(path)
        if extra:
            entry.update(extra)
        stages[stage] = entry
        self._write(vin, record)

    def _write(self, vin: str, record: dict) -> None:
        p = self._path(vin)
        tmp = p.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            tmp.replace(p)  # атомарный rename
        except Exception:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise
