# -*- coding: utf-8 -*-
"""vla-park-poller: haalt data op bij machines die zelf niets publiceren.

Modbus TCP en REST hebben geen push. Iemand moet ze POLLEN, en dat is precies
het punt: bij die protocollen is de frequentie een keuze van de datalaag en niet
van de machine, en het tijdstempel is per definitie aankomsttijd. Dat is geen
tekortkoming van deze implementatie maar van het protocol, en het hoort in de
conditioning-regel te staan (`timestamp_source: none`) en niet verzwegen te
worden.

Wat hij publiceert is RUWE data op `raw/vla-park/{machine}/{native}`, precies
zoals de andere transporten. De conditioner weet daardoor niet via welk
protocol iets binnenkwam, en dat hoort ook zo: zou hij dat wel weten, dan kreeg
je zes takken code die elk net iets anders doen, en dan zit het verschil straks
in de data.

De decodering hier is met OPZET een tweede implementatie, los van
packml-sim/modbus_surface/server.py. Zouden beide kanten dezelfde functie
delen, dan bewijst de round-trip alleen dat een functie zichzelf kan omkeren.
"""

from __future__ import annotations

import io
import json
import logging
import os
import time

log = logging.getLogger("park-poller")

MODEL_DIR = os.environ.get("MODEL_DIR", "/model")
RAW_ROOT = os.environ.get("RAW_ROOT", "raw/vla-park")

# Hartslag van de kwaliteitscompanions. Zie Schedule hieronder.
QUALITY_KEEPALIVE_S = float(os.environ.get("QUALITY_KEEPALIVE_S", "30"))
# De snelheid van de pollus zelf. Sneller dan dit kan geen enkele klasse.
TICK_S = float(os.environ.get("POLL_INTERVAL_S", "1.0"))


# ---------------------------------------------------------------------- tempo

class Schedule:
    """Houdt per punt bij wanneer het weer de bus op mag.

    Dit is GEEN deadband. Een deadband werkt op engineering-waarden en hoort in
    de conditioner; die grens blijft staan. Dit is de sampling class uit
    `signal-template.json` naleven, en dat is niets anders dan de connector
    goed configureren. Een teller die per 30 s ververst elke seconde uitlezen
    levert geen extra informatie op, alleen extra berichten.

    Waarom dit er niet meteen in zat: gemeten op 2026-08-11 stond
    `blend-tank-01` op 58 msg/s waar het budget 6,2 is. Alle 30 registers plus
    30 .Q-companions, elke seconde, ongeacht klasse of verandering. Ter
    vergelijking: `filler-01` publiceert zelf, respecteert de klassen wel, en
    zit op 6,5 msg/s.

    Twee soorten punten:

      - cyclisch (fast 1 s / normal 5 s / slow 30 s): elk interval één bericht,
        ongeacht de waarde. Wat daarna nog overbodig is filtert de deadband van
        de conditioner eruit, en dat blijft zichtbaar in zijn teller.
      - onchange (toestand, setpoints, alarmwoord): zodra de waarde verandert,
        plus een hartslag van `expected_interval_s`. Zonder die hartslag is een
        constante toestand onzichtbaar voor een conditioner die later opstart.
        Dat is exact de val waar de .Q-companions eerder in liepen.

    Een kwaliteitswissel wordt NOOIT uitgesteld, dezelfde regel als in de
    conditioner: een deadband mag een waarde onderdrukken, een
    kwaliteitsverandering nooit.
    """

    def __init__(self, tick_s=1.0):
        self.tick_s = float(tick_s)
        self._last_pub = {}
        self._last_val = {}

    @staticmethod
    def _spec(rule):
        cls = rule.get("sampling_class") or "normal"
        return cls == "onchange", float(rule.get("expected_interval_s") or 5.0)

    def acquire_due(self, key, rule, now):
        """Moet deze waarde nu worden OPGEHAALD?

        Alleen zinvol voor REST, waar elk punt een eigen request kost. Bij
        Modbus lezen we sowieso het hele registerblok in één transactie, dus
        daar valt niets te besparen aan de leeskant.
        """
        on_change, interval = self._spec(rule)
        last = self._last_pub.get(key)
        if last is None:
            return True
        return (now - last) >= (self.tick_s if on_change else interval) - 1e-6

    def publish_due(self, key, rule, value, now):
        on_change, interval = self._spec(rule)
        last = self._last_pub.get(key)
        if last is None:
            return self._mark(key, value, now)
        if on_change:
            if value != self._last_val.get(key) or (now - last) >= interval:
                return self._mark(key, value, now)
            return False
        if (now - last) >= interval - 1e-6:
            return self._mark(key, value, now)
        return False

    def quality_due(self, key, value, now):
        last = self._last_pub.get(key)
        if (last is None or value != self._last_val.get(key)
                or (now - last) >= QUALITY_KEEPALIVE_S - 1e-6):
            return self._mark(key, value, now)
        return False

    def _mark(self, key, value, now):
        self._last_pub[key] = now
        self._last_val[key] = value
        return True


