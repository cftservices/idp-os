FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8078
# Dashboard runs with zero deps; MQTT/OPC-UA paths activate via env vars.
CMD ["python", "sim.py"]
