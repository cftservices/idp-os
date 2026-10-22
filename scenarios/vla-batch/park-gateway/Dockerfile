FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gateway.py selftest.py ./

ENV PYTHONUNBUFFERED=1 MODEL_DIR=/model

ENTRYPOINT ["python", "gateway.py"]
