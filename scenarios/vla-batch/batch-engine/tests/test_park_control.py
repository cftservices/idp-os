# -*- coding: utf-8 -*-
"""Storingsbediening voor lijn Vla-B. Geen broker, geen machines, geen netwerk.

De twee eigenschappen die er echt toe doen:

  1. Offline-veilig. Zonder draaiende machines geeft elke aanroep een nette
     status terug in plaats van een exception. Een MES-laag die crasht omdat een
     simulator er niet is, is erger dan geen knop.
  2. Geen spookstoringen. De catalogus komt uit het FAULTS-attribuut van de
     physics-klassen; een storing die daar niet in staat wordt GEWEIGERD en niet
     doorgestuurd. Anders bouw je een knop die niets doet, en dat prikt het
     publiek er als eerste uit.
"""

from __future__ import annotations

import os

import pytest

from vla.park_control import ParkControl

MODEL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "..", "factory-model")


@pytest.fixture()
def park():
    return ParkControl(model_dir=MODEL, mqtt_publish=None, connect_timeout_s=0.2,
                       retries=0)


@pytest.fixture()
def park_mqtt():
    sent = []
    p = ParkControl(model_dir=MODEL, mqtt_publish=lambda t, b: sent.append((t, b)),
                    connect_timeout_s=0.2, retries=0)
    p.sent = sent
    return p


def test_catalogus_is_gevuld_en_komt_uit_de_fysica(park):
    cat = park.catalogue()
    assert cat["load_error"] is None
    machines = {m["equipment_id"]: m for m in cat["machines"]}
    assert len(machines) == 12, sorted(machines)
    # Elke machine heeft minstens een storing, en die zijn allemaal fN-codes.
    for eq, m in machines.items():
        assert m["faults"], eq
        assert all(f.startswith("f") for f in m["faults"]), (eq, m["faults"])
    # De pasteur implementeert er drie; die staan vast in physics/pasteurizer.py.
    assert set(machines["pasteuriser-01"]["faults"]) == {"f1", "f8", "f12"}


def test_transport_per_machine_klopt_met_het_protocol(park):
    m = {x["equipment_id"]: x for x in park.catalogue()["machines"]}
    # OPC-UA en OPC-DA krijgen de methode-route: die werkt ook als de broker
    # plat ligt, en bij een demo over storingen wil je niet dat je storingsknop
    # afhangt van het onderdeel dat misschien stuk is.
    assert m["pasteuriser-01"]["transport"] == "opcua"
    assert m["separator-01"]["transport"] == "opcua"
    # Modbus, MQTT, REST en SQL gaan via het Command-topic; park_runner houdt
    # die MQTT-verbinding open ongeacht het oppervlak.
    assert m["blend-tank-01"]["transport"] == "mqtt"
    assert m["filler-01"]["transport"] == "mqtt"
    assert m["case-packer-01"]["transport"] == "mqtt"


def test_onbekende_machine_wordt_geweigerd(park):
    r = park.inject("bestaat-niet-01", "f8", 0.5)
    assert r["ok"] is False
    assert "onbekende machine" in r["error"]
    assert "pasteuriser-01" in r["known"]


def test_spookstoring_wordt_geweigerd_en_niet_verstuurd(park_mqtt):
    # f99 bestaat nergens; blend-tank kent alleen f8 en f13.
    r = park_mqtt.inject("blend-tank-01", "f99", 1.0)
    assert r["ok"] is False
    assert "kent storing" in r["error"]
    assert r["known"] == ["f8", "f13"] or set(r["known"]) == {"f8", "f13"}
    # En er is NIETS de deur uit gegaan.
    assert park_mqtt.sent == []


def test_offline_valt_niet_om(park):
    """Geen machines, geen MQTT: nette status, geen exception."""
    r = park.inject("pasteuriser-01", "f8", 0.6)
    assert r["ok"] is False
    assert r["connected"] is False
    assert "error" in r
    # en clear net zo goed
    c = park.clear("pasteuriser-01", "f8")
    assert c["ok"] is False and c["connected"] is False


def test_mqtt_route_publiceert_op_het_juiste_topic(park_mqtt):
    r = park_mqtt.inject("blend-tank-01", "f8", 0.7)
    assert r["ok"] is True
    assert r["transport"] == "mqtt"
    topic, body = park_mqtt.sent[-1]
    assert topic == ("DairyWorks/Vla-B/Mixing/blend-tank-01/Command/Fault/Inject")
    assert '"fault": "f8"' in body or '"fault":"f8"' in body
    assert "0.7" in body
    # fire-and-forget hoort zichtbaar te zijn, niet weggepoetst
    assert "geen bevestiging" in r["note"]


def test_magnitude_wordt_geklemd(park_mqtt):
    assert park_mqtt.inject("blend-tank-01", "f8", 5.0)["magnitude"] == 1.0
    assert park_mqtt.inject("blend-tank-01", "f8", -2.0)["magnitude"] == 0.0


def test_actieve_storingen_worden_bijgehouden_en_gewist(park_mqtt):
    park_mqtt.inject("blend-tank-01", "f8", 0.4)
    park_mqtt.inject("filler-01", "f12", 0.9)
    cat = {m["equipment_id"]: m for m in park_mqtt.catalogue()["machines"]}
    assert cat["blend-tank-01"]["active_faults"] == {"f8": 0.4}
    assert cat["filler-01"]["active_faults"] == {"f12": 0.9}

    park_mqtt.clear("blend-tank-01", "f8")
    cat = {m["equipment_id"]: m for m in park_mqtt.catalogue()["machines"]}
    assert cat["blend-tank-01"]["active_faults"] == {}
    assert cat["filler-01"]["active_faults"] == {"f12": 0.9}


def test_clear_all_ruimt_alles_op(park_mqtt):
    park_mqtt.inject("blend-tank-01", "f8", 0.4)
    park_mqtt.inject("filler-01", "f12", 0.9)
    park_mqtt.inject("case-packer-01", "f8", 0.2)
    res = park_mqtt.clear_all()
    assert res["cleared"] == 12
    for m in park_mqtt.catalogue()["machines"]:
        assert m["active_faults"] == {}, m["equipment_id"]
