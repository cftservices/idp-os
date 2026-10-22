"""Het anti-patroon: rechtstreeks line protocol de historian in.

Met OPZET ingebouwd, op precies een machine (`bottle-filler-01`), naast zijn
normale weg. Dit is de scherpste vijf seconden van de demo.

    "Deze machine staat al in de historian, klaar toch?"

Nee. Wat er in `idp_bypass` landt is een reeks getallen en verder niets:

    geen eenheid          is 22.4 een graad, een liter of een procent?
    geen kwaliteit        stond de sensor aan?
    geen asset            welke machine, welke lijn, welke area?
    geen signal_uuid      overleeft dit een hernoeming bovenstrooms?
    geen batchkoppeling   bij welke productie hoorde dit?

Dezelfde meting via de modellaag heeft dat allemaal wel. Een historian is
net zomin de data layer als OPC UA dat is, en dat is precies wat je hier naast
elkaar op het scherm kunt zetten.

Landt in database `idp_bypass` en NOOIT in `idp_park`. Zou hij in idp_park
belanden, dan vervuilt het tegenvoorbeeld de echte metingen en is de demo zijn
eigen argument kwijt. Er staat een assertie op.
"""

from __future__ import annotations

import base64
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("packml-sim.bypass")

FORBIDDEN_DB = "idp_park"


class TDengineDirect:
    """Schrijft ruwe waarden als InfluxDB line protocol naar TDengine."""

    def __init__(self, cfg):
        by = (cfg.get("tdengine_bypass") or {})
        self.enabled = bool(by.get("enabled"))
        self.database = by.get("database", "idp_bypass")
        self.url = by.get("url", "http://vla-tdengine:6041/influxdb/v1/write")
        # De omgeving wint van de unit-config. De unit-config is GEGENEREERD en
        # gaat dus de repo in; daar hoort geen wachtwoord in te staan. Op de VPS
        # komt het uit .env, en die wordt nooit gesynchroniseerd.
        self.user = os.environ.get("TD_BYPASS_USER") or by.get("user", "root")
        self.password = (os.environ.get("TD_BYPASS_PASSWORD")
                         or by.get("password", "taosdata"))
        self.unit_id = cfg.get("unit_id") or cfg.get("equipment") or "unit"

        if self.database == FORBIDDEN_DB:
            raise ValueError(
                "het bypass-pad mag NOOIT naar %s schrijven: dan vervuilt het "
                "tegenvoorbeeld de gemodelleerde metingen en is de demo zijn "
                "eigen argument kwijt" % FORBIDDEN_DB)

        self._warned = False


    @staticmethod
    def _line(measurement, tags, value, ts_ms):
        """Kaal line protocol. Let op wat er NIET in staat: geen eenheid, geen
        kwaliteit, geen herkomst. Dat is niet luiheid maar het punt."""
        tagstr = ",".join("%s=%s" % (k, str(v).replace(" ", "\\ ").replace(",", "\\,"))
                          for k, v in tags.items())
        try:
            field = "value=%s" % float(value)
        except (TypeError, ValueError):
            field = 'valuestr="%s"' % str(value).replace('"', '\\"')
        return "%s,%s %s %d" % (measurement, tagstr, field, ts_ms)

    def _basic_auth(self):
        """Authorization METEEN meesturen, niet wachten op een challenge.

        TDengine antwoordt met een kale 401 zonder WWW-Authenticate-header.
        urllib's HTTPBasicAuthHandler reageert alleen OP die header, dus die
        stuurt de credentials nooit en elke write faalde met 401. Dat viel niet
        op omdat dit pad met opzet stil faalt.
        """
        raw = ("%s:%s" % (self.user, self.password)).encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def write(self, native_values, ts_ms):
        """{native_name: waarde} -> line protocol. Faalt stil: dit pad mag de
        machine nooit ophouden, en het is per definitie niet kritiek. De EERSTE
        fout gaat wel als waarschuwing naar het log, want stil falen mag geen
        stil verdwijnen worden."""
        if not self.enabled or not native_values:
            return 0
        lines = [self._line("raw_bottling", {"tag": name}, v, ts_ms)
                 for name, v in native_values.items()]
        body = "\n".join(lines).encode("utf-8")
        # precision=ms hoort erbij: zonder deze parameter leest het endpoint
        # het tijdstempel als nanoseconden en antwoordt het met
        # "Timestamp data out of range" (0x60b).
        url = "%s?db=%s&precision=ms" % (self.url, self.database)
        try:
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Authorization": self._basic_auth(),
                         "Content-Type": "text/plain; charset=utf-8"})
            urllib.request.urlopen(req, timeout=3).read()
            self._warned = False
            return len(lines)
        except (urllib.error.URLError, OSError) as e:
            detail = ""
            if isinstance(e, urllib.error.HTTPError):
                try:
                    detail = " | %s" % e.read().decode("utf-8", "replace")[:200]
                except Exception:  # noqa: BLE001
                    pass
            if not getattr(self, "_warned", False):
                self._warned = True
                log.warning("bypass-write naar %s mislukt (niet kritiek, maar "
                            "idp_bypass blijft dan leeg): %s%s",
                            self.database, e, detail)
            else:
                log.debug("bypass-write mislukt: %s%s", e, detail)
            return 0
