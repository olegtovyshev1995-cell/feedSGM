#!/usr/bin/env bash
# Прогон пайплайна на сервере: autoru_export → enrich → host_photos → build (фиды).
# Стадии независимы: падение одной логируется, остальные продолжают.
# Флаг --loop запускает раз в сутки в RUN_AT (иначе — один прогон и выход).
set -uo pipefail

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%SZ')] $*"; }

# Секрет Google — из файла в переменную окружения (R1), если задан путь.
if [ -n "${GOOGLE_SA_JSON_FILE:-}" ] && [ -f "${GOOGLE_SA_JSON_FILE}" ]; then
  export GOOGLE_SA_JSON="$(cat "${GOOGLE_SA_JSON_FILE}")"
fi

run_stage() {
  local name="$1"; shift
  log "стадия ${name}: старт"
  if "$@"; then
    log "стадия ${name}: ок"
  else
    log "стадия ${name}: ошибка (код $?) — продолжаю следующую"
  fi
}

run_once() {
  log "=== прогон пайплайна начат ==="
  # A0: выгрузка всех объявлений кабинета Auto.ru по API → exports/
  # (нужны AUTORU_API_KEY + логин/пароль или сессия). Без ключа — пропуск.
  if [ -n "${AUTORU_API_KEY:-}${AUTORU_API_KEY_FILE:-}" ]; then
    run_stage "autoru_export" python scripts/autoru_export.py --category "${AUTORU_CATEGORY:-all}"
    # Отдельный файл только с неактивными (выключить: AUTORU_EXPORT_INACTIVE=0).
    if [ "${AUTORU_EXPORT_INACTIVE:-1}" != "0" ]; then
      run_stage "autoru_export_inactive" python scripts/autoru_export.py \
        --category "${AUTORU_CATEGORY:-all}" --inactive
    fi
    # Фид Drom из активных объявлений Auto.ru → feeds/drom_autoru.xml.
    run_stage "autoru_to_drom" python -m src.autoru_to_drom
  else
    log "стадия autoru_export: пропуск (AUTORU_API_KEY не задан)"
  fi
  # B+C1: спецификация + подбор фото (нужны GOOGLE_SA_JSON + ANTHROPIC_API_KEY).
  run_stage "enrich"       python -m src.enrich
  # C3+C4: хостинг и уникализация фото (нужен IMGBB_API_KEY).
  run_stage "host_photos"  python -m src.host_photos
  # D: сборка фидов в feeds/ (нужен GOOGLE_SA_JSON).
  run_stage "build"        python -m src.build
  log "=== прогон пайплайна завершён ==="
}

seconds_until() {
  # Секунды до ближайшего наступления времени RUN_AT (HH:MM).
  local at="$1" now target
  now=$(date -u +%s)
  target=$(date -u -d "today ${at}" +%s 2>/dev/null || echo 0)
  if [ "${target}" -le "${now}" ]; then
    target=$(date -u -d "tomorrow ${at}" +%s)
  fi
  echo $((target - now))
}

if [ "${1:-}" = "--loop" ]; then
  RUN_AT="${RUN_AT:-06:00}"
  log "планировщик запущен, ежедневный прогон в ${RUN_AT} UTC"
  # Первый прогон — сразу при старте контейнера, чтобы не ждать до утра.
  run_once
  while true; do
    sleep_for=$(seconds_until "${RUN_AT}")
    log "следующий прогон через $((sleep_for / 3600))ч $(((sleep_for % 3600) / 60))м"
    sleep "${sleep_for}"
    run_once
  done
else
  run_once
fi
