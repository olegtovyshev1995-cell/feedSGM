#!/usr/bin/env python3
"""Детерминированная конвертация единиц и расчёт производных величин.
Использование:
  python3 convert.py in 210.9          -> мм
  python3 convert.py lb 5363           -> кг
  python3 convert.py gal 20.3          -> л
  python3 convert.py lbft 430          -> Нм
  python3 convert.py cuft 43.5         -> л
  python3 convert.py kw 292            -> л.с. (PS)
  python3 convert.py ps 397            -> кВт
  python3 convert.py mpg 16            -> л/100 км
  python3 convert.py mph 112           -> км/ч
  python3 convert.py tire 285/70R17    -> диаметр мм и дюймы
  python3 convert.py payload 3130 2475 -> грузоподъёмность = полная - снаряжённая
Вывод — только цифры с единицами, округление по правилам скилла.
"""
import sys, re

def main(a):
    if len(a) < 2:
        print(__doc__); return
    k = a[0].lower(); v = a[1:]
    f = lambda i=0: float(v[i].replace(',', '.'))
    if k == 'in':      print(f"{f()*25.4:.0f} мм")
    elif k == 'ft':    print(f"{f()*0.3048:.2f} м")
    elif k == 'lb':    print(f"{f()*0.45359237:.0f} кг")
    elif k == 'gal':   print(f"{f()*3.785411784:.1f} л")
    elif k == 'lbft':  print(f"{f()*1.3558179:.0f} Нм")
    elif k == 'cuft':  print(f"{f()*28.316846592:.0f} л")
    elif k == 'kw':    print(f"{f()*1.35962:.0f} л.с. (PS)")
    elif k == 'ps':    print(f"{f()*0.73549875:.0f} кВт")
    elif k == 'hp':    print(f"{f()*1.01387:.0f} PS (из SAE hp; обычно указывать как есть с пометкой SAE)")
    elif k == 'mpg':   print(f"{235.214583/f():.1f} л/100 км")
    elif k == 'mph':   print(f"{f()*1.609344:.0f} км/ч")
    elif k == 'psi':   print(f"{f()*0.0689476:.2f} бар")
    elif k == 'tire':
        m = re.match(r'(\d{3})/(\d{2})\s*[Rr]?\s*(\d{2})', v[0])
        if not m: print('формат: 285/70R17'); return
        w, ar, rim = map(int, m.groups())
        d = rim*25.4 + 2*w*ar/100
        print(f"диаметр {d:.0f} мм = {d/25.4:.1f}\"; ширина {w} мм")
    elif k == 'payload':
        gvm, kerb = f(0), f(1)
        print(f"грузоподъёмность {gvm-kerb:.0f} кг (полная {gvm:.0f} − снаряжённая {kerb:.0f})")
    else:
        print(__doc__)

if __name__ == '__main__':
    main(sys.argv[1:])