def budget_msg_s(rules):
    """Wat deze regels bij naleving zouden kosten, in msg/s.

    Voor onchange-punten is dit de ONDERGRENS: de hartslag. Elke echte
    verandering komt daar bovenop, en dat hoort ook zo.
    """
    total = 0.0
    for r in rules:
        _, interval = Schedule._spec(r)
        total += 1.0 / interval if interval else 0.0
        if r.get("quality_topic_suffix"):
            total += 1.0 / QUALITY_KEEPALIVE_S
    return total


# ------------------------------------------------------------------ decoderen

def decode(words, encoding):
    """Registerwoorden -> waarde. De inverse van modbus_surface.encode()."""
    if encoding == "ascii_packed":
        chars = []
        for w in words:
            chars.append(chr((w >> 8) & 0xFF))
            chars.append(chr(w & 0xFF))
        return "".join(chars).rstrip("\0")
    if encoding == "int32_hi_lo":
        if len(words) < 2:
            return 0
        # Hoog woord EERST. Omgekeerd lezen geeft een gigantisch maar volstrekt
        # plausibel getal, en niets in het protocol waarschuwt daarvoor.
        return (int(words[0]) << 16) | int(words[1])
    return int(words[0]) if words else 0


STATUS_TO_RAW = {0: 0, 1: 1, 2: 2}


# ------------------------------------------------------------------- modbus

class ModbusTarget:
    """Eén Modbus-machine: registerkaart plus verbinding."""

    def __init__(self, machine, rules):
        self.machine = machine
        self.rules = [r for r in rules if r.get("modbus_addr") is not None]
        first = self.rules[0]
        self.status_addr = first.get("modbus_status_addr")
        self.base = min(r["modbus_addr"] for r in self.rules)
        top = max(r["modbus_addr"] + r["modbus_width"] for r in self.rules)
        if self.status_addr:
            top = max(top, self.status_addr + 1)
        self.count = top - self.base
        self.host = os.environ.get(
            "MODBUS_HOST_%s" % machine.replace("-", "_").upper(), machine)
        self.port = int(os.environ.get("MODBUS_PORT", "5020"))
        self.device_id = int(os.environ.get("MODBUS_UNIT_ID", "1"))
        self.client = None
        self.ok = False
        self.sched = Schedule(TICK_S)

    def connect(self):
        from pymodbus.client import ModbusTcpClient
        self.client = ModbusTcpClient(self.host, port=self.port, timeout=3)
        self.ok = self.client.connect()
        if self.ok:
            log.info("%s: verbonden met %s:%d, %d registers vanaf %d",
                     self.machine, self.host, self.port, self.count, self.base)
        else:
            log.warning("%s: geen verbinding met %s:%d", self.machine,
                        self.host, self.port)
        return self.ok

    def poll(self):
        """[(topic, payload)] of een lege lijst."""
        if not self.ok and not self.connect():
            return []
        try:
            rr = self.client.read_holding_registers(
                self.base - 40001, count=self.count, device_id=self.device_id)
        except Exception as e:  # noqa: BLE001
            log.warning("%s: leesfout %s", self.machine, e)
            self.ok = False
            return []
        if rr is None or rr.isError():
            log.warning("%s: modbus-fout %s", self.machine, rr)
            self.ok = False
            return []

        regs = rr.registers
        status = None
        if self.status_addr is not None:
            i = self.status_addr - self.base
            if 0 <= i < len(regs):
                status = regs[i]

        # Het hele blok is in één transactie binnen; wat er nu nog gebeurt is
        # alleen beslissen wat er de bus op mag. Zie Schedule.
        now = time.monotonic()
        out = []
        for r in self.rules:
            i = r["modbus_addr"] - self.base
            w = r["modbus_width"]
            if i < 0 or i + w > len(regs):
                continue
            value = decode(regs[i:i + w], r["modbus_encoding"])
            key = r["native_name"]
            topic = "%s/%s/%s" % (RAW_ROOT, self.machine, key)
            # Geen tijdstempel: Modbus heeft er geen. De conditioner gebruikt
            # aankomsttijd en LABELT dat ook zo. Er hier een verzinnen zou een
            # precisie suggereren die niet bestaat.
            if self.sched.publish_due(key, r, value, now):
                out.append((topic, json.dumps({"v": value})))
            if status is not None:
                # Eén statuswoord voor het hele apparaat, uitgewaaierd over alle
                # signalen. Zo hoeft de conditioner niets van Modbus te weten.
                qraw = STATUS_TO_RAW.get(status, 2)
                if self.sched.quality_due(key + ".Q", qraw, now):
                    out.append(("%s.Q" % topic, str(qraw)))
        return out


