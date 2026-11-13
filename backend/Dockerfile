FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app

COPY --chown=app:app pyproject.toml requirements.lock ./
COPY --chown=app:app src ./src
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic

RUN pip install --no-cache-dir -c requirements.lock .

USER app

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn autoescrow.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
