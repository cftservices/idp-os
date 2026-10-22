FROM python:3.12-slim

WORKDIR /app

# tzdata is nodig voor assume_tz. Zonder dit pakket kan zoneinfo Europe/Amsterdam
# niet vinden en valt de conditioner terug op standaardtijd; dan is elke
# vendor-c-tijdstempel in de zomer een uur mis. Dat is precies het soort fout
# dat pas in oktober opvalt.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY condition.py crosscheck.py app.py selftest.py ./

ENV PYTHONUNBUFFERED=1 MODEL_DIR=/model
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3).status==200 else 1)"

ENTRYPOINT ["python", "app.py"]
