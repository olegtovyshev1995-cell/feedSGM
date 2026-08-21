# Раздача фидов со своего сервера (только веб, без пайплайна)

Для случая, когда фиды собираются локально (из карточек `data/cards/*` через
`scripts.build_from_card`) и лежат в репозитории, а сервер нужен лишь чтобы
отдавать их по постоянной ссылке для Drom и Auto.ru.

Поднимается один контейнер **Caddy**: раздаёт `feeds/*.xml` и сам выпускает
HTTPS-сертификат. Пайплайн, ключи Google/Claude/imgbb — не нужны.

## Что нужно один раз

- Сервер с Linux и sudo.
- Открытые порты **80** и **443**.
- Желательно домен с A-записью на IP сервера (для HTTPS). Можно и без него —
  тогда раздача по HTTP на IP.

## Шаги

```bash
# 1. Docker (если ещё нет)
curl -fsSL https://get.docker.com | sh

# 2. Код на сервер
git clone https://github.com/olegtovyshev1995-cell/feedSGM.git
cd feedSGM
git checkout claude/product-feed-moscow-region-4skxut   # ветка с фидами

# 3. Настройка домена
cd deploy/serve-only
cp .env.example .env
nano .env            # DOMAIN=feed.ваш-домен.ру   (или DOMAIN=:80 без домена)

# 4. Запуск
docker compose up -d

# 5. Проверка
curl -I https://feed.ваш-домен.ру/feeds/autoru.xml    # ожидаем 200
```

Ссылки для площадок:

```
https://<DOMAIN>/feeds/autoru.xml
https://<DOMAIN>/feeds/drom.xml
```

## Обновить фиды позже

Пересобрали карточку и запушили новые `feeds/*.xml` — на сервере:

```bash
cd feedSGM && bash deploy/serve-only/update.sh
```

Caddy отдаёт файлы с диска, перезапуск не нужен.
