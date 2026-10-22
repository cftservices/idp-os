"""OPC-UA-oppervlak voor een parkmachine.

Wat hier ontstaat is met opzet GEEN nette adresruimte. De nodes heten zoals de
leverancier ze noemt, plat en zonder hierarchie:

    ns=2;s=Ch1.Dev1.TT_2049_PV        de meting, als integer in tienden Fahrenheit
    ns=2;s=Ch1.Dev1.TT_2049_PV.Q      het DA-qualityword, in een APART item

Dat is de kern van de demonstratie. Je kunt hier met UaExpert op inprikken en je
ziet een lijst getallen zonder eenheid, zonder betekenis en zonder verband. De
modellaag maakt daar `DairyWorks/Vla-B/Cook/pasteuriser-01/Status/temp_out_C`
van, met eenheid, kwaliteit, tijd en een stabiele identiteit.

Eerlijk zijn over OPC-DA. OPC-DA is DCOM, draait alleen op Windows, en wij
hebben het NIET geimplementeerd. Wat hier staat is hoe je een DA-eiland in de
praktijk bereikt vanaf een Linux-datalaag: een DA-server achter een DA/UA-
tunneller, en je verbindt met het UA-endpoint van die tunneller. Het transport
is dus UA. De DATA is DA: platte ItemID's, kwaliteit in een companion-item,
geen meettijd, geschaalde integers. Dat is het deel dat telt.

Namespace-index. De server WEIGERT te starten als register_namespace() een
andere index teruggeeft dan het model declareert. De gegenereerde adreslijst in
init-park.sh hardcodeert `ns=2`; zou de index verschuiven, dan abonneert
MonsterMQ zich in stilte op niets en lijkt de machine gewoon stil te staan.
Liever een container die niet start dan een demo die leeg blijft.
"""

from __future__ import annotations

import asyncio
import logging
import threading

log = logging.getLogger("packml-sim.opcua")

try:
    # uamethod hangt aan het pakket zelf, niet aan asyncua.ua. Dat is een
    # makkelijke vergissing en hij faalt pas bij het opbouwen van de adresruimte.
    from asyncua import Server, ua, uamethod
    HAVE_ASYNCUA = True
except ImportError:  # pragma: no cover - alleen in een omgeving zonder asyncua
    Server = None
    ua = None
    uamethod = None
    HAVE_ASYNCUA = False


class NamespaceIndexMismatch(RuntimeError):
    pass


_VARIANT = {
    "Double": "Double",
    "Float": "Float",
    "Int16": "Int16",
    "Int32": "Int32",
    "Int64": "Int64",
    "UInt32": "UInt32",
    "Boolean": "Boolean",
    "String": "String",
}


