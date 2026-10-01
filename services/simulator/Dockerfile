FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY services/simulator/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/simulator/main.py ./main.py
COPY services/simulator/ev_fleet_simulator ./ev_fleet_simulator
COPY packages/event_schemas ./event_schemas
CMD ["python", "-m", "ev_fleet_simulator"]
