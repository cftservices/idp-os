FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY packml/ ./packml/
COPY physics/ ./physics/
COPY mqtt/ ./mqtt/
# Park-uitbreiding (lijn Vla-B). Een vergeten COPY hier is geen importfout bij
# het bouwen maar een crash bij het starten van de container, en dat kwam in de
# vorige poging pas op de VPS aan het licht. Blijft dus naast de rest staan.
COPY signals/ ./signals/
COPY vendor/ ./vendor/
COPY follow/ ./follow/
COPY opcua_surface/ ./opcua_surface/
COPY modbus_surface/ ./modbus_surface/
COPY rest_surface/ ./rest_surface/
COPY sql_surface/ ./sql_surface/
COPY server.py .
COPY park_runner.py .
COPY tdengine_direct.py .
COPY selftest_park.py .
COPY selftest_regression.py .
COPY selftest_regression_snapshot.json .

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "server.py"]
