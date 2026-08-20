#!/usr/bin/env python3
"""Проверка текста объявления: длина, заголовок, плотность ключа, запрещённые слова, блоки.
Использование:
  python3 check_ad.py <файл_с_текстом.txt> --title "Заголовок" --key "Ford Ranger Raptor"
Выводит таблицу: параметр | значение | норма | ok/!!
"""
import sys, re, argparse

BAD = ["уникальн","роскошн","лучш","топов","культов","флагманск","легендарн","максимальн",
       "срочно","выгодная цена","мечта","без предоплаты","эксклюзивн","премиальн","идеальн","#"]
BLOCKS = ["ЗА ЧТО УКАЗАНА СТОИМОСТЬ","ЧТО ВЫ ПОЛУЧАЕТЕ","ПОЧЕМУ НАМ ДОВЕРЯЮТ",
          "ПОЧЕМУ ЗАКАЗАТЬ СЕЙЧАС","ПОЗВОНИТЕ","В ИЗБРАННОЕ"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file"); ap.add_argument("--title", default="")
    ap.add_argument("--key", default=""); ap.add_argument("--min", type=int, default=4300)
    ap.add_argument("--max", type=int, default=4600)
    a = ap.parse_args()
    t = open(a.file, encoding="utf-8").read()
    n = len(t)
    rows = []
    rows.append(("Длина описания", n, f"{a.min}–{a.max}", a.min <= n <= a.max))
    if a.title:
        rows.append(("Длина заголовка", len(a.title), "≤ 50", len(a.title) <= 50))
    if a.key:
        words = re.findall(r"\w+", t.lower())
        k = t.lower().count(a.key.lower())
        dens = 100 * k * len(a.key.split()) / max(1, len(words))
        rows.append((f"Ключ «{a.key}»: вхождений", k, "3–6", 3 <= k <= 6))
        rows.append(("Плотность ключа, %", round(dens, 2), "≤ 0.5", dens <= 0.5))
    found = [b for b in BAD if b in t.lower()]
    rows.append(("Запрещённые слова", ", ".join(found) or "нет", "нет", not found))
    missing = [b for b in BLOCKS if b not in t.upper()]
    rows.append(("Блоки на месте", "все" if not missing else "нет: " + "; ".join(missing), "6/6", not missing))
    rows.append(("Телефон в тексте", "есть" if re.search(r"\+?\d[\d\s\-()]{9,}\d", t) else "нет", "нет",
                 not re.search(r"\+?\d[\d\s\-()]{9,}\d", t)))
    rows.append(("Ссылки в тексте", "есть" if re.search(r"https?://|www\.", t) else "нет", "нет",
                 not re.search(r"https?://|www\.", t)))
    rows.append(("Плейсхолдеров [УТОЧНИТЬ]", t.count("[УТОЧНИТЬ"), "показать пользователю", True))
    lines = [l for l in t.splitlines() if l.strip()]
    longl = sum(1 for l in lines if len(l) > 60)
    rows.append(("Строк длиннее 60 симв.", longl, "минимум", longl < 15))
    print("| Параметр | Значение | Норма | Статус |\n|---|---|---|---|")
    for p, v, nrm, ok in rows:
        print(f"| {p} | {v} | {nrm} | {'ok' if ok else '!!'} |")

if __name__ == "__main__":
    main()
