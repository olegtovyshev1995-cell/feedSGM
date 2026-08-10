"""Валидация данных ДО записи фида.

Падаем на битых данных, пока фид ещё не тронут: проверяем, что в листе
есть все обязательные колонки (R10) и что в обязательных ячейках нет
пустот (R2), а Id уникальны и заданы (R6). Ошибка содержит точные номера
строк — как в таблице, с учётом заголовка.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RowIssue:
    """Проблема в конкретной строке таблицы."""

    row_number: int  # номер строки как в Google-таблице (1-based)
    problems: list[str] = field(default_factory=list)


class ValidationFailure(Exception):
    """Данные листа не прошли валидацию.

    Собирает и отсутствующие колонки, и построчные проблемы, чтобы
    пользователь увидел сразу весь список, а не по одной ошибке за прогон.
    """

    def __init__(
        self,
        platform: str,
        missing_columns: list[str],
        row_issues: list[RowIssue],
    ) -> None:
        self.platform = platform
        self.missing_columns = missing_columns
        self.row_issues = row_issues
        super().__init__(self._render())

    def _render(self) -> str:
        lines = [f"[{self.platform}] данные не прошли валидацию:"]
        if self.missing_columns:
            lines.append(
                "  Отсутствуют обязательные колонки: "
                + ", ".join(self.missing_columns)
            )
        for issue in self.row_issues:
            lines.append(
                f"  Строка {issue.row_number}: " + "; ".join(issue.problems)
            )
        return "\n".join(lines)


def validate_rows(
    platform: str,
    headers: list[str],
    rows: list[tuple[int, dict[str, str]]],
    required: list[str],
    id_column: str,
) -> None:
    """Проверяет колонки и данные. Бросает ValidationFailure при проблемах.

    Порядок проверок:
      1. Наличие обязательных колонок и id_column в заголовке (R10).
         Если колонок нет — дальше построчно проверять нечего, падаем сразу.
      2. Построчно: обязательные поля непустые (R2), Id задан и уникален (R6).
    """
    # ── 1. Обязательные колонки существуют ───────────────────────────
    header_set = set(headers)
    needed = list(dict.fromkeys([*required, id_column]))  # уникально, с порядком
    missing = [c for c in needed if c not in header_set]
    if missing:
        # Без нужных колонок номера строк бессмысленны — падаем на схеме.
        raise ValidationFailure(platform, missing_columns=missing, row_issues=[])

    if not rows:
        raise ValidationFailure(
            platform,
            missing_columns=[],
            row_issues=[RowIssue(0, ["в листе нет ни одной строки с данными"])],
        )

    # ── 2. Построчная проверка ───────────────────────────────────────
    issues: list[RowIssue] = []
    seen_ids: dict[str, int] = {}

    for row_number, record in rows:
        problems: list[str] = []

        for col in required:
            if not record.get(col, "").strip():
                problems.append(f"пустое обязательное поле {col!r}")

        id_value = record.get(id_column, "").strip()
        if not id_value:
            problems.append(f"пустой {id_column!r} (нужен стабильный Id объявления)")
        elif id_value in seen_ids:
            problems.append(
                f"дубль {id_column}={id_value!r} "
                f"(впервые в строке {seen_ids[id_value]})"
            )
        else:
            seen_ids[id_value] = row_number

        if problems:
            issues.append(RowIssue(row_number, problems))

    if issues:
        raise ValidationFailure(platform, missing_columns=[], row_issues=issues)
