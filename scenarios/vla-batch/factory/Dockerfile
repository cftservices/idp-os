FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY physics.py server.py selftest.py ./

# OPC-UA endpoint (the "black box with SCADA buttons")
EXPOSE 4840

# opc.tcp://0.0.0.0:4840/DairyWorks , ns=2 (urn:dairyworks)
CMD ["python", "server.py"]
