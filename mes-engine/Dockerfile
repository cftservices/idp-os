FROM python:3.12-slim

WORKDIR /app

# System deps kept minimal; weasyprint (optional) needs extra libs — not installed
# here so the base image stays small. EBR falls back to HTML when weasyprint absent.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV MQTT_HOST=monstermq \
    MQTT_PORT=1883
# MONGO_URL optional — omit for in-memory store.

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