def _coerce(value, vtype_name):
    """Python-waarde naar het type dat de node verwacht.

    Zonder dit weigert asyncua de write met een typefout en blijft de node op
    zijn beginwaarde staan. Dat crasht niets en logt (op debug) bijna niets, dus
    je ziet een adresruimte vol nullen en zoekt de fout in de simulatie.
    """
    if vtype_name == "String":
        return str(value)
    if vtype_name in ("Int16", "Int32", "Int64", "UInt32"):
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return 0
    if vtype_name == "Boolean":
        return bool(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class OpcUaSurface:
    """Draait een asyncua-server in een eigen thread, gevoed vanuit de simlus."""

    def __init__(self, cfg, on_inject_fault=None, on_clear_fault=None,
                 on_write_setpoint=None):
        if not HAVE_ASYNCUA:
            raise RuntimeError(
                "asyncua ontbreekt. Voeg het toe aan packml-sim/requirements.txt "
                "en zorg dat de Dockerfile de nieuwe packages meekopieert.")
        ua_cfg = cfg.get("opcua") or {}
        self.endpoint = ua_cfg.get("endpoint", "opc.tcp://0.0.0.0:4840/Vendor")
        self.namespace_uri = ua_cfg.get("namespace_uri", "urn:vendor")
        self.want_index = int(ua_cfg.get("namespace_index", 2))
        self.strict_index = bool(ua_cfg.get("strict_namespace_index", True))
        self.unit_id = cfg.get("unit_id") or cfg.get("equipment") or "unit"
        self.signals = list(cfg.get("signals") or [])
        self.q_suffix = (cfg.get("quality_companion") or {}).get("suffix")

        self.on_inject_fault = on_inject_fault
        self.on_clear_fault = on_clear_fault
        self.on_write_setpoint = on_write_setpoint

        self._loop = None
        self._thread = None
        self._server = None
        self._nodes = {}
        self._types = {}
        self._ready = threading.Event()
        self._error = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ opzet

    def _bind_endpoint(self):
        """De endpoint-URL in het model wijst naar de containernaam; luisteren
        doet hij op alle interfaces. Anders bindt hij aan een hostnaam die
        binnen de container niet naar zichzelf resolvet."""
        ep = self.endpoint
        try:
            head, rest = ep.split("://", 1)
            hostport, path = (rest.split("/", 1) + [""])[:2]
            port = hostport.split(":")[1] if ":" in hostport else "4840"
            return "%s://0.0.0.0:%s/%s" % (head, port, path)
        except (ValueError, IndexError):
            return ep

    async def _build(self):
        server = Server()
        await server.init()
        server.set_endpoint(self._bind_endpoint())
        server.set_server_name("%s (vendor gateway)" % self.unit_id)

        idx = await server.register_namespace(self.namespace_uri)
        if idx != self.want_index:
            msg = ("namespace-index %d in plaats van de gedeclareerde %d voor %r. "
                   "De gegenereerde adreslijst hardcodeert de index, dus de ingest "
                   "zou stil niets lezen." % (idx, self.want_index, self.namespace_uri))
            if self.strict_index:
                raise NamespaceIndexMismatch(msg)
            log.warning("%s (strict staat uit, doorgaan)", msg)
        self.ns = idx

        objects = server.nodes.objects
        # Platte itemlijst, geen mappenstructuur. Dat is de hele grap.
        folder = await objects.add_folder(ua.NodeId("Items", idx), "Items")

        for s in self.signals:
            native = s["native_name"]
            # Het NATIVE type, niet het canonieke. temp_out_C is canoniek een
            # Double maar komt hier binnen als Int32 in tienden Fahrenheit.
            dt = s.get("native_datatype") or s.get("datatype", "Double")
            vt = getattr(ua.VariantType, _VARIANT.get(dt, "Double"))
            var = await folder.add_variable(
                ua.NodeId(native, idx), native, _coerce(0, dt), varianttype=vt)
            if s.get("writable"):
                await var.set_writable()
            self._nodes[native] = var
            self._types[native] = dt

            if self.q_suffix:
                qn = native + self.q_suffix
                qdt = s.get("quality_datatype", "Int32")
                qvar = await folder.add_variable(
                    ua.NodeId(qn, idx), qn, _coerce(192, qdt),
                    varianttype=getattr(ua.VariantType, _VARIANT.get(qdt, "Int32")))
                self._nodes[qn] = qvar
                self._types[qn] = qdt

        # Storingsmethodes. Zelfde vorm als het Batch-object van de monoliet,
        # zodat batch-engine het bewezen connect-per-call-patroon uit
        # vla/opcua_control.py kan hergebruiken. Werkt ook als de broker plat ligt.
        machine = await objects.add_object(ua.NodeId(self.unit_id, idx), self.unit_id)

        @uamethod
        def InjectFault(parent, fault_id: str, magnitude: float) -> int:  # noqa: N802
            try:
                if self.on_inject_fault:
                    self.on_inject_fault(str(fault_id), float(magnitude))
                log.info("InjectFault %s @ %.2f", fault_id, magnitude)
                return 0
            except Exception as e:  # noqa: BLE001
                log.warning("InjectFault mislukt: %s", e)
                return 1

        @uamethod
        def ClearFault(parent, fault_id: str) -> int:  # noqa: N802
            try:
                if self.on_clear_fault:
                    self.on_clear_fault(str(fault_id) or None)
                log.info("ClearFault %s", fault_id or "(alle)")
                return 0
            except Exception as e:  # noqa: BLE001
                log.warning("ClearFault mislukt: %s", e)
                return 1

        await machine.add_method(
            ua.NodeId("InjectFault", idx), "InjectFault", InjectFault,
            [ua.VariantType.String, ua.VariantType.Double], [ua.VariantType.Int32])
        await machine.add_method(
            ua.NodeId("ClearFault", idx), "ClearFault", ClearFault,
            [ua.VariantType.String], [ua.VariantType.Int32])

        self._server = server
        return server

    async def _run(self):
        server = await self._build()
        async with server:
            log.info("OPC-UA op %s, ns=%d, %d nodes",
                     self._bind_endpoint(), self.ns, len(self._nodes))
            self._ready.set()
            while not self._stop.is_set():
                await asyncio.sleep(0.2)

    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run())
        except Exception as e:  # noqa: BLE001
            self._error = e
            log.error("OPC-UA-server gestopt: %s", e)
            self._ready.set()
        finally:
            try:
                self._loop.close()
            except Exception:  # noqa: BLE001
                pass

    # -------------------------------------------------------------------- api

    def start(self, timeout=20.0):
        self._thread = threading.Thread(target=self._thread_main,
                                        name="opcua-%s" % self.unit_id, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("OPC-UA-server kwam niet op binnen %.0fs" % timeout)
        if self._error:
            raise self._error

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    def _variant(self, name, value):
        """Waarde plus EXPLICIET variant-type.

        Een kale Python-int laat asyncua als Int64 typeren, en een Int32-node
        weigert die write. Het gevolg is geen crash maar een node die op zijn
        beginwaarde blijft staan: een adresruimte vol nullen, en je zoekt de
        fout in de simulatie terwijl hij in het transport zit.
        """
        dt = self._types.get(name, "Double")
        return ua.Variant(_coerce(value, dt),
                          getattr(ua.VariantType, _VARIANT.get(dt, "Double")))

    def write(self, native_name, value):
        """Thread-veilig een waarde in de adresruimte zetten."""
        node = self._nodes.get(native_name)
        if node is None or self._loop is None or self._loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(
                node.write_value(self._variant(native_name, value)),
                self._loop).result(timeout=2.0)
        except Exception as e:  # noqa: BLE001
            log.debug("write %s mislukt: %s", native_name, e)

    def write_many(self, pairs):
        if self._loop is None or self._loop.is_closed():
            return

        async def _all():
            for name, value in pairs:
                node = self._nodes.get(name)
                if node is None:
                    continue
                try:
                    await node.write_value(self._variant(name, value))
                except Exception as e:  # noqa: BLE001
                    log.warning("write %s mislukt: %s", name, e)

        try:
            asyncio.run_coroutine_threadsafe(_all(), self._loop).result(timeout=5.0)
        except Exception as e:  # noqa: BLE001
            log.warning("write_many mislukt: %s", e)
