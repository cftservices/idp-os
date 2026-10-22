"""Modbus TCP-oppervlak voor een parkmachine.

Het armste protocol van het park, en daarom het sterkste argument.

Een holding register is zestien bits. Geen naam, geen eenheid, geen kwaliteit,
geen tijdstempel, geen datatype. Register 40115 is een getal en verder helemaal
niets. Alles wat het betekenis geeft komt van buiten, uit
factory-model/park-conditioning.json, en nergens anders vandaan.

Drie codeervormen, alle drie zoals apparaten het echt doen:

    int16         een analoge waarde als ruwe count 0..27648 over een EU-bereik
    int32_hi_lo   een teller over twee registers, HOOG WOORD EERST. De
                  woordvolgorde is de klassiekste Modbus-valstrik die er is:
                  omgekeerd gelezen krijg je een gigantisch maar volstrekt
                  plausibel getal, en niets in het protocol waarschuwt je.
    ascii_packed  tekst als twee tekens per register, big-endian, 16 tekens.
                  Modbus kent geen strings; zo doen apparaten het.

En EEN statusregister voor het hele apparaat. Geen kwaliteit per meting, want
zo werkt een registerblok niet. Zeg die beperking hardop in de demo: gaat er
een sensor stuk, dan zegt dit woord alleen DAT er iets mis is, niet WAT.
"""

from __future__ import annotations

import asyncio
import logging
import threading

log = logging.getLogger("packml-sim.modbus")

try:
    from pymodbus.datastore import (ModbusDeviceContext, ModbusServerContext,
                                    ModbusSequentialDataBlock)
    from pymodbus.server import ModbusTcpServer
    HAVE_PYMODBUS = True
except ImportError:  # pragma: no cover
    HAVE_PYMODBUS = False

STATUS_OK, STATUS_FAULT, STATUS_SIM = 0, 1, 2


def encode(value, encoding, width):
    """Waarde -> lijst van 16-bits registerwoorden."""
    if encoding == "ascii_packed":
        s = str(value)[: width * 2].ljust(width * 2, "\0")
        return [((ord(s[i]) & 0xFF) << 8) | (ord(s[i + 1]) & 0xFF)
                for i in range(0, width * 2, 2)]
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        n = 0
    if encoding == "int32_hi_lo":
        n = max(0, min(n, 0xFFFFFFFF))
        return [(n >> 16) & 0xFFFF, n & 0xFFFF]   # hoog woord EERST
    return [max(0, min(n, 0xFFFF))]


class ModbusSurface:
    """Draait een Modbus TCP-server in een eigen thread, gevoed vanuit de simlus."""

    def __init__(self, cfg):
        if not HAVE_PYMODBUS:
            raise RuntimeError(
                "pymodbus ontbreekt. Voeg het toe aan packml-sim/requirements.txt "
                "en zorg dat de Dockerfile het nieuwe package meekopieert.")
        mb = cfg.get("modbus") or {}
        self.port = int(mb.get("port", 5020))
        self.device_id = int(mb.get("unit_id", 1))
        self.unit_id = cfg.get("unit_id") or cfg.get("equipment") or "unit"
        self.signals = list(cfg.get("signals") or [])

        addrs = []
        self.status_addr = None
        for s in self.signals:
            d = s.get("distort") or {}
            a, w = d.get("modbus_addr"), d.get("modbus_width", 1)
            if a is None:
                raise ValueError("%s: signaal %s heeft geen modbus_addr; "
                                 "draai tools/gen-park.py" % (self.unit_id, s["name"]))
            addrs.extend(range(a, a + w))
            self.status_addr = d.get("modbus_status_addr", self.status_addr)
        if self.status_addr is not None:
            addrs.append(self.status_addr)

        # Modbus-adressen in de PLC-notatie (4xxxx) zijn 1-based en bevatten het
        # functiecode-voorvoegsel; op de draad gaat een 0-based offset, waarbij
        # 40001 overeenkomt met wire-adres 0. Dat verschil van precies 40001 is
        # de tweede klassieke Modbus-valstrik, en het kost je een
        # "illegal data address" die nergens naar wijst.
        #
        # Het datablok begint dus op WIRE-adres, niet op nul en niet op het
        # PLC-adres. Een client die netjes 40101 vertaalt naar 100 moet hem
        # kunnen vinden.
        self.offset = 40001
        self.base = min(addrs)
        self.size = max(addrs) - self.base + 2
        wire_start = self.base - self.offset

        block = ModbusSequentialDataBlock(wire_start, [0] * (self.size + 4))
        device = ModbusDeviceContext(hr=block, ir=block)
        self.context = ModbusServerContext(devices={self.device_id: device},
                                           single=False)
        self._block = block
        self._loop = None
        self._thread = None
        self._server = None
        self._ready = threading.Event()
        self._error = None

    # ------------------------------------------------------------------- api

    def _wire(self, plc_addr):
        """PLC-adres (40115) -> index in het datablok.

        Twee correcties, en allebei kosten ze een middag als je ze mist:

          -40001  PLC-notatie naar wire-adres. Holding register 40001 is
                  wire-adres 0; het voorvoegsel 4 is de functiecode en hoort
                  niet op de draad.
          +1      pymodbus telt er op de LEESweg intern 1 bij op (de datastore
                  is 1-based), maar setValues() doet dat niet. Schrijf je zonder
                  deze +1, dan leest een correcte client stelselmatig het
                  volgende register. Geen foutmelding, gewoon andere getallen:
                  het niveau van de tank leest als de temperatuur.

        Empirisch vastgesteld met een adres-is-waarde-patroon, niet aangenomen.
        """
        return plc_addr - self.offset + 1

    async def _run(self):
        self._server = ModbusTcpServer(self.context, address=("0.0.0.0", self.port))
        self._ready.set()
        log.info("Modbus TCP op :%d, device %d, registers %d..%d",
                 self.port, self.device_id, self.base, self.base + self.size - 1)
        await self._server.serve_forever()

    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run())
        except Exception as e:  # noqa: BLE001
            self._error = e
            log.error("Modbus-server gestopt: %s", e)
            self._ready.set()

    def start(self, timeout=15.0):
        self._thread = threading.Thread(target=self._thread_main,
                                        name="modbus-%s" % self.unit_id, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("Modbus-server kwam niet op binnen %.0fs" % timeout)
        if self._error:
            raise self._error

    def stop(self):
        if self._server is not None and self._loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._server.shutdown(), self._loop).result(timeout=5)
            except Exception:  # noqa: BLE001
                pass
        if self._thread:
            self._thread.join(timeout=5.0)

    def write_signals(self, native_values, status=STATUS_OK):
        """{native_name: waarde} -> registers. native_name IS het PLC-adres."""
        for s in self.signals:
            d = s.get("distort") or {}
            v = native_values.get(s["name"])
            if v is None:
                continue
            words = encode(v, d.get("modbus_encoding", "int16"),
                           d.get("modbus_width", 1))
            self._block.setValues(self._wire(d["modbus_addr"]), words)
        if self.status_addr is not None:
            self._block.setValues(self._wire(self.status_addr), [int(status)])
