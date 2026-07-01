FROM python:3.11-slim

WORKDIR /app

# System deps kept minimal; asyncua + paho-mqtt are pure-Python wheels.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY connector.py .

# Defaults match the vla-batch build contract; override via docker-compose env.
ENV OPCUA_URL=opc.tcp://vla-factory:4840/DairyWorks \
    MQTT_HOST=monstermq \
    MQTT_PORT=1883 \
    POLL_SEC=1.0 \
    UNS_ROOT=DairyWorks/Vla \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1

CMD ["python", "connector.py"]
