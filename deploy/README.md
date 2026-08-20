# Развёртывание на своём сервере

Пакет разворачивает генератор фидов на вашем VPS через Docker Compose:
пайплайн крутится по расписанию, а Caddy раздаёт фиды по HTTPS.

```
Ваш сервер
├── scheduler (Docker)  — раз в сутки: enrich → host_photos → build
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

---

## Полезные команды

| Действие | Команда |
|---|---|
| Разовый прогон только спеки | `docker compose run --rm scheduler python -m src.enrich` |
| Разовый прогон фото | `docker compose run --rm scheduler python -m src.host_photos` |
| Пересобрать фиды сейчас | `docker compose run --rm scheduler python -m src.build` |
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
| Фиды не обновляются | смотрите `docker compose logs scheduler` — какая стадия упала |
