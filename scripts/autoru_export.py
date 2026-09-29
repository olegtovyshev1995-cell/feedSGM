"""Выгрузка всех объявлений кабинета Auto.ru через API (apiauto.ru/1.0).

Схема работы с API Auto.ru для дилеров:

1. Каждый запрос несёт заголовок ``x-authorization: Vertis <API-ключ>``.
   Ключ выдаёт Auto.ru (менеджер/поддержка) — это доступ к API, а не пароль.
2. Сессия пользователя кабинета — заголовок ``x-session-id``. Её можно
   передать готовой (AUTORU_SESSION_ID) или получить скриптом:
   ``POST /auth/login {"login", "password"}`` → ``session.id``.
3. Список объявлений — ``GET /user/offers/{category}?page=N&page_size=M``,
   ответ: ``offers[]`` + ``pagination.total_page_count``. Скрипт идёт по
   страницам, пока не заберёт все.

Результат:
- ``<out>/autoru_offers_<category>_<дата>.json`` — сырые объявления целиком
  (все поля, как отдал API);
- ``<out>/autoru_offers_<category>_<дата>.csv`` — плоская таблица ключевых
  полей для Excel/Google Sheets;
- ``<out>/autoru_offers_<category>_latest.{json,csv}`` — копия последней
  выгрузки под стабильным именем (для следующих стадий на сервере).

Секреты — только из окружения (R1):
  AUTORU_API_KEY                  — обязательно (можно с префиксом «Vertis »);
  AUTORU_SESSION_ID               — либо он,
  AUTORU_LOGIN + AUTORU_PASSWORD  — либо логин/пароль кабинета.
Любую переменную можно задать файлом: AUTORU_PASSWORD_FILE=/путь и т.п.

Пример:
  export AUTORU_API_KEY=...  AUTORU_LOGIN=...  AUTORU_PASSWORD=...
  python scripts/autoru_export.py --category all
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("autoru_export")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "https://apiauto.ru/1.0"
CATEGORIES = ("all", "cars", "moto", "trucks")

# Временные сбои/лимит — ретраим с backoff. Прочие 4xx — ошибка запроса.
_RETRYABLE = {429, 500, 502, 503, 504}

# requester(method, url, headers, params, json_body) -> (status_code, json_body_dict)
Requester = Callable[[str, str, dict, "dict | None", "dict | None"], "tuple[int, dict]"]


class AutoruApiError(RuntimeError):
    """Понятная ошибка API Auto.ru."""


def _requests_requester(timeout_seconds: int) -> Requester:
    """Реальный HTTP через requests (импорт ленивый — тесты обходятся без него)."""
    import requests

    def call(method: str, url: str, headers: dict, params: dict | None, body: dict | None):
        resp = requests.request(
            method, url, headers=headers, params=params, json=body, timeout=timeout_seconds
        )
        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text[:500]}
        return resp.status_code, data

    return call


class AutoruClient:
    """Минимальный клиент API Auto.ru: логин + постраничный список объявлений."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        session_id: str | None = None,
        requester: Requester | None = None,
        timeout_seconds: int = 30,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise AutoruApiError("Не задан API-ключ Auto.ru (AUTORU_API_KEY).")
        # Ключ принимаем и «голым», и в виде «Vertis <ключ>» (как его выдаёт кабинет).
        api_key = api_key.strip()
        if api_key.lower().startswith("vertis "):
            api_key = api_key[len("vertis "):].strip()
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self._request = requester or _requests_requester(timeout_seconds)
        self.max_retries = max_retries
        self._sleep = sleep

    def _headers(self) -> dict:
        headers = {
            "x-authorization": f"Vertis {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.session_id:
            headers["x-session-id"] = self.session_id
        return headers

    def _call(self, method: str, path: str, *, params: dict | None = None,
              body: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        delay = 2.0
        for attempt in range(1, self.max_retries + 1):
            status, data = self._request(method, url, self._headers(), params, body)
            if status == 200:
                return data
            if status in _RETRYABLE and attempt < self.max_retries:
                logger.warning("%s %s → HTTP %s, повтор через %.0fс", method, path, status, delay)
                self._sleep(delay)
                delay *= 2
                continue
            raise AutoruApiError(self._explain(status, path, data))
        raise AutoruApiError(f"{method} {path}: исчерпаны попытки")  # pragma: no cover

    @staticmethod
    def _explain(status: int, path: str, data: dict) -> str:
        detail = data.get("detailed_error") or data.get("error") or data
        if status == 401:
            hint = "проверьте AUTORU_API_KEY и сессию/логин-пароль"
        elif status == 403:
            hint = "у ключа или пользователя нет доступа к этому методу"
        else:
            hint = "см. ответ API"
        return f"Auto.ru API {path}: HTTP {status} ({hint}): {detail}"

    def login(self, login: str, password: str) -> str:
        """POST /auth/login → сохраняет и возвращает session.id."""
        data = self._call("POST", "/auth/login", body={"login": login, "password": password})
        session_id = (data.get("session") or {}).get("id")
        if not session_id:
            raise AutoruApiError(f"/auth/login не вернул session.id: {data}")
        self.session_id = session_id
        return session_id

    def iter_offers(self, category: str = "all", *, page_size: int = 100,
                    status: str | None = None, max_pages: int = 10_000):
        """Генератор всех объявлений категории, страница за страницей."""
        if not self.session_id:
            raise AutoruApiError("Нет сессии: задайте AUTORU_SESSION_ID или логин/пароль.")
        page = 1
        while page <= max_pages:
            params: dict[str, Any] = {"page": page, "page_size": page_size}
            if status:
                params["status"] = status
            data = self._call("GET", f"/user/offers/{category}", params=params)
            offers = data.get("offers") or []
            pagination = data.get("pagination") or {}
            total_pages = int(pagination.get("total_page_count") or 0)
            logger.info("страница %s/%s: %s объявл.", page, total_pages or "?", len(offers))
            yield from offers
            # Стоп: пустая страница или дошли до последней по pagination.
            if not offers or (total_pages and page >= total_pages):
                return
            page += 1


def dig(obj: Any, dotted: str) -> Any:
    """Безопасно достаёт вложенное поле по пути 'a.b.c'."""
    for key in dotted.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _ms_to_iso(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


# Колонка CSV → путь в объекте объявления (или функция от него).
CSV_COLUMNS: dict[str, str | Callable[[dict], Any]] = {
    "id": "id",
    "category": "category",
    "section": "section",
    "status": "status",
    "mark": "car_info.mark",
    "model": "car_info.model",
    "generation": "car_info.super_gen.name",
    "modification": "car_info.tech_param.human_name",
    "body_type": "car_info.body_type",
    "engine_type": "car_info.engine_type",
    "transmission": "car_info.transmission",
    "drive": "car_info.drive",
    "year": "documents.year",
    "vin": "documents.vin",
    "mileage": "state.mileage",
    "color_hex": "color_hex",
    "price": lambda o: dig(o, "price_info.price") or dig(o, "price_info.RUR"),
    "currency": "price_info.currency",
    "availability": "availability",
    "created": lambda o: _ms_to_iso(dig(o, "additional_info.creation_date")),
    "updated": lambda o: _ms_to_iso(dig(o, "additional_info.update_date")),
    "photos": lambda o: len(dig(o, "state.image_urls") or []),
    "description": "description",
}


def offer_to_row(offer: dict) -> dict:
    row = {}
    for column, source in CSV_COLUMNS.items():
        value = source(offer) if callable(source) else dig(offer, source)
        row[column] = "" if value is None else value
    return row


def write_outputs(offers: list[dict], out_dir: Path, category: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    json_path = out_dir / f"autoru_offers_{category}_{stamp}.json"
    csv_path = out_dir / f"autoru_offers_{category}_{stamp}.csv"
    json_path.write_text(json.dumps(offers, ensure_ascii=False, indent=2), encoding="utf-8")
    # utf-8-sig — чтобы Excel сразу открыл кириллицу без кракозябр.
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS), delimiter=";")
        writer.writeheader()
        for offer in offers:
            writer.writerow(offer_to_row(offer))
    # Стабильные имена «последней выгрузки» — их читают следующие стадии на сервере.
    for src in (json_path, csv_path):
        latest = out_dir / f"autoru_offers_{category}_latest{src.suffix}"
        tmp = latest.with_suffix(latest.suffix + ".tmp")
        tmp.write_bytes(src.read_bytes())
        os.replace(tmp, latest)
    return json_path, csv_path


def _env_or_file(name: str) -> str | None:
    """Значение из env NAME или из файла по пути NAME_FILE (секреты на сервере)."""
    value = os.environ.get(name)
    if value:
        return value.strip()
    path = os.environ.get(f"{name}_FILE")
    if path and Path(path).is_file():
        return Path(path).read_text(encoding="utf-8").strip() or None
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Выгрузка всех объявлений Auto.ru через API")
    parser.add_argument("--category", choices=CATEGORIES, default="all")
    parser.add_argument("--status", default=None,
                        help="фильтр по статусу (напр. ACTIVE, INACTIVE); по умолчанию — все")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--out", default=str(PROJECT_ROOT / "exports"))
    parser.add_argument("--base-url", default=os.environ.get("AUTORU_API_URL", DEFAULT_BASE_URL))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        client = AutoruClient(
            _env_or_file("AUTORU_API_KEY") or "",
            base_url=args.base_url,
            session_id=_env_or_file("AUTORU_SESSION_ID"),
        )
        if not client.session_id:
            login = _env_or_file("AUTORU_LOGIN")
            password = _env_or_file("AUTORU_PASSWORD")
            if not (login and password):
                raise AutoruApiError(
                    "Задайте AUTORU_SESSION_ID или пару AUTORU_LOGIN/AUTORU_PASSWORD."
                )
            client.login(login, password)
        offers = list(client.iter_offers(args.category, page_size=args.page_size,
                                         status=args.status))
    except AutoruApiError as exc:
        logger.error("%s", exc)
        return 1

    json_path, csv_path = write_outputs(offers, Path(args.out), args.category)
    logger.info("выгружено объявлений: %s", len(offers))
    print(json_path)
    print(csv_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
