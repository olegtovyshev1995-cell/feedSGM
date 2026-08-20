"""Базовый интерфейс мапперов и общие XML-утилиты.

Каждая площадка — отдельный класс-наследник BaseMapper. Оркестратор
работает только через этот интерфейс и ничего не знает о конкретной схеме.
Новая площадка = новый файл-маппер + строка в реестре MAPPERS.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from src.config import PlatformConfig


def _build_illegal_xml_regex() -> "re.Pattern[str]":
    """Компилирует регулярку недопустимых в XML 1.0 символов.

    Разрешены: #x9, #xA, #xD и диапазоны #x20–#xD7FF, #xE000–#xFFFD,
    #x10000–#x10FFFF. Всё остальное (управляющие символы) вырезаем — иначе
    фид не распарсится у площадки. Диапазон строим через chr(), чтобы не
    держать в исходнике «сырые» управляющие/суррогатные символы.
    """
    allowed = (
        chr(0x09) + chr(0x0A) + chr(0x0D)
        + chr(0x20) + "-" + chr(0xD7FF)
        + chr(0xE000) + "-" + chr(0xFFFD)
        + chr(0x10000) + "-" + chr(0x10FFFF)
    )
    return re.compile("[^" + allowed + "]")


_ILLEGAL_XML_CHARS = _build_illegal_xml_regex()


def xml_escape(value: object) -> str:
    """Экранирует текст для вставки между тегами.

    Обязательно экранируем & < > (R2): незаэкранированный амперсанд
    отклоняет весь фид. Кавычки в текстовом содержимом экранировать не
    обязательно, но для единообразия тоже кодируем.
    """
    text = "" if value is None else str(value)
    text = _ILLEGAL_XML_CHARS.sub("", text)
    # Порядок важен: сначала &, иначе повторно закодируем уже вставленные &.
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    return text


def xml_attr_escape(value: object) -> str:
    """Экранирует значение для атрибута (используется в url=\"...\")."""
    return xml_escape(value)


def cdata(value: object) -> str:
    """Оборачивает текст в CDATA, сохраняя HTML-разметку внутри.

    Нужно для описаний, где площадка ждёт HTML-теги (<p>, <br>, <strong>)
    как разметку, а не как экранированный текст. Последовательность ']]>'
    внутри значения безопасно разрывается, чтобы не закрыть секцию раньше.
    """
    text = "" if value is None else str(value)
    text = _ILLEGAL_XML_CHARS.sub("", text)
    text = text.replace("]]>", "]]]]><![CDATA[>")
    return "<![CDATA[" + text + "]]>"


def split_list(value: str, sep: str) -> list[str]:
    """Разбивает мультизначное поле (например, список фото) по разделителю."""
    if not value:
        return []
    return [item.strip() for item in value.split(sep) if item.strip()]


class BaseMapper(ABC):
    """Общий контракт маппера: набор dict-строк → строка XML-фида."""

    #: человекочитаемый ключ площадки, совпадает с name в конфиге и MAPPERS
    platform: str = "base"

    def __init__(self, config: PlatformConfig) -> None:
        self.config = config

    @abstractmethod
    def build_xml(self, records: list[dict[str, str]]) -> str:
        """Собирает готовый XML-фид из уже провалидированных строк.

        На вход приходят только валидные записи (обязательные поля
        заполнены). Реализация отвечает за экранирование значений.
        """
        raise NotImplementedError
