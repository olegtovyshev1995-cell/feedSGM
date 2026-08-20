"""Проверка опубликованных фидов по их публичным URL (post-publish healthcheck).

Площадки (Avito, Auto.ru, Drom) забирают фид сами по ссылке. Этот скрипт
эмулирует их обращение: скачивает каждый фид по публичному URL, проверяет
HTTP 200, well-formed XML и что в фиде есть объявления. Ненулевой код
выхода — если хоть один фид недоступен или битый.

Зависимостей нет (только стандартная библиотека). Базовый URL берётся из
--base-url, либо из env FEEDS_BASE_URL, либо собирается из переменных
GitHub Actions (GITHUB_REPOSITORY + GITHUB_REF_NAME).
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from xml.dom.minidom import parseString

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Корневой тег «объявления» для каждого формата — по нему считаем количество.
ITEM_TAGS = {"avito": "Ad", "autoru": "car", "drom": "Offer"}


def resolve_base_url(explicit: str | None) -> str:
    """Определяет базовый URL каталога feeds/ (без завершающего слэша)."""
    if explicit:
        return explicit.rstrip("/")
    env = os.environ.get("FEEDS_BASE_URL")
    if env:
        return env.rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY")   # owner/repo
    ref = os.environ.get("GITHUB_REF_NAME")      # имя ветки
    if repo and ref:
        return f"https://raw.githubusercontent.com/{repo}/{ref}/feeds"
    raise SystemExit(
        "Не удалось определить базовый URL фидов. Передайте --base-url "
        "или задайте FEEDS_BASE_URL "
        "(например, https://raw.githubusercontent.com/<owner>/<repo>/<branch>/feeds)."
    )


def feed_url(base_url: str, filename: str) -> str:
    """Собирает полный URL фида из базы и имени файла."""
    return f"{base_url.rstrip('/')}/{filename}"


def load_platforms(config_path: str) -> list[dict]:
    """Читает из конфига список включённых площадок (name + output)."""
    p = Path(config_path)
    if not p.is_file():
        # Фолбэк на шаблон: структура площадок в нём та же.
        p = PROJECT_ROOT / "config" / "config.example.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    platforms = []
    for item in data.get("platforms", []):
        if item.get("enabled", True):
            platforms.append({"name": item["name"], "output": item["output"]})
    return platforms


def check_one(name: str, url: str, timeout: int) -> tuple[bool, str]:
    """Скачивает и валидирует один фид. Возвращает (ok, сообщение)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "feedSGM-healthcheck"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            body = resp.read()
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code} по {url}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return False, f"недоступен {url}: {exc}"

    if status != 200:
        return False, f"HTTP {status} по {url}"

    try:
        dom = parseString(body)
    except Exception as exc:  # noqa: BLE001 — любой парс-сбой = битый фид
        return False, f"невалидный XML по {url}: {exc}"

    item_tag = ITEM_TAGS.get(name)
    count = len(dom.getElementsByTagName(item_tag)) if item_tag else -1
    if item_tag and count == 0:
        return False, f"фид пустой (нет <{item_tag}>) по {url}"

    size_kb = len(body) / 1024
    return True, f"OK {url} — объявлений: {count}, размер: {size_kb:.1f} КБ"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Healthcheck опубликованных фидов")
    parser.add_argument("--base-url", default=None, help="База URL каталога feeds/")
    parser.add_argument(
        "--config",
        default=os.environ.get("CONFIG_PATH", str(PROJECT_ROOT / "config" / "config.yaml")),
    )
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args(argv)

    base = resolve_base_url(args.base_url)
    platforms = load_platforms(args.config)
    if not platforms:
        print("Нет включённых площадок в конфиге", file=sys.stderr)
        return 1

    failures = 0
    for pf in platforms:
        url = feed_url(base, pf["output"])
        ok, msg = check_one(pf["name"], url, args.timeout)
        prefix = "✓" if ok else "✗"
        print(f"[{pf['name']}] {prefix} {msg}", file=sys.stderr)
        if not ok:
            failures += 1

    if failures:
        print(f"Проверка провалена: {failures} из {len(platforms)} фидов", file=sys.stderr)
        return 1
    print(f"Все фиды доступны и валидны ({len(platforms)})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
