"""Адаптер C3: хостинг фото → публичные ссылки.

Интерфейс PhotoHost отделяет пайплайн от конкретного сервиса. Реализация
ImgbbPhotoHost работает по API v1 imgbb (POST /1/upload). Ключ — только из
env (R1). HTTP изолирован через инъектируемый poster, поэтому логику можно
тестировать без сети и без установленного requests.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

logger = logging.getLogger("photo_host")

# Коды, которые имеет смысл ретраить (временный сбой/лимит). 4xx (кроме 429)
# не ретраим — это ошибка запроса.
_RETRYABLE = {429, 500, 502, 503, 504}


class PhotoHostError(RuntimeError):
    """Понятная ошибка загрузки фото в хостинг."""


@dataclass
class HostedImage:
    """Результат заливки одной картинки."""

    url: str                 # прямая ссылка на изображение
    delete_url: str | None   # ссылка удаления (для очистки)
    id: str | None
    raw: dict                # полный ответ сервиса (на всякий случай)


# poster(endpoint, data) -> (status_code, json_body_dict)
Poster = Callable[[str, dict], "tuple[int, dict]"]


class PhotoHost(Protocol):
    """Контракт хостинга: даём картинку (URL или байты) → получаем ссылку."""

    def upload_url(self, image_url: str, name: str | None = None) -> HostedImage: ...
    def upload_bytes(self, data: bytes, name: str | None = None) -> HostedImage: ...


class ImgbbPhotoHost:
    """Хостинг фото через imgbb API v1."""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = "https://api.imgbb.com/1/upload",
        timeout_seconds: int = 60,
        max_retries: int = 4,
        expiration_seconds: int | None = None,
        poster: Poster | None = None,
    ) -> None:
        if not api_key:
            raise PhotoHostError("Пустой API-ключ imgbb (ожидается env IMGBB_API_KEY)")
        self._key = api_key
        self._endpoint = endpoint
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._expiration = expiration_seconds
        self._poster = poster or self._requests_poster

    # ── публичный интерфейс ──────────────────────────────────────────
    def upload_url(self, image_url: str, name: str | None = None) -> HostedImage:
        """Заливает картинку по её URL (imgbb сам скачает и перезальёт)."""
        if not image_url:
            raise PhotoHostError("Пустой URL картинки")
        return self._upload(image_url, name)

    def upload_bytes(self, data: bytes, name: str | None = None) -> HostedImage:
        """Заливает картинку из байтов (base64)."""
        if not data:
            raise PhotoHostError("Пустые данные картинки")
        encoded = base64.b64encode(data).decode("ascii")
        return self._upload(encoded, name)

    def upload_file(self, path: str | Path, name: str | None = None) -> HostedImage:
        p = Path(path)
        return self.upload_bytes(p.read_bytes(), name or p.name)

    # ── внутреннее ───────────────────────────────────────────────────
    def _upload(self, image_value: str, name: str | None) -> HostedImage:
        # imgbb принимает key/expiration в query, но передаём всё формой —
        # безопаснее для длинного base64 (см. примечание в доках API).
        data = {"key": self._key, "image": image_value}
        if self._expiration is not None:
            data["expiration"] = str(self._expiration)
        if name:
            data["name"] = name

        body = self._request_with_retry(data)
        return self._parse(body)

    def _request_with_retry(self, data: dict) -> dict:
        last: str | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                status, body = self._poster(self._endpoint, data)
            except Exception as exc:  # сетевые сбои транспорта
                if attempt < self._max_retries:
                    delay = 2 ** attempt
                    logger.warning("imgbb: сетевой сбой (%d/%d), повтор через %dс: %s",
                                   attempt, self._max_retries, delay, exc)
                    time.sleep(delay)
                    last = str(exc)
                    continue
                raise PhotoHostError(f"imgbb недоступен после {self._max_retries} попыток: {exc}") from exc

            if status == 200 and body.get("success"):
                return body

            # Разбор ошибки imgbb: {"error":{"message":...}} или status_txt.
            msg = ""
            err = body.get("error")
            if isinstance(err, dict):
                msg = err.get("message", "")
            msg = msg or body.get("status_txt", "") or f"HTTP {status}"

            if status in _RETRYABLE and attempt < self._max_retries:
                delay = 2 ** attempt
                logger.warning("imgbb: %s (%d/%d), повтор через %dс",
                               msg, attempt, self._max_retries, delay)
                time.sleep(delay)
                last = msg
                continue

            raise PhotoHostError(f"imgbb отклонил загрузку (HTTP {status}): {msg}")

        raise PhotoHostError(f"imgbb: исчерпаны попытки: {last}")

    @staticmethod
    def _parse(body: dict) -> HostedImage:
        data = body.get("data") or {}
        # Прямая ссылка: url → display_url → image.url (устойчиво к вариациям).
        url = data.get("url") or data.get("display_url")
        if not url:
            image = data.get("image")
            if isinstance(image, dict):
                url = image.get("url")
        if not url:
            raise PhotoHostError(f"imgbb: в ответе нет ссылки на картинку: {data!r}")
        return HostedImage(
            url=url,
            delete_url=data.get("delete_url"),
            id=data.get("id"),
            raw=body,
        )

    def _requests_poster(self, endpoint: str, data: dict) -> "tuple[int, dict]":
        """Реальный HTTP-транспорт (ленивый импорт requests)."""
        import requests

        resp = requests.post(endpoint, data=data, timeout=self._timeout)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        return resp.status_code, body


def build_photo_host(photos_cfg) -> PhotoHost:
    """Собирает реализацию PhotoHost по конфигу. Ключ — только из env (R1)."""
    from src.config import require_env

    host_name = getattr(photos_cfg, "host", "imgbb")
    if host_name != "imgbb":
        raise PhotoHostError(
            f"Неизвестный фото-хостинг {host_name!r}. Поддерживается: imgbb."
        )
    api_key = require_env("IMGBB_API_KEY")
    return ImgbbPhotoHost(
        api_key,
        endpoint=photos_cfg.endpoint,
        timeout_seconds=photos_cfg.request_timeout_seconds,
        max_retries=photos_cfg.max_retries,
        expiration_seconds=photos_cfg.expiration_seconds,
    )