# --------------------------------------------------------------------- rest

class RestTarget:
    """Eén REST-machine, in de vorm van de bestaande ip21-stub."""

    def __init__(self, machine, rules):
        self.machine = machine
        self.rules = rules
        self.base_url = os.environ.get(
            "REST_URL_%s" % machine.replace("-", "_").upper(),
            "http://%s:8000" % machine)
        self.session = None
        self.sched = Schedule(TICK_S)

    def poll(self):
        import urllib.error
        import urllib.request
        now = time.monotonic()
        out = []
        for r in self.rules:
            key = r["native_name"]
            # Bij REST kost elk punt een eigen request. Een teller die per 30 s
            # ververst elke seconde ophalen belast niet alleen de bus maar ook
            # de machine, en dat is bij een OEM-gateway precies het verschil
            # tussen meelezen en in de weg lopen.
            if not self.sched.acquire_due(key, r, now):
                continue
            url = "%s/tags/%s/current" % (self.base_url, key)
            try:
                with urllib.request.urlopen(url, timeout=3) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, ValueError, OSError) as e:
                log.debug("%s: %s onbereikbaar (%s)", self.machine, url, e)
                continue
            # Vergelijken op de WAARDE en niet op de hele payload: die draagt
            # een tijdstempel dat elke poll verandert, en dan is "onchange"
            # altijd waar.
            mark = body.get("value") if isinstance(body, dict) else body
            if self.sched.publish_due(key, r, mark, now):
                topic = "%s/%s/%s" % (RAW_ROOT, self.machine, key)
                out.append((topic, json.dumps(body)))
        return out


# --------------------------------------------------------------------- main

def load_targets():
    with io.open(os.path.join(MODEL_DIR, "park-conditioning.json"),
                 encoding="utf-8") as fh:
        rules = json.load(fh)["rules"]
    by_machine = {}
    for r in rules:
        by_machine.setdefault(r["source_system"], []).append(r)

    targets = []
    for machine, rs in sorted(by_machine.items()):
        if any(x.get("modbus_addr") is not None for x in rs):
            targets.append(ModbusTarget(machine, rs))
        elif rs and rs[0].get("protocol") == "rest":
            targets.append(RestTarget(machine, rs))
    return targets, rules


def main():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s", datefmt="%H:%M:%S")

    import paho.mqtt.client as mqtt

    targets, rules = load_targets()
    if not targets:
        log.warning("geen pollbare machines in het model; niets te doen")
    for t in targets:
        log.info("doel: %s (%s), %d punten, budget %.1f msg/s",
                 t.machine, type(t).__name__, len(t.rules),
                 budget_msg_s(t.rules))
    if targets:
        log.info("samen %.1f msg/s bij naleving van de sampling classes; "
                 "meet met mosquitto_sub -t '%s/#' als je twijfelt",
                 sum(budget_msg_s(t.rules) for t in targets), RAW_ROOT)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="vla-park-poller")
    user = os.environ.get("MQTT_USERNAME")
    if user:
        client.username_pw_set(user, os.environ.get("MQTT_PASSWORD") or None)
    client.connect(os.environ.get("MQTT_HOST", "monstermq"),
                   int(os.environ.get("MQTT_PORT", 1883)), 60)
    client.loop_start()

    interval = float(os.environ.get("POLL_INTERVAL_S", "1.0"))
    hb = "DairyWorks/Vla-B/DataQuality/connector-poller/Status/last_poll_ok"
    try:
        while True:
            started = time.time()
            sent, alive = 0, 0
            for t in targets:
                msgs = t.poll()
                if msgs:
                    alive += 1
                for topic, payload in msgs:
                    client.publish(topic, payload, qos=0)
                    sent += 1
            # Hartslag. Een poller die dood gaat levert geen foutmelding op maar
            # stilte, en stilte lijkt op een machine die niets doet. Daarom
            # publiceert hij actief dat hij nog leeft.
            client.publish(hb, json.dumps({
                "value": alive, "unit": "", "quality": "GOOD",
                "targets": len(targets), "messages": sent}), qos=0)
            time.sleep(max(0.05, interval - (time.time() - started)))
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
