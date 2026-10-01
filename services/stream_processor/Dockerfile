FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY services/stream_processor/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/event_schemas ./event_schemas
COPY services/api/app/repositories/mongo_telemetry.py ./api_storage/mongo_telemetry.py
COPY services/api/app/repositories/redis_state.py ./api_storage/redis_state.py
COPY services/api/app/config.py ./api_storage/config.py
COPY services/stream_processor/app ./evfleet_processor
CMD ["python", "-m", "evfleet_processor.main"]
