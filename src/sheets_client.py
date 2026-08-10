"""Чтение листа Google Sheets: таймаут, ретраи с backoff, обработка 403/404.

Сетевой слой изолирован здесь. Наверх отдаём уже «сырые» строки листа
(list[list[str]]) — бизнес-логика (валидация, маппинг) о Google не знает.
"""

from __future__ import annotations

import logging
import time

# Импорты google-библиотек делаются лениво внутри методов SheetsClient.
# Это позволяет импортировать модуль (и чистую функцию rows_to_dicts,
# и обработку ошибок) без установленного google-стека — например, в тестах
# маппинга/валидации, которые к сети не ходят.

logger = logging.getLogger("sheets")

# Read-only: сервисному аккаунту достаточно роли Viewer к таблице (R1).
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Коды, которые имеет смысл ретраить: временный сбой Google / превышение
# квоты. 403/404 сюда НЕ входят — это ошибки доступа/адреса, ретрай не
# поможет и только затянет job (R4).
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class SheetsAccessError(RuntimeError):
    """Понятная ошибка доступа к таблице (403/404) для вывода пользователю."""


class SheetsClient:
    """Тонкая обёртка над Sheets API v4 с ретраями и таймаутом."""

    def __init__(
        self,
        sa_info: dict,
        timeout_seconds: int,
        max_retries: int,
    ) -> None:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build as build_service

        self._timeout = timeout_seconds
        self._max_retries = max_retries
        creds = service_account.Credentials.from_service_account_info(
            sa_info, scopes=SCOPES
        )
        # cache_discovery=False — не тащим файловый кэш discovery в CI.
        self._service = build_service(
            "sheets", "v4", credentials=creds, cache_discovery=False
        )

    def read_values(self, spreadsheet_id: str, sheet_name: str) -> list[list[str]]:
        """Возвращает все значения листа как список строк (список ячеек-строк).

        Пустые хвостовые ячейки Google не присылает — строки могут быть
        разной длины; выравнивание делает вызывающая сторона.
        """
        # Диапазон = всё содержимое листа. Имя листа в кавычках на случай
        # пробелов/кириллицы (например, "Фид (легковые)").
        rng = f"'{sheet_name}'"
        request = (
            self._service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=rng)
        )

        result = self._execute_with_retry(request, spreadsheet_id, sheet_name)
        values = result.get("values", [])
        logger.info("прочитано строк с листа %r: %d", sheet_name, len(values))
        return values

    def _execute_with_retry(self, request, spreadsheet_id: str, sheet_name: str):
        """Выполняет запрос с таймаутом и экспоненциальным backoff.

        Backoff: 2 → 4 → 8с (для max_retries=4 это 3 паузы между 4 попытками).
        Обоснование чисел: короткие всплески квоты Google Sheets обычно
        рассасываются за единицы секунд; удвоение даёт суммарно ~14с
        ожидания при разумном числе попыток.
        """
        from googleapiclient.errors import HttpError

        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                # num_retries=0: ретраи полностью под нашим контролем ниже.
                return request.execute(num_retries=0)
            except HttpError as exc:
                status = getattr(exc.resp, "status", None)
                status = int(status) if status is not None else None

                # 403/404 — сразу человекочитаемая подсказка, без ретраев (R3).
                if status == 403:
                    raise SheetsAccessError(
                        f"403 Forbidden к таблице {spreadsheet_id!r}, лист "
                        f"{sheet_name!r}. Выдайте сервисному аккаунту доступ "
                        f"Viewer к этой Google-таблице."
                    ) from exc
                if status == 404:
                    raise SheetsAccessError(
                        f"404 Not Found: таблица {spreadsheet_id!r} или лист "
                        f"{sheet_name!r} не найдены. Проверьте spreadsheet_id "
                        f"и точное имя листа в конфиге."
                    ) from exc

                if status in RETRYABLE_STATUS and attempt < self._max_retries:
                    delay = 2 ** attempt  # 2, 4, 8, ...
                    logger.warning(
                        "Google API вернул %s (попытка %d/%d), повтор через %dс",
                        status, attempt, self._max_retries, delay,
                    )
                    last_exc = exc
                    time.sleep(delay)
                    continue

                # Непонятный/неретраибельный код или исчерпаны попытки.
                raise RuntimeError(
                    f"Ошибка Google Sheets API (HTTP {status}) для листа "
                    f"{sheet_name!r}: {exc}"
                ) from exc

            except TimeoutError as exc:
                # Таймаут сокета — ретраибелен наравне с 5xx.
                if attempt < self._max_retries:
                    delay = 2 ** attempt
                    logger.warning(
                        "Таймаут запроса к Google (попытка %d/%d), повтор через %dс",
                        attempt, self._max_retries, delay,
                    )
                    last_exc = exc
                    time.sleep(delay)
                    continue
                raise RuntimeError(
                    f"Таймаут запроса к Google Sheets для листа {sheet_name!r} "
                    f"после {self._max_retries} попыток"
                ) from exc

        # Досюда доходим только если все попытки исчерпаны на ретраибельной ошибке.
        raise RuntimeError(
            f"Не удалось прочитать лист {sheet_name!r} после "
            f"{self._max_retries} попыток: {last_exc}"
        ) from last_exc


def rows_to_dicts(
    values: list[list[str]],
    header_row: int,
    data_start_row: int,
) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    """Превращает сырые значения листа в заголовок + пронумерованные строки.

    Возвращает:
      headers      — список имён колонок из header_row;
      rows         — список пар (номер_строки_в_таблице, {колонка: значение}).
                     Полностью пустые строки пропускаются.
    Номер строки — как в самой таблице (1-based), чтобы ошибки валидатора
    указывали на реальную строку с учётом заголовка (R2).
    """
    if len(values) < header_row:
        return [], []

    headers = [str(c).strip() for c in values[header_row - 1]]

    rows: list[tuple[int, dict[str, str]]] = []
    for idx in range(data_start_row - 1, len(values)):
        raw = values[idx]
        # Выравниваем длину строки под заголовок (Google обрезает пустой хвост).
        cells = [str(c) for c in raw] + [""] * (len(headers) - len(raw))
        record = {h: cells[i].strip() for i, h in enumerate(headers) if h}
        if not any(v for v in record.values()):
            continue  # пустая строка-разделитель — пропускаем
        table_row_number = idx + 1  # 1-based номер строки в таблице
        rows.append((table_row_number, record))

    return headers, rows
