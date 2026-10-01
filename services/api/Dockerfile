FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY services/api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/api/app ./app
COPY services/api/alembic.ini ./alembic.ini
COPY services/api/alembic ./alembic
COPY packages/event_schemas ./event_schemas
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python -m app.init_nosql && python -m app.seed && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
