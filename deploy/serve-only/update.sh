#!/usr/bin/env bash
# Обновить фиды на сервере: подтянуть свежие файлы из репозитория.
# Caddy отдаёт их с диска, перезапуск не нужен.
set -euo pipefail
cd "$(dirname "$0")/../.."
git pull --ff-only
echo "Фиды обновлены:"
ls -la feeds/*.xml
