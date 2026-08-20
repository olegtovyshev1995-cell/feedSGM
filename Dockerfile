# Образ пайплайна генератора фидов. Один образ на все стадии
# (enrich / host_photos / build) — запускаются как разные команды.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Сначала зависимости — лучше кэшируется между сборками.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Затем код.
COPY . .

# Непривилегированный пользователь (безопасность по умолчанию).
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/feeds /app/data \
    && chown -R app:app /app
USER app

# По умолчанию — базовая сборка фидов; планировщик переопределяет команду.
CMD ["python", "-m", "src.build"]
