FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

# factory-model is mounted at /model (see docker-compose.dairyworks.yml)
ENV FACTORY_MODEL=/model/isa95-dairyworks.json \
    OPCUA_ENDPOINT=opc.tcp://0.0.0.0:4840/DairyWorks \
    MQTT_HOST=monstermq \
    MQTT_PORT=1883

EXPOSE 4840
CMD ["python", "server.py"]
