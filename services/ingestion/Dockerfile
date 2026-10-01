FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY services/ingestion/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/ingestion/app ./evfleet_ingestion
COPY packages/event_schemas ./event_schemas
CMD ["python", "-m", "evfleet_ingestion.main"]
