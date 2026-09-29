# Развёртывание на своём сервере

Пакет разворачивает генератор фидов на вашем VPS через Docker Compose:
пайплайн крутится по расписанию, а Caddy раздаёт фиды по HTTPS.

```
Ваш сервер
├── scheduler (Docker)  — раз в сутки: autoru_export → enrich → host_photos → build
└── caddy (Docker)      — https://<домен>/feeds/*.xml  (авто-сертификат)
```

Секреты и реальный конфиг живут ТОЛЬКО на сервере (в git их нет) — это
серверный аналог GitHub Secrets.

---

## 0. Что нужно один раз

- Сервер с Linux и root/sudo-доступом (SSH или веб-консоль панели).
- Домен, A-запись которого направлена на IP сервера (для HTTPS).
- Открытые порты **80** и **443**.
- Ключи: `ANTHROPIC_API_KEY`, `IMGBB_API_KEY`, (позже) `PIXFLOW_API_KEY`,
  и JSON-ключ сервисного аккаунта Google.

## 1. Установить Docker (если ещё нет)

```bash
curl -fsSL https://get.docker.com | sh
```

Проверка: `docker --version && docker compose version`.

## 2. Получить код на сервер

```bash
git clone https://github.com/olegtovyshev1995-cell/feedSGM.git
cd feedSGM/deploy
```

(Обновление в будущем — `git pull` в папке проекта и `docker compose up -d --build`.)

## 3. Заполнить конфиг и секреты

```bash
# конфиг пайплайна (площадки, ingest, agent, photos)
cp ../config/config.example.yaml config.yaml
nano config.yaml           # впишите spreadsheet_id и имена листов

# переменные окружения (домен, ключи)
cp .env.example .env
nano .env                  # DOMAIN, ANTHROPIC_API_KEY, IMGBB_API_KEY, ...

# ключ сервисного аккаунта Google — файлом
mkdir -p secrets
nano secrets/google-sa.json   # вставьте весь JSON-ключ
```

Не забудьте выдать сервисному аккаунту **Viewer** к вашей Google-таблице.

## 4. Запустить

```bash
docker compose up -d --build
docker compose logs -f scheduler     # видно прогон стадий
```

При старте пайплайн прогоняется сразу, дальше — каждый день в `RUN_AT`.

## 5. Проверить фиды

```bash
curl -I https://<ВАШ_ДОМЕН>/feeds/avito.xml
```

Ссылки для площадок:
- `https://<домен>/feeds/avito.xml`
- `https://<домен>/feeds/autoru.xml`
- `https://<домен>/feeds/drom.xml`
- `https://<домен>/feeds/drom_autoru.xml` — Drom из активных объявлений Auto.ru

---

## Выгрузка объявлений Auto.ru по API

В `.env` заполните `AUTORU_API_KEY` (ключ из кабинета, можно с `Vertis `)
и `AUTORU_LOGIN` + `AUTORU_PASSWORD` (или `AUTORU_SESSION_ID`). Если ключ
задан, каждый прогон начинается со стадии `autoru_export`: все объявления
кабинета сохраняются в том `exports`:

- `autoru_offers_all_<дата>.json|csv` — снимок каждой выгрузки;
- `autoru_offers_all_latest.json|csv` — последняя выгрузка (стабильное имя);
- `autoru_offers_all_inactive_latest.json|csv` — только неактивные
  (отключить: `AUTORU_EXPORT_INACTIVE=0`).

Наружу через Caddy выгрузки **не** раздаются (это данные кабинета).

### Фид Drom из объявлений Auto.ru

Следом за выгрузкой стадия `autoru_to_drom` собирает из **активных легковых**
объявлений Auto.ru фид Drom: `https://<домен>/feeds/drom_autoru.xml` — эту
ссылку отдают Drom (`client@drom.ru` или кабинет автозагрузки). Коды Auto.ru
переводятся в текстовые поля Drom (`sMark`, `sTransmission`, `Color` …), фото —
ссылки Auto.ru максимального размера. Объявления без обязательных полей
Drom (марка, модель, город, год, VIN, цена) пропускаются, список — в логе.
Если в объявлениях нет города — задайте `DROM_CITY` в `.env`.

```bash
docker compose run --rm scheduler python -m src.autoru_to_drom
```

```bash
# выгрузить прямо сейчас
docker compose run --rm scheduler python scripts/autoru_export.py --category all
# только неактивные
docker compose run --rm scheduler python scripts/autoru_export.py --inactive
# скопировать CSV на хост
docker compose cp scheduler:/app/exports/autoru_offers_all_latest.csv .
```

## Полезные команды

| Действие | Команда |
|---|---|
| Разовый прогон только спеки | `docker compose run --rm scheduler python -m src.enrich` |
| Разовый прогон фото | `docker compose run --rm scheduler python -m src.host_photos` |
| Пересобрать фиды сейчас | `docker compose run --rm scheduler python -m src.build` |
| Выгрузить объявления Auto.ru | `docker compose run --rm scheduler python scripts/autoru_export.py` |
| Логи | `docker compose logs -f scheduler` |
| Обновить код | `git pull && docker compose up -d --build` |
| Остановить | `docker compose down` |
| Бэкап кэша/фидов | тома `feeds`, `data` (см. `docker volume ls`) |

## Безопасность

- `.env`, `config.yaml`, `secrets/` — на сервере, вне git (заблокированы `.gitignore`).
- Контейнер работает под непривилегированным пользователем.
- Секреты не пишутся в образ (передаются через env/тома в рантайме).

## Диагностика

| Симптом | Причина / решение |
|---|---|
| Нет HTTPS / сертификат не выпускается | DNS домена не указывает на сервер, или закрыты порты 80/443 |
| `403 Forbidden к таблице` | не выдан Viewer сервисному аккаунту |
| `Не задана переменная окружения ANTHROPIC_API_KEY` | пусто в `.env` |
| `autoru_export: HTTP 401` | неверный `AUTORU_API_KEY` или логин/пароль кабинета |
| Фиды не обновляются | смотрите `docker compose logs scheduler` — какая стадия упала |
