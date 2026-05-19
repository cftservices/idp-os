FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY packml/ ./packml/
COPY physics/ ./physics/
COPY mqtt/ ./mqtt/
COPY server.py .

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "server.py"]
