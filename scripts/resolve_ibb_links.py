#!/usr/bin/env python3
"""Превращает страницы imgbb (ibb.co/XXXX) в прямые ссылки на файл.

Дром грузит фото по схеме «одно фото — одна ссылка» и принимает только
файлы .jpg/.jpeg. Ссылка вида https://ibb.co/C5988sKL — это HTML-страница
просмотра, по ней фотография не загрузится. Прямая выглядит так:
https://i.ibb.co/<хеш>/<имя>.jpg — её imgbb показывает как «Прямая ссылка».

Скрипт открывает каждую страницу и достаёт прямую ссылку из og:image.
Порядок ссылок сохраняется — он же станет порядком фотографий в объявлении.

    python -m scripts.resolve_ibb_links data/cards/ibb_links.txt
    python -m scripts.resolve_ibb_links data/cards/ibb_links.txt \
        --update-card data/cards/ford-ranger-raptor-2026.yaml

Запускать там, где есть доступ к ibb.co (ваша машина или CI) — из среды
разработки этот домен закрыт прокси.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

_USER_AGENT = "Mozilla/5.0 (compatible; feedSGM/1.0)"

# og:image у imgbb указывает ровно на прямую ссылку файла.
_OG_IMAGE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
# Запасной вариант: любая прямая ссылка i.ibb.co на .jpg/.png в разметке.
_DIRECT = re.compile(r'https://i\.ibb\.co/[A-Za-z0-9._/-]+\.(?:jpe?g|png|webp)', re.IGNORECASE)


def extract_direct_url(html: str) -> str | None:
    """Достаёт прямую ссылку на файл из HTML страницы imgbb."""
    match = _OG_IMAGE.search(html)
    if match and "i.ibb.co" in match.group(1):
        return match.group(1)
    fallback = _DIRECT.search(html)
    return fallback.group(0) if fallback else None


def read_links(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        line.strip() for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]


def fetch(url: str, timeout: int) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def update_card(card_path: Path, urls: list[str]) -> None:
    """Подменяет значение Photos в YAML-карточке, не трогая остальное."""
    text = card_path.read_text(encoding="utf-8")
    joined = "|".join(urls)
    new_text, count = re.subn(
        r'^(\s*Photos:\s*).*$',
        lambda m: f'{m.group(1)}"{joined}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if not count:
        raise SystemExit(f"{card_path}: не нашёл строку 'Photos:' для замены")
    card_path.write_text(new_text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("links", help="файл со ссылками ibb.co, по одной в строке")
    parser.add_argument("--update-card", help="YAML-карточка: подставить в поле Photos")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args(argv)

    links = read_links(Path(args.links))
    if not links:
        raise SystemExit(f"{args.links}: нет ссылок")

    resolved: list[str] = []
    failed: list[str] = []
    for i, link in enumerate(links, 1):
        if "i.ibb.co" in link:
            resolved.append(link)          # уже прямая
            continue
        try:
            direct = extract_direct_url(fetch(link, args.timeout))
        except Exception as exc:  # noqa: BLE001 — сеть, показываем как есть
            print(f"[{i}/{len(links)}] {link} — ошибка: {exc}", file=sys.stderr)
            failed.append(link)
            continue
        if direct:
            resolved.append(direct)
            print(f"[{i}/{len(links)}] {direct}", file=sys.stderr)
        else:
            print(f"[{i}/{len(links)}] {link} — прямая ссылка не найдена", file=sys.stderr)
            failed.append(link)

    if failed:
        print(f"\nне разрешено ссылок: {len(failed)}", file=sys.stderr)

    if args.update_card:
        if failed:
            raise SystemExit("карточка не обновлена: часть ссылок не разрешилась")
        update_card(Path(args.update_card), resolved)
        print(f"{args.update_card}: подставлено {len(resolved)} ссылок")
    else:
        print("|".join(resolved))

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
