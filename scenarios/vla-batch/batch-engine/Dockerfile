# batch-engine — Vla Batch v2 MES-laag (FastAPI + report)
FROM python:3.11-slim

WORKDIR /app

# offline-first: deps are small, no build toolchain needed
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vla/ ./vla/
COPY app.py selftest.py ./

EXPOSE 8000

# healthcheck via the contract /api/v1/health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; \
  sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/health',timeout=3).status==200 else 1)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
