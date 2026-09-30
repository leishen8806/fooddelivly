FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system --gid 10001 teacafe \
    && useradd --system --uid 10001 --gid teacafe --home-dir /app teacafe

WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=teacafe:teacafe backend/ ./
COPY --chown=teacafe:teacafe docs/ui/locales.json ./locales.json
ENV LOCALES_PATH=/app/locales.json

USER teacafe
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
