FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir paho-mqtt==2.1.0 requests==2.32.3
COPY bridge.py .
CMD ["python", "bridge.py"]
